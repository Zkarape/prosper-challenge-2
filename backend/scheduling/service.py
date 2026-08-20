"""Shared typed/voice conversation loop for deterministic scheduling."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from conversation import OfferKind, OfferOption, PendingOffer, UsageEvent, UsageLedger
from extraction import ExtractionValidator, OpenAIExtractor, SemanticValidationError

from .availability import MockAvailability, MockBookingService, Slot
from .catalog import Catalog
from .engine import SchedulingEngine
from .extractor import RuleBasedExtractor
from .state import PreferencePriority, Requirement, SchedulingRequest, TimePreference
from .storage import (
    InMemoryConversationStore,
    load_default_agent_config,
    postgres_store_from_environment,
)


GREETING = "Hi, I’m the clinic’s scheduling assistant. How can I help you today?"


@dataclass
class Conversation:
    patient_request: SchedulingRequest
    message_number: int = 0
    pending_offer: PendingOffer | None = None
    booking: dict[str, Any] | None = None
    last_result: dict[str, Any] | None = None
    processed_messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejected_alternatives: set[str] = field(default_factory=set)
    status: str = "ACTIVE"
    outcome: str | None = None
    safe: bool | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def state(self) -> SchedulingRequest:
        """Temporary compatibility for callers created before the rename."""

        return self.patient_request


class ConversationService:
    """Extract observations, validate them, and execute only checked actions."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        extractor: Any | None = None,
        today_provider=date.today,
        store: Any | None = None,
        availability: Any | None = None,
        booking_service: Any | None = None,
        usage_ledger: UsageLedger | None = None,
    ):
        self.catalog = catalog
        self.engine = SchedulingEngine(catalog)
        self.extractor = extractor or RuleBasedExtractor(catalog)
        self.validator = ExtractionValidator()
        self.store = store or InMemoryConversationStore()
        self.availability = availability or MockAvailability(
            catalog,
            reservation_store=self.store if self.store.durable else None,
        )
        self.booking_service = booking_service or (
            self.store
            if self.store.durable
            else MockBookingService(self.availability)
        )
        self.usage_ledger = usage_ledger or UsageLedger(self.store.record_usage_event)
        self.today_provider = today_provider
        # Kept for compatibility with existing local debugging code. Production
        # code goes through the store and never relies on this process-local dict.
        self.conversations = getattr(self.store, "conversations", {})

    @classmethod
    def default(cls) -> "ConversationService":
        catalog_path = Path(__file__).parents[1] / "data" / "catalog.json"
        catalog = Catalog.from_json(catalog_path)
        mode = os.getenv("EXTRACTOR_MODE", "auto").casefold()
        if mode == "openai" or (mode == "auto" and os.getenv("OPENAI_API_KEY")):
            extractor = OpenAIExtractor()
        else:
            extractor = RuleBasedExtractor(catalog)
        store = postgres_store_from_environment(catalog) or InMemoryConversationStore()
        store.sync_configuration(
            catalog=catalog,
            agent_config=load_default_agent_config(),
        )
        return cls(catalog, extractor=extractor, store=store)

    @property
    def extractor_mode(self) -> str:
        return self.extractor.mode

    @property
    def storage_mode(self) -> str:
        return "postgresql" if self.store.durable else "memory"

    def create_conversation(self) -> dict[str, Any]:
        request = SchedulingRequest()
        conversation = Conversation(patient_request=request)
        self.store.create(conversation, catalog_hash=self.catalog.version)
        return {
            "conversation_id": request.conversation_id,
            "assistant_message": GREETING,
            "patient_request": request.to_dict(),
            "state": request.to_dict(),
            "pending_offer": None,
            "extractor_mode": self.extractor_mode,
            "status": conversation.status,
            "started_at": conversation.started_at.isoformat(),
        }

    def get_conversation(self, conversation_id: str) -> Conversation:
        return self.store.get(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        self.store.delete(conversation_id)

    def finish_conversation(
        self, conversation_id: str, *, forced_outcome: str | None = None
    ) -> dict[str, Any]:
        """Finalize a call and derive its outcome from server-owned facts."""

        conversation = self.get_conversation(conversation_id)
        if conversation.status != "ACTIVE":
            return self.store.conversation_evaluation(conversation_id)

        if forced_outcome == "SYSTEM_ERROR":
            status, outcome, safe = "FAILED", "SYSTEM_ERROR", False
        elif forced_outcome == "PATIENT_ABANDONED":
            status, outcome, safe = "ABANDONED", "PATIENT_ABANDONED", False
        elif conversation.booking and conversation.booking.get("status") == "confirmed":
            status, outcome, safe = "COMPLETED", "BOOKING_CONFIRMED", True
        else:
            result = conversation.last_result or {}
            action = result.get("next_action", {}).get("type")
            decision = result.get("decision", {}).get("status")
            if action == "HANDOFF_TO_STAFF":
                status, outcome, safe = "COMPLETED", "HANDED_OFF", True
            elif decision == "INFORMATION_ANSWERED":
                status, outcome, safe = "COMPLETED", "INFORMATION_ANSWERED", True
            elif any(
                blocker.get("code") and blocker.get("recoverable") is False
                for blocker in result.get("blockers", [])
            ):
                status, outcome, safe = "COMPLETED", "CORRECTLY_BLOCKED", True
            else:
                status, outcome, safe = "ABANDONED", "PATIENT_ABANDONED", False

        self.store.finish_conversation(
            conversation_id,
            status=status,
            outcome=outcome,
            safe=safe,
        )
        return self.store.conversation_evaluation(conversation_id)

    def conversation_evaluation(self, conversation_id: str) -> dict[str, Any]:
        return self.store.conversation_evaluation(conversation_id)

    def evaluation_summary(self) -> dict[str, Any]:
        return self.store.evaluation_summary()

    @staticmethod
    def _complete_conversation_object(
        conversation: Conversation, *, outcome: str, safe: bool = True
    ) -> None:
        conversation.status = "COMPLETED" if safe else "FAILED"
        conversation.outcome = outcome
        conversation.safe = safe
        conversation.ended_at = datetime.now(timezone.utc)

    def process_turn(
        self,
        conversation_id: str,
        utterance: str,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            with self.store.locked(conversation_id) as conversation:
                return self._process_turn_locked(
                    conversation,
                    utterance,
                    message_id=message_id,
                )
        except ValueError as exc:
            # A retry of the final message must remain idempotent even though
            # the successful booking has already closed the conversation.
            if str(exc) == "CONVERSATION_CLOSED" and message_id:
                conversation = self.get_conversation(conversation_id)
                previous = conversation.processed_messages.get(message_id)
                if previous is not None:
                    return previous
            raise

    def _process_turn_locked(
        self,
        conversation: Conversation,
        utterance: str,
        *,
        message_id: str | None,
    ) -> dict[str, Any]:
        conversation_id = conversation.patient_request.conversation_id
        clean_utterance = utterance.strip()
        if not clean_utterance:
            raise ValueError("UTTERANCE_REQUIRED")
        message_id = message_id or f"message_{uuid4().hex[:12]}"
        if message_id in conversation.processed_messages:
            return conversation.processed_messages[message_id]

        conversation.message_number += 1
        started = perf_counter()
        trace: list[dict[str, Any]] = []
        recovery_option = self._match_recovery_option(
            conversation.pending_offer, clean_utterance
        )
        extraction_result = None
        validated = None
        turn_usage_events: list[UsageEvent] = []
        if recovery_option is not None:
            self._trace(
                trace,
                "Extract",
                perf_counter(),
                "Recovery choice matched",
                "A server-authored option was resolved without an LLM call",
                "success",
                {"option_id": recovery_option.option_id},
            )
            result = self._accept_recovery_option(
                conversation, recovery_option, trace
            )
        else:
            turn_usage_events, extraction_result, validated = self._extract_and_validate(
                conversation,
                clean_utterance,
                trace,
                turn_id=message_id,
            )

            if validated is None:
                result = self._safe_extraction_failure(conversation, trace)
            elif validated.unclear_references or validated.pending_answer == "UNCLEAR":
                result = self._clarify_reference(conversation, trace)
            elif validated.pending_answer != "NONE":
                recovery_selection = (
                    conversation.pending_offer is not None
                    and conversation.pending_offer.kind == OfferKind.RECOVERY_OPTIONS
                    and validated.pending_answer == "SELECT"
                )
                compatible_option_answer = self._pending_answer_matches_patch(
                    conversation.pending_offer, validated
                )
                if (
                    validated.patch
                    and set(validated.patch) != {"observed_intents"}
                    and not recovery_selection
                    and not compatible_option_answer
                ):
                    result = self._clarify_mixed_answer(conversation, trace)
                else:
                    result = self._handle_pending_answer(conversation, validated, trace)
            elif (
                conversation.pending_offer is not None
                and not validated.patch
                and not validated.unclear_references
            ):
                result = self._repeat_pending_question(conversation, trace)
            else:
                observed = validated.patch.get("observed_intents", [])
                if observed == ["ASK_INFORMATION"]:
                    result = self._answer_information(conversation, trace, validated.patch)
                else:
                    before = conversation.patient_request.fingerprint()
                    if validated.patch:
                        conversation.patient_request = conversation.patient_request.apply_patch(
                            validated.patch
                        )
                    after = conversation.patient_request.fingerprint()
                    if before != after:
                        conversation.pending_offer = None
                    result = self._evaluate_request(conversation, trace, validated.patch)

        telemetry = extraction_result.telemetry.to_dict() if extraction_result else {}
        total_input_tokens = sum(item.input_tokens for item in turn_usage_events)
        total_cached_tokens = sum(
            item.cached_input_tokens for item in turn_usage_events
        )
        total_output_tokens = sum(item.output_tokens for item in turn_usage_events)
        priced_costs = [
            item.estimated_cost_usd
            for item in turn_usage_events
            if item.estimated_cost_usd is not None
        ]
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        result.update(
            conversation_id=conversation_id,
            message_id=message_id,
            message_number=conversation.message_number,
            patient_text=clean_utterance,
            patient_request=conversation.patient_request.to_dict(),
            state=conversation.patient_request.to_dict(),
            pending_offer=(
                conversation.pending_offer.to_dict(include_values=False)
                if conversation.pending_offer
                else None
            ),
            trace=trace,
            total_latency_ms=elapsed_ms,
            extractor_mode=self.extractor_mode,
            extraction_output=(
                extraction_result.parsed.model_dump(mode="json")
                if extraction_result is not None
                else None
            ),
            validated_extraction=(
                {
                    "patch": validated.patch,
                    "pending_answer": validated.pending_answer,
                    "selection_ordinal": validated.selection_ordinal,
                    "raw_selection_text": validated.raw_selection_text,
                    "unclear_references": validated.unclear_references,
                }
                if validated is not None
                else None
            ),
            extraction_telemetry=telemetry,
            usage_events=[item.to_dict() for item in turn_usage_events],
            usage={
                "model": telemetry.get("model"),
                "model_call_count": len(turn_usage_events),
                "input_tokens": total_input_tokens,
                "cached_input_tokens": total_cached_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "estimated_cost_usd": (
                    round(sum(priced_costs), 8) if priced_costs else None
                ),
            },
        )
        conversation.processed_messages[message_id] = result
        if len(conversation.processed_messages) > 100:
            oldest = next(iter(conversation.processed_messages))
            conversation.processed_messages.pop(oldest, None)
        return result

    def _extract_and_validate(
        self,
        conversation: Conversation,
        utterance: str,
        trace: list[dict[str, Any]],
        *,
        turn_id: str,
    ) -> tuple[list[UsageEvent], Any | None, Any | None]:
        stage = perf_counter()
        corrective_feedback = None
        last_result = None
        usage_events: list[UsageEvent] = []
        for attempt in range(2):
            try:
                last_result = self.extractor.extract(
                    patient_text=utterance,
                    patient_request=conversation.patient_request.to_dict(),
                    pending_offer=(
                        conversation.pending_offer.to_dict(include_values=False)
                        if conversation.pending_offer
                        else None
                    ),
                    corrective_feedback=corrective_feedback,
                )
            except Exception as exc:
                self._trace(
                    trace,
                    "Extract",
                    stage,
                    "Extraction unavailable",
                    type(exc).__name__,
                    "warning",
                    {"validation_status": "ERROR"},
                )
                return usage_events, last_result, None

            if last_result.telemetry.model:
                event = UsageEvent.from_telemetry(
                    conversation_id=conversation.patient_request.conversation_id,
                    turn_id=turn_id,
                    stage="EXTRACTION",
                    telemetry=last_result.telemetry,
                )
                # The usage write happens immediately after the billable call,
                # independently of whether semantic validation accepts it.
                self.usage_ledger.record_call(event)
                usage_events.append(event)

            try:
                validated = self.validator.validate_and_convert(
                    extraction=last_result.parsed,
                    transcript=utterance,
                    patient_request=conversation.patient_request,
                    pending_offer=conversation.pending_offer,
                )
                self._trace(
                    trace,
                    "Extract",
                    stage,
                    "Patient observations validated",
                    f"{len(validated.patch)} trusted field change(s)",
                    "success",
                    self._safe_patch_summary(validated.patch, validated.pending_answer),
                )
                return usage_events, last_result, validated
            except SemanticValidationError as exc:
                corrective_feedback = str(exc)
                if attempt == 0:
                    continue
                self._trace(
                    trace,
                    "Extract",
                    stage,
                    "Extraction rejected",
                    corrective_feedback,
                    "warning",
                    {"validation_status": "REJECTED", "attempts": 2},
                )
                return usage_events, last_result, None
        return usage_events, last_result, None

    def _handle_pending_answer(
        self, conversation: Conversation, validated: Any, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        offer = conversation.pending_offer
        if offer is None:
            return self._clarify_reference(conversation, trace)
        if offer.request_fingerprint != conversation.patient_request.fingerprint():
            conversation.pending_offer = None
            return self._evaluate_request(conversation, trace, {})

        answer = validated.pending_answer
        if (
            offer.kind == OfferKind.FIELD_OPTIONS
            and offer.options
            and offer.options[0].option_id == "referral_on_file"
            and answer in {"ACCEPT", "REJECT"}
        ):
            option = offer.options[0] if answer == "ACCEPT" else offer.options[1]
            return self._accept_field_option(conversation, option, trace)
        if answer == "REJECT":
            for option in offer.options:
                conversation.rejected_alternatives.add(option.option_id)
            conversation.pending_offer = None
            self._trace(trace, "Decision", perf_counter(), "Offer declined", "No booking write was attempted", "active")
            return self._base_result(
                conversation,
                "No problem. Nothing was booked. Tell me what you’d like to change.",
                {},
            )

        option = None
        if answer == "SELECT":
            option = self._selected_offer_option(offer, validated)
        elif answer == "ACCEPT" and len(offer.options) == 1:
            option = offer.options[0]
        if option is None:
            self._trace(trace, "Decision", perf_counter(), "Ask for one offered option", "No option was guessed", "active")
            return self._base_result(
                conversation,
                f"Which option would you like—{self._option_words(len(offer.options))}?",
                {},
                offered_slots=self._offered_slots(offer),
            )

        if offer.kind == OfferKind.ALTERNATIVE_LOCATION:
            return self._accept_alternative(conversation, option, trace)
        if offer.kind == OfferKind.RECOVERY_OPTIONS:
            return self._accept_recovery_option(conversation, option, trace)
        if offer.kind == OfferKind.SLOT_OPTIONS:
            return self._select_slot(conversation, option, trace)
        if offer.kind == OfferKind.CONFIRM_BOOKING:
            return self._confirm_booking(conversation, offer, option, trace)
        if offer.kind == OfferKind.FIELD_OPTIONS:
            return self._accept_field_option(conversation, option, trace)
        return self._clarify_reference(conversation, trace)

    @staticmethod
    def _match_recovery_option(
        offer: PendingOffer | None, patient_text: str
    ) -> OfferOption | None:
        if offer is None or offer.kind != OfferKind.RECOVERY_OPTIONS:
            return None
        patterns = (
            (0, r"\b(first|one|different|another|other|change)\b"),
            (1, r"\b(second|two|staff|person|human|clinic help|help with)\b"),
        )
        for index, pattern in patterns:
            if re.search(pattern, patient_text, re.I) and index < len(offer.options):
                return offer.options[index]
        return None

    @staticmethod
    def _selected_offer_option(offer: PendingOffer, validated: Any) -> OfferOption | None:
        if validated.selection_ordinal is not None:
            return offer.option_for_ordinal(validated.selection_ordinal)
        raw_text = (validated.raw_selection_text or "").casefold().strip()
        if not raw_text:
            return None
        normalized_raw = " ".join(raw_text.split())
        matches = [
            option
            for option in offer.options
            if normalized_raw in " ".join(option.label.casefold().split())
            or " ".join(option.label.casefold().split()) in normalized_raw
        ]
        return matches[0] if len(matches) == 1 else None

    def _pending_answer_matches_patch(
        self, offer: PendingOffer | None, validated: Any
    ) -> bool:
        if offer is None or offer.kind != OfferKind.FIELD_OPTIONS:
            return False
        option = self._selected_offer_option(offer, validated)
        if option is None and validated.pending_answer == "ACCEPT" and len(offer.options) == 1:
            option = offer.options[0]
        if option is None:
            return False
        offered_fields = set(option.value.get("patch", {}))
        extracted_fields = set(validated.patch) - {"observed_intents"}
        return bool(extracted_fields) and extracted_fields <= offered_fields

    def _accept_recovery_option(
        self,
        conversation: Conversation,
        option: OfferOption,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action = option.value.get("action")
        conversation.pending_offer = None
        if action == "CHANGE_APPOINTMENT_TYPE":
            patch = {"appointment_type": {"operation": "CLEAR"}}
            conversation.patient_request = conversation.patient_request.apply_patch(patch)
            self._trace(
                trace,
                "Apply",
                perf_counter(),
                "Patient chose a different appointment type",
                "The ineligible MRI request was removed; patient status was preserved",
                "success",
                {"fields": ["appointment_type"]},
            )
            return self._evaluate_request(conversation, trace, patch)

        engine_result = dict(conversation.last_result or self._empty_engine_result(conversation))
        engine_result["next_action"] = {"type": "HANDOFF_TO_STAFF"}
        conversation.last_result = engine_result
        self._complete_conversation_object(conversation, outcome="HANDED_OFF")
        self._trace(
            trace,
            "Decision",
            perf_counter(),
            "Explain staff handoff",
            "No appointment was booked or promised",
            "active",
        )
        return self._base_result(
            conversation,
            "Clinic staff need to help with this MRI request. I haven’t booked anything.",
            {},
            engine_result=engine_result,
        )

    def _accept_alternative(
        self, conversation: Conversation, option: OfferOption, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        candidate = option.value["candidate"]
        patch: dict[str, Any] = {}
        request = conversation.patient_request
        if request.provider and candidate["provider_id"] != self._resolved_id(request.provider.raw_text, "provider"):
            patch["provider"] = {
                "operation": "REPLACE",
                "raw_text": candidate["provider_name"],
                "requirement": "REQUIRED",
            }
        if request.location and candidate["location_id"] != self._resolved_id(request.location.raw_text, "location"):
            patch["location"] = {
                "operation": "REPLACE",
                "raw_text": candidate["location_name"],
                "requirement": "REQUIRED",
            }
        conversation.patient_request = request.apply_patch(patch)
        conversation.pending_offer = None
        self._trace(trace, "Apply", perf_counter(), "Alternative accepted", "Only the offered field was changed", "success", {"fields": sorted(patch)})
        return self._evaluate_request(conversation, trace, patch)

    def _accept_field_option(
        self, conversation: Conversation, option: OfferOption, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        patch = option.value["patch"]
        conversation.patient_request = conversation.patient_request.apply_patch(patch)
        conversation.pending_offer = None
        self._trace(trace, "Apply", perf_counter(), "Answer applied", option.label, "success", {"fields": sorted(patch)})
        return self._evaluate_request(conversation, trace, patch)

    def _select_slot(
        self, conversation: Conversation, option: OfferOption, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        candidate = option.value["candidate"]
        slot = self._slot_from_dict(option.value["slot"])
        if not self.availability.is_available(slot.id):
            conversation.pending_offer = None
            return self._evaluate_request(conversation, trace, {})
        confirmation_option = OfferOption(
            option_id=f"confirm_{slot.id}",
            label=option.label,
            value={"candidate": candidate, "slot": slot.to_dict()},
        )
        conversation.pending_offer = PendingOffer(
            kind=OfferKind.CONFIRM_BOOKING,
            request_fingerprint=conversation.patient_request.fingerprint(),
            catalog_version=self.catalog.version,
            options=[confirmation_option],
        )
        self._trace(trace, "Validate", perf_counter(), "Selected slot is still available", f"{slot.duration_min}-minute slot", "success")
        self._trace(trace, "Decision", perf_counter(), "Request exact booking confirmation", conversation.pending_offer.offer_id, "active")
        return self._base_result(
            conversation,
            (
                f"Please confirm: {self._format_slot(slot)} with {candidate['provider_name']} "
                f"at {candidate['location_name']}. Should I book it?"
            ),
            {},
        )

    def _confirm_booking(
        self,
        conversation: Conversation,
        offer: PendingOffer,
        option: OfferOption,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidate = option.value["candidate"]
        slot = self._slot_from_dict(option.value["slot"])
        current_result = self.engine.evaluate(conversation.patient_request)
        valid_ids = {item["candidate_id"] for item in current_result["valid_candidates"]}
        if candidate["candidate_id"] not in valid_ids:
            conversation.pending_offer = None
            self._trace(trace, "Validate", perf_counter(), "Offer is no longer bookable", "Eligibility changed", "warning")
            return self._evaluate_request(conversation, trace, {})

        stage = perf_counter()
        try:
            # Availability is checked and claimed atomically by the booking
            # adapter. A pre-check here would create a race between workers and
            # would hide an already-confirmed idempotent retry.
            booking = self.booking_service.book(
                conversation_id=conversation.patient_request.conversation_id,
                offer_id=offer.offer_id,
                candidate=candidate,
                slot=slot,
                offered_request_fingerprint=offer.request_fingerprint,
                current_request_fingerprint=conversation.patient_request.fingerprint(),
                offered_catalog_version=offer.catalog_version,
                current_catalog_version=self.catalog.version,
            )
        except ValueError as exc:
            if str(exc) != "SLOT_NO_LONGER_AVAILABLE":
                raise
            conversation.pending_offer = None
            self._trace(
                trace,
                "Validate",
                stage,
                "Selected slot was just taken",
                "The booking system rejected the write; no booking was claimed",
                "warning",
            )
            return self._evaluate_request(conversation, trace, {})
        if (
            booking.get("status") != "confirmed"
            or not booking.get("booking_id")
            or booking.get("offer_id") != offer.offer_id
        ):
            raise ValueError("BOOKING_SERVICE_DID_NOT_CONFIRM")
        conversation.booking = booking
        conversation.pending_offer = None
        conversation.last_result = current_result
        self._complete_conversation_object(
            conversation, outcome="BOOKING_CONFIRMED"
        )
        self._trace(trace, "Book", stage, "Booking system confirmed the appointment", booking["booking_id"], "success", {"booking_id": booking["booking_id"], "offer_id": offer.offer_id})
        self._trace(trace, "Decision", perf_counter(), "Report confirmed booking", "Success came from the booking system", "active")
        return self._base_result(
            conversation,
            f"You’re booked for {self._format_slot(slot)} with {candidate['provider_name']} at {candidate['location_name']}.",
            {},
            booking=booking,
        )

    def _evaluate_request(
        self,
        conversation: Conversation,
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        request = conversation.patient_request
        if request.current_goal == "BOOK_APPOINTMENT" and request.time is None:
            request.time = TimePreference(
                raw_text="earliest available (default)",
                objective="EARLIEST_AVAILABLE",
            )
            if request.primary_priority == PreferencePriority.UNSPECIFIED:
                request.primary_priority = PreferencePriority.EARLIEST_TIME
            self._trace(
                trace,
                "Default",
                perf_counter(),
                "Earliest availability selected",
                "No time preference was stated, so the scheduler used its earliest-time default",
                "success",
                {
                    "field": "time",
                    "value": "EARLIEST_AVAILABLE",
                    "source": "SERVER_DEFAULT",
                },
            )

        if conversation.patient_request.current_goal in {
            "RESCHEDULE_APPOINTMENT",
            "CANCEL_APPOINTMENT",
        }:
            goal = conversation.patient_request.current_goal
            engine_result = {
                "catalog_version": self.catalog.version,
                "request_fingerprint": conversation.patient_request.fingerprint(),
                "resolution": {},
                "decision": {"status": "STAFF_HANDOFF_REQUIRED"},
                "blockers": [
                    {
                        "code": "GOAL_NOT_AUTOMATED",
                        "field": "current_goal",
                        "reason": "This demo automates new bookings only.",
                        "recoverable": False,
                    }
                ],
                "rule_results": [],
                "valid_candidates": [],
                "relaxation_candidates": [],
                "next_action": {"type": "HANDOFF_TO_STAFF"},
            }
            conversation.last_result = engine_result
            conversation.pending_offer = None
            self._complete_conversation_object(conversation, outcome="HANDED_OFF")
            action = "rescheduling" if goal == "RESCHEDULE_APPOINTMENT" else "cancellation"
            self._trace(trace, "Decision", perf_counter(), "Route to clinic staff", f"Automated {action} is outside this demo", "active")
            return self._base_result(
                conversation,
                f"I can help with new bookings here, but clinic staff need to handle {action}.",
                patch,
                engine_result=engine_result,
            )
        stage = perf_counter()
        engine_result = self.engine.evaluate(conversation.patient_request)
        conversation.last_result = engine_result
        resolved = [item for item in engine_result["resolution"].values() if item["status"] == "RESOLVED"]
        self._trace(trace, "Resolve", stage, f"{len(resolved)} catalog identities resolved", self._resolution_detail(engine_result), "success" if resolved else "warning")

        stage = perf_counter()
        failures = [item for item in engine_result["rule_results"] if item["status"] == "FAIL"]
        unknowns = [item for item in engine_result["rule_results"] if item["status"] == "UNKNOWN"]
        self._trace(
            trace,
            "Rules",
            stage,
            "All relevant rules passed" if not failures and not unknowns else f"{len(failures)} failure(s), {len(unknowns)} unknown(s)",
            self._rule_detail(failures or unknowns),
            "success" if not failures and not unknowns else "warning",
            {"failure_codes": [item["rule"] for item in failures], "unknown_fields": [item["field"] for item in unknowns]},
        )

        action = engine_result["next_action"]["type"]
        if action == "QUERY_AVAILABILITY":
            return self._offer_slots(conversation, engine_result, trace, patch)
        if action == "OFFER_ALTERNATIVES":
            return self._offer_alternative(conversation, engine_result, trace, patch)
        if action in {"ASK_REQUIRED_FIELD", "ASK_CLARIFICATION"}:
            return self._ask_engine_question(conversation, engine_result, trace, patch)
        if self._can_offer_ineligible_patient_recovery(engine_result):
            return self._offer_ineligible_patient_recovery(
                conversation, engine_result, trace, patch
            )

        conversation.pending_offer = None
        response = self._response_for_engine(engine_result)
        self._trace(trace, "Decision", perf_counter(), "Cannot safely schedule", "No invalid candidate was offered", "active")
        return self._base_result(conversation, response, patch, engine_result=engine_result)

    @staticmethod
    def _can_offer_ineligible_patient_recovery(result: dict[str, Any]) -> bool:
        return any(
            blocker.get("code") == "APPOINTMENT_ALLOWS_NEW_PATIENTS"
            for blocker in result["blockers"]
        )

    def _offer_ineligible_patient_recovery(
        self,
        conversation: Conversation,
        result: dict[str, Any],
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        options = [
            OfferOption(
                "change_appointment_type",
                "look for a different appointment type",
                {"action": "CHANGE_APPOINTMENT_TYPE"},
            ),
            OfferOption(
                "request_staff_help",
                "get help from clinic staff",
                {"action": "HANDOFF_TO_STAFF"},
            ),
        ]
        conversation.pending_offer = PendingOffer(
            kind=OfferKind.RECOVERY_OPTIONS,
            request_fingerprint=conversation.patient_request.fingerprint(),
            catalog_version=self.catalog.version,
            options=options,
        )
        self._trace(
            trace,
            "Decision",
            perf_counter(),
            "Offer safe next steps",
            "The appointment type was not silently substituted",
            "active",
            {"option_ids": [option.option_id for option in options]},
        )
        appointment_resolution = result["resolution"].get("appointment_type", {})
        selected_appointment = appointment_resolution.get("selected") or {}
        appointment_name = selected_appointment.get("name", "This appointment type")
        return self._base_result(
            conversation,
            (
                f"{appointment_name} appointments are available only to existing patients. "
                "Would you like to look for a different appointment type that accepts "
                "new patients, or get help from clinic staff with the original request?"
            ),
            patch,
            engine_result=result,
        )

    def _offer_slots(
        self,
        conversation: Conversation,
        result: dict[str, Any],
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        ranked: list[tuple[int, Slot, dict[str, Any]]] = []
        for rank, candidate in enumerate(result["valid_candidates"][:10]):
            for slot in self.availability.find_slots(
                candidate, self.today_provider() + timedelta(days=1), limit=3
            ):
                ranked.append((rank, slot, candidate))
        if conversation.patient_request.primary_priority.value == "EARLIEST_TIME":
            ranked.sort(key=lambda item: (item[1].start, item[0], item[1].id))
        else:
            ranked.sort(key=lambda item: (item[0], item[1].start, item[1].id))
        earliest_first = (
            conversation.patient_request.primary_priority
            == PreferencePriority.EARLIEST_TIME
        )
        selected = ranked[:1] if earliest_first else ranked[:3]
        options = [
            OfferOption(
                option_id=f"option_{index + 1}_{slot.id}",
                label=f"{self._format_slot(slot)} with {candidate['provider_name']} at {candidate['location_name']}",
                value={"candidate": candidate, "slot": slot.to_dict()},
            )
            for index, (_, slot, candidate) in enumerate(selected)
        ]
        conversation.pending_offer = PendingOffer(
            kind=OfferKind.SLOT_OPTIONS,
            request_fingerprint=conversation.patient_request.fingerprint(),
            catalog_version=self.catalog.version,
            options=options,
        )
        self._trace(
            trace,
            "Decision",
            perf_counter(),
            "Offer earliest checked slot" if earliest_first else f"Offer {len(options)} checked slots",
            "Compared availability across eligible candidates",
            "active",
            {
                "offer_id": conversation.pending_offer.offer_id,
                "option_ids": [item.option_id for item in options],
            },
        )
        choices = "; ".join(f"{index + 1}) {item.label}" for index, item in enumerate(options))
        message = (
            f"The earliest opening is {options[0].label}. Does that time work for you?"
            if earliest_first and options
            else f"I found these openings: {choices}. Which option works best?"
        )
        return self._base_result(
            conversation,
            message,
            patch,
            engine_result=result,
            offered_slots=[item.value["slot"] for item in options],
        )

    def _offer_alternative(
        self,
        conversation: Conversation,
        result: dict[str, Any],
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        alternatives = [
            item
            for item in result["relaxation_candidates"]
            if item["candidate_id"] not in conversation.rejected_alternatives
        ]
        if not alternatives:
            conversation.pending_offer = None
            return self._base_result(
                conversation,
                "I don’t have another safe alternative to offer. A staff member can help with next steps.",
                patch,
                engine_result=result,
            )
        alternative = alternatives[0]
        option = OfferOption(
            option_id=alternative["candidate_id"],
            label=f"{alternative['provider_name']} at {alternative['location_name']}",
            value={"candidate": alternative},
        )
        conversation.pending_offer = PendingOffer(
            kind=OfferKind.ALTERNATIVE_LOCATION,
            request_fingerprint=conversation.patient_request.fingerprint(),
            catalog_version=self.catalog.version,
            options=[option],
        )
        reason = result["blockers"][0]["reason"] if result["blockers"] else "That exact combination is not available."
        self._trace(trace, "Decision", perf_counter(), "Offer one safe alternative", f"Patient permission required · {conversation.pending_offer.offer_id}", "active")
        return self._base_result(
            conversation,
            f"{reason} I can offer {option.label} instead. Would that work?",
            patch,
            engine_result=result,
        )

    def _ask_engine_question(
        self,
        conversation: Conversation,
        result: dict[str, Any],
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        field_name = result["next_action"]["fields"][0]
        resolution = result["resolution"].get(field_name, {})
        options: list[OfferOption] = []
        if field_name == "patient_status":
            options = [
                OfferOption("patient_new", "a new patient", {"patch": {"patient_status": {"operation": "SET", "value": "NEW"}}}),
                OfferOption("patient_existing", "an existing patient", {"patch": {"patient_status": {"operation": "SET", "value": "EXISTING"}}}),
            ]
            message = "Are you a new patient or have you been seen at this clinic before?"
        elif field_name == "referral_status":
            options = [
                OfferOption("referral_on_file", "referral is on file", {"patch": {"referral_status": {"operation": "SET", "value": "ON_FILE"}}}),
                OfferOption("referral_not_on_file", "no referral is on file", {"patch": {"referral_status": {"operation": "SET", "value": "NOT_ON_FILE"}}}),
            ]
            message = "Is there a referral on file for this appointment?"
        elif resolution.get("status") == "AMBIGUOUS":
            for index, candidate in enumerate(resolution.get("candidates", [])[:3]):
                options.append(
                    OfferOption(
                        f"identity_{field_name}_{index + 1}",
                        candidate["name"],
                        {"patch": {field_name: {"operation": "REPLACE", "raw_text": candidate["name"], "requirement": "UNSPECIFIED"}}},
                    )
                )
            labels = ", or ".join(f"{index + 1}) {item.label}" for index, item in enumerate(options))
            message = f"I found more than one match: {labels}. Which one did you mean?"
        else:
            message = "What type of appointment would you like to schedule?"

        conversation.pending_offer = (
            PendingOffer(
                kind=OfferKind.FIELD_OPTIONS,
                request_fingerprint=conversation.patient_request.fingerprint(),
                catalog_version=self.catalog.version,
                options=options,
            )
            if options
            else None
        )
        self._trace(trace, "Decision", perf_counter(), "Ask one relevant question", field_name, "active")
        return self._base_result(conversation, message, patch, engine_result=result)

    def _answer_information(
        self,
        conversation: Conversation,
        trace: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        request = conversation.patient_request.apply_patch(patch)
        message = "What would you like to know about our providers, locations, or appointment types?"
        resolution: dict[str, Any] = {}
        if request.provider:
            found = self.catalog.resolve_provider(request.provider.raw_text)
            resolution["provider"] = found.to_dict()
            if found.status == "RESOLVED":
                provider = self.catalog.providers[found.selected["id"]]
                locations = [self.catalog.locations[item]["name"] for item in provider["location_ids"]]
                message = f"{provider['name']} practices at {', '.join(locations)}."
        elif request.location:
            found = self.catalog.resolve_location(request.location.raw_text)
            resolution["location"] = found.to_dict()
            if found.status == "RESOLVED":
                location = self.catalog.locations[found.selected["id"]]
                message = f"{location['name']} is at {location['address']} and is open {location['hours']}."
        elif request.appointment_type:
            found = self.catalog.resolve_appointment_type(request.appointment_type.raw_text)
            resolution["appointment_type"] = found.to_dict()
            if found.status == "RESOLVED":
                appointment = self.catalog.appointment_types[found.selected["id"]]
                referral = "requires a referral" if appointment["requires_referral"] else "does not require a referral"
                message = f"{appointment['name']} takes {appointment['duration_min']} minutes and {referral}."
        engine_result = {
            "catalog_version": self.catalog.version,
            "request_fingerprint": request.fingerprint(),
            "resolution": resolution,
            "decision": {"status": "INFORMATION_ANSWERED"},
            "blockers": [],
            "rule_results": [],
            "valid_candidates": [],
            "relaxation_candidates": [],
            "next_action": {"type": "ANSWER_INFORMATION"},
        }
        conversation.last_result = engine_result
        self._trace(trace, "Decision", perf_counter(), "Answer from catalog data", "No catalog list was sent to the LLM", "active")
        return self._base_result(conversation, message, patch, engine_result=engine_result)

    def _safe_extraction_failure(self, conversation: Conversation, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._trace(trace, "Decision", perf_counter(), "Ask for clarification", "Patient request was not changed", "active")
        return self._base_result(conversation, "Sorry, I didn’t understand that safely. Could you say it another way?", {})

    def _clarify_reference(self, conversation: Conversation, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._trace(trace, "Decision", perf_counter(), "Clarify the reference", "No option or booking was guessed", "active")
        return self._base_result(conversation, "I’m not sure what that refers to. Could you name the option or detail you mean?", {})

    def _clarify_mixed_answer(self, conversation: Conversation, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._trace(trace, "Decision", perf_counter(), "Clarify answer and change", "No pending offer was accepted", "active")
        return self._base_result(conversation, "I heard both an answer and a change. What would you like me to use?", {})

    def _repeat_pending_question(
        self, conversation: Conversation, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        offer = conversation.pending_offer
        self._trace(trace, "Decision", perf_counter(), "Clarify the pending offer", "No answer or change was guessed", "active")
        if offer and offer.kind == OfferKind.CONFIRM_BOOKING:
            message = "I haven’t booked it yet. Please say yes to confirm or no to cancel."
        elif offer and offer.kind == OfferKind.SLOT_OPTIONS and len(offer.options) == 1:
            message = "Does that earliest time work for you? Please say yes or no."
        elif offer and offer.kind in {OfferKind.SLOT_OPTIONS, OfferKind.FIELD_OPTIONS}:
            message = f"Which option would you like—{self._option_words(len(offer.options))}?"
        else:
            message = "Would that alternative work? Please say yes or no."
        return self._base_result(
            conversation,
            message,
            {},
            offered_slots=self._offered_slots(offer) if offer else [],
        )

    def _base_result(
        self,
        conversation: Conversation,
        message: str,
        patch: dict[str, Any],
        *,
        engine_result: dict[str, Any] | None = None,
        offered_slots: list[dict[str, Any]] | None = None,
        booking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "assistant_message": message,
            "state_patch": patch,
            "engine_result": engine_result or conversation.last_result or self._empty_engine_result(conversation),
            "offered_slots": offered_slots or [],
            "booking": booking if booking is not None else conversation.booking,
        }

    def _empty_engine_result(self, conversation: Conversation) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog.version,
            "request_fingerprint": conversation.patient_request.fingerprint(),
            "resolution": {},
            "decision": {"status": "AWAITING_CLARIFICATION"},
            "blockers": [],
            "rule_results": [],
            "valid_candidates": [],
            "relaxation_candidates": [],
            "next_action": {"type": "ASK_CLARIFICATION"},
        }

    @staticmethod
    def _safe_patch_summary(patch: dict[str, Any], pending_answer: str) -> dict[str, Any]:
        return {
            "fields": sorted(key for key in patch if key != "observed_intents"),
            "observed_intents": patch.get("observed_intents", []),
            "pending_answer": pending_answer,
        }

    @staticmethod
    def _trace(
        trace: list[dict[str, Any]],
        stage_name: str,
        started: float,
        title: str,
        detail: str,
        tone: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        trace.append({
            "stage": stage_name,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "title": title,
            "detail": detail,
            "tone": tone,
            "data": data or {},
        })

    @staticmethod
    def _resolution_detail(result: dict[str, Any]) -> str:
        names = [item["selected"]["name"] for item in result["resolution"].values() if item["status"] == "RESOLVED"]
        return " · ".join(names) if names else "No catalog identity resolved yet"

    @staticmethod
    def _rule_detail(rules: list[dict[str, Any]]) -> str:
        return " · ".join(item["reason"] for item in rules[:3]) if rules else "Eligibility and constraints are satisfied"

    def _response_for_engine(self, result: dict[str, Any]) -> str:
        if result["blockers"]:
            return result["blockers"][0].get("reason", "I couldn’t find a valid appointment combination.")
        return "I couldn’t find a valid appointment combination. A staff member can help with next steps."

    def _resolved_id(self, raw_text: str, field_name: str) -> str | None:
        resolver = getattr(self.catalog, f"resolve_{field_name}")
        result = resolver(raw_text)
        return result.selected["id"] if result.status == "RESOLVED" else None

    @staticmethod
    def _slot_from_dict(value: dict[str, Any]) -> Slot:
        timezone_name = value.get("timezone")
        start = datetime.fromisoformat(value["start"])
        end = datetime.fromisoformat(value["end"])
        if timezone_name:
            timezone = ZoneInfo(timezone_name)
            start = start.astimezone(timezone)
            end = end.astimezone(timezone)
        return Slot(
            id=value["slot_id"],
            candidate_id=value["candidate_id"],
            start=start,
            end=end,
        )

    @staticmethod
    def _offered_slots(offer: PendingOffer) -> list[dict[str, Any]]:
        return [option.value["slot"] for option in offer.options if "slot" in option.value]

    @staticmethod
    def _option_words(count: int) -> str:
        words = ["first", "second", "third"][:count]
        return ", ".join(words[:-1]) + (" or " if len(words) > 1 else "") + (words[-1] if words else "the named option")

    @staticmethod
    def _format_slot(slot: Slot) -> str:
        return slot.start.strftime("%A, %B %-d at %-I:%M %p %Z")
