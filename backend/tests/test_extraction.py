from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conversation import OfferKind, OfferOption, PendingOffer
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

    def test_keep_cannot_smuggle_a_new_value(self):
        wire = keep_extraction()
        wire["provider"]["raw_text"] = "Dr. Lee"
        with self.assertRaisesRegex(SemanticValidationError, "KEEP"):
            self.validate(wire, "Dr. Lee")

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
                    wire["provider"]["raw_text"] = "invented"
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
        self.assertIn("KEEP", extractor.calls[1])
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


if __name__ == "__main__":
    unittest.main()
