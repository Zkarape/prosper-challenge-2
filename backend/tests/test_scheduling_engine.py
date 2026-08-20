from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, SchedulingEngine, SchedulingRequest


class SchedulingEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        cls.engine = SchedulingEngine(cls.catalog)

    def dental_request(self, location_requirement="REQUIRED"):
        return SchedulingRequest().apply_patch(
            {
                "observed_intents": ["BOOK_APPOINTMENT"],
                "patient_status": "NEW",
                "referral_status": "NOT_ON_FILE",
                "appointment_type": {"raw_text": "dental cleaning"},
                "provider": {
                    "raw_text": "Dr. Wei Lee",
                    "requirement": "UNSPECIFIED",
                },
                "location": {
                    "raw_text": "Richmond",
                    "requirement": location_requirement,
                },
                "time": {
                    "raw_text": "earliest",
                    "objective": "EARLIEST_AVAILABLE",
                },
                "primary_priority": {"value": "EARLIEST_TIME"},
            }
        )

    def test_required_invalid_location_keeps_exact_proof(self):
        result = self.engine.evaluate(self.dental_request())
        self.assertEqual(result["decision"]["status"], "NO_EXACT_MATCH")
        self.assertEqual(result["valid_candidates"], [])
        requested_rule = next(
            item
            for item in result["rule_results"]
            if item["rule"] == "LOCATION_HAS_REQUIRED_CAPABILITY"
            and item["candidate_id"] is None
        )
        self.assertEqual(requested_rule["status"], "FAIL")
        self.assertIn("Richmond Care Center", requested_rule["reason"])
        self.assertEqual(
            [item["candidate_id"] for item in result["relaxation_candidates"]],
            ["appt_074:prov_018:loc_001"],
        )
        self.assertEqual(
            result["blockers"][0]["code"], "LOCATION_MISSING_CAPABILITY"
        )

    def test_preferred_invalid_location_allows_ranked_alternative(self):
        result = self.engine.evaluate(self.dental_request("PREFERRED"))
        self.assertEqual(result["decision"]["status"], "READY_FOR_AVAILABILITY")
        self.assertEqual(result["valid_candidates"][0]["location_id"], "loc_001")

    def test_requested_provider_mismatch_has_a_specific_failure(self):
        request = SchedulingRequest().apply_patch(
            {
                "patient_status": "EXISTING",
                "appointment_type": {"raw_text": "New Patient Consultation"},
                "provider": {
                    "raw_text": "Dr. David Chen",
                    "requirement": "REQUIRED",
                },
            }
        )
        result = self.engine.evaluate(request)
        proof = next(
            item
            for item in result["rule_results"]
            if item["rule"] == "PROVIDER_OFFERS_APPOINTMENT"
        )
        self.assertEqual(proof["status"], "FAIL")
        self.assertIn("does not offer", proof["reason"])

    def test_question_planner_asks_service_before_other_unknowns(self):
        result = self.engine.evaluate(SchedulingRequest())
        self.assertEqual(result["decision"]["status"], "NEEDS_INFORMATION")
        self.assertEqual(result["next_action"]["fields"], ["appointment_type"])

    def test_decisive_new_patient_failure_does_not_ask_referral(self):
        request = SchedulingRequest().apply_patch(
            {
                "patient_status": "NEW",
                "appointment_type": {"raw_text": "Pre-operative Evaluation"},
            }
        )
        result = self.engine.evaluate(request)
        self.assertEqual(result["decision"]["status"], "BLOCKED")
        self.assertEqual(result["next_action"]["type"], "CANNOT_SCHEDULE")
        self.assertEqual(result["blockers"][0]["code"], "APPOINTMENT_ALLOWS_NEW_PATIENTS")

    def test_referral_is_asked_when_it_changes_the_outcome(self):
        request = SchedulingRequest().apply_patch(
            {
                "patient_status": "NEW",
                "appointment_type": {"raw_text": "Cardiology Consultation"},
            }
        )
        result = self.engine.evaluate(request)
        self.assertEqual(result["next_action"]["fields"], ["referral_status"])

    def test_duplicate_provider_name_stays_ambiguous_without_context(self):
        resolution = self.catalog.resolve_provider("Dr. Linda Ramirez")
        self.assertEqual(resolution.status, "AMBIGUOUS")

    def test_verbose_patient_phrase_resolves_knee_mri(self):
        resolution = self.catalog.resolve_appointment_type(
            "an appointment for my knee MRI"
        )
        self.assertEqual(resolution.status, "RESOLVED")
        self.assertEqual(resolution.selected["id"], "appt_065")
        self.assertEqual(resolution.match_method, "NAME_TOKENS_IN_QUERY")

    def test_catalog_version_is_a_content_hash(self):
        self.assertTrue(self.catalog.version.startswith("sha256:"))
        self.assertEqual(len(self.catalog.version), 71)


if __name__ == "__main__":
    unittest.main()
