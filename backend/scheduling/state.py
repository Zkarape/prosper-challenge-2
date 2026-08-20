"""The patient scheduling request owned and changed only by application code."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any
from uuid import uuid4


class PatientStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    NEW = "NEW"
    EXISTING = "EXISTING"
    CONFLICTING = "CONFLICTING"


class ReferralStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    ON_FILE = "ON_FILE"
    NOT_ON_FILE = "NOT_ON_FILE"
    CONFLICTING = "CONFLICTING"


class Requirement(str, Enum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    UNSPECIFIED = "UNSPECIFIED"


class PreferencePriority(str, Enum):
    EARLIEST_TIME = "EARLIEST_TIME"
    PROVIDER = "PROVIDER"
    LOCATION = "LOCATION"
    UNSPECIFIED = "UNSPECIFIED"


@dataclass
class EntityRequest:
    raw_text: str
    requirement: Requirement = Requirement.UNSPECIFIED


@dataclass
class TimePreference:
    raw_text: str
    objective: str = "UNSPECIFIED"


@dataclass
class SchedulingRequest:
    conversation_id: str = field(default_factory=lambda: f"conv_{uuid4().hex[:12]}")
    current_goal: str | None = None
    patient_status: PatientStatus = PatientStatus.UNKNOWN
    referral_status: ReferralStatus = ReferralStatus.UNKNOWN
    appointment_type: EntityRequest | None = None
    provider: EntityRequest | None = None
    location: EntityRequest | None = None
    time: TimePreference | None = None
    primary_priority: PreferencePriority = PreferencePriority.UNSPECIFIED

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SchedulingRequest":
        """Restore the compact request snapshot kept by durable storage."""

        def entity(name: str) -> EntityRequest | None:
            item = value.get(name)
            if not item:
                return None
            return EntityRequest(
                raw_text=item["raw_text"],
                requirement=Requirement(item.get("requirement", "UNSPECIFIED")),
            )

        time_value = value.get("time")
        return cls(
            conversation_id=value["conversation_id"],
            current_goal=value.get("current_goal"),
            patient_status=PatientStatus(value.get("patient_status", "UNKNOWN")),
            referral_status=ReferralStatus(value.get("referral_status", "UNKNOWN")),
            appointment_type=entity("appointment_type"),
            provider=entity("provider"),
            location=entity("location"),
            time=(
                TimePreference(
                    raw_text=time_value["raw_text"],
                    objective=time_value.get("objective", "UNSPECIFIED"),
                )
                if time_value
                else None
            ),
            primary_priority=PreferencePriority(
                value.get("primary_priority", "UNSPECIFIED")
            ),
        )

    def apply_patch(self, patch: dict[str, Any]) -> "SchedulingRequest":
        """Apply a trusted patch and return a new patient request."""

        updated = deepcopy(self)

        observed = patch.get("observed_intents", [])
        for intent in observed:
            if intent in {
                "BOOK_APPOINTMENT",
                "RESCHEDULE_APPOINTMENT",
                "CANCEL_APPOINTMENT",
            }:
                updated.current_goal = intent

        if "patient_status" in patch:
            updated.patient_status = PatientStatus(_patch_value(patch["patient_status"]))
        if "referral_status" in patch:
            updated.referral_status = ReferralStatus(_patch_value(patch["referral_status"]))

        for field_name in ("appointment_type", "provider", "location"):
            if field_name not in patch:
                continue
            entity_patch = patch[field_name]
            operation = entity_patch.get("operation", "SET")
            if operation == "CLEAR":
                replacement = None
            elif operation in {"SET", "REPLACE"}:
                raw_text = entity_patch.get("raw_text", "").strip()
                if not raw_text:
                    raise ValueError(f"{field_name} must include raw_text when set")
                default_requirement = (
                    "REQUIRED" if field_name == "appointment_type" else "UNSPECIFIED"
                )
                replacement = EntityRequest(
                    raw_text=raw_text,
                    requirement=Requirement(
                        entity_patch.get("requirement", default_requirement)
                    ),
                )
            else:
                raise ValueError(f"Unsupported patch operation: {operation}")
            setattr(updated, field_name, replacement)

        if "time" in patch:
            time_patch = patch["time"]
            if time_patch.get("operation") == "CLEAR":
                updated.time = None
            else:
                raw_text = time_patch.get("raw_text", "").strip()
                if not raw_text:
                    raise ValueError("time must include raw_text when set")
                updated.time = TimePreference(
                    raw_text=raw_text,
                    objective=time_patch.get("objective", "UNSPECIFIED"),
                )

        if "primary_priority" in patch:
            priority_patch = patch["primary_priority"]
            if priority_patch.get("operation") == "CLEAR":
                updated.primary_priority = PreferencePriority.UNSPECIFIED
            else:
                updated.primary_priority = PreferencePriority(priority_patch["value"])

        return updated

    def fingerprint(self) -> str:
        """Stable identity for the scheduling facts behind an offer."""

        request_data = self.to_dict()
        request_data.pop("conversation_id", None)
        encoded = json.dumps(request_data, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return _enum_values(asdict(self))


# Backwards-compatible import name while callers migrate to the clearer term.
SchedulingState = SchedulingRequest


def _patch_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value["value"]
    return value


def _enum_values(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_enum_values(item) for item in value]
    return value
