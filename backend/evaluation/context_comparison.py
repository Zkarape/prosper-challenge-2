"""Paired proof for compact-state versus full-transcript LLM context."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

from scheduling.service import ConversationService
from scheduling.storage import InMemoryConversationStore


class ContextComparisonRunner:
    """Run identical conversations while changing only the context strategy."""

    KIND = "CONTEXT_STRATEGY_COMPARISON"
    STRATEGIES = ("compact", "bounded_recent", "full_history")

    def __init__(
        self,
        *,
        catalog: Any,
        configured_extractor: Any,
        dataset_path: str | Path,
        run_store: Any | None = None,
        repetitions: int | None = None,
    ):
        self.catalog = catalog
        self.configured_extractor = configured_extractor
        self.dataset_path = Path(dataset_path)
        self.run_store = run_store
        configured_repetitions = repetitions or int(
            os.getenv("CONTEXT_COMPARISON_REPETITIONS", "3")
        )
        self.repetitions = max(1, min(configured_repetitions, 10))
        self._runs: dict[str, dict[str, Any]] = {}
        self._latest_run_id: str | None = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return callable(
            getattr(self.configured_extractor, "for_context_strategy", None)
        )

    def dataset(self) -> dict[str, Any]:
        payload = json.loads(self.dataset_path.read_text())
        scenarios = payload.get("scenarios", [])
        return {
            **payload,
            "scenario_count": len(scenarios),
            "turn_count": sum(len(item["patient_turns"]) for item in scenarios),
            "repetitions": self.repetitions,
            "scenario_trial_count": len(scenarios) * self.repetitions,
            "turn_count_per_strategy": sum(
                len(item["patient_turns"]) for item in scenarios
            )
            * self.repetitions,
            "total_patient_turns": sum(
                len(item["patient_turns"]) for item in scenarios
            )
            * self.repetitions
            * len(self.STRATEGIES),
            "available": self.available,
        }

    def start(self) -> dict[str, Any]:
        if not self.available:
            raise ValueError("CONTEXT_COMPARISON_REQUIRES_STRUCTURED_LLM")
        run_id = f"context_eval_{uuid4().hex[:12]}"
        placeholder = {
            "kind": self.KIND,
            "run_id": run_id,
            "status": "RUNNING",
            "dataset_version": self.dataset().get("dataset_version"),
            "model": getattr(self.configured_extractor, "model", None),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "duration_ms": None,
            "strategies": None,
            "comparison": None,
            "error": None,
        }
        self._save(placeholder)
        Thread(
            target=self._finish,
            args=(run_id,),
            name=f"context-comparison-{run_id}",
            daemon=True,
        ).start()
        return placeholder

    def _finish(self, run_id: str) -> None:
        try:
            self.run(run_id=run_id)
        except Exception as exc:
            current = self.get(run_id) or {"run_id": run_id, "kind": self.KIND}
            self._save(
                {
                    **current,
                    "status": "ERROR",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    def run(self, *, run_id: str | None = None) -> dict[str, Any]:
        if not self.available:
            raise ValueError("CONTEXT_COMPARISON_REQUIRES_STRUCTURED_LLM")
        dataset = self.dataset()
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        strategy_results = self._run_paired(dataset["scenarios"])
        run = {
            "kind": self.KIND,
            "run_id": run_id or f"context_eval_{uuid4().hex[:12]}",
            "status": "COMPLETED",
            "dataset_version": dataset.get("dataset_version"),
            "dataset_review_status": dataset.get("review_status"),
            "methodology": dataset.get("methodology"),
            "model": getattr(self.configured_extractor, "model", None),
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "strategies": strategy_results,
            "comparison": self._comparison(strategy_results),
            "error": None,
        }
        self._save(run)
        return run

    def _run_paired(
        self, scenarios: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Run scenario chains concurrently while preserving turn order in each."""

        extractors = {
            strategy: self.configured_extractor.for_context_strategy(strategy)
            for strategy in self.STRATEGIES
        }
        jobs = [
            (strategy, item, trial)
            for item in scenarios
            for strategy in self.STRATEGIES
            for trial in range(1, self.repetitions + 1)
        ]
        concurrency = min(
            len(jobs),
            max(1, int(os.getenv("CONTEXT_COMPARISON_CONCURRENCY", "4"))),
        )
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                (
                    strategy,
                    pool.submit(
                        self._run_scenario,
                        item,
                        extractors[strategy],
                        strategy,
                        trial,
                    ),
                )
                for strategy, item, trial in jobs
            ]
            grouped = {strategy: [] for strategy in self.STRATEGIES}
            for strategy, future in futures:
                grouped[strategy].append(future.result())
        return {
            strategy: self._summarize_strategy(strategy, grouped[strategy])
            for strategy in self.STRATEGIES
        }

    def _summarize_strategy(
        self, strategy: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        results.sort(key=lambda item: (item["scenario_id"], item["trial"]))
        passed = sum(item["status"] == "PASS" for item in results)
        unique_scenarios = len({item["scenario_id"] for item in results})
        input_tokens = sum(item["usage"]["input_tokens"] for item in results)
        cached_tokens = sum(
            item["usage"]["cached_input_tokens"] for item in results
        )
        output_tokens = sum(item["usage"]["output_tokens"] for item in results)
        priced = [
            item["usage"]["estimated_cost_usd"]
            for item in results
            if item["usage"]["estimated_cost_usd"] is not None
        ]
        all_turns = [turn for item in results for turn in item["turns"]]
        first_turns = [item["turns"][0] for item in results if item["turns"]]
        last_turns = [item["turns"][-1] for item in results if item["turns"]]
        return {
            "strategy": strategy,
            "scenario_count": unique_scenarios,
            "repetitions": self.repetitions,
            "scenario_trial_count": len(results),
            "passed_scenario_trials": passed,
            "failed_scenario_trials": len(results) - passed,
            # Kept for older stored runs and clients. These are trial counts.
            "passed_scenarios": passed,
            "failed_scenarios": len(results) - passed,
            "accuracy_percent": _percentage(passed, len(results)),
            "patient_turn_count": len(all_turns),
            "model_call_count": sum(
                item["usage"]["model_call_count"] for item in results
            ),
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": round(sum(priced), 8) if priced else None,
            "model_latency_ms": round(
                sum(item["usage"]["model_latency_ms"] for item in results), 2
            ),
            "average_first_turn_input_tokens": _average(
                [item["input_tokens"] for item in first_turns]
            ),
            "average_last_turn_input_tokens": _average(
                [item["input_tokens"] for item in last_turns]
            ),
            "tokens_per_passed_scenario": (
                round((input_tokens + output_tokens) / passed, 2)
                if passed
                else None
            ),
            "scenarios": results,
        }

    def _run_scenario(
        self,
        scenario: dict[str, Any],
        extractor: Any,
        strategy: str,
        trial: int,
    ) -> dict[str, Any]:
        store = InMemoryConversationStore()
        service = ConversationService(
            self.catalog,
            extractor=extractor,
            store=store,
        )
        created = service.create_conversation()
        conversation_id = created["conversation_id"]
        turns: list[dict[str, Any]] = []
        error: str | None = None
        last_response: dict[str, Any] | None = None
        for number, utterance in enumerate(scenario["patient_turns"], start=1):
            try:
                response = service.process_turn(
                    conversation_id,
                    utterance,
                    message_id=(
                        f"{scenario['scenario_id']}_{strategy}_trial_{trial}_{number}"
                    ),
                )
                last_response = response
                usage = response.get("usage") or {}
                turns.append(
                    {
                        "turn": number,
                        "patient_utterance": utterance,
                        "decision_status": (response.get("engine_result") or {})
                        .get("decision", {})
                        .get("status"),
                        "action_type": (response.get("engine_result") or {})
                        .get("next_action", {})
                        .get("type"),
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "cached_input_tokens": int(
                            usage.get("cached_input_tokens") or 0
                        ),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                        "model_call_count": int(usage.get("model_call_count") or 0),
                        "model_latency_ms": round(
                            sum(
                                float(item.get("latency_ms") or 0)
                                for item in response.get("usage_events", [])
                            ),
                            2,
                        ),
                    }
                )
            except Exception as exc:
                error = f"turn {number}: {type(exc).__name__}: {exc}"
                break

        conversation = service.get_conversation(conversation_id)
        differences = self._grade(
            scenario.get("expected_final") or {},
            conversation.patient_request.to_dict(),
            last_response,
            conversation.booking,
        )
        if error:
            differences.append(error)
        priced = [
            event.estimated_cost_usd
            for event in store.usage_events
            if event.estimated_cost_usd is not None
        ]
        return {
            "scenario_id": scenario["scenario_id"],
            "title": scenario["title"],
            "trial": trial,
            "status": "PASS" if not differences else "FAIL",
            "differences": differences,
            "turns": turns,
            "final_patient_request": conversation.patient_request.to_dict(),
            "final_decision_status": (
                (last_response or {}).get("engine_result") or {}
            ).get("decision", {}).get("status"),
            "final_action_type": (
                (last_response or {}).get("engine_result") or {}
            ).get("next_action", {}).get("type"),
            "booking_status": (conversation.booking or {}).get("status"),
            "usage": {
                "model_call_count": len(store.usage_events),
                "input_tokens": sum(item.input_tokens for item in store.usage_events),
                "cached_input_tokens": sum(
                    item.cached_input_tokens for item in store.usage_events
                ),
                "output_tokens": sum(item.output_tokens for item in store.usage_events),
                "total_tokens": sum(
                    item.input_tokens + item.output_tokens
                    for item in store.usage_events
                ),
                "estimated_cost_usd": round(sum(priced), 8) if priced else None,
                "model_latency_ms": round(
                    sum(item.latency_ms for item in store.usage_events), 2
                ),
            },
        }

    def _grade(
        self,
        expected: dict[str, Any],
        patient_request: dict[str, Any],
        response: dict[str, Any] | None,
        booking: dict[str, Any] | None,
    ) -> list[str]:
        differences: list[str] = []
        expected_request = expected.get("patient_request") or {}
        for field, expected_value in expected_request.items():
            actual_value = patient_request.get(field)
            if isinstance(expected_value, dict) and "raw_text_resolves_to" in expected_value:
                raw_text = (actual_value or {}).get("raw_text")
                resolution = self._resolve_field(field, raw_text)
                actual_id = (
                    resolution.selected.get("id")
                    if resolution is not None and resolution.selected
                    else None
                )
                if actual_id != expected_value["raw_text_resolves_to"]:
                    differences.append(
                        f"patient_request.{field} resolved to {actual_id!r}; "
                        f"expected {expected_value['raw_text_resolves_to']!r}"
                    )
            elif isinstance(expected_value, dict):
                for child, child_expected in expected_value.items():
                    child_actual = (actual_value or {}).get(child)
                    if child_actual != child_expected:
                        differences.append(
                            f"patient_request.{field}.{child} was {child_actual!r}; "
                            f"expected {child_expected!r}"
                        )
            elif actual_value != expected_value:
                differences.append(
                    f"patient_request.{field} was {actual_value!r}; expected {expected_value!r}"
                )

        engine = (response or {}).get("engine_result") or {}
        actual_decision = (engine.get("decision") or {}).get("status")
        actual_action = (engine.get("next_action") or {}).get("type")
        if actual_decision != expected.get("decision_status"):
            differences.append(
                f"decision_status was {actual_decision!r}; expected {expected.get('decision_status')!r}"
            )
        if actual_action != expected.get("action_type"):
            differences.append(
                f"action_type was {actual_action!r}; expected {expected.get('action_type')!r}"
            )
        actual_booking = (booking or {}).get("status")
        if actual_booking != expected.get("booking_status"):
            differences.append(
                f"booking_status was {actual_booking!r}; expected {expected.get('booking_status')!r}"
            )
        return differences

    def _resolve_field(self, field: str, raw_text: str | None) -> Any | None:
        if not raw_text:
            return None
        if field == "appointment_type":
            return self.catalog.resolve_appointment_type(raw_text)
        if field == "provider":
            return self.catalog.resolve_provider(raw_text)
        if field == "location":
            return self.catalog.resolve_location(raw_text)
        return None

    @staticmethod
    def _comparison(strategies: dict[str, dict[str, Any]]) -> dict[str, Any]:
        compact = strategies["compact"]
        bounded = strategies["bounded_recent"]
        full = strategies["full_history"]
        input_saved = full["input_tokens"] - bounded["input_tokens"]
        total_saved = full["total_tokens"] - bounded["total_tokens"]
        accuracy_delta = round(
            bounded["accuracy_percent"] - full["accuracy_percent"], 2
        )
        both_perfect = (
            bounded["accuracy_percent"] == 100
            and full["accuracy_percent"] == 100
        )
        if input_saved <= 0:
            conclusion = "NOT_SUPPORTED_TOKEN_SAVINGS"
        elif bounded["accuracy_percent"] < full["accuracy_percent"]:
            conclusion = "TRADEOFF_REQUIRES_REVIEW"
        elif both_perfect:
            conclusion = "SUPPORTED_WITHIN_BENCHMARK"
        else:
            conclusion = "INCONCLUSIVE_ACCURACY"
        bounded_cost = bounded.get("estimated_cost_usd")
        full_cost = full.get("estimated_cost_usd")
        return {
            "selected_strategy": "bounded_recent",
            "baseline_strategy": "full_history",
            "conclusion": conclusion,
            "input_tokens_saved": input_saved,
            "input_tokens_saved_percent": _percentage(input_saved, full["input_tokens"]),
            "total_tokens_saved": total_saved,
            "total_tokens_saved_percent": _percentage(total_saved, full["total_tokens"]),
            "estimated_cost_saved_usd": (
                round(full_cost - bounded_cost, 8)
                if full_cost is not None and bounded_cost is not None
                else None
            ),
            "accuracy_delta_percentage_points": accuracy_delta,
            "same_accuracy": bounded["accuracy_percent"] == full["accuracy_percent"],
            "both_strategies_passed_every_scenario": both_perfect,
            "compact_accuracy_percent": compact["accuracy_percent"],
            "bounded_recent_accuracy_percent": bounded["accuracy_percent"],
            "full_history_accuracy_percent": full["accuracy_percent"],
        }

    def _save(self, run: dict[str, Any]) -> None:
        with self._lock:
            self._runs[run["run_id"]] = deepcopy(run)
            self._latest_run_id = run["run_id"]
            while len(self._runs) > 10:
                self._runs.pop(next(iter(self._runs)))
        save = getattr(self.run_store, "save_evaluation_run", None)
        if save is not None:
            save(run)

    def get(self, run_id: str) -> dict[str, Any] | None:
        load = getattr(self.run_store, "get_evaluation_run", None)
        if load is not None:
            run = load(run_id)
            if run is not None and run.get("kind") == self.KIND:
                return run
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run is not None else None

    def latest(self) -> dict[str, Any] | None:
        load = getattr(self.run_store, "latest_context_comparison", None)
        if load is not None:
            run = load()
            if run is not None:
                return run
        with self._lock:
            if self._latest_run_id is None:
                return None
            return deepcopy(self._runs[self._latest_run_id])


def _percentage(numerator: float, denominator: float) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def _average(values: list[int]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0
