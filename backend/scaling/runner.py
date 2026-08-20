"""Concurrent, network-free benchmark for the post-transcript scheduling path.

This is intentionally not presented as a full voice-vendor load test. It proves
that extraction, validation, state updates, catalog policy and response routing
can process a 100-session burst without shared-state corruption.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from math import ceil
from threading import Barrier, BrokenBarrierError, RLock, Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

from scheduling import ConversationService, RuleBasedExtractor
from scheduling.storage import InMemoryConversationStore


REAL_TIME_BUDGET_MS = 2_000
SCENARIOS = (
    (
        "earliest_dental",
        "I'm a new patient. Book the earliest dental cleaning at Mission District.",
    ),
    (
        "knee_mri",
        "I'm an existing patient and need a knee MRI as soon as possible.",
    ),
    (
        "provider_preference",
        "I need a dental cleaning and I prefer Dr. Wei Lee.",
    ),
    (
        "policy_block",
        "I'm a new patient and need a cardiology consultation.",
    ),
)


class ScalabilityRunner:
    """Run one bounded 100-session burst and retain recent results in memory."""

    def __init__(self, *, catalog: Any):
        self.catalog = catalog
        self._runs: dict[str, dict[str, Any]] = {}
        self._latest_run_id: str | None = None
        self._lock = RLock()

    def start(self, *, target_sessions: int = 100) -> dict[str, Any]:
        if not 1 <= target_sessions <= 100:
            raise ValueError("target_sessions must be between 1 and 100")
        with self._lock:
            if self._latest_run_id:
                latest = self._runs.get(self._latest_run_id)
                if latest and latest["status"] == "RUNNING":
                    raise ValueError("SCALABILITY_TEST_ALREADY_RUNNING")
            run_id = f"scale_{uuid4().hex[:12]}"
            placeholder = {
                "run_id": run_id,
                "status": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": None,
                "target_sessions": target_sessions,
                "completed_sessions": 0,
                "real_time_budget_ms": REAL_TIME_BUDGET_MS,
                "summary": None,
                "scope": self.scope(),
                "error": None,
            }
            self._runs[run_id] = deepcopy(placeholder)
            self._latest_run_id = run_id
        Thread(
            target=self._finish,
            kwargs={"run_id": run_id, "target_sessions": target_sessions},
            name=f"scalability-{run_id}",
            daemon=True,
        ).start()
        return placeholder

    @staticmethod
    def scope() -> dict[str, list[str]]:
        return {
            "included": [
                "100 patient sessions released as one concurrent burst",
                "structured local extraction and semantic validation",
                "isolated conversation state and pending actions",
                "catalog resolution, policy rules and candidate ranking",
                "deterministic response routing",
            ],
            "excluded": [
                "microphone audio, STT and TTS vendor capacity",
                "OpenAI network latency and provider rate limits",
                "Supabase network and connection-pool capacity",
            ],
        }

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_run_id is None:
                return None
            return deepcopy(self._runs[self._latest_run_id])

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run else None

    def _finish(self, *, run_id: str, target_sessions: int) -> None:
        try:
            self._run(run_id=run_id, target_sessions=target_sessions)
        except Exception as exc:  # pragma: no cover - defensive job boundary
            with self._lock:
                current = self._runs.get(run_id, {"run_id": run_id})
                self._runs[run_id] = {
                    **current,
                    "status": "ERROR",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                }

    def _run(self, *, run_id: str, target_sessions: int) -> None:
        started = perf_counter()
        barrier = Barrier(target_sessions) if target_sessions > 1 else None
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=target_sessions) as pool:
            futures = [
                pool.submit(self._run_session, index, barrier)
                for index in range(target_sessions)
            ]
            for future in as_completed(futures):
                results.append(future.result())
                with self._lock:
                    self._runs[run_id]["completed_sessions"] = len(results)

        wall_time_ms = round((perf_counter() - started) * 1000, 2)
        latencies = sorted(item["latency_ms"] for item in results)
        successful = [item for item in results if item["status"] == "PASS"]
        conversation_ids = {item.get("conversation_id") for item in successful}
        unique_state = len(conversation_ids) == len(successful)
        p50 = self._percentile(latencies, 50)
        p95 = self._percentile(latencies, 95)
        within_budget = wall_time_ms <= REAL_TIME_BUDGET_MS and p95 <= REAL_TIME_BUDGET_MS
        target_met = len(successful) == target_sessions and unique_state and within_budget
        scenario_counts = {
            name: sum(item["scenario"] == name for item in results)
            for name, _ in SCENARIOS
        }
        summary = {
            "target_met": target_met,
            "submitted_sessions": target_sessions,
            "successful_sessions": len(successful),
            "failed_sessions": target_sessions - len(successful),
            "wall_time_ms": wall_time_ms,
            "sessions_per_second": round(target_sessions / max(wall_time_ms / 1000, 0.001), 1),
            "p50_session_latency_ms": p50,
            "p95_session_latency_ms": p95,
            "unique_conversation_ids": len(conversation_ids),
            "isolated_state": unique_state,
            "within_real_time_budget": within_budget,
            "scenario_counts": scenario_counts,
        }
        with self._lock:
            self._runs[run_id] = {
                **self._runs[run_id],
                "status": "COMPLETED",
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "completed_sessions": target_sessions,
                "summary": summary,
                "samples": sorted(results, key=lambda item: item["session_number"])[:8],
            }
            while len(self._runs) > 10:
                self._runs.pop(next(iter(self._runs)))

    def _run_session(self, index: int, barrier: Barrier | None) -> dict[str, Any]:
        scenario, utterance = SCENARIOS[index % len(SCENARIOS)]
        store = InMemoryConversationStore()
        service = ConversationService(
            self.catalog,
            extractor=RuleBasedExtractor(self.catalog),
            store=store,
        )
        created = service.create_conversation()
        if barrier is not None:
            try:
                barrier.wait(timeout=10)
            except BrokenBarrierError as exc:
                raise RuntimeError("Concurrent start barrier failed") from exc
        started = perf_counter()
        try:
            response = service.process_turn(
                created["conversation_id"],
                utterance,
                message_id=f"scale_{index + 1}",
            )
            result = response["engine_result"]
            return {
                "session_number": index + 1,
                "conversation_id": created["conversation_id"],
                "scenario": scenario,
                "status": "PASS",
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "decision": result["decision"]["status"],
                "action": result["next_action"]["type"],
            }
        except Exception as exc:  # keep the burst running so failures are measured
            return {
                "session_number": index + 1,
                "conversation_id": created["conversation_id"],
                "scenario": scenario,
                "status": "FAIL",
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _percentile(values: list[float], percentile: int) -> float:
        if not values:
            return 0.0
        index = max(0, ceil((percentile / 100) * len(values)) - 1)
        return round(values[index], 2)
