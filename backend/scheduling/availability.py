"""Repeatable mock availability and safe in-memory booking for the demo."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import Catalog
from .engine import Candidate


@dataclass(frozen=True)
class Slot:
    id: str
    candidate_id: str
    start: datetime
    end: datetime

    @property
    def duration_min(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.id,
            "candidate_id": self.candidate_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_min": self.duration_min,
            "timezone": getattr(self.start.tzinfo, "key", str(self.start.tzinfo)),
        }


class MockAvailability:
    """Generate stable slots from candidate IDs instead of random data."""

    def __init__(self, catalog: Catalog, timezone: str = "America/Los_Angeles"):
        self.catalog = catalog
        self.default_timezone = timezone
        self._booked_slot_ids: set[str] = set()

    def find_slots(
        self,
        candidate: Candidate | dict[str, Any],
        start_date: date,
        days: int = 14,
        limit: int = 3,
    ) -> list[Slot]:
        candidate_id, appointment_type_id = _candidate_identity(candidate)
        duration = self.catalog.appointment_types[appointment_type_id]["duration_min"]
        seed = int(sha256(candidate_id.encode()).hexdigest()[:8], 16)
        timezone_name = (
            candidate.get("timezone", self.default_timezone)
            if isinstance(candidate, dict)
            else self.catalog.timezone_for_location(candidate.location_id)
        )
        timezone = ZoneInfo(timezone_name)
        slots: list[Slot] = []

        for day_offset in range(days):
            slot_date = start_date + timedelta(days=day_offset)
            if slot_date.weekday() >= 5:
                continue
            hour = 9 + ((seed + day_offset * 3) % 7)
            minute = 30 if (seed + day_offset) % 2 else 0
            start = datetime.combine(slot_date, time(hour, minute), timezone)
            end = start + timedelta(minutes=duration)
            slot_id = "slot_" + sha256(
                f"{candidate_id}:{start.isoformat()}".encode()
            ).hexdigest()[:12]
            if slot_id in self._booked_slot_ids:
                continue
            slots.append(Slot(slot_id, candidate_id, start, end))
            if len(slots) == limit:
                break
        return slots

    def is_available(self, slot_id: str) -> bool:
        return slot_id not in self._booked_slot_ids

    def reserve(self, slot_id: str) -> None:
        if not self.is_available(slot_id):
            raise ValueError("SLOT_NO_LONGER_AVAILABLE")
        self._booked_slot_ids.add(slot_id)


class MockBookingService:
    """Book a confirmed slot once, even when a request is retried."""

    def __init__(self, availability: MockAvailability):
        self.availability = availability
        self._by_idempotency_key: dict[str, dict[str, Any]] = {}

    def book(
        self,
        *,
        conversation_id: str,
        offer_id: str,
        candidate: Candidate | dict[str, Any],
        slot: Slot,
        offered_request_fingerprint: str,
        current_request_fingerprint: str,
        offered_catalog_version: str,
        current_catalog_version: str,
    ) -> dict[str, Any]:
        if not offer_id:
            raise ValueError("OFFER_ID_REQUIRED")
        previous = self._by_idempotency_key.get(offer_id)
        if previous:
            return previous

        candidate_id, _ = _candidate_identity(candidate)
        if offered_request_fingerprint != current_request_fingerprint:
            raise ValueError("PATIENT_REQUEST_CHANGED")
        if offered_catalog_version != current_catalog_version:
            raise ValueError("CATALOG_CHANGED")
        if slot.candidate_id != candidate_id:
            raise ValueError("SLOT_CANDIDATE_MISMATCH")

        appointment_type_id = candidate_id.split(":", 1)[0]
        required_duration = self.availability.catalog.appointment_types[
            appointment_type_id
        ]["duration_min"]
        if slot.duration_min < required_duration:
            raise ValueError("SLOT_TOO_SHORT")

        self.availability.reserve(slot.id)
        booking = {
            "booking_id": "booking_" + sha256(offer_id.encode()).hexdigest()[:12],
            "offer_id": offer_id,
            "conversation_id": conversation_id,
            "candidate_id": candidate_id,
            "slot": slot.to_dict(),
            "status": "confirmed",
        }
        self._by_idempotency_key[offer_id] = booking
        return booking


def _candidate_identity(candidate: Candidate | dict[str, Any]) -> tuple[str, str]:
    if isinstance(candidate, Candidate):
        return candidate.id, candidate.appointment_type_id
    candidate_id = candidate.get("candidate_id")
    appointment_type_id = candidate.get("appointment_type_id")
    if not candidate_id or not appointment_type_id:
        raise ValueError("Candidate requires candidate_id and appointment_type_id")
    return candidate_id, appointment_type_id
