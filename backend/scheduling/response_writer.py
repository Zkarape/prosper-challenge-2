"""LLM adapter that turns a checked scheduling result into spoken text."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any


RESPONSE_PROMPT_VERSION = "2026-08-21.1"
RESPONSE_PROMPT = """You write the clinic scheduling assistant's next spoken reply.
The response_plan is authoritative. Express it naturally, but never add, remove,
or change a fact, option, time, provider, location, policy result, or booking state.
Never claim that an appointment is booked unless booking_confirmed is true.
Keep the reply to one or two short sentences. Ask only the question already present
in deterministic_draft. Output only the patient-facing reply, with no labels."""

INTERNAL_ID = re.compile(
    r"\b(?:loc|prov|appt|slot|offer|booking|candidate)_[a-z0-9]+\b", re.I
)
FALSE_BOOKING_CLAIM = re.compile(
    r"\b(?:your appointment|the appointment|it)\s+(?:is|has been)\s+"
    r"(?:successfully\s+)?(?:booked|confirmed|scheduled)\b",
    re.I,
)


@dataclass(frozen=True)
class ResponseTelemetry:
    model: str
    prompt_version: str
    schema_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    duration_ms: float
    response_id: str | None
    status: str


@dataclass(frozen=True)
class ResponseWritingResult:
    text: str
    telemetry: ResponseTelemetry


class OpenAIResponseWriter:
    """Rewrite a deterministic response plan without granting action authority."""

    mode = "OPENAI_RESPONSE_WRITER"

    def __init__(self, *, client: Any | None = None, model: str | None = None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client
        self.model = model or os.getenv(
            "RESPONSE_MODEL", os.getenv("EXTRACTION_MODEL", "gpt-5.4-mini")
        )

    def write(
        self,
        *,
        patient_text: str,
        deterministic_draft: str,
        engine_result: dict[str, Any],
        pending_offer: dict[str, Any] | None,
        booking: dict[str, Any] | None,
        recent_context: list[dict[str, str]],
    ) -> ResponseWritingResult:
        payload = {
            "latest_patient_utterance": patient_text,
            "recent_context": recent_context[-2:],
            "response_plan": {
                "deterministic_draft": deterministic_draft,
                "decision_status": (engine_result.get("decision") or {}).get("status"),
                "next_action": (engine_result.get("next_action") or {}).get("type"),
                "blocker_reasons": [
                    item.get("reason")
                    for item in engine_result.get("blockers", [])[:2]
                    if item.get("reason")
                ],
                "pending_offer_kind": (pending_offer or {}).get("kind"),
                "booking_confirmed": bool(
                    booking and booking.get("status") == "confirmed"
                ),
            },
        }
        started = perf_counter()
        response = self.client.responses.create(
            model=self.model,
            instructions=RESPONSE_PROMPT,
            input=json.dumps(payload, ensure_ascii=False),
            max_output_tokens=160,
            store=False,
        )
        duration_ms = round((perf_counter() - started) * 1000, 2)
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        text = str(getattr(response, "output_text", "") or "").strip()
        telemetry = ResponseTelemetry(
            model=self.model,
            prompt_version=RESPONSE_PROMPT_VERSION,
            schema_version="spoken-text-v1",
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            duration_ms=duration_ms,
            response_id=getattr(response, "id", None),
            status=str(getattr(response, "status", "completed")),
        )
        return ResponseWritingResult(text=text, telemetry=telemetry)


def safe_spoken_response(
    generated: str,
    *,
    deterministic_draft: str,
    booking_confirmed: bool,
) -> str | None:
    """Accept only bounded patient-facing text; otherwise use the checked draft."""

    text = " ".join(generated.split())
    if not text or len(text) > 500 or INTERNAL_ID.search(text):
        return None
    if not booking_confirmed and FALSE_BOOKING_CLAIM.search(text):
        return None
    if booking_confirmed and not re.search(r"\b(?:booked|confirmed|scheduled)\b", text, re.I):
        return None
    draft_asks = "?" in deterministic_draft
    if draft_asks and "?" not in text:
        return None
    return text
