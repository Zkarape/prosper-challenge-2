"""Conversation-level model usage accounting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


# Price snapshot from the official model page on 2026-08-20. Cached tokens are
# already included in input_tokens, so they replace—not add to—the input charge.
PRICE_SNAPSHOTS: dict[str, dict[str, Any]] = {
    "gpt-5.4-mini": {
        "input_usd_per_million": 0.75,
        "cached_input_usd_per_million": 0.075,
        "output_usd_per_million": 4.50,
        "price_snapshot": "openai:2026-08-20",
    }
}


@dataclass(frozen=True)
class UsageEvent:
    conversation_id: str
    turn_id: str
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    estimated_cost_usd: float | None
    latency_ms: float
    provider_response_id: str | None = None
    price_snapshot: str | None = None
    usage_event_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.usage_event_id:
            object.__setattr__(self, "usage_event_id", f"usage_{uuid4().hex}")
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "total_tokens": self.total_tokens}

    @classmethod
    def from_telemetry(
        cls,
        *,
        conversation_id: str,
        turn_id: str,
        stage: str,
        telemetry: Any,
    ) -> "UsageEvent":
        pricing = PRICE_SNAPSHOTS.get(telemetry.model)
        cost = None
        price_snapshot = None
        if pricing:
            cached = min(telemetry.cached_input_tokens, telemetry.input_tokens)
            uncached = telemetry.input_tokens - cached
            cost = round(
                (
                    uncached * pricing["input_usd_per_million"]
                    + cached * pricing["cached_input_usd_per_million"]
                    + telemetry.output_tokens * pricing["output_usd_per_million"]
                )
                / 1_000_000,
                8,
            )
            price_snapshot = pricing["price_snapshot"]
        return cls(
            conversation_id=conversation_id,
            turn_id=turn_id,
            stage=stage,
            model=telemetry.model,
            input_tokens=telemetry.input_tokens,
            output_tokens=telemetry.output_tokens,
            cached_input_tokens=telemetry.cached_input_tokens,
            estimated_cost_usd=cost,
            latency_ms=telemetry.duration_ms,
            provider_response_id=telemetry.response_id,
            price_snapshot=price_snapshot,
        )


class UsageLedger:
    """Record every model call and provide an in-memory fallback for tests."""

    def __init__(
        self, record_sink: Callable[[UsageEvent], None] | None = None
    ) -> None:
        self.events: list[UsageEvent] = []
        self.record_sink = record_sink

    def record_call(self, event: UsageEvent) -> None:
        self.events.append(event)
        if self.record_sink is not None:
            self.record_sink(event)

    def events_for_conversation(self, conversation_id: str) -> list[UsageEvent]:
        return [
            event
            for event in self.events
            if event.conversation_id == conversation_id
        ]

    def conversation_tokens(self, conversation_id: str) -> int:
        return sum(
            event.total_tokens
            for event in self.events_for_conversation(conversation_id)
        )
