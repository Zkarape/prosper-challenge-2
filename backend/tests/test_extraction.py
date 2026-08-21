from datetime import date
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conversation import OfferKind, OfferOption, PendingOffer, UsageEvent
from extraction import ExtractionValidator, OpenAIExtractor, SemanticValidationError, TurnExtraction
from extraction.llm_extractor import ExtractionResult, ExtractionTelemetry
from extraction.schema import keep_extraction
from scheduling import Catalog, ConversationService, SchedulingRequest
from scheduling.extractor import RuleBasedExtractor


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")
        self.validator = ExtractionValidator()
        self.request = SchedulingRequest()

    def validate(self, wire, transcript="hello", pending=None):
        return self.validator.validate_and_convert(
            extraction=TurnExtraction.model_validate(wire),
            transcript=transcript,
            patient_request=self.request,
            pending_offer=pending,
        )

    def test_keep_values_are_discarded_without_changing_the_request(self):
        wire = keep_extraction()
        wire["provider"]["raw_text"] = "Dr. Lee"
        validated = self.validate(wire, "Dr. Lee")
        self.assertNotIn("provider", validated.patch)

    def test_evidence_must_come_from_latest_utterance(self):
        wire = keep_extraction()
        wire["location"] = {
            "operation": "SET",
            "raw_text": "Richmond",
            "requirement": "UNSPECIFIED",
            "evidence": "Mission District",
        }
        with self.assertRaisesRegex(SemanticValidationError, "not grounded"):
            self.validate(wire, "Richmond")

    def test_unrelated_words_cannot_clear_patient_status(self):
        self.request = SchedulingRequest.from_dict(
            {"conversation_id": "conv_test", "patient_status": "EXISTING"}
        )
        wire = keep_extraction()
        wire["patient_status"] = {
            "operation": "CLEAR",
            "value": None,
            "evidence": "any doctor is fine",
        }
        validated = self.validate(wire, "Actually, any doctor is fine.")
        self.assertNotIn("patient_status", validated.patch)

    def test_any_provider_is_a_deterministic_clear(self):
        self.request = SchedulingRequest.from_dict(
            {
                "conversation_id": "conv_test",
                "provider": {
                    "raw_text": "Dr. Hannah Nguyen",
                    "requirement": "PREFERRED",
                }
            }
        )
        wire = keep_extraction()
        wire["provider"] = {
            "operation": "REPLACE",
            "raw_text": "any doctor",
            "requirement": "UNSPECIFIED",
            "evidence": "any doctor",
        }
        validated = self.validate(wire, "Actually, any doctor is fine.")
        self.assertEqual(validated.patch["provider"], {"operation": "CLEAR"})

    def test_select_requires_a_pending_offer(self):
        wire = keep_extraction()
        wire["pending_answer"] = {
            "value": "SELECT",
            "raw_selection_text": "first",
            "ordinal": 1,
            "evidence": "first",
        }
        with self.assertRaisesRegex(SemanticValidationError, "pending offer"):
            self.validate(wire, "the first one")

    def test_select_keeps_only_ordinal_not_an_llm_catalog_id(self):
        wire = keep_extraction()
        wire["pending_answer"] = {
            "value": "SELECT",
            "raw_selection_text": "first",
            "ordinal": 1,
            "evidence": "first",
        }
        pending = PendingOffer(
            kind=OfferKind.SLOT_OPTIONS,
            request_fingerprint=self.request.fingerprint(),
            catalog_version=self.catalog.version,
            options=[OfferOption("server_option", "First slot", {"slot": {}})],
        )
        validated = self.validate(wire, "the first one", pending)
        self.assertEqual(validated.selection_ordinal, 1)
        self.assertNotIn("server_option", str(validated))

    def test_plain_provider_name_is_unspecified_not_required(self):
        result = RuleBasedExtractor(self.catalog).extract(
            patient_text="I want Dr. Wei Lee.",
            patient_request={},
            pending_offer=None,
        )
        self.assertEqual(result.parsed.provider.requirement.value, "UNSPECIFIED")

    def test_requirement_words_are_bound_to_the_relevant_entity(self):
        wire = keep_extraction()
        wire["provider"] = {
            "operation": "SET",
            "raw_text": "Dr. Linda Ramirez",
            "requirement": "REQUIRED",
            "evidence": "Dr. Linda Ramirez",
        }
        wire["location"] = {
            "operation": "SET",
            "raw_text": "Richmond",
            "requirement": "UNSPECIFIED",
            "evidence": "Richmond",
        }
        transcript = (
            "I need an MRI with Dr. Linda Ramirez in Richmond. "
            "Richmond is required."
        )
        validated = self.validate(wire, transcript)
        self.assertEqual(validated.patch["provider"]["requirement"], "UNSPECIFIED")
        self.assertEqual(validated.patch["location"]["requirement"], "REQUIRED")

    def test_accept_ignores_non_authoritative_selection_text(self):
        wire = keep_extraction()
        wire["pending_answer"] = {
            "value": "ACCEPT",
            "raw_selection_text": "yes that works",
            "ordinal": None,
            "evidence": "yes that works",
        }
        pending = PendingOffer(
            kind=OfferKind.ALTERNATIVE_LOCATION,
            request_fingerprint=self.request.fingerprint(),
            catalog_version=self.catalog.version,
            options=[OfferOption("alternative", "Mission District", {})],
        )
        validated = self.validate(wire, "yes that works", pending)
        self.assertEqual(validated.pending_answer, "ACCEPT")
        self.assertIsNone(validated.raw_selection_text)

    def test_reported_conflict_is_not_changed_to_information_intent(self):
        wire = keep_extraction()
        wire["observed_intents"] = ["ASK_INFORMATION"]
        wire["referral_status"] = {
            "operation": "SET",
            "value": "CONFLICTING",
            "evidence": "sent, but the front desk never received it",
        }
        transcript = "The referral was sent, but the front desk never received it."
        validated = self.validate(wire, transcript)
        self.assertNotIn("observed_intents", validated.patch)
        self.assertEqual(
            validated.patch["referral_status"]["value"], "CONFLICTING"
        )

    def test_catalog_alias_guard_recovers_an_omitted_appointment_mention(self):
        self.request.current_goal = "BOOK_APPOINTMENT"
        validator = ExtractionValidator(self.catalog)
        validated = validator.validate_and_convert(
            extraction=TurnExtraction.model_validate(keep_extraction()),
            transcript="I need a follow-up with my doctor.",
            patient_request=self.request,
            pending_offer=None,
        )
        self.assertEqual(
            validated.patch["appointment_type"]["raw_text"], "follow up"
        )

    def test_service_retries_once_with_validation_feedback(self):
        local = RuleBasedExtractor(self.catalog)

        class RepairingExtractor:
            mode = "TEST_REPAIR"

            def __init__(self):
                self.calls = []

            def extract(self, **kwargs):
                self.calls.append(kwargs.get("corrective_feedback"))
                if len(self.calls) == 1:
                    wire = keep_extraction()
                    wire["provider"] = {
                        "operation": "SET",
                        "raw_text": "invented",
                        "requirement": "UNSPECIFIED",
                        "evidence": "invented",
                    }
                    return ExtractionResult(
                        TurnExtraction.model_validate(wire),
                        ExtractionTelemetry(None, "test", "test", 0, 0, 0, 0, None, "completed"),
                    )
                return local.extract(**kwargs)

        extractor = RepairingExtractor()
        service = ConversationService(
            self.catalog,
            extractor=extractor,
            today_provider=lambda: date(2026, 8, 19),
        )
        conversation_id = service.create_conversation()["conversation_id"]
        response = service.process_turn(conversation_id, "Book a dental cleaning")
        self.assertEqual(len(extractor.calls), 2)
        self.assertIn("evidence", extractor.calls[1])
        self.assertEqual(response["patient_request"]["appointment_type"]["raw_text"], "dental cleaning")

    def test_openai_adapter_uses_responses_parse_with_the_strict_model(self):
        class FakeResponses:
            def __init__(self):
                self.kwargs = None

            def parse(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    output_parsed=TurnExtraction.model_validate(keep_extraction()),
                    usage=SimpleNamespace(
                        input_tokens=10,
                        output_tokens=5,
                        input_tokens_details=SimpleNamespace(cached_tokens=3),
                    ),
                    id="resp_test",
                    status="completed",
                )

        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        result = OpenAIExtractor(client=client, model="test-model").extract(
            patient_text="hello",
            patient_request=self.request.to_dict(),
            pending_offer=None,
        )
        self.assertIs(responses.kwargs["text_format"], TurnExtraction)
        self.assertEqual(result.telemetry.cached_input_tokens, 3)

    def test_full_history_is_an_explicit_extractor_strategy(self):
        class FakeResponses:
            def __init__(self):
                self.kwargs = None

            def parse(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    output_parsed=TurnExtraction.model_validate(keep_extraction()),
                    usage=SimpleNamespace(
                        input_tokens=20,
                        output_tokens=5,
                        input_tokens_details=SimpleNamespace(cached_tokens=0),
                    ),
                    id="resp_history_test",
                    status="completed",
                )

        responses = FakeResponses()
        extractor = OpenAIExtractor(
            client=SimpleNamespace(responses=responses),
            model="test-model",
            context_strategy="full_history",
        )
        extractor.extract(
            patient_text="Actually, any doctor is fine.",
            patient_request=self.request.to_dict(),
            pending_offer=None,
            conversation_history=[
                {"role": "patient", "text": "I prefer Dr. Lee."},
                {"role": "assistant", "text": "I will look for Dr. Lee."},
            ],
        )
        payload = json.loads(responses.kwargs["input"][1]["content"])
        self.assertEqual(len(payload["conversation_history"]), 2)
        compact = extractor.for_context_strategy("compact")
        self.assertEqual(compact.context_strategy, "compact")

    def test_usage_event_prices_cached_tokens_without_double_counting(self):
        telemetry = ExtractionTelemetry(
            "gpt-5.4-mini",
            "test",
            "test",
            600,
            100,
            90,
            420,
            "resp_pricing",
            "completed",
        )
        event = UsageEvent.from_telemetry(
            conversation_id="conv_test",
            turn_id="turn_test",
            stage="EXTRACTION",
            telemetry=telemetry,
        )
        self.assertEqual(event.total_tokens, 690)
        self.assertEqual(event.estimated_cost_usd, 0.0007875)

    def test_conversation_evaluation_sums_all_model_calls(self):
        local = RuleBasedExtractor(self.catalog)

        class MeteredExtractor:
            mode = "OPENAI_STRUCTURED"

            def __init__(self):
                self.number = 0

            def extract(self, **kwargs):
                self.number += 1
                parsed = local.extract(**kwargs).parsed
                return ExtractionResult(
                    parsed,
                    ExtractionTelemetry(
                        "gpt-5.4-mini",
                        "test",
                        "test",
                        600,
                        100,
                        90,
                        420,
                        f"resp_{self.number}",
                        "completed",
                    ),
                )

        service = ConversationService(
            self.catalog,
            extractor=MeteredExtractor(),
            today_provider=lambda: date(2026, 8, 19),
        )
        conversation_id = service.create_conversation()["conversation_id"]
        service.process_turn(conversation_id, "Book a dental cleaning")
        service.process_turn(conversation_id, "I am a new patient")
        evaluation = service.finish_conversation(
            conversation_id, forced_outcome="PATIENT_ABANDONED"
        )
        self.assertEqual(evaluation["model_call_count"], 2)
        self.assertEqual(evaluation["total_tokens"], 1380)
        self.assertEqual(evaluation["turn_count"], 2)
        self.assertEqual(evaluation["outcome"], "PATIENT_ABANDONED")
        summary = service.evaluation_summary()
        self.assertEqual(summary["total_tokens_finalized"], 1380)
        self.assertIsNone(summary["tokens_per_safe_completed_task"])


if __name__ == "__main__":
    unittest.main()
