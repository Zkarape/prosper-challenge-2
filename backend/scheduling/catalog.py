"""Catalog loading, validation, and explainable text resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


def normalize(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"\bdoctor\b", "dr", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


@dataclass(frozen=True)
class Resolution:
    status: str
    query: str
    selected: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    match_method: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "query": self.query,
            "selected": self.selected,
            "candidates": self.candidates,
            "match_method": self.match_method,
        }


class Catalog:
    def __init__(
        self,
        data: dict[str, Any],
        version: str | None = None,
        default_timezone: str = "America/Los_Angeles",
    ):
        self.data = data
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        self.version = version or f"sha256:{sha256(encoded.encode()).hexdigest()}"
        self.default_timezone = default_timezone
        self.locations = {item["id"]: item for item in data["locations"]}
        self.providers = {item["id"]: item for item in data["providers"]}
        self.appointment_types = {
            item["id"]: item for item in data["appointment_types"]
        }
        self.policies = list(data.get("policies", []))
        self.validate()

    @classmethod
    def from_json(cls, path: str | Path) -> "Catalog":
        path = Path(path)
        return cls(json.loads(path.read_text()))

    def timezone_for_location(self, location_id: str) -> str:
        location = self.locations[location_id]
        return location.get("timezone", self.default_timezone)

    def validate(self) -> None:
        all_ids = [
            item["id"]
            for group in ("locations", "providers", "appointment_types")
            for item in self.data[group]
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Catalog IDs must be unique")
        for provider in self.providers.values():
            missing_locations = set(provider["location_ids"]) - self.locations.keys()
            missing_types = (
                set(provider["appointment_type_ids"]) - self.appointment_types.keys()
            )
            if missing_locations or missing_types:
                raise ValueError(
                    f"Provider {provider['id']} has missing references: "
                    f"locations={sorted(missing_locations)}, "
                    f"appointment_types={sorted(missing_types)}"
                )
        for item in (
            list(self.locations.values())
            + list(self.providers.values())
            + list(self.appointment_types.values())
        ):
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list) or not all(
                isinstance(alias, str) and normalize(alias) for alias in aliases
            ):
                raise ValueError(f"Catalog aliases are invalid for {item['id']}")

    def resolve_appointment_type(self, query: str) -> Resolution:
        return self._resolve(query, self.appointment_types.values(), "appointment_type")

    def resolve_provider(
        self, query: str, appointment_type_id: str | None = None
    ) -> Resolution:
        records: Iterable[dict[str, Any]] = self.providers.values()
        resolution = self._resolve(query, records, "provider")
        if appointment_type_id and resolution.candidates:
            narrowed = [
                item
                for item in resolution.candidates
                if appointment_type_id in self.providers[item["id"]]["appointment_type_ids"]
            ]
            if len(narrowed) == 1:
                return Resolution(
                    "RESOLVED",
                    query,
                    narrowed[0],
                    narrowed,
                    "CONTEXTUAL_APPOINTMENT_MATCH",
                )
            if narrowed:
                return Resolution("AMBIGUOUS", query, None, narrowed, resolution.match_method)
        return resolution

    def resolve_location(self, query: str) -> Resolution:
        return self._resolve(query, self.locations.values(), "location")

    def find_entity_mention(self, text: str, entity_type: str) -> str | None:
        """Find an exact catalog name or alias inside a longer utterance.

        This does not resolve or choose an ID. It only returns patient wording
        that literally matches a catalog-owned phrase, so ambiguous aliases
        remain ambiguous for the scheduling engine.
        """

        groups = {
            "appointment_type": self.appointment_types,
            "provider": self.providers,
            "location": self.locations,
        }
        records = groups[entity_type].values()
        normalized_text = normalize(text)
        mentions: set[str] = set()
        for record in records:
            for phrase in [record["name"], *record.get("aliases", [])]:
                normalized_phrase = normalize(phrase)
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])",
                    normalized_text,
                ):
                    mentions.add(normalized_phrase)
        return max(mentions, key=lambda item: (len(item.split()), len(item))) if mentions else None

    def _resolve(
        self, query: str, records: Iterable[dict[str, Any]], entity_type: str
    ) -> Resolution:
        normalized_query = normalize(query)
        if not normalized_query:
            return Resolution("UNRESOLVED", query, None, [], None)
        alias_query = re.sub(
            r"^(?:a|an|the|my|our)\s+", "", normalized_query
        )

        scored: list[tuple[int, dict[str, Any], str]] = []
        query_tokens = set(normalized_query.split())
        for record in records:
            normalized_name = normalize(record["name"])
            normalized_aliases = {
                normalize(alias) for alias in record.get("aliases", [])
            }
            name_tokens = set(normalized_name.split())
            if normalized_name == normalized_query:
                score, method = 100, "EXACT_NAME"
            elif alias_query in normalized_aliases:
                score, method = 95, "EXACT_ALIAS"
            elif normalized_name.startswith(normalized_query) or normalized_query.startswith(
                normalized_name
            ):
                score, method = 90, "PREFIX_NAME"
            elif normalized_query in normalized_name:
                score, method = 80, "CONTAINS_NAME"
            elif query_tokens and query_tokens <= name_tokens:
                score, method = 70, "TOKEN_MATCH"
            elif name_tokens and name_tokens <= query_tokens:
                score, method = 65, "NAME_TOKENS_IN_QUERY"
            else:
                continue
            scored.append((score, self._summary(record, entity_type), method))

        if not scored:
            return Resolution("UNRESOLVED", query, None, [], None)
        best_score = max(score for score, _, _ in scored)
        best = [item for score, item, _ in scored if score == best_score]
        method = next(method for score, _, method in scored if score == best_score)
        if len(best) == 1:
            return Resolution("RESOLVED", query, best[0], best, method)
        return Resolution("AMBIGUOUS", query, None, best, method)

    @staticmethod
    def _summary(record: dict[str, Any], entity_type: str) -> dict[str, Any]:
        summary = {"id": record["id"], "name": record["name"]}
        if entity_type == "provider":
            summary.update(
                specialty=record["specialty"], location_ids=record["location_ids"]
            )
        elif entity_type == "location":
            summary.update(address=record["address"], city=record["city"])
        elif entity_type == "appointment_type":
            summary.update(
                specialty=record["specialty"], duration_min=record["duration_min"]
            )
        return summary
