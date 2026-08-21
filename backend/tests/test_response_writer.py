from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scheduling import Catalog, ConversationService
from scheduling.response_writer import (
    OpenAIResponseWriter,
    ResponseTelemetry,
    ResponseWritingResult,
)


class FixedResponseWriter:
    mode = "TEST_RESPONSE_WRITER"

    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def write(self, **kwargs):
        self.calls.append(kwargs)
        return ResponseWritingResult(
            text=self.text,
            telemetry=ResponseTelemetry(
                model="gpt-5.4-mini",
                prompt_version="test",
                schema_version="spoken-text-v1",
                input_tokens=80,
                cached_input_tokens=10,
                output_tokens=16,
                duration_ms=125.0,
                response_id="resp_writer_test",
                status="completed",
            ),
        )


class ResponseWriterTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog.from_json(BACKEND / "data" / "catalog.json")

    def test_generated_text_reaches_the_turn_result_and_usage_ledger(self):
        writer = FixedResponseWriter(
            "Could you tell me whether you are a new or existing patient?"
        )
        service = ConversationService(
            self.catalog,
            response_writer=writer,
            today_provider=lambda: date(2026, 8, 19),
        )
        conversation_id = service.create_conversation()["conversation_id"]

        result = service.process_turn(conversation_id, "Book a dental cleaning.")

        self.assertEqual(result["assistant_message"], writer.text)
        self.assertEqual(result["response_writer_mode"], "TEST_RESPONSE_WRITER")
        self.assertEqual(result["usage"]["model_call_count"], 1)
        self.assertEqual(result["usage_events"][0]["stage"], "RESPONSE_WRITING")
        self.assertEqual(result["latency_breakdown"]["response_writing_ms"], 125.0)
        self.assertEqual(writer.calls[0]["patient_text"], "Book a dental cleaning.")

    def test_unsafe_generated_booking_claim_uses_checked_fallback(self):
        writer = FixedResponseWriter("Your appointment has been booked.")
        service = ConversationService(self.catalog, response_writer=writer)
        conversation_id = service.create_conversation()["conversation_id"]

        result = service.process_turn(conversation_id, "Book a dental cleaning.")

        self.assertNotEqual(result["assistant_message"], writer.text)
        self.assertNotIn("has been booked", result["assistant_message"])
        self.assertIn("?", result["assistant_message"])
        self.assertEqual(result["usage"]["model_call_count"], 1)

    def test_openai_adapter_uses_checked_plan_and_provider_usage(self):
        class Responses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    output_text="Would Tuesday at 9 AM work for you?",
                    usage=SimpleNamespace(
                        input_tokens=90,
                        output_tokens=12,
                        input_tokens_details=SimpleNamespace(cached_tokens=20),
                    ),
                    id="resp_openai_writer",
                    status="completed",
                )

        responses = Responses()
        writer = OpenAIResponseWriter(
            client=SimpleNamespace(responses=responses), model="test-model"
        )
        result = writer.write(
            patient_text="As soon as possible.",
            deterministic_draft="Would Tuesday at 9 AM work for you?",
            engine_result={
                "decision": {"status": "SLOTS_FOUND"},
                "next_action": {"type": "OFFER_SLOTS"},
                "blockers": [],
            },
            pending_offer={"kind": "SLOT_OPTIONS"},
            booking=None,
            recent_context=[],
        )

        payload = json.loads(responses.kwargs["input"])
        self.assertEqual(
            payload["response_plan"]["deterministic_draft"],
            "Would Tuesday at 9 AM work for you?",
        )
        self.assertFalse(payload["response_plan"]["booking_confirmed"])
        self.assertEqual(result.telemetry.input_tokens, 90)
        self.assertEqual(result.telemetry.cached_input_tokens, 20)


if __name__ == "__main__":
    unittest.main()
