"""Shared runtime storage.

The in-memory implementation keeps unit tests and zero-setup demos fast. When
``DATABASE_URL`` is configured, the PostgreSQL implementation owns all mutable
conversation and booking state so any backend worker can continue a call.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from conversation import PendingOffer, UsageEvent

from .availability import Slot, _candidate_identity
from .catalog import Catalog
from .state import SchedulingRequest


DEFAULT_CLINIC_ID = "prosper_demo_clinic"
DEFAULT_AGENT_ID = "prosper_scheduler"


class InMemoryConversationStore:
    """Process-local adapter used when PostgreSQL is not configured."""

    durable = False

    def __init__(self):
        self.conversations: dict[str, Any] = {}
        self.usage_events: list[UsageEvent] = []
        self.evaluation_runs: dict[str, dict[str, Any]] = {}

    def create(self, conversation: Any, *, catalog_hash: str) -> None:
        self.conversations[conversation.patient_request.conversation_id] = conversation

    def get(self, conversation_id: str) -> Any:
        try:
            return self.conversations[conversation_id]
        except KeyError as exc:
            raise KeyError("CONVERSATION_NOT_FOUND") from exc

    @contextmanager
    def locked(self, conversation_id: str) -> Iterator[Any]:
        conversation = self.get(conversation_id)
        with conversation.lock:
            if conversation.status != "ACTIVE":
                raise ValueError("CONVERSATION_CLOSED")
            yield conversation

    def delete(self, conversation_id: str) -> None:
        self.conversations.pop(conversation_id, None)

    def record_usage_event(self, event: UsageEvent) -> None:
        if not any(item.usage_event_id == event.usage_event_id for item in self.usage_events):
            self.usage_events.append(event)

    def finish_conversation(
        self,
        conversation_id: str,
        *,
        status: str,
        outcome: str,
        safe: bool,
    ) -> Any:
        conversation = self.get(conversation_id)
        conversation.status = status
        conversation.outcome = outcome
        conversation.safe = safe
        conversation.ended_at = conversation.ended_at or _utc_now()
        return conversation

    def conversation_evaluation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.get(conversation_id)
        events = [
            item.to_dict()
            for item in self.usage_events
            if item.conversation_id == conversation_id
        ]
        return _conversation_evaluation_dict(conversation, events)

    def evaluation_summary(self) -> dict[str, Any]:
        evaluations = [
            self.conversation_evaluation(conversation_id)
            for conversation_id in self.conversations
        ]
        return _aggregate_evaluations(evaluations)

    def save_evaluation_run(self, run: dict[str, Any]) -> None:
        self.evaluation_runs[run["run_id"]] = deepcopy(run)

    def get_evaluation_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.evaluation_runs.get(run_id)
        return deepcopy(run) if run is not None else None

    def latest_evaluation_run(self) -> dict[str, Any] | None:
        pipeline_runs = [
            item
            for item in self.evaluation_runs.values()
            if item.get("kind") != "CONTEXT_STRATEGY_COMPARISON"
        ]
        if not pipeline_runs:
            return None
        latest = max(
            pipeline_runs,
            key=lambda item: (item["started_at"], item["run_id"]),
        )
        return deepcopy(latest)

    def latest_context_comparison(self) -> dict[str, Any] | None:
        runs = [
            item
            for item in self.evaluation_runs.values()
            if item.get("kind") == "CONTEXT_STRATEGY_COMPARISON"
        ]
        if not runs:
            return None
        latest = max(
            runs,
            key=lambda item: (item["started_at"], item["run_id"]),
        )
        return deepcopy(latest)

    def sync_configuration(
        self, *, catalog: Catalog, agent_config: dict[str, Any] | None
    ) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {"storage": "memory", "database": "not_configured"}


class PostgresRuntimeStore:
    """PostgreSQL conversation repository and atomic booking ledger."""

    durable = True

    def __init__(
        self,
        database_url: str,
        *,
        catalog: Catalog,
        clinic_id: str = DEFAULT_CLINIC_ID,
        pool_size: int = 10,
        turn_claim_seconds: int = 120,
    ):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError(
                "DATABASE_URL is set, but PostgreSQL dependencies are missing. "
                "Run `make install`."
            ) from exc

        self.catalog = catalog
        self.clinic_id = clinic_id
        self.agent_id = f"{clinic_id}:{DEFAULT_AGENT_ID}"
        self.turn_claim_seconds = turn_claim_seconds
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=pool_size,
            timeout=10,
            open=True,
            # Supabase transaction pooling doesn't support prepared statements.
            # Disabling psycopg's automatic prepare works with direct, session,
            # and transaction-pooled PostgreSQL URLs.
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
        )
        self._verify_schema()

    def _verify_schema(self) -> None:
        try:
            with self.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT to_regclass('public.conversations') AS conversations,
                           to_regclass('public.evaluation_runs') AS evaluation_runs
                    """
                ).fetchone()
        except Exception as exc:
            raise RuntimeError(
                "Could not connect to PostgreSQL. Check DATABASE_URL and database access."
            ) from exc
        if not row or row["conversations"] is None:
            raise RuntimeError(
                "PostgreSQL is connected but not migrated. Run `make db-migrate`."
            )
        if row["evaluation_runs"] is None:
            raise RuntimeError(
                "PostgreSQL migrations are outdated. Run `make db-migrate`."
            )

    def sync_configuration(
        self, *, catalog: Catalog, agent_config: dict[str, Any] | None
    ) -> None:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                """
                INSERT INTO clinics (clinic_id, name)
                VALUES (%s, %s)
                ON CONFLICT (clinic_id) DO NOTHING
                """,
                (self.clinic_id, "Prosper Demo Clinic"),
            )
            connection.execute(
                """
                INSERT INTO catalog_snapshots (clinic_id, catalog_hash, catalog)
                VALUES (%s, %s, %s)
                ON CONFLICT (clinic_id, catalog_hash) DO NOTHING
                """,
                (self.clinic_id, catalog.version, Jsonb(catalog.data)),
            )
            if agent_config is not None:
                encoded = json.dumps(
                    agent_config, sort_keys=True, separators=(",", ":")
                )
                config_hash = "sha256:" + sha256(encoded.encode()).hexdigest()
                publication_id = "agentpub_" + sha256(
                    f"{self.agent_id}:{config_hash}".encode()
                ).hexdigest()[:16]
                connection.execute(
                    """
                    INSERT INTO agents (agent_id, clinic_id, name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (agent_id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    (
                        self.agent_id,
                        self.clinic_id,
                        agent_config.get("name", "Prosper Scheduler"),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO agent_publications
                        (publication_id, agent_id, config_hash, config)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (agent_id, config_hash) DO NOTHING
                    """,
                    (
                        publication_id,
                        self.agent_id,
                        config_hash,
                        Jsonb(agent_config),
                    ),
                )

    def create(self, conversation: Any, *, catalog_hash: str) -> None:
        from psycopg.types.json import Jsonb

        publication_id = self._current_agent_publication_id()
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations
                    (conversation_id, clinic_id, agent_publication_id,
                     catalog_hash, patient_request)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    conversation.patient_request.conversation_id,
                    self.clinic_id,
                    publication_id,
                    catalog_hash,
                    Jsonb(conversation.patient_request.to_dict()),
                ),
            )

    def _current_agent_publication_id(self) -> str | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT publication_id
                FROM agent_publications
                WHERE agent_id = %s
                ORDER BY published_at DESC
                LIMIT 1
                """,
                (self.agent_id,),
            ).fetchone()
        return row["publication_id"] if row else None

    def get(self, conversation_id: str) -> Any:
        with self.pool.connection() as connection:
            row = self._conversation_row(connection, conversation_id, lock=False)
            return self._restore_conversation(connection, row)

    @contextmanager
    def locked(self, conversation_id: str) -> Iterator[Any]:
        # Claim the conversation in a short transaction, then release the
        # connection before the LLM call. This serializes turns without making
        # database pool size equal LLM concurrency.
        processing_token = uuid4().hex
        with self.pool.connection() as connection, connection.transaction():
            claimed = connection.execute(
                """
                UPDATE conversations
                SET processing_token = %s,
                    processing_until = now() + (%s * interval '1 second')
                WHERE conversation_id = %s
                  AND clinic_id = %s
                  AND deleted_at IS NULL
                  AND status = 'ACTIVE'
                  AND (
                      processing_token IS NULL
                      OR processing_until < now()
                  )
                RETURNING conversation_id
                """,
                (
                    processing_token,
                    self.turn_claim_seconds,
                    conversation_id,
                    self.clinic_id,
                ),
            ).fetchone()
            if claimed is None:
                exists = connection.execute(
                    """
                    SELECT status FROM conversations
                    WHERE conversation_id = %s AND clinic_id = %s
                      AND deleted_at IS NULL
                    """,
                    (conversation_id, self.clinic_id),
                ).fetchone()
                if exists is None:
                    raise KeyError("CONVERSATION_NOT_FOUND")
                if exists["status"] != "ACTIVE":
                    raise ValueError("CONVERSATION_CLOSED")
                raise ValueError("CONVERSATION_BUSY")
            row = self._conversation_row(connection, conversation_id, lock=False)
            conversation = self._restore_conversation(connection, row)

        starting_message_number = conversation.message_number
        try:
            yield conversation
            with self.pool.connection() as connection, connection.transaction():
                self._save_conversation(
                    connection,
                    conversation,
                    starting_message_number=starting_message_number,
                    processing_token=processing_token,
                )
        except Exception:
            self._release_processing_claim(conversation_id, processing_token)
            raise

    def _conversation_row(self, connection: Any, conversation_id: str, *, lock: bool):
        suffix = " FOR NO KEY UPDATE" if lock else ""
        row = connection.execute(
            """
            SELECT conversation_id, patient_request, message_number, booking,
                   last_engine_result, rejected_alternatives, status, outcome,
                   safe, created_at, ended_at
            FROM conversations
            WHERE conversation_id = %s AND clinic_id = %s AND deleted_at IS NULL
            """
            + suffix,
            (conversation_id, self.clinic_id),
        ).fetchone()
        if row is None:
            raise KeyError("CONVERSATION_NOT_FOUND")
        return row

    @staticmethod
    def _restore_conversation(connection: Any, row: dict[str, Any]) -> Any:
        # Imported lazily to avoid a module cycle with ConversationService.
        from .service import Conversation

        offer_row = connection.execute(
            """
            SELECT offer_id, kind, request_fingerprint, catalog_hash, options
            FROM pending_offers
            WHERE conversation_id = %s AND expires_at > now()
            """,
            (row["conversation_id"],),
        ).fetchone()
        pending_offer = None
        if offer_row:
            pending_offer = PendingOffer.from_dict(
                {
                    "offer_id": offer_row["offer_id"],
                    "kind": offer_row["kind"],
                    "request_fingerprint": offer_row["request_fingerprint"],
                    "catalog_version": offer_row["catalog_hash"],
                    "options": offer_row["options"],
                }
            )
        turn_rows = connection.execute(
            """
            SELECT message_id, response
            FROM conversation_turns
            WHERE conversation_id = %s
            ORDER BY message_number DESC
            LIMIT 100
            """,
            (row["conversation_id"],),
        ).fetchall()
        return Conversation(
            patient_request=SchedulingRequest.from_dict(row["patient_request"]),
            message_number=row["message_number"],
            pending_offer=pending_offer,
            booking=row["booking"],
            last_result=row["last_engine_result"],
            processed_messages={item["message_id"]: item["response"] for item in reversed(turn_rows)},
            rejected_alternatives=set(row["rejected_alternatives"] or []),
            status=row["status"],
            outcome=row["outcome"],
            safe=row["safe"],
            started_at=row["created_at"],
            ended_at=row["ended_at"],
            lock=RLock(),
        )

    def _save_conversation(
        self,
        connection: Any,
        conversation: Any,
        *,
        starting_message_number: int,
        processing_token: str,
    ) -> None:
        from psycopg.types.json import Jsonb

        saved = connection.execute(
            """
            UPDATE conversations
            SET patient_request = %s, message_number = %s, booking = %s,
                last_engine_result = %s, rejected_alternatives = %s,
                updated_at = now(), processing_token = NULL,
                processing_until = NULL, intent = %s, status = %s,
                outcome = %s, safe = %s, ended_at = %s
            WHERE conversation_id = %s AND processing_token = %s
            RETURNING conversation_id
            """,
            (
                Jsonb(conversation.patient_request.to_dict()),
                conversation.message_number,
                Jsonb(conversation.booking) if conversation.booking is not None else None,
                Jsonb(conversation.last_result)
                if conversation.last_result is not None
                else None,
                Jsonb(sorted(conversation.rejected_alternatives)),
                conversation.patient_request.current_goal,
                conversation.status,
                conversation.outcome,
                conversation.safe,
                conversation.ended_at,
                conversation.patient_request.conversation_id,
                processing_token,
            ),
        ).fetchone()
        if saved is None:
            raise RuntimeError("CONVERSATION_PROCESSING_CLAIM_LOST")
        conversation_id = conversation.patient_request.conversation_id
        connection.execute(
            "DELETE FROM pending_offers WHERE conversation_id = %s",
            (conversation_id,),
        )
        if conversation.pending_offer is not None:
            offer = conversation.pending_offer
            connection.execute(
                """
                INSERT INTO pending_offers
                    (offer_id, conversation_id, kind, request_fingerprint,
                     catalog_hash, options)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    offer.offer_id,
                    conversation_id,
                    offer.kind.value,
                    offer.request_fingerprint,
                    offer.catalog_version,
                    Jsonb([item.to_dict() for item in offer.options]),
                ),
            )

        if conversation.message_number <= starting_message_number:
            return
        result = next(
            (
                item
                for item in reversed(list(conversation.processed_messages.values()))
                if item.get("message_number") == conversation.message_number
            ),
            None,
        )
        if result is None:
            return
        usage = result.get("usage", {})
        connection.execute(
            """
            INSERT INTO conversation_turns
                (conversation_id, message_id, message_number, patient_text,
                 response, input_tokens, cached_input_tokens, output_tokens,
                 total_latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, message_id) DO NOTHING
            """,
            (
                conversation_id,
                result["message_id"],
                result["message_number"],
                result.get("patient_text", ""),
                Jsonb(result),
                usage.get("input_tokens", 0),
                usage.get("cached_input_tokens", 0),
                usage.get("output_tokens", 0),
                result.get("total_latency_ms"),
            ),
        )

    def _release_processing_claim(
        self, conversation_id: str, processing_token: str
    ) -> None:
        try:
            with self.pool.connection() as connection:
                connection.execute(
                    """
                    UPDATE conversations
                    SET processing_token = NULL, processing_until = NULL
                    WHERE conversation_id = %s AND processing_token = %s
                    """,
                    (conversation_id, processing_token),
                )
        except Exception:
            # The lease expires, so a temporary database failure cannot leave a
            # conversation permanently stuck.
            return

    def delete(self, conversation_id: str) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """
                UPDATE conversations
                SET deleted_at = now(), updated_at = now()
                WHERE conversation_id = %s
                  AND clinic_id = %s
                """,
                (conversation_id, self.clinic_id),
            )

    def record_usage_event(self, event: UsageEvent) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO usage_events
                    (usage_event_id, conversation_id, turn_id, stage, model,
                     input_tokens, cached_input_tokens, output_tokens,
                     estimated_cost_usd, latency_ms, provider_response_id,
                     price_snapshot, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    event.usage_event_id,
                    event.conversation_id,
                    event.turn_id,
                    event.stage,
                    event.model,
                    event.input_tokens,
                    event.cached_input_tokens,
                    event.output_tokens,
                    event.estimated_cost_usd,
                    event.latency_ms,
                    event.provider_response_id,
                    event.price_snapshot,
                    event.created_at,
                ),
            )

    def finish_conversation(
        self,
        conversation_id: str,
        *,
        status: str,
        outcome: str,
        safe: bool,
    ) -> Any:
        with self.pool.connection() as connection:
            updated = connection.execute(
                """
                UPDATE conversations
                SET status = %s, outcome = %s, safe = %s,
                    ended_at = coalesce(ended_at, now()), updated_at = now()
                WHERE conversation_id = %s AND deleted_at IS NULL
                  AND clinic_id = %s
                RETURNING conversation_id
                """,
                (status, outcome, safe, conversation_id, self.clinic_id),
            ).fetchone()
        if updated is None:
            raise KeyError("CONVERSATION_NOT_FOUND")
        return self.get(conversation_id)

    def conversation_evaluation(self, conversation_id: str) -> dict[str, Any]:
        with self.pool.connection() as connection:
            evaluation = connection.execute(
                """
                SELECT * FROM conversation_evaluations
                WHERE conversation_id = %s AND clinic_id = %s
                """,
                (conversation_id, self.clinic_id),
            ).fetchone()
            if evaluation is None:
                raise KeyError("CONVERSATION_NOT_FOUND")
            events = connection.execute(
                """
                SELECT usage_event_id, conversation_id, turn_id, stage, model,
                       input_tokens, cached_input_tokens, output_tokens,
                       total_tokens, estimated_cost_usd, latency_ms,
                       provider_response_id, price_snapshot, created_at
                FROM usage_events
                WHERE conversation_id = %s
                ORDER BY created_at
                """,
                (conversation_id,),
            ).fetchall()
        return {
            **_json_safe_row(evaluation),
            "usage_events": [_json_safe_row(item) for item in events],
        }

    def evaluation_summary(self) -> dict[str, Any]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_evaluations WHERE clinic_id = %s",
                (self.clinic_id,),
            ).fetchall()
        return _aggregate_evaluations([_json_safe_row(row) for row in rows])

    def save_evaluation_run(self, run: dict[str, Any]) -> None:
        """Upsert the complete, immutable-to-the-UI evaluation snapshot."""

        from psycopg.types.json import Jsonb

        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs
                    (clinic_id, run_id, status, started_at, result)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (clinic_id, run_id) DO UPDATE
                SET status = EXCLUDED.status,
                    result = EXCLUDED.result,
                    updated_at = now()
                """,
                (
                    self.clinic_id,
                    run["run_id"],
                    run["status"],
                    run["started_at"],
                    Jsonb(run),
                ),
            )

    def get_evaluation_run(self, run_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT result
                FROM evaluation_runs
                WHERE clinic_id = %s AND run_id = %s
                """,
                (self.clinic_id, run_id),
            ).fetchone()
        return deepcopy(row["result"]) if row is not None else None

    def latest_evaluation_run(self) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT result
                FROM evaluation_runs
                WHERE clinic_id = %s
                  AND coalesce(result->>'kind', 'PIPELINE')
                      <> 'CONTEXT_STRATEGY_COMPARISON'
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (self.clinic_id,),
            ).fetchone()
        return deepcopy(row["result"]) if row is not None else None

    def latest_context_comparison(self) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT result
                FROM evaluation_runs
                WHERE clinic_id = %s
                  AND result->>'kind' = 'CONTEXT_STRATEGY_COMPARISON'
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (self.clinic_id,),
            ).fetchone()
        return deepcopy(row["result"]) if row is not None else None

    def is_available(self, slot_id: str) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT (
                    EXISTS (SELECT 1 FROM reserved_slots WHERE slot_id = %s)
                    OR EXISTS (
                        SELECT 1 FROM bookings
                        WHERE slot_id = %s AND status = 'confirmed'
                    )
                ) AS unavailable
                """,
                (slot_id, slot_id),
            ).fetchone()
        return not row["unavailable"]

    def reserve(self, slot_id: str) -> None:
        with self.pool.connection() as connection:
            inserted = connection.execute(
                """
                INSERT INTO reserved_slots (slot_id)
                VALUES (%s)
                ON CONFLICT (slot_id) DO NOTHING
                RETURNING slot_id
                """,
                (slot_id,),
            ).fetchone()
        if inserted is None:
            raise ValueError("SLOT_NO_LONGER_AVAILABLE")

    def book(
        self,
        *,
        conversation_id: str,
        offer_id: str,
        candidate: Any,
        slot: Slot,
        offered_request_fingerprint: str,
        current_request_fingerprint: str,
        offered_catalog_version: str,
        current_catalog_version: str,
    ) -> dict[str, Any]:
        if not offer_id:
            raise ValueError("OFFER_ID_REQUIRED")
        candidate_id, appointment_type_id = _candidate_identity(candidate)
        if offered_request_fingerprint != current_request_fingerprint:
            raise ValueError("PATIENT_REQUEST_CHANGED")
        if offered_catalog_version != current_catalog_version:
            raise ValueError("CATALOG_CHANGED")
        if slot.candidate_id != candidate_id:
            raise ValueError("SLOT_CANDIDATE_MISMATCH")
        required_duration = self.catalog.appointment_types[appointment_type_id][
            "duration_min"
        ]
        if slot.duration_min < required_duration:
            raise ValueError("SLOT_TOO_SHORT")

        from psycopg.types.json import Jsonb

        booking_id = "booking_" + sha256(offer_id.encode()).hexdigest()[:12]
        booking = {
            "booking_id": booking_id,
            "offer_id": offer_id,
            "conversation_id": conversation_id,
            "candidate_id": candidate_id,
            "slot": slot.to_dict(),
            "status": "confirmed",
        }
        with self.pool.connection() as connection, connection.transaction():
            previous = connection.execute(
                "SELECT booking_id, offer_id, conversation_id, candidate_id, slot, status "
                "FROM bookings WHERE offer_id = %s",
                (offer_id,),
            ).fetchone()
            if previous:
                return self._booking_dict(previous)
            if connection.execute(
                "SELECT 1 FROM reserved_slots WHERE slot_id = %s", (slot.id,)
            ).fetchone():
                self._record_booking_attempt(
                    connection, offer_id, conversation_id, slot.id, "rejected", "slot_reserved"
                )
                raise ValueError("SLOT_NO_LONGER_AVAILABLE")

            inserted = connection.execute(
                """
                INSERT INTO bookings
                    (booking_id, offer_id, conversation_id, candidate_id,
                     slot_id, slot, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'confirmed')
                ON CONFLICT DO NOTHING
                RETURNING booking_id
                """,
                (
                    booking_id,
                    offer_id,
                    conversation_id,
                    candidate_id,
                    slot.id,
                    Jsonb(slot.to_dict()),
                ),
            ).fetchone()
            if inserted is None:
                previous = connection.execute(
                    "SELECT booking_id, offer_id, conversation_id, candidate_id, slot, status "
                    "FROM bookings WHERE offer_id = %s",
                    (offer_id,),
                ).fetchone()
                if previous:
                    return self._booking_dict(previous)
                self._record_booking_attempt(
                    connection, offer_id, conversation_id, slot.id, "rejected", "slot_taken"
                )
                raise ValueError("SLOT_NO_LONGER_AVAILABLE")
            self._record_booking_attempt(
                connection, offer_id, conversation_id, slot.id, "confirmed", None
            )
        return booking

    @staticmethod
    def _booking_dict(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "booking_id": row["booking_id"],
            "offer_id": row["offer_id"],
            "conversation_id": row["conversation_id"],
            "candidate_id": row["candidate_id"],
            "slot": row["slot"],
            "status": row["status"],
        }

    @staticmethod
    def _record_booking_attempt(
        connection: Any,
        offer_id: str,
        conversation_id: str,
        slot_id: str,
        outcome: str,
        detail: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO booking_attempts
                (offer_id, conversation_id, slot_id, outcome, detail)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (offer_id, conversation_id, slot_id, outcome, detail),
        )

    def health(self) -> dict[str, Any]:
        with self.pool.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"storage": "postgresql", "database": "connected"}


def postgres_store_from_environment(catalog: Catalog) -> PostgresRuntimeStore | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None
    return PostgresRuntimeStore(
        database_url,
        catalog=catalog,
        clinic_id=os.getenv("CLINIC_ID", DEFAULT_CLINIC_ID),
        pool_size=int(os.getenv("DATABASE_POOL_SIZE", "10")),
        turn_claim_seconds=int(os.getenv("TURN_CLAIM_SECONDS", "120")),
    )


def load_default_agent_config() -> dict[str, Any] | None:
    path = Path(__file__).parents[1] / "example_flow.json"
    return json.loads(path.read_text()) if path.exists() else None


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _conversation_evaluation_dict(
    conversation: Any, events: list[dict[str, Any]]
) -> dict[str, Any]:
    priced_costs = [
        item["estimated_cost_usd"]
        for item in events
        if item.get("estimated_cost_usd") is not None
    ]
    return {
        "conversation_id": conversation.patient_request.conversation_id,
        "status": conversation.status,
        "intent": conversation.patient_request.current_goal,
        "outcome": conversation.outcome,
        "safe": conversation.safe,
        "started_at": conversation.started_at.isoformat(),
        "ended_at": (
            conversation.ended_at.isoformat() if conversation.ended_at else None
        ),
        "turn_count": conversation.message_number,
        "model_call_count": len(events),
        "input_tokens": sum(item["input_tokens"] for item in events),
        "cached_input_tokens": sum(
            item["cached_input_tokens"] for item in events
        ),
        "output_tokens": sum(item["output_tokens"] for item in events),
        "total_tokens": sum(item["total_tokens"] for item in events),
        "estimated_cost_usd": (
            round(sum(priced_costs), 8) if priced_costs else None
        ),
        "model_latency_ms": round(sum(item["latency_ms"] for item in events), 3),
        "usage_events": events,
    }


def _aggregate_evaluations(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    finalized = [item for item in evaluations if item["status"] != "ACTIVE"]
    safe_completed = [item for item in finalized if item.get("safe") is True]
    confirmed = [
        item for item in finalized if item.get("outcome") == "BOOKING_CONFIRMED"
    ]
    booking_conversations = [
        item for item in finalized if item.get("intent") == "BOOK_APPOINTMENT"
    ]
    total_tokens = sum(int(item["total_tokens"]) for item in finalized)
    booking_tokens = sum(
        int(item["total_tokens"]) for item in booking_conversations
    )
    priced_costs = [
        float(item["estimated_cost_usd"])
        for item in finalized
        if item.get("estimated_cost_usd") is not None
    ]
    return {
        "conversation_count": len(evaluations),
        "active_conversation_count": len(evaluations) - len(finalized),
        "finalized_conversation_count": len(finalized),
        "safe_completed_task_count": len(safe_completed),
        "confirmed_booking_count": len(confirmed),
        "total_tokens_finalized": total_tokens,
        "tokens_per_safe_completed_task": (
            round(total_tokens / len(safe_completed), 2)
            if safe_completed
            else None
        ),
        "booking_conversation_tokens": booking_tokens,
        "tokens_per_confirmed_booking": (
            round(booking_tokens / len(confirmed), 2) if confirmed else None
        ),
        "estimated_cost_usd_finalized": (
            round(sum(priced_costs), 8) if priced_costs else None
        ),
    }


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    from datetime import date, datetime
    from decimal import Decimal

    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result
