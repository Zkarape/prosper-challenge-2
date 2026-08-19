"""OpenAI Structured Outputs adapter for scheduling fact extraction."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any, Protocol

from .prompt import EXTRACTION_PROMPT, PROMPT_VERSION, SCHEMA_VERSION
from .schema import TurnExtraction


@dataclass(frozen=True)
class ExtractionTelemetry:
    model: str | None
    prompt_version: str
    schema_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    duration_ms: float
    response_id: str | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    parsed: TurnExtraction
    telemetry: ExtractionTelemetry


class Extractor(Protocol):
    mode: str

    def extract(
        self,
        *,
        patient_text: str,
        patient_request: dict[str, Any],
        pending_offer: dict[str, Any] | None,
        corrective_feedback: str | None = None,
    ) -> ExtractionResult: ...


class OpenAIExtractor:
    mode = "OPENAI_STRUCTURED"

    def __init__(self, *, client: Any | None = None, model: str | None = None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client
        self.model = model or os.getenv("EXTRACTION_MODEL", "gpt-5.4-mini")

    def extract(
        self,
        *,
        patient_text: str,
        patient_request: dict[str, Any],
        pending_offer: dict[str, Any] | None,
        corrective_feedback: str | None = None,
    ) -> ExtractionResult:
        payload = {
            "current_patient_request": patient_request,
            "pending_offer": pending_offer,
            "latest_patient_utterance": patient_text,
        }
        input_messages = [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        if corrective_feedback:
            input_messages.append(
                {
                    "role": "developer",
                    "content": (
                        "Correct the previous extraction. Validation error: "
                        f"{corrective_feedback}. Return the full schema again."
                    ),
                }
            )

        started = perf_counter()
        response = self.client.responses.parse(
            model=self.model,
            input=input_messages,
            text_format=TurnExtraction,
        )
        duration_ms = round((perf_counter() - started) * 1000, 2)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            status = getattr(response, "status", "incomplete")
            raise RuntimeError(f"EXTRACTION_{str(status).upper()}")

        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        telemetry = ExtractionTelemetry(
            model=self.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            duration_ms=duration_ms,
            response_id=getattr(response, "id", None),
            status=str(getattr(response, "status", "completed")),
        )
        return ExtractionResult(parsed=parsed, telemetry=telemetry)
