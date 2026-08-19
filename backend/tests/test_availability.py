from datetime import date, timedelta
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, MockAvailability, MockBookingService, SchedulingEngine, SchedulingRequest, Slot


class AvailabilityAndBookingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        cls.engine = SchedulingEngine(cls.catalog)

    def eligible_dental_candidate(self):
        request = SchedulingRequest().apply_patch(
            {
                "patient_status": "NEW",
                "appointment_type": {"raw_text": "Dental Cleaning"},
                "provider": {"raw_text": "Dr. Wei Lee", "requirement": "PREFERRED"},
                "location": {"raw_text": "Mission District", "requirement": "REQUIRED"},
            }
        )
        result = self.engine.evaluate(request)
        return request, result["valid_candidates"][0]

    def booking_args(self, request, candidate, slot, **overrides):
        values = {
            "conversation_id": request.conversation_id,
            "offer_id": "offer_123",
            "candidate": candidate,
            "slot": slot,
            "offered_request_fingerprint": request.fingerprint(),
            "current_request_fingerprint": request.fingerprint(),
            "offered_catalog_version": self.catalog.version,
            "current_catalog_version": self.catalog.version,
        }
        values.update(overrides)
        return values

    def test_slots_are_deterministic_and_have_catalog_timezone(self):
        _, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        first = availability.find_slots(candidate, date(2026, 8, 19))
        second = availability.find_slots(candidate, date(2026, 8, 19))
        self.assertEqual(first, second)
        self.assertTrue(all(slot.duration_min == 45 for slot in first))
        self.assertEqual(first[0].to_dict()["timezone"], "America/Los_Angeles")

    def test_success_requires_booking_id_offer_id_and_confirmed_status(self):
        request, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        slot = availability.find_slots(candidate, date(2026, 8, 19))[0]
        booking = MockBookingService(availability).book(
            **self.booking_args(request, candidate, slot)
        )
        self.assertEqual(booking["status"], "confirmed")
        self.assertTrue(booking["booking_id"].startswith("booking_"))
        self.assertEqual(booking["offer_id"], "offer_123")

    def test_idempotent_offer_creates_one_booking(self):
        request, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        service = MockBookingService(availability)
        slot = availability.find_slots(candidate, date(2026, 8, 19))[0]
        args = self.booking_args(request, candidate, slot)
        self.assertEqual(service.book(**args), service.book(**args))

    def test_changed_request_is_rejected(self):
        request, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        slot = availability.find_slots(candidate, date(2026, 8, 19))[0]
        with self.assertRaisesRegex(ValueError, "PATIENT_REQUEST_CHANGED"):
            MockBookingService(availability).book(
                **self.booking_args(
                    request,
                    candidate,
                    slot,
                    current_request_fingerprint="different",
                )
            )

    def test_changed_catalog_is_rejected(self):
        request, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        slot = availability.find_slots(candidate, date(2026, 8, 19))[0]
        with self.assertRaisesRegex(ValueError, "CATALOG_CHANGED"):
            MockBookingService(availability).book(
                **self.booking_args(
                    request, candidate, slot, current_catalog_version="different"
                )
            )

    def test_short_slot_is_rejected(self):
        request, candidate = self.eligible_dental_candidate()
        availability = MockAvailability(self.catalog)
        generated = availability.find_slots(candidate, date(2026, 8, 19))[0]
        short = Slot(
            id="slot_too_short",
            candidate_id=generated.candidate_id,
            start=generated.start,
            end=generated.start + timedelta(minutes=30),
        )
        with self.assertRaisesRegex(ValueError, "SLOT_TOO_SHORT"):
            MockBookingService(availability).book(
                **self.booking_args(request, candidate, short)
            )


if __name__ == "__main__":
    unittest.main()
