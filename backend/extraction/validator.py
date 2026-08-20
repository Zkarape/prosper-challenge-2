"""Semantic validation between untrusted model output and patient request updates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import PatchOperation, PendingAnswer, TurnExtraction


INTERNAL_ID = re.compile(r"\b(?:loc|prov|appt|slot|offer|booking)_[a-z0-9]+\b", re.I)
MAX_ENTITY_LENGTH = 160
MAX_EVIDENCE_LENGTH = 240


class SemanticValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedExtraction:
    patch: dict[str, Any]
    pending_answer: str
    selection_ordinal: int | None
    raw_selection_text: str | None
    unclear_references: list[dict[str, Any]]


class ExtractionValidator:
    def validate_and_convert(
        self,
        *,
        extraction: TurnExtraction,
        transcript: str,
        patient_request: Any,
        pending_offer: Any | None,
    ) -> ValidatedExtraction:
        transcript_normalized = _normalize(transcript)
        patch: dict[str, Any] = {}

        observed = [item.value for item in extraction.observed_intents]
        if observed:
            patch["observed_intents"] = observed

        for field_name in ("patient_status", "referral_status"):
            change = getattr(extraction, field_name)
            self._validate_change(change, field_name, transcript_normalized, "value")
            if change.operation != PatchOperation.KEEP:
                if change.operation == PatchOperation.CLEAR:
                    value = "UNKNOWN"
                else:
                    value = change.value.value
                    current = getattr(patient_request, field_name).value
                    if (
                        change.operation == PatchOperation.SET
                        and current not in {"UNKNOWN", value}
                    ):
                        value = "CONFLICTING"
                patch[field_name] = {"operation": change.operation.value, "value": value}

        for field_name in ("appointment_type", "provider", "location"):
            change = getattr(extraction, field_name)
            self._validate_change(change, field_name, transcript_normalized, "raw_text")
            current_entity = getattr(patient_request, field_name)
            if (
                change.operation == PatchOperation.SET
                and current_entity is not None
                and _normalize(change.raw_text or "") != _normalize(current_entity.raw_text)
            ):
                raise SemanticValidationError(
                    f"{field_name}: use REPLACE to change an existing value"
                )
            if change.operation != PatchOperation.KEEP:
                payload: dict[str, Any] = {"operation": change.operation.value}
                if change.raw_text is not None:
                    payload["raw_text"] = change.raw_text.strip()
                if change.requirement is not None:
                    payload["requirement"] = change.requirement.value
                patch[field_name] = payload

        change = extraction.time
        self._validate_change(change, "time", transcript_normalized, "raw_text")
        if change.operation != PatchOperation.KEEP:
            payload = {"operation": change.operation.value}
            if change.raw_text is not None:
                payload["raw_text"] = change.raw_text.strip()
            if change.objective is not None:
                payload["objective"] = change.objective.value
            patch["time"] = payload

        priority = extraction.primary_priority
        self._validate_change(priority, "primary_priority", transcript_normalized, "value")
        if priority.operation != PatchOperation.KEEP:
            patch["primary_priority"] = {
                "operation": priority.operation.value,
                "value": priority.value.value if priority.value is not None else None,
            }

        answer = extraction.pending_answer
        self._validate_text(answer.evidence, "pending_answer.evidence", transcript_normalized)
        if answer.raw_selection_text:
            self._validate_text(
                answer.raw_selection_text,
                "pending_answer.raw_selection_text",
                transcript_normalized,
            )
        if answer.value in {PendingAnswer.ACCEPT, PendingAnswer.REJECT, PendingAnswer.SELECT}:
            if pending_offer is None:
                raise SemanticValidationError(
                    f"{answer.value.value} requires a current pending offer"
                )
        if answer.value == PendingAnswer.SELECT:
            if answer.ordinal is None and not answer.raw_selection_text:
                raise SemanticValidationError("SELECT requires an ordinal or selection text")
            if answer.ordinal is not None and answer.ordinal < 1:
                raise SemanticValidationError("Selection ordinal must be one-based")
        elif answer.ordinal is not None or answer.raw_selection_text is not None:
            raise SemanticValidationError(
                "Selection details are allowed only when pending_answer is SELECT"
            )

        unclear = [item.model_dump(mode="json") for item in extraction.unclear_references]
        for item in extraction.unclear_references:
            self._validate_text(item.evidence, "unclear_reference.evidence", transcript_normalized)
            self._validate_text(item.raw_text, "unclear_reference.raw_text", transcript_normalized)

        return ValidatedExtraction(
            patch=patch,
            pending_answer=answer.value.value,
            selection_ordinal=answer.ordinal,
            raw_selection_text=answer.raw_selection_text,
            unclear_references=unclear,
        )

    def _validate_change(
        self,
        change: Any,
        field_name: str,
        transcript_normalized: str,
        value_field: str,
    ) -> None:
        value = getattr(change, value_field)
        other_values = [
            item
            for name, item in vars(change).items()
            if name not in {"operation", "evidence"} and item is not None
        ]
        if change.operation == PatchOperation.KEEP:
            # Structured models occasionally repeat current context in fields that
            # are explicitly marked KEEP. Those values have no authority and are
            # safely discarded rather than making the patient repeat the turn.
            return
        if change.operation in {PatchOperation.SET, PatchOperation.REPLACE} and value is None:
            raise SemanticValidationError(
                f"{field_name}: {change.operation.value} requires {value_field}"
            )
        if change.operation == PatchOperation.CLEAR and other_values:
            raise SemanticValidationError(f"{field_name}: CLEAR cannot include a value")
        self._validate_text(change.evidence, f"{field_name}.evidence", transcript_normalized)
        if isinstance(value, str):
            self._validate_entity_text(value, field_name)

    @staticmethod
    def _validate_entity_text(value: str, field_name: str) -> None:
        if not value.strip() or len(value) > MAX_ENTITY_LENGTH:
            raise SemanticValidationError(f"{field_name}: invalid text length")
        if INTERNAL_ID.search(value):
            raise SemanticValidationError(f"{field_name}: internal IDs are not accepted")

    @staticmethod
    def _validate_text(
        value: str | None, field_name: str, transcript_normalized: str
    ) -> None:
        if value is None:
            return
        if len(value) > MAX_EVIDENCE_LENGTH:
            raise SemanticValidationError(f"{field_name}: text is too long")
        normalized = _normalize(value)
        if not normalized or normalized not in transcript_normalized:
            raise SemanticValidationError(
                f"{field_name}: evidence is not grounded in the latest utterance"
            )


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())
