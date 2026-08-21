from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, ConversationService, RuleBasedExtractor  # noqa: E402


class LargeCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = Catalog.from_json(BACKEND / "data" / "large-catalog.json")

    def test_stress_profile_has_expected_size(self) -> None:
        self.assertEqual(len(self.catalog.locations), 250)
        self.assertEqual(len(self.catalog.providers), 2_500)
        self.assertEqual(len(self.catalog.appointment_types), 500)

    def test_original_evaluation_entities_still_resolve(self) -> None:
        self.assertEqual(
            self.catalog.resolve_appointment_type("MRI - Knee").selected["id"],
            "appt_065",
        )
        self.assertEqual(
            self.catalog.resolve_location("Midtown Medical Group").selected["id"],
            "loc_005",
        )

    def test_generated_data_is_isolated_from_original_scenarios(self) -> None:
        original_providers = [
            item for item in self.catalog.providers.values()
            if not item["id"].startswith("stress_")
        ]
        generated_providers = [
            item for item in self.catalog.providers.values()
            if item["id"].startswith("stress_")
        ]
        self.assertTrue(all(
            not appointment_id.startswith("stress_")
            for provider in original_providers
            for appointment_id in provider["appointment_type_ids"]
        ))
        self.assertTrue(all(
            appointment_id.startswith("stress_")
            for provider in generated_providers
            for appointment_id in provider["appointment_type_ids"]
        ))

    def test_every_generated_appointment_has_an_eligible_route(self) -> None:
        offered_count = Counter(
            appointment_id
            for provider in self.catalog.providers.values()
            for appointment_id in provider["appointment_type_ids"]
        )
        for appointment in self.catalog.appointment_types.values():
            if not appointment["id"].startswith("stress_"):
                continue
            self.assertGreater(offered_count[appointment["id"]], 0)
            capability = appointment.get("required_capability")
            eligible = any(
                appointment["id"] in provider["appointment_type_ids"]
                and any(
                    not capability
                    or capability in self.catalog.locations[location_id]["capabilities"]
                    for location_id in provider["location_ids"]
                )
                for provider in self.catalog.providers.values()
            )
            self.assertTrue(eligible, appointment["id"])

    def test_catalog_contains_real_ambiguity(self) -> None:
        duplicate_names = Counter(
            provider["name"] for provider in self.catalog.providers.values()
        )
        self.assertTrue(any(count > 1 for count in duplicate_names.values()))
        result = self.catalog.resolve_appointment_type("network Diagnostic Review")
        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertGreater(len(result.candidates), 10)

    def test_generated_record_completes_the_real_scheduling_pipeline(self) -> None:
        appointment = next(
            item
            for item in self.catalog.appointment_types.values()
            if item["id"].startswith("stress_") and item["requires_referral"]
        )
        provider = next(
            item
            for item in self.catalog.providers.values()
            if appointment["id"] in item["appointment_type_ids"]
        )
        capability = appointment.get("required_capability")
        location_id = next(
            location_id
            for location_id in provider["location_ids"]
            if not capability
            or capability in self.catalog.locations[location_id]["capabilities"]
        )
        location = self.catalog.locations[location_id]
        service = ConversationService(
            self.catalog,
            extractor=RuleBasedExtractor(self.catalog),
        )
        conversation_id = service.create_conversation()["conversation_id"]

        response = service.process_turn(
            conversation_id,
            (
                "I am an existing patient and my referral is on file. "
                f"Book {appointment['name']} at {location['name']}."
            ),
        )

        patch = response["validated_extraction"]["patch"]
        self.assertEqual(patch["appointment_type"]["raw_text"], appointment["name"])
        self.assertEqual(patch["location"]["raw_text"], location["name"])
        self.assertEqual(response["engine_result"]["decision"]["status"], "READY_FOR_AVAILABILITY")
        self.assertEqual(response["pending_offer"]["kind"], "SLOT_OPTIONS")


if __name__ == "__main__":
    unittest.main()
