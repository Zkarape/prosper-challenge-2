from datetime import date
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, ConversationService


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        self.service = ConversationService(catalog, today_provider=lambda: date(2026, 8, 19))
        self.conversation_id = self.service.create_conversation()["conversation_id"]

    def turn(self, utterance, message_id=None):
        return self.service.process_turn(
            self.conversation_id, utterance, message_id=message_id
        )

    def start_golden_path(self):
        return self.turn(
            "I'm a new patient looking for the earliest dental cleaning with "
            "Dr. Wei Lee. Richmond is a must."
        )

    def test_golden_path_uses_three_server_owned_offers_then_books(self):
        first = self.start_golden_path()
        self.assertEqual(first["engine_result"]["decision"]["status"], "NO_EXACT_MATCH")
        self.assertEqual(first["pending_offer"]["kind"], "ALTERNATIVE_LOCATION")
        self.assertIn("does not have the required dental capability", first["assistant_message"])

        second = self.turn("Yes, Mission District works.")
        self.assertEqual(second["pending_offer"]["kind"], "SLOT_OPTIONS")
        self.assertEqual(len(second["offered_slots"]), 1)
        self.assertIn("earliest opening", second["assistant_message"])

        third = self.turn("The first one.")
        self.assertEqual(third["pending_offer"]["kind"], "CONFIRM_BOOKING")
        self.assertIsNone(third["booking"])
        self.assertIn("Please confirm", third["assistant_message"])

        fourth = self.turn("Yes, book it.")
        self.assertEqual(fourth["booking"]["status"], "confirmed")
        self.assertEqual(fourth["booking"]["offer_id"], third["pending_offer"]["offer_id"])
        self.assertIsNone(fourth["pending_offer"])
        self.assertIn("booked", fourth["assistant_message"])
        evaluation = self.service.conversation_evaluation(self.conversation_id)
        self.assertEqual(evaluation["status"], "COMPLETED")
        self.assertEqual(evaluation["outcome"], "BOOKING_CONFIRMED")
        self.assertTrue(evaluation["safe"])

    def test_unclear_slot_selection_does_not_guess(self):
        self.turn(
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available."
        )
        response = self.turn("Maybe sometime later.")
        self.assertEqual(response["pending_offer"]["kind"], "SLOT_OPTIONS")
        self.assertIn("earliest time", response["assistant_message"])

    def test_missing_time_defaults_to_earliest_without_weekday_question(self):
        first = self.turn("I want to book an appointment for my knee MRI.")
        self.assertEqual(first["patient_request"]["time"]["objective"], "EARLIEST_AVAILABLE")
        self.assertEqual(first["patient_request"]["primary_priority"], "EARLIEST_TIME")
        self.assertEqual(first["engine_result"]["next_action"]["fields"], ["patient_status"])
        self.assertNotIn("day of the week", first["assistant_message"].lower())
        self.assertTrue(
            any(item["stage"] == "Default" for item in first["trace"])
        )

        second = self.turn("I am an existing patient.")
        self.assertIn("referral", second["assistant_message"].lower())
        third = self.turn("Yes, it is on file.")
        self.assertEqual(len(third["offered_slots"]), 1)
        self.assertIn("earliest opening", third["assistant_message"])
        self.assertNotIn("day of the week", third["assistant_message"].lower())

    def test_ineligible_new_patient_is_offered_a_safe_recovery_path(self):
        self.turn("I want to book an appointment for my knee MRI.")
        blocked = self.turn("I am a new patient.")

        self.assertEqual(blocked["pending_offer"]["kind"], "RECOVERY_OPTIONS")
        self.assertIn("different appointment type", blocked["assistant_message"])
        self.assertIn("clinic staff", blocked["assistant_message"])

        evaluation = self.service.finish_conversation(self.conversation_id)
        self.assertEqual(evaluation["outcome"], "CORRECTLY_BLOCKED")
        self.assertTrue(evaluation["safe"])

        # Use a new conversation to verify the recovery branch itself.
        self.conversation_id = self.service.create_conversation()["conversation_id"]
        self.turn("I want to book an appointment for my knee MRI.")
        self.turn("I am a new patient.")

        recovery = self.turn("I want a different appointment.")
        self.assertIsNone(recovery["patient_request"]["appointment_type"])
        self.assertEqual(recovery["patient_request"]["patient_status"], "NEW")
        self.assertIn("What type of appointment", recovery["assistant_message"])

    def test_declined_confirmation_does_not_book(self):
        self.turn(
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available."
        )
        self.turn("First")
        response = self.turn("No, do not book it")
        self.assertIsNone(response["booking"])
        self.assertIsNone(response["pending_offer"])
        self.assertIn("Nothing was booked", response["assistant_message"])

    def test_unclear_confirmation_keeps_exact_offer_pending(self):
        self.turn(
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available."
        )
        selected = self.turn("First")
        response = self.turn("Maybe")
        self.assertEqual(
            response["pending_offer"]["offer_id"], selected["pending_offer"]["offer_id"]
        )
        self.assertIn("yes to confirm or no to cancel", response["assistant_message"])

    def test_request_change_replaces_a_stale_slot_offer(self):
        initial = self.turn(
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available."
        )
        changed = self.turn("Actually, any doctor is fine.")
        self.assertNotEqual(
            initial["pending_offer"]["offer_id"], changed["pending_offer"]["offer_id"]
        )
        self.assertIsNone(changed["patient_request"]["provider"])

    def test_slot_lost_before_confirmation_is_not_booked(self):
        offered = self.turn(
            "I'm a new patient. Book a dental cleaning with Dr. Wei Lee at "
            "Mission District, earliest available."
        )
        slot_id = offered["offered_slots"][0]["slot_id"]
        self.turn("First")
        self.service.availability.reserve(slot_id)
        response = self.turn("Yes")
        self.assertIsNone(response["booking"])
        self.assertEqual(response["pending_offer"]["kind"], "SLOT_OPTIONS")
        self.assertNotEqual(response["offered_slots"][0]["slot_id"], slot_id)

    def test_duplicate_message_id_is_idempotent(self):
        first = self.turn("Book a dental cleaning", message_id="message_same")
        second = self.turn("This different text is ignored", message_id="message_same")
        self.assertEqual(first, second)
        conversation = self.service.get_conversation(self.conversation_id)
        self.assertEqual(conversation.message_number, 1)

    def test_information_question_does_not_become_a_booking_decision(self):
        response = self.turn("Where does Dr. Wei Lee work?")
        self.assertEqual(response["engine_result"]["next_action"]["type"], "ANSWER_INFORMATION")
        self.assertIn("practices at", response["assistant_message"])
        self.assertIsNone(response["patient_request"]["current_goal"])
        self.assertIsNone(response["patient_request"]["provider"])

    def test_yes_answers_a_referral_question_without_llm_policy_authority(self):
        first = self.turn("I'm a new patient. Book a Cardiology Consultation.")
        self.assertEqual(first["pending_offer"]["kind"], "FIELD_OPTIONS")
        self.assertIn("referral", first["assistant_message"].lower())
        second = self.turn("Yes")
        self.assertEqual(second["patient_request"]["referral_status"], "ON_FILE")
        self.assertNotEqual(second["engine_result"]["decision"]["status"], "BLOCKED")

    def test_cancel_goal_is_handed_off_without_entering_booking(self):
        response = self.turn("I need to cancel my appointment")
        self.assertEqual(response["engine_result"]["next_action"]["type"], "HANDOFF_TO_STAFF")
        self.assertIsNone(response["booking"])


if __name__ == "__main__":
    unittest.main()
