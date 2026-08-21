from __future__ import annotations

from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, ConversationService


class SpeculativeTurnTests(unittest.TestCase):
    def setUp(self):
        catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        self.service = ConversationService(catalog)
        self.conversation_id = self.service.create_conversation()["conversation_id"]

    def test_preview_does_not_mutate_state_until_matching_final_text_commits(self):
        prepared = self.service.prepare_turn(
            self.conversation_id,
            "I want to book a dental cleaning",
            message_id="voice_preview_1",
        )

        self.assertIsNotNone(prepared)
        before = self.service.get_conversation(self.conversation_id)
        self.assertEqual(before.message_number, 0)
        self.assertIsNone(before.patient_request.appointment_type)

        result = self.service.commit_prepared_turn(
            self.conversation_id,
            "I want to book a dental cleaning.",
            prepared,
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["speculation"]["used"])
        after = self.service.get_conversation(self.conversation_id)
        self.assertEqual(after.message_number, 1)
        self.assertEqual(after.patient_request.appointment_type.raw_text, "dental cleaning")

    def test_changed_final_transcript_discards_preview(self):
        prepared = self.service.prepare_turn(
            self.conversation_id,
            "I want to book a dental cleaning",
            message_id="voice_preview_2",
        )

        result = self.service.commit_prepared_turn(
            self.conversation_id,
            "I want to book a dental cleaning and I am a new patient",
            prepared,
        )

        self.assertIsNone(result)
        current = self.service.get_conversation(self.conversation_id)
        self.assertEqual(current.message_number, 0)
        self.assertIsNone(current.patient_request.appointment_type)

    def test_state_change_makes_preview_stale(self):
        prepared = self.service.prepare_turn(
            self.conversation_id,
            "I want to book a dental cleaning",
            message_id="voice_preview_3",
        )
        self.service.process_turn(self.conversation_id, "I am a new patient")

        result = self.service.commit_prepared_turn(
            self.conversation_id,
            "I want to book a dental cleaning",
            prepared,
        )

        self.assertIsNone(result)
        current = self.service.get_conversation(self.conversation_id)
        self.assertEqual(current.message_number, 1)
        self.assertEqual(current.patient_request.patient_status.value, "NEW")

    def test_preview_is_disabled_while_an_offer_awaits_an_answer(self):
        self.service.process_turn(
            self.conversation_id,
            "I want to book a dental cleaning",
        )
        current = self.service.get_conversation(self.conversation_id)
        self.assertIsNotNone(current.pending_offer)

        prepared = self.service.prepare_turn(
            self.conversation_id,
            "Yes, book that appointment",
        )

        self.assertIsNone(prepared)
        self.assertEqual(current.message_number, 1)


if __name__ == "__main__":
    unittest.main()
