"""Strict, observation-only schema for one patient utterance."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(str, Enum):
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"
    ASK_INFORMATION = "ASK_INFORMATION"


class PatchOperation(str, Enum):
    KEEP = "KEEP"
    SET = "SET"
    REPLACE = "REPLACE"
    CLEAR = "CLEAR"


class Requirement(str, Enum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    UNSPECIFIED = "UNSPECIFIED"


class PatientStatusValue(str, Enum):
    NEW = "NEW"
    EXISTING = "EXISTING"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class ReferralStatusValue(str, Enum):
    ON_FILE = "ON_FILE"
    NOT_ON_FILE = "NOT_ON_FILE"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class PendingAnswer(str, Enum):
    NONE = "NONE"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    SELECT = "SELECT"
    UNCLEAR = "UNCLEAR"


class TimeObjective(str, Enum):
    EARLIEST_AVAILABLE = "EARLIEST_AVAILABLE"
    SPECIFIC_TIME = "SPECIFIC_TIME"
    FLEXIBLE = "FLEXIBLE"
    UNSPECIFIED = "UNSPECIFIED"


class PreferencePriority(str, Enum):
    EARLIEST_TIME = "EARLIEST_TIME"
    PROVIDER = "PROVIDER"
    LOCATION = "LOCATION"
    UNSPECIFIED = "UNSPECIFIED"


class PatientStatusChange(StrictModel):
    operation: PatchOperation
    value: PatientStatusValue | None
    evidence: str | None


class ReferralStatusChange(StrictModel):
    operation: PatchOperation
    value: ReferralStatusValue | None
    evidence: str | None


class EntityChange(StrictModel):
    operation: PatchOperation
    raw_text: str | None
    requirement: Requirement | None
    evidence: str | None


class TimeChange(StrictModel):
    operation: PatchOperation
    raw_text: str | None
    objective: TimeObjective | None
    evidence: str | None


class PriorityChange(StrictModel):
    operation: PatchOperation
    value: PreferencePriority | None
    evidence: str | None


class PendingOfferAnswer(StrictModel):
    value: PendingAnswer
    raw_selection_text: str | None
    ordinal: int | None
    evidence: str | None


class UnclearReference(StrictModel):
    raw_text: str
    possible_field: str | None
    evidence: str


class TurnExtraction(StrictModel):
    observed_intents: list[Intent]
    patient_status: PatientStatusChange
    referral_status: ReferralStatusChange
    appointment_type: EntityChange
    provider: EntityChange
    location: EntityChange
    time: TimeChange
    primary_priority: PriorityChange
    pending_answer: PendingOfferAnswer
    unclear_references: list[UnclearReference]


def keep_extraction() -> dict:
    """Return the complete neutral wire object required by Structured Outputs."""

    return {
        "observed_intents": [],
        "patient_status": {"operation": "KEEP", "value": None, "evidence": None},
        "referral_status": {"operation": "KEEP", "value": None, "evidence": None},
        "appointment_type": {
            "operation": "KEEP",
            "raw_text": None,
            "requirement": None,
            "evidence": None,
        },
        "provider": {
            "operation": "KEEP",
            "raw_text": None,
            "requirement": None,
            "evidence": None,
        },
        "location": {
            "operation": "KEEP",
            "raw_text": None,
            "requirement": None,
            "evidence": None,
        },
        "time": {
            "operation": "KEEP",
            "raw_text": None,
            "objective": None,
            "evidence": None,
        },
        "primary_priority": {
            "operation": "KEEP",
            "value": None,
            "evidence": None,
        },
        "pending_answer": {
            "value": "NONE",
            "raw_selection_text": None,
            "ordinal": None,
            "evidence": None,
        },
        "unclear_references": [],
    }
