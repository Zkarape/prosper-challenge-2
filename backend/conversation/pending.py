"""Server-authored offers that short patient answers may refer to."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class OfferKind(str, Enum):
    ALTERNATIVE_LOCATION = "ALTERNATIVE_LOCATION"
    FIELD_OPTIONS = "FIELD_OPTIONS"
    RECOVERY_OPTIONS = "RECOVERY_OPTIONS"
    SLOT_OPTIONS = "SLOT_OPTIONS"
    CONFIRM_BOOKING = "CONFIRM_BOOKING"


@dataclass(frozen=True)
class OfferOption:
    option_id: str
    label: str
    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PendingOffer:
    kind: OfferKind
    request_fingerprint: str
    catalog_version: str
    options: list[OfferOption]
    offer_id: str = field(default_factory=lambda: f"offer_{uuid4().hex[:12]}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PendingOffer":
        """Restore a full server-owned offer from durable storage."""

        return cls(
            offer_id=value["offer_id"],
            kind=OfferKind(value["kind"]),
            request_fingerprint=value["request_fingerprint"],
            catalog_version=value["catalog_version"],
            options=[
                OfferOption(
                    option_id=item["option_id"],
                    label=item["label"],
                    value=item["value"],
                )
                for item in value.get("options", [])
            ],
        )

    def to_dict(self, *, include_values: bool = True) -> dict[str, Any]:
        options = []
        for option in self.options:
            item = {"option_id": option.option_id, "label": option.label}
            if include_values:
                item["value"] = option.value
            options.append(item)
        return {
            "offer_id": self.offer_id,
            "kind": self.kind.value,
            "request_fingerprint": self.request_fingerprint,
            "catalog_version": self.catalog_version,
            "options": options,
        }

    def option_for_ordinal(self, ordinal: int) -> OfferOption | None:
        index = ordinal - 1
        return self.options[index] if 0 <= index < len(self.options) else None
