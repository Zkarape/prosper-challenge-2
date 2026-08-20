"""Run reviewed and draft cases through the real scheduling turn path.

The evaluator deliberately uses plain comparisons, not another LLM. That makes
every pass and failure repeatable and inspectable during an interview.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

from conversation import OfferKind, OfferOption, PendingOffer
from scheduling.extractor import RuleBasedExtractor
from scheduling.service import Conversation, ConversationService
from scheduling.state import SchedulingRequest
from scheduling.storage import InMemoryConversationStore


class EvaluationRunner:
    """Execute one case or a complete dataset against production code paths."""

    def __init__(
        self,
        *,
        catalog: Any,
        configured_extractor: Any,
        dataset_path: str | Path,
    ):
        self.catalog = catalog
        self.configured_extractor = configured_extractor
        self.dataset_path = Path(dataset_path)
        self._runs: dict[str, dict[str, Any]] = {}
        self._latest_run_id: str | None = None
        self._lock = RLock()

    def dataset(self) -> dict[str, Any]:
        payload = json.loads(self.dataset_path.read_text())
        cases = []
        for item in payload.get("test_cases", []):
            expected = item.get("expected") or {}
            engine = expected.get("engine") or {}
            complete = bool(
                expected.get("extraction_patch") is not None
                and expected.get("state_after_changes") is not None
                and engine.get("decision_status")
                and engine.get("action_type")
            )
            cases.append({**item, "definition_complete": complete})
        return {
            **payload,
            "case_count": len(cases),
            "defined_case_count": sum(bool(item["definition_complete"]) for item in cases),
            "manual_authored_case_count": sum(
                bool(item.get("manual_completion")) for item in cases
            ),
            "cases": cases,
        }

    def run(
        self,
        *,
        case_ids: list[str] | None = None,
        extractor: str = "configured",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        suite = self.dataset()
        all_cases = suite["cases"]
        by_id = {item["test_case_id"]: item for item in all_cases}
        selected_ids = (
            case_ids
            if case_ids is not None
            else [item["test_case_id"] for item in all_cases]
        )
        unknown = sorted(set(selected_ids) - by_id.keys())
        if unknown:
            raise ValueError(f"Unknown evaluation case: {', '.join(unknown)}")
        if not selected_ids:
            raise ValueError("At least one evaluation case is required")

        if extractor == "local":
            selected_extractor = RuleBasedExtractor(self.catalog)
        elif extractor == "configured":
            selected_extractor = self.configured_extractor
        else:
            raise ValueError("extractor must be 'configured' or 'local'")

        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        selected_cases = [by_id[case_id] for case_id in selected_ids]
        concurrency = min(
            len(selected_cases),
            max(1, int(os.getenv("EVALUATION_CONCURRENCY", "4"))),
        )
        if extractor == "configured" and concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                results = list(
                    pool.map(
                        lambda item: self._run_case(item, selected_extractor),
                        selected_cases,
                    )
                )
        else:
            results = [
                self._run_case(item, selected_extractor) for item in selected_cases
            ]
        ended_at = datetime.now(timezone.utc)
        run_id = run_id or f"eval_{uuid4().hex[:12]}"
        total_tokens = sum(item["usage"]["total_tokens"] for item in results)
        priced = [
            item["usage"]["estimated_cost_usd"]
            for item in results
            if item["usage"]["estimated_cost_usd"] is not None
        ]

        def passed(stage: str) -> int:
            return sum(
                any(
                    stage_result["id"] == stage and stage_result["status"] == "PASS"
                    for stage_result in item["stages"]
                )
                for item in results
            )

        run = {
            "run_id": run_id,
            "status": "COMPLETED",
            "dataset_version": suite.get("dataset_version"),
            "catalog_version": self.catalog.version,
            "extractor_mode": getattr(selected_extractor, "mode", type(selected_extractor).__name__),
            "model": getattr(selected_extractor, "model", None),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "summary": {
                "case_count": len(results),
                "passed_case_count": sum(item["overall_status"] == "PASS" for item in results),
                "failed_case_count": sum(item["overall_status"] == "FAIL" for item in results),
                "error_case_count": sum(item["overall_status"] == "ERROR" for item in results),
                "extraction_passed": passed("extraction"),
                "validation_passed": passed("validation"),
                "state_passed": passed("state"),
                "engine_passed": passed("engine"),
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(sum(priced), 8) if priced else None,
            },
            "cases": results,
        }
        with self._lock:
            self._runs[run_id] = deepcopy(run)
            self._latest_run_id = run_id
            while len(self._runs) > 10:
                self._runs.pop(next(iter(self._runs)))
        return run

    def start(
        self,
        *,
        case_ids: list[str] | None = None,
        extractor: str = "configured",
    ) -> dict[str, Any]:
        """Start a run without holding an HTTP request open for 40 model calls."""

        # Validate IDs and extractor choice before returning a job to the caller.
        suite = self.dataset()
        known = {item["test_case_id"] for item in suite["cases"]}
        selected = case_ids if case_ids is not None else sorted(known)
        unknown = sorted(set(selected) - known)
        if unknown:
            raise ValueError(f"Unknown evaluation case: {', '.join(unknown)}")
        if not selected:
            raise ValueError("At least one evaluation case is required")
        if extractor not in {"configured", "local"}:
            raise ValueError("extractor must be 'configured' or 'local'")

        run_id = f"eval_{uuid4().hex[:12]}"
        started_at = datetime.now(timezone.utc).isoformat()
        placeholder = {
            "run_id": run_id,
            "status": "RUNNING",
            "dataset_version": suite.get("dataset_version"),
            "catalog_version": self.catalog.version,
            "extractor_mode": (
                getattr(self.configured_extractor, "mode", "configured")
                if extractor == "configured"
                else "LOCAL_STRUCTURED"
            ),
            "model": (
                getattr(self.configured_extractor, "model", None)
                if extractor == "configured"
                else None
            ),
            "started_at": started_at,
            "ended_at": None,
            "duration_ms": None,
            "summary": None,
            "cases": [],
            "error": None,
        }
        with self._lock:
            self._runs[run_id] = deepcopy(placeholder)
            self._latest_run_id = run_id
        Thread(
            target=self._finish_started_run,
            kwargs={
                "run_id": run_id,
                "case_ids": case_ids,
                "extractor": extractor,
            },
            name=f"evaluation-{run_id}",
            daemon=True,
        ).start()
        return placeholder

    def _finish_started_run(
        self,
        *,
        run_id: str,
        case_ids: list[str] | None,
        extractor: str,
    ) -> None:
        try:
            self.run(case_ids=case_ids, extractor=extractor, run_id=run_id)
        except Exception as exc:
            with self._lock:
                current = self._runs.get(run_id, {"run_id": run_id})
                self._runs[run_id] = {
                    **current,
                    "status": "ERROR",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                }

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_run_id is None:
                return None
            return deepcopy(self._runs[self._latest_run_id])

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run is not None else None

    def _run_case(self, case: dict[str, Any], extractor: Any) -> dict[str, Any]:
        started = perf_counter()
        try:
            request = self._request_from_case(case)
            pending_offer = self._pending_offer_from_case(case, request)
            store = InMemoryConversationStore()
            case_service = ConversationService(
                self.catalog,
                extractor=extractor,
                store=store,
            )
            conversation = Conversation(
                patient_request=request,
                pending_offer=pending_offer,
                last_result=case_service.engine.evaluate(request),
            )
            store.create(conversation, catalog_hash=self.catalog.version)
            response = case_service.process_turn(
                request.conversation_id,
                case["input"]["patient_utterance"],
                message_id=f"{case['test_case_id']}_turn",
            )
            stages = self._grade(case, response)
            usage = response.get("usage") or {}
            normalized_usage = {
                "model": usage.get("model"),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "estimated_cost_usd": usage.get("estimated_cost_usd"),
            }
            return {
                "test_case_id": case["test_case_id"],
                "title": case["title"],
                "tags": case.get("tags", []),
                "overall_status": (
                    "ERROR"
                    if any(item["status"] == "ERROR" for item in stages)
                    else "PASS"
                    if all(item["status"] == "PASS" for item in stages)
                    else "FAIL"
                ),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "stages": stages,
                "usage": normalized_usage,
                "error": None,
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return {
                "test_case_id": case["test_case_id"],
                "title": case["title"],
                "tags": case.get("tags", []),
                "overall_status": "ERROR",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "stages": [
                    {
                        "id": stage,
                        "label": label,
                        "status": "ERROR" if stage == "extraction" else "SKIPPED",
                        "expected": None,
                        "actual": None,
                        "differences": [error],
                    }
                    for stage, label in (
                        ("extraction", "Extraction meaning"),
                        ("validation", "Validation"),
                        ("state", "State update"),
                        ("engine", "Engine decision"),
                    )
                ],
                "usage": {
                    "model": getattr(extractor, "model", None),
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": None,
                },
                "error": error,
            }

    def _grade(
        self, case: dict[str, Any], response: dict[str, Any]
    ) -> list[dict[str, Any]]:
        expected = case["expected"]
        validated = response.get("validated_extraction")
        actual_extraction = (
            self._actual_extraction(validated)
            if validated is not None
            else self._raw_extraction(response.get("extraction_output"))
        )
        expected_extraction = self._expected_extraction(expected.get("extraction_patch") or {})
        extraction_differences = _differences(expected_extraction, actual_extraction)

        validation_reason = next(
            (
                item.get("detail")
                for item in reversed(response.get("trace") or [])
                if item.get("stage") == "Extract" and item.get("tone") == "warning"
            ),
            None,
        )
        validation_actual = (
            "ACCEPTED" if validated is not None else f"REJECTED: {validation_reason or 'unknown reason'}"
        )
        if validated is None and response.get("extraction_output") is None and _is_system_error(
            validation_reason
        ):
            reason = f"Extractor unavailable: {validation_reason}"
            return [
                _stage(
                    "extraction",
                    "Extraction meaning",
                    expected_extraction,
                    None,
                    [reason],
                    status="ERROR",
                ),
                _stage(
                    "validation",
                    "Validation",
                    "ACCEPTED",
                    "NOT RUN",
                    [reason],
                    status="ERROR",
                ),
                _stage(
                    "state",
                    "State update",
                    self._expected_state(expected.get("state_after_changes") or {}),
                    None,
                    ["Not graded because extraction did not run."],
                    status="SKIPPED",
                ),
                _stage(
                    "engine",
                    "Engine decision",
                    self._expected_engine(expected),
                    None,
                    ["Not graded because extraction did not run."],
                    status="SKIPPED",
                ),
            ]
        validation_differences = [] if validated is not None else [
            "validation: expected ACCEPTED, got REJECTED"
        ]

        expected_state = self._expected_state(expected.get("state_after_changes") or {})
        actual_state = self._evaluation_state(response)
        state_differences = _differences(expected_state, actual_state, partial=True)

        expected_engine = self._expected_engine(expected)
        actual_engine = self._actual_engine(response.get("engine_result") or {})
        if "resolution" in expected_engine:
            expected_resolution_fields = expected_engine["resolution"].keys()
            actual_engine["resolution"] = {
                key: value
                for key, value in actual_engine.get("resolution", {}).items()
                if key in expected_resolution_fields
            }
        else:
            actual_engine.pop("resolution", None)
        engine_differences = _differences(expected_engine, actual_engine)

        return [
            _stage(
                "extraction",
                "Extraction meaning",
                expected_extraction,
                actual_extraction,
                extraction_differences,
            ),
            _stage(
                "validation",
                "Validation",
                "ACCEPTED",
                validation_actual,
                validation_differences,
            ),
            _stage(
                "state",
                "State update",
                expected_state,
                actual_state,
                state_differences,
            ),
            _stage(
                "engine",
                "Engine decision",
                expected_engine,
                actual_engine,
                engine_differences,
            ),
        ]

    def _request_from_case(self, case: dict[str, Any]) -> SchedulingRequest:
        suite = self.dataset()
        state = {**suite.get("default_state", {}), **case["input"].get("state_before", {})}
        active = state.get("active_intents") or []
        current_goal = state.get("current_goal")
        for intent in reversed(active):
            if intent in {
                "BOOK_APPOINTMENT",
                "RESCHEDULE_APPOINTMENT",
                "CANCEL_APPOINTMENT",
            }:
                current_goal = intent
                break

        def entity(name: str) -> dict[str, Any] | None:
            value = state.get(name)
            if not value:
                return None
            return {
                "raw_text": value["raw_text"],
                "requirement": value.get("requirement", "UNSPECIFIED"),
            }

        time_value = state.get("time")
        return SchedulingRequest.from_dict(
            {
                "conversation_id": f"eval_{case['test_case_id']}_{uuid4().hex[:8]}",
                "current_goal": current_goal,
                "patient_status": state.get("patient_status", "UNKNOWN"),
                "referral_status": state.get("referral_status", "UNKNOWN"),
                "appointment_type": entity("appointment_type"),
                "provider": entity("provider"),
                "location": entity("location"),
                "time": (
                    {
                        "raw_text": time_value["raw_text"],
                        "objective": time_value.get("objective", "UNSPECIFIED"),
                    }
                    if time_value
                    else None
                ),
                "primary_priority": state.get("primary_priority", "UNSPECIFIED"),
            }
        )

    def _pending_offer_from_case(
        self, case: dict[str, Any], request: SchedulingRequest
    ) -> PendingOffer | None:
        pending = case["input"].get("pending_action_before")
        if not pending:
            return None
        pending_type = pending["type"]
        target = pending.get("target") or {}
        fingerprint = request.fingerprint()

        if pending_type == "COLLECT_REFERRAL_STATUS":
            kind = OfferKind.FIELD_OPTIONS
            options = [
                OfferOption(
                    "referral_on_file",
                    "Yes, a referral is on file",
                    {"patch": {"referral_status": {"operation": "SET", "value": "ON_FILE"}}},
                ),
                OfferOption(
                    "referral_not_on_file",
                    "No referral is on file",
                    {"patch": {"referral_status": {"operation": "SET", "value": "NOT_ON_FILE"}}},
                ),
            ]
        elif pending_type == "ALLOW_ALTERNATIVE_LOCATION":
            kind = OfferKind.ALTERNATIVE_LOCATION
            candidate = self._candidate_for_target(request, target)
            options = [
                OfferOption(
                    target.get("to_location_id", "alternative_location"),
                    target.get("to_location_name", "Offered location"),
                    {"candidate": candidate},
                )
            ]
        elif pending_type == "CONFIRM_BOOKING":
            kind = OfferKind.CONFIRM_BOOKING
            candidate_id = target.get("candidate_id", "unknown:unknown:unknown")
            appointment_id, provider_id, location_id = candidate_id.split(":", 2)
            candidate = {
                "candidate_id": candidate_id,
                "appointment_type_id": appointment_id,
                "provider_id": provider_id,
                "location_id": location_id,
                "provider_name": self.catalog.providers.get(provider_id, {}).get("name", provider_id),
                "location_name": self.catalog.locations.get(location_id, {}).get("name", location_id),
                "timezone": self.catalog.timezone_for_location(location_id),
            }
            options = [
                OfferOption(
                    target.get("slot_id", "slot_evaluation"),
                    "Previously offered appointment",
                    {
                        "candidate": candidate,
                        "slot": {
                            "slot_id": target.get("slot_id", "slot_evaluation"),
                            "candidate_id": candidate_id,
                            "start": "2030-01-07T09:00:00-08:00",
                            "end": "2030-01-07T10:00:00-08:00",
                        },
                    },
                )
            ]
        else:
            raise ValueError(f"Unsupported pending action in dataset: {pending_type}")

        return PendingOffer(
            offer_id=pending["id"],
            kind=kind,
            request_fingerprint=fingerprint,
            catalog_version=self.catalog.version,
            options=options,
        )

    def _candidate_for_target(
        self, request: SchedulingRequest, target: dict[str, Any]
    ) -> dict[str, Any]:
        appointment = self.catalog.resolve_appointment_type(
            request.appointment_type.raw_text if request.appointment_type else ""
        )
        provider = self.catalog.resolve_provider(
            request.provider.raw_text if request.provider else "",
            appointment.selected["id"] if appointment.selected else None,
        )
        appointment_id = appointment.selected["id"] if appointment.selected else "unknown"
        provider_id = provider.selected["id"] if provider.selected else "unknown"
        location_id = target["to_location_id"]
        return {
            "candidate_id": f"{appointment_id}:{provider_id}:{location_id}",
            "appointment_type_id": appointment_id,
            "provider_id": provider_id,
            "location_id": location_id,
            "provider_name": (
                provider.selected["name"] if provider.selected else "Offered provider"
            ),
            "location_name": target["to_location_name"],
            "timezone": self.catalog.timezone_for_location(location_id),
        }

    @staticmethod
    def _expected_extraction(value: dict[str, Any]) -> dict[str, Any]:
        # Version 1 called the accumulated goal "intents". It is state, not a
        # latest-turn observation, so it is intentionally graded in State update.
        output = {}
        for key, item in value.items():
            if key in {"intents", "primary_priority"}:
                continue
            output[key] = _semantic_value(item)
        return output

    @staticmethod
    def _actual_extraction(validated: dict[str, Any] | None) -> dict[str, Any]:
        if validated is None:
            return {}
        output = {
            key: _semantic_value(value)
            for key, value in (validated.get("patch") or {}).items()
            if key not in {"observed_intents", "primary_priority"}
        }
        if validated.get("pending_answer") != "NONE":
            pending = {"value": validated.get("pending_answer")}
            if validated.get("selection_ordinal") is not None:
                pending["ordinal"] = validated["selection_ordinal"]
            if validated.get("raw_selection_text") is not None:
                pending["raw_selection_text"] = validated["raw_selection_text"]
            output["pending_answer"] = pending
        if validated.get("unclear_references"):
            output["unclear_references"] = [
                _semantic_value(item) for item in validated["unclear_references"]
            ]
        return output

    @staticmethod
    def _raw_extraction(raw: dict[str, Any] | None) -> dict[str, Any]:
        if raw is None:
            return {}
        output = {}
        for key in (
            "patient_status",
            "referral_status",
            "appointment_type",
            "provider",
            "location",
            "time",
        ):
            value = raw.get(key) or {}
            if value.get("operation") != "KEEP":
                output[key] = _semantic_value(value)
        pending = raw.get("pending_answer") or {}
        if pending.get("value") != "NONE":
            output["pending_answer"] = _semantic_value(pending)
        if raw.get("unclear_references"):
            output["unclear_references"] = _semantic_value(raw["unclear_references"])
        return output

    @staticmethod
    def _expected_state(value: dict[str, Any]) -> dict[str, Any]:
        output = {}
        for key, item in value.items():
            if key == "active_intents":
                goals = [
                    intent
                    for intent in item
                    if intent in {
                        "BOOK_APPOINTMENT",
                        "RESCHEDULE_APPOINTMENT",
                        "CANCEL_APPOINTMENT",
                    }
                ]
                output["current_goal"] = goals[-1] if goals else None
            elif key not in {"version", "confirmed_state_version"}:
                output[key] = _state_value(item)
        return output

    @staticmethod
    def _evaluation_state(response: dict[str, Any]) -> dict[str, Any]:
        state = deepcopy(response.get("patient_request") or response.get("state") or {})
        state["selected_candidate_id"] = None
        state["selected_slot_id"] = None
        booking = response.get("booking")
        if booking:
            state["selected_candidate_id"] = booking.get("candidate_id")
            state["selected_slot_id"] = (booking.get("slot") or {}).get("slot_id")
        return _state_value(state)

    @staticmethod
    def _expected_engine(expected: dict[str, Any]) -> dict[str, Any]:
        engine = expected.get("engine") or {}
        output = {
            "decision_status": engine.get("decision_status"),
            "action_type": engine.get("action_type"),
            "question_fields": sorted(engine.get("question_fields") or []),
            "blocker_codes": sorted(engine.get("blocker_codes") or []),
            "valid_candidate_ids": sorted(engine.get("valid_candidate_ids") or []),
            "relaxation_candidate_ids": sorted(
                engine.get("relaxation_candidate_ids") or []
            ),
            "requires_patient_permission": bool(engine.get("requires_patient_permission")),
        }
        resolution = expected.get("resolution") or {}
        if resolution:
            output["resolution"] = _expected_resolution(resolution)
        return output

    @staticmethod
    def _actual_engine(value: dict[str, Any]) -> dict[str, Any]:
        action = value.get("next_action") or {}
        output = {
            "decision_status": (value.get("decision") or {}).get("status"),
            "action_type": action.get("type"),
            "question_fields": sorted(action.get("fields") or []),
            "blocker_codes": sorted(
                item["code"]
                for item in value.get("blockers", [])
                if item.get("code") is not None
            ),
            "valid_candidate_ids": sorted(
                item.get("candidate_id") for item in value.get("valid_candidates", [])
            ),
            "relaxation_candidate_ids": sorted(
                item.get("candidate_id")
                for item in value.get("relaxation_candidates", [])
            ),
            "requires_patient_permission": bool(
                action.get("requires_patient_permission", False)
            ),
        }
        if value.get("resolution"):
            output["resolution"] = _actual_resolution(value["resolution"])
        return output


def _stage(
    stage_id: str,
    label: str,
    expected: Any,
    actual: Any,
    differences: list[str],
    *,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "status": status or ("PASS" if not differences else "FAIL"),
        "expected": expected,
        "actual": actual,
        "differences": differences,
    }


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key in {"evidence", "timezone", "priority"} or item is None:
                continue
            if key == "operation" and item in {"SET", "REPLACE"}:
                output[key] = "SET_OR_REPLACE"
            elif key in {"raw_text", "raw_selection_text"}:
                output[key] = _semantic_text(str(item))
            else:
                output[key] = _semantic_value(item)
        return output
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    return value


def _state_value(value: Any) -> Any:
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key in {"timezone", "priority", "conversation_id"}:
                continue
            output[key] = _semantic_text(str(item)) if key == "raw_text" else _state_value(item)
        return output
    if isinstance(value, list):
        return [_state_value(item) for item in value]
    return value


def _semantic_text(value: str) -> str:
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())
    return re.sub(r"^(?:a|an|the)\s+", "", normalized)


def _expected_resolution(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {
            "status": item.get("status"),
            "selected_id": item.get("selected_id"),
            "candidate_ids": sorted(item.get("candidate_ids") or []),
        }
        for key, item in value.items()
    }


def _actual_resolution(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {
            "status": item.get("status"),
            "selected_id": (item.get("selected") or {}).get("id"),
            "candidate_ids": sorted(
                candidate.get("id") for candidate in item.get("candidates", [])
            ),
        }
        for key, item in value.items()
    }


def _differences(
    expected: Any,
    actual: Any,
    *,
    path: str = "",
    partial: bool = False,
) -> list[str]:
    name = path or "value"
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{name}: expected an object, got {actual!r}"]
        output: list[str] = []
        for key, expected_value in expected.items():
            child = f"{path}.{key}" if path else key
            if key not in actual:
                output.append(f"{child}: missing; expected {expected_value!r}")
            else:
                output.extend(
                    _differences(
                        expected_value,
                        actual[key],
                        path=child,
                        partial=partial,
                    )
                )
        if not partial:
            for key in actual.keys() - expected.keys():
                child = f"{path}.{key}" if path else key
                output.append(f"{child}: unexpected {actual[key]!r}")
        return output
    if expected != actual:
        return [f"{name}: expected {expected!r}, got {actual!r}"]
    return []


def _is_system_error(reason: str | None) -> bool:
    if not reason:
        return False
    return reason in {
        "APIConnectionError",
        "APITimeoutError",
        "AuthenticationError",
        "PermissionDeniedError",
        "RateLimitError",
    }
