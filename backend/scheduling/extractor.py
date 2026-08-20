"""Offline structured extractor used by tests and no-key local demos.

It implements the same observation-only contract as the OpenAI extractor. It
is deliberately small; production uses ``OpenAIExtractor`` when configured.
"""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any

from extraction.llm_extractor import ExtractionResult, ExtractionTelemetry
from extraction.prompt import PROMPT_VERSION, SCHEMA_VERSION
from extraction.schema import TurnExtraction, keep_extraction

from .catalog import Catalog, normalize


class RuleBasedExtractor:
    mode = "LOCAL_STRUCTURED"

    LOCATION_ALIASES = {
        "richmond": "Richmond",
        "mission district": "Mission District",
        "mission": "Mission",
    }
    APPOINTMENT_ALIASES = {
        "dental cleaning": "dental cleaning",
        "cleaning": "cleaning",
        "knee mri": "knee MRI",
        "physical therapy": "physical therapy",
        "pt evaluation": "PT evaluation",
    }

    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def extract(
        self,
        patient_text: str | None = None,
        *,
        patient_request: dict[str, Any] | None = None,
        pending_offer: dict[str, Any] | None = None,
        corrective_feedback: str | None = None,
    ) -> ExtractionResult:
        del corrective_feedback
        if patient_text is None:
            raise ValueError("patient_text is required")
        started = perf_counter()
        current = patient_request or {}
        wire = keep_extraction()
        normalized = normalize(patient_text)

        if re.search(r"\b(book|schedule|appointment|opening)\b", normalized):
            wire["observed_intents"].append("BOOK_APPOINTMENT")
        if re.search(r"\b(reschedule|move my appointment)\b", normalized):
            wire["observed_intents"].append("RESCHEDULE_APPOINTMENT")
        if re.search(r"\b(cancel my appointment|cancel appointment)\b", normalized):
            wire["observed_intents"].append("CANCEL_APPOINTMENT")
        if "?" in patient_text or re.search(r"\b(do you|does|what|where|when|who)\b", normalized):
            wire["observed_intents"].append("ASK_INFORMATION")

        pending_answer = self._pending_answer(patient_text, pending_offer)
        if pending_answer is not None:
            wire["pending_answer"] = pending_answer
            return self._result(wire, started)

        status_match = re.search(r"\b(new patient|first visit|never been)\b", patient_text, re.I)
        if status_match:
            self._set_status(wire, current, "patient_status", "NEW", status_match.group(0))
        else:
            status_match = re.search(
                r"\b(existing patient|returning patient|seen .{0,30} before)\b",
                patient_text,
                re.I,
            )
            if status_match:
                self._set_status(
                    wire, current, "patient_status", "EXISTING", status_match.group(0)
                )

        referral_match = re.search(
            r"\b(referral (?:is |was )?(?:on file|sent)|have a referral)\b",
            patient_text,
            re.I,
        )
        if referral_match:
            self._set_status(
                wire, current, "referral_status", "ON_FILE", referral_match.group(0)
            )
        else:
            referral_match = re.search(
                r"\b(no referral|do not have a referral|without a referral)\b",
                patient_text,
                re.I,
            )
            if referral_match:
                self._set_status(
                    wire,
                    current,
                    "referral_status",
                    "NOT_ON_FILE",
                    referral_match.group(0),
                )

        self._extract_entity(
            wire,
            current,
            patient_text,
            "appointment_type",
            self.APPOINTMENT_ALIASES,
            self.catalog.appointment_types.values(),
            default_requirement="UNSPECIFIED",
        )
        self._extract_entity(
            wire,
            current,
            patient_text,
            "provider",
            {},
            self.catalog.providers.values(),
            default_requirement="UNSPECIFIED",
        )
        self._extract_entity(
            wire,
            current,
            patient_text,
            "location",
            self.LOCATION_ALIASES,
            self.catalog.locations.values(),
            default_requirement="UNSPECIFIED",
        )

        self._extract_clears(wire, patient_text)

        earliest = re.search(
            r"\b(earliest|soonest|first available|as soon as possible|asap)\b",
            patient_text,
            re.I,
        )
        if earliest:
            operation = "REPLACE" if current.get("time") else "SET"
            wire["time"] = {
                "operation": operation,
                "raw_text": earliest.group(0),
                "objective": "EARLIEST_AVAILABLE",
                "evidence": earliest.group(0),
            }
            wire["primary_priority"] = {
                "operation": "REPLACE" if current.get("primary_priority") else "SET",
                "value": "EARLIEST_TIME",
                "evidence": earliest.group(0),
            }

        return self._result(wire, started)

    def _extract_entity(
        self,
        wire: dict[str, Any],
        current: dict[str, Any],
        patient_text: str,
        field_name: str,
        aliases: dict[str, str],
        records: Any,
        *,
        default_requirement: str,
    ) -> None:
        match = self._match_text(patient_text, aliases, records)
        if match is None:
            return
        requirement = default_requirement
        if field_name in {"provider", "location"}:
            occurrence = re.search(re.escape(match), patient_text, re.I)
            sentence = patient_text
            if occurrence:
                left = max(
                    patient_text.rfind(".", 0, occurrence.start()),
                    patient_text.rfind("?", 0, occurrence.start()),
                    patient_text.rfind("!", 0, occurrence.start()),
                )
                right_candidates = [
                    index
                    for mark in ".?!"
                    if (index := patient_text.find(mark, occurrence.end())) != -1
                ]
                right = min(right_candidates) if right_candidates else len(patient_text)
                sentence = patient_text[left + 1 : right]
            normalized_sentence = normalize(sentence)
            if re.search(r"\b(must|only|has to|have to|a must|required)\b", normalized_sentence):
                requirement = "REQUIRED"
            elif re.search(r"\b(prefer|preferred|ideally|if possible)\b", normalized_sentence):
                requirement = "PREFERRED"
        operation = "REPLACE" if current.get(field_name) else "SET"
        wire[field_name] = {
            "operation": operation,
            "raw_text": match,
            "requirement": requirement,
            "evidence": match,
        }
        if field_name == "appointment_type" and "BOOK_APPOINTMENT" not in wire["observed_intents"]:
            wire["observed_intents"].append("BOOK_APPOINTMENT")

    @staticmethod
    def _extract_clears(wire: dict[str, Any], patient_text: str) -> None:
        clear_patterns = {
            "provider": r"\b(any (?:doctor|provider)|(?:doctor|provider) does not matter)\b",
            "location": r"\b(any location|location (?:does not|doesn't) matter)\b",
        }
        for field_name, pattern in clear_patterns.items():
            match = re.search(pattern, patient_text, re.I)
            if match:
                wire[field_name] = {
                    "operation": "CLEAR",
                    "raw_text": None,
                    "requirement": None,
                    "evidence": match.group(0),
                }

    @staticmethod
    def _set_status(
        wire: dict[str, Any],
        current: dict[str, Any],
        field_name: str,
        value: str,
        evidence: str,
    ) -> None:
        existing = current.get(field_name, "UNKNOWN")
        operation = "REPLACE" if existing not in {None, "UNKNOWN", value} else "SET"
        wire[field_name] = {
            "operation": operation,
            "value": value,
            "evidence": evidence,
        }

    @staticmethod
    def _pending_answer(
        patient_text: str, pending_offer: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        text = normalize(patient_text)
        ordinal_words = {
            "first": 1,
            "1": 1,
            "one": 1,
            "second": 2,
            "2": 2,
            "two": 2,
            "third": 3,
            "3": 3,
            "three": 3,
        }
        if pending_offer:
            if pending_offer.get("kind") == "RECOVERY_OPTIONS":
                recovery_patterns = (
                    (1, r"\b(different|another|other|change|new patient appointment)\b"),
                    (2, r"\b(staff|person|human|clinic help|help with)\b"),
                )
                for ordinal, pattern in recovery_patterns:
                    match = re.search(pattern, patient_text, re.I)
                    if match:
                        return {
                            "value": "SELECT",
                            "raw_selection_text": match.group(0),
                            "ordinal": ordinal,
                            "evidence": match.group(0),
                        }
            for token, ordinal in ordinal_words.items():
                match = re.search(rf"\b{re.escape(token)}\b", patient_text, re.I)
                if match:
                    return {
                        "value": "SELECT",
                        "raw_selection_text": match.group(0),
                        "ordinal": ordinal,
                        "evidence": match.group(0),
                    }
            if RuleBasedExtractor.is_negative(patient_text):
                evidence = RuleBasedExtractor._answer_evidence(patient_text, negative=True)
                return {
                    "value": "REJECT",
                    "raw_selection_text": None,
                    "ordinal": None,
                    "evidence": evidence,
                }
            if RuleBasedExtractor.is_affirmative(patient_text):
                evidence = RuleBasedExtractor._answer_evidence(patient_text, negative=False)
                return {
                    "value": "ACCEPT",
                    "raw_selection_text": None,
                    "ordinal": None,
                    "evidence": evidence,
                }
        elif re.fullmatch(r"\s*(yes|yeah|yep|no|nope|okay|ok)\s*[.!]?\s*", patient_text, re.I):
            evidence = patient_text.strip().rstrip(".!?")
            return {
                "value": "UNCLEAR",
                "raw_selection_text": None,
                "ordinal": None,
                "evidence": evidence,
            }
        return None

    @staticmethod
    def _answer_evidence(patient_text: str, *, negative: bool) -> str:
        pattern = r"\b(no|nope|do not)\b" if negative else r"\b(yes|yeah|yep|sure|okay|ok|works|confirm|book it)\b"
        match = re.search(pattern, patient_text, re.I)
        return match.group(0) if match else patient_text.strip()

    @staticmethod
    def _match_text(patient_text: str, aliases: dict[str, str], records: Any) -> str | None:
        matches: list[str] = []
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\b", patient_text, re.I)
            if match:
                matches.append(match.group(0))
        for record in records:
            match = re.search(rf"\b{re.escape(record['name'])}\b", patient_text, re.I)
            if match:
                matches.append(match.group(0))
        return max(matches, key=len) if matches else None

    @staticmethod
    def is_affirmative(utterance: str) -> bool:
        text = normalize(utterance)
        return bool(re.search(r"\b(yes|yeah|yep|sure|okay|ok|works|confirm|book it)\b", text))

    @staticmethod
    def is_negative(utterance: str) -> bool:
        text = normalize(utterance)
        return bool(re.search(r"\b(no|nope|different|another|do not)\b", text))

    @staticmethod
    def _result(wire: dict[str, Any], started: float) -> ExtractionResult:
        parsed = TurnExtraction.model_validate(wire)
        telemetry = ExtractionTelemetry(
            model=None,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            response_id=None,
            status="completed",
        )
        return ExtractionResult(parsed=parsed, telemetry=telemetry)
