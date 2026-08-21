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
    def __init__(self, catalog: Any | None = None):
        self.catalog = catalog

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

        observed = self._validated_intents(
            [item.value for item in extraction.observed_intents],
            transcript,
            patient_request,
            pending_offer,
        )
        if observed:
            patch["observed_intents"] = observed

        for field_name in ("patient_status", "referral_status"):
            change = getattr(extraction, field_name)
            self._validate_change(change, field_name, transcript_normalized, "value")
            if change.operation != PatchOperation.KEEP:
                if not self._status_change_is_grounded(
                    field_name,
                    change.value.value if change.value is not None else None,
                    change.operation,
                    transcript,
                ):
                    # A model can attach an unrelated exact quote to a status
                    # patch. Isolate that bad field instead of discarding other
                    # valid observations from the same patient turn.
                    continue
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
                if value == getattr(patient_request, field_name).value:
                    continue
                patch[field_name] = {"operation": change.operation.value, "value": value}

        for field_name in ("appointment_type", "provider", "location"):
            change = getattr(extraction, field_name)
            current_entity = getattr(patient_request, field_name)
            if (
                field_name in {"provider", "location"}
                and self._explicitly_removes_restriction(field_name, transcript)
            ):
                if current_entity is not None:
                    patch[field_name] = {"operation": "CLEAR"}
                continue
            self._validate_change(change, field_name, transcript_normalized, "raw_text")
            if (
                change.operation == PatchOperation.SET
                and current_entity is not None
                and _normalize(change.raw_text or "") != _normalize(current_entity.raw_text)
            ):
                raise SemanticValidationError(
                    f"{field_name}: use REPLACE to change an existing value"
                )
            if change.operation == PatchOperation.CLEAR and current_entity is None:
                # Clearing an already-empty preference changes nothing. Keeping
                # it out of the trusted patch makes the downstream state change
                # describe only meaningful patient updates.
                continue
            if change.operation != PatchOperation.KEEP:
                payload: dict[str, Any] = {"operation": change.operation.value}
                if change.raw_text is not None:
                    payload["raw_text"] = change.raw_text.strip()
                if change.requirement is not None:
                    payload["requirement"] = self._grounded_requirement(
                        field_name,
                        change.raw_text or "",
                        transcript,
                    )
                patch[field_name] = payload

        if (
            self.catalog is not None
            and "appointment_type" not in patch
            and getattr(patient_request, "appointment_type", None) is None
            and (
                "BOOK_APPOINTMENT" in observed
                or getattr(patient_request, "current_goal", None)
                == "BOOK_APPOINTMENT"
            )
        ):
            mention = self.catalog.find_entity_mention(
                transcript, "appointment_type"
            )
            if mention:
                patch["appointment_type"] = {
                    "operation": "SET",
                    "raw_text": mention,
                    "requirement": "UNSPECIFIED",
                }

        change = extraction.time
        self._validate_change(change, "time", transcript_normalized, "raw_text")
        if change.operation != PatchOperation.KEEP:
            payload = {"operation": change.operation.value}
            if change.raw_text is not None:
                payload["raw_text"] = change.raw_text.strip()
            if change.objective is not None:
                objective = change.objective.value
                if objective == "EARLIEST_AVAILABLE" and re.search(
                    r"\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|before|after|at\s+\d|noon)\b",
                    change.raw_text or "",
                    re.I,
                ):
                    objective = "SPECIFIC_TIME"
                payload["objective"] = objective
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
        # Extra copied wording has no authority for ACCEPT/REJECT. It is safely
        # ignored; only SELECT may use an ordinal or selection text to choose an
        # option.

        unclear = [item.model_dump(mode="json") for item in extraction.unclear_references]
        for item in extraction.unclear_references:
            self._validate_text(item.evidence, "unclear_reference.evidence", transcript_normalized)
            self._validate_text(item.raw_text, "unclear_reference.raw_text", transcript_normalized)

        pending_answer = answer.value.value
        pending_kind = getattr(getattr(pending_offer, "kind", None), "value", None)
        scheduling_changes = set(patch) - {"observed_intents"}
        if (
            pending_kind == "CONFIRM_BOOKING"
            and pending_answer == "REJECT"
            and scheduling_changes
        ):
            # A replacement fact is the actionable meaning. Applying it safely
            # invalidates the stale confirmation, so it must not enter the
            # ambiguous "answer plus change" branch.
            pending_answer = "NONE"

        return ValidatedExtraction(
            patch=patch,
            pending_answer=pending_answer,
            selection_ordinal=(
                answer.ordinal if answer.value == PendingAnswer.SELECT else None
            ),
            raw_selection_text=(
                answer.raw_selection_text
                if answer.value == PendingAnswer.SELECT
                else None
            ),
            unclear_references=unclear,
        )

    @staticmethod
    def _status_change_is_grounded(
        field_name: str,
        value: str | None,
        operation: PatchOperation,
        transcript: str,
    ) -> bool:
        normalized = _normalize(transcript)
        uncertain = bool(
            re.search(r"\b(?:do not know|don t know|not sure|unsure|unknown)\b", normalized)
        )
        if operation == PatchOperation.CLEAR or value == "UNKNOWN":
            return uncertain
        if field_name == "patient_status":
            new = bool(
                re.search(
                    r"\b(?:new patient|first visit|first time patient|never been|i am new)\b",
                    normalized,
                )
            )
            existing = bool(
                re.search(
                    r"\b(?:existing patient|returning patient|current patient|"
                    r"established patient|seen .{0,30} before)\b",
                    normalized,
                )
            )
            if value == "NEW":
                return new
            if value == "EXISTING":
                return existing
            return value == "CONFLICTING" and new and existing
        referral_on_file = bool(
            re.search(
                r"\b(?:referral .{0,20}(?:on file|sent|received)|have a referral)\b",
                normalized,
            )
        )
        referral_missing = bool(
            re.search(
                r"\b(?:no referral|do not have a referral|without a referral|"
                r"referral .{0,20}(?:not on file|not received|missing))\b",
                normalized,
            )
            or (
                "referral" in normalized
                and re.search(r"\b(?:not|never) received\b", normalized)
            )
        )
        if value == "ON_FILE":
            return referral_on_file
        if value == "NOT_ON_FILE":
            return referral_missing
        return value == "CONFLICTING" and referral_on_file and referral_missing

    @staticmethod
    def _explicitly_removes_restriction(field_name: str, transcript: str) -> bool:
        normalized = _normalize(transcript)
        noun = "(?:doctor|provider)" if field_name == "provider" else "(?:location|clinic)"
        return bool(
            re.search(rf"\bany {noun}\b", normalized)
            or re.search(rf"\b{noun} (?:does not|doesn t) matter\b", normalized)
            or re.search(rf"\bno {noun} preference\b", normalized)
            or (field_name == "provider" and re.search(r"\bwhoever is (?:fine|available|right)\b", normalized))
        )

    @staticmethod
    def _validated_intents(
        observed: list[str],
        transcript: str,
        patient_request: Any,
        pending_offer: Any | None,
    ) -> list[str]:
        normalized = _normalize(transcript)
        question = bool(
            "?" in transcript
            or re.search(
                r"^(?:what|where|when|who|which|how|do|does|is|are|can|could|would)\b",
                normalized,
            )
        )
        cleaned = [
            intent
            for intent in observed
            if intent != "ASK_INFORMATION" or question
        ]
        pending_kind = getattr(getattr(pending_offer, "kind", None), "value", None)
        if (
            pending_kind in {"CONFIRM_BOOKING", "SLOT_OPTIONS"}
            and getattr(patient_request, "current_goal", None) == "BOOK_APPOINTMENT"
            and "RESCHEDULE_APPOINTMENT" in cleaned
        ):
            cleaned = [
                "BOOK_APPOINTMENT" if item == "RESCHEDULE_APPOINTMENT" else item
                for item in cleaned
            ]
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def _grounded_requirement(
        field_name: str, raw_text: str, transcript: str
    ) -> str:
        if field_name not in {"provider", "location"}:
            return "UNSPECIFIED"
        occurrence = re.search(re.escape(raw_text), transcript, re.I)
        sentence = transcript
        if occurrence:
            left = max(
                transcript.rfind(".", 0, occurrence.start()),
                transcript.rfind("?", 0, occurrence.start()),
                transcript.rfind("!", 0, occurrence.start()),
                transcript.rfind(",", 0, occurrence.start()),
                transcript.rfind(";", 0, occurrence.start()),
            )
            right_candidates = [
                index
                for mark in ".?!,;"
                if (index := transcript.find(mark, occurrence.end())) != -1
            ]
            right = min(right_candidates) if right_candidates else len(transcript)
            sentence = transcript[left + 1 : right]
        normalized = _normalize(sentence)
        full = _normalize(transcript)
        normalized_entity = _normalize(raw_text)
        hard_word = r"(?:must|only|has to|have to|required|cannot change)"
        entity_hard = any(
            normalized_entity
            and (
                re.search(
                    rf"\b{re.escape(normalized_entity)}\b.{{0,20}}\b{hard_word}\b",
                    segment,
                )
                or re.search(
                    rf"\b{hard_word}\b.{{0,20}}\b{re.escape(normalized_entity)}\b",
                    segment,
                )
            )
            for segment in (
                _normalize(item)
                for item in re.split(r"[.?!;]+", transcript)
            )
        )
        field_hard = bool(
            field_name == "location"
            and re.search(r"\b(?:that\s+)?(?:location|clinic)\b.{0,25}\brequired\b", full)
            or field_name == "provider"
            and re.search(r"\b(?:that\s+)?(?:doctor|provider)\b.{0,25}\brequired\b", full)
        )
        if (
            re.search(rf"\b{hard_word}\b", normalized)
            or entity_hard
            or field_hard
        ):
            return "REQUIRED"
        if re.search(
            r"\b(?:prefer|preferred|preferably|ideally|if possible)\b",
            normalized,
        ):
            return "PREFERRED"
        return "UNSPECIFIED"

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
