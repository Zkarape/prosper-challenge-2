"""Deterministic catalog resolution, rule evaluation, and candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .catalog import Catalog, Resolution
from .state import PatientStatus, ReferralStatus, Requirement, SchedulingRequest


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    status: RuleStatus
    reason: str
    field: str | None = None
    candidate_id: str | None = None
    recoverable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "status": self.status.value,
            "field": self.field,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class Candidate:
    appointment_type_id: str
    provider_id: str
    location_id: str
    provider_name: str
    location_name: str
    preference_breakdown: dict[str, Any]

    @property
    def id(self) -> str:
        return f"{self.appointment_type_id}:{self.provider_id}:{self.location_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.id,
            "appointment_type_id": self.appointment_type_id,
            "provider_id": self.provider_id,
            "location_id": self.location_id,
            "provider_name": self.provider_name,
            "location_name": self.location_name,
            "timezone": self.preference_breakdown["timezone"],
            "preference_breakdown": self.preference_breakdown,
        }


class SchedulingEngine:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def evaluate(self, patient_request: SchedulingRequest) -> dict[str, Any]:
        resolutions = self._resolve(patient_request)
        identity_problem = self._identity_problem(resolutions)
        if identity_problem:
            blocker = identity_problem[0]
            action = "ASK_CLARIFICATION" if blocker["kind"] == "AMBIGUOUS" else "ASK_REQUIRED_FIELD"
            return self._result(
                patient_request,
                resolutions,
                status="NEEDS_INFORMATION",
                blockers=[blocker],
                rule_results=[],
                next_action={"type": action, "fields": [blocker["field"]]},
            )

        appointment_id = resolutions["appointment_type"].selected["id"]
        appointment = self.catalog.appointment_types[appointment_id]

        decisive_failure = self._decisive_patient_failure(patient_request, appointment)
        if decisive_failure:
            return self._result(
                patient_request,
                resolutions,
                status="BLOCKED",
                blockers=[self._blocker(decisive_failure)],
                rule_results=[decisive_failure],
                next_action={"type": "CANNOT_SCHEDULE"},
            )

        relevant_unknown = self._next_relevant_unknown(patient_request, appointment_id)
        if relevant_unknown:
            rule = RuleResult(
                rule=f"{relevant_unknown.upper()}_KNOWN",
                status=RuleStatus.UNKNOWN,
                field=relevant_unknown,
                reason=self._unknown_reason(relevant_unknown),
                recoverable=True,
            )
            return self._result(
                patient_request,
                resolutions,
                status="NEEDS_INFORMATION",
                blockers=[{"field": relevant_unknown, "kind": "UNKNOWN"}],
                rule_results=[rule],
                next_action={"type": "ASK_REQUIRED_FIELD", "fields": [relevant_unknown]},
            )

        referral_failure = self._referral_failure(patient_request, appointment)
        if referral_failure:
            return self._result(
                patient_request,
                resolutions,
                status="BLOCKED",
                blockers=[self._blocker(referral_failure)],
                rule_results=[referral_failure],
                next_action={"type": "CANNOT_SCHEDULE"},
            )

        requested_rules = self._requested_combination_rules(
            patient_request, resolutions, appointment_id
        )
        evaluated = self._construct_and_evaluate_candidates(
            patient_request, resolutions, appointment_id
        )
        valid = [item for item, rules in evaluated if not self._has_failure(rules)]
        all_candidate_rules = [rule for _, rules in evaluated for rule in rules]

        exact_valid = [
            item
            for item in valid
            if self._matches_named_request(item, patient_request, resolutions)
        ]
        named_option_unsatisfied = (
            (patient_request.provider is not None or patient_request.location is not None)
            and not exact_valid
        )
        requires_permission = self._requires_permission_for_alternatives(
            patient_request, valid, resolutions, named_option_unsatisfied
        )

        if valid and not requires_permission:
            status = "READY_FOR_AVAILABILITY"
            next_action = {"type": "QUERY_AVAILABILITY"}
            relaxation_candidates: list[dict[str, Any]] = []
        elif valid:
            status = "NO_EXACT_MATCH"
            relaxation_candidates = [
                {
                    **candidate.to_dict(),
                    "requires_relaxing": self._changed_named_fields(
                        candidate, patient_request, resolutions
                    ),
                    "requires_patient_permission": True,
                }
                for candidate in valid[:5]
            ]
            next_action = {
                "type": "OFFER_ALTERNATIVES",
                "candidate_ids": [item["candidate_id"] for item in relaxation_candidates],
                "requires_patient_permission": True,
            }
        else:
            relaxed = self._minimal_change_candidates(
                self._relaxable_candidates(evaluated),
                patient_request,
                resolutions,
            )
            if relaxed:
                status = "NO_EXACT_MATCH"
                relaxation_candidates = [
                    {
                        **candidate.to_dict(),
                        "requires_relaxing": self._changed_named_fields(
                            candidate, patient_request, resolutions
                        ),
                        "requires_patient_permission": True,
                    }
                    for candidate in relaxed[:5]
                ]
                next_action = {
                    "type": "OFFER_ALTERNATIVES",
                    "candidate_ids": [item["candidate_id"] for item in relaxation_candidates],
                    "requires_patient_permission": True,
                }
            else:
                status = "NO_MATCH"
                relaxation_candidates = []
                next_action = {"type": "CANNOT_SCHEDULE"}

        failed_requested = [rule for rule in requested_rules if rule.status == RuleStatus.FAIL]
        failed_candidates = [rule for rule in all_candidate_rules if rule.status == RuleStatus.FAIL]
        blockers = [self._blocker(rule) for rule in failed_requested]
        if not blockers and not valid:
            blockers = [self._blocker(rule) for rule in failed_candidates[:10]]

        return self._result(
            patient_request,
            resolutions,
            status=status,
            blockers=blockers,
            rule_results=requested_rules + all_candidate_rules,
            valid_candidates=[item.to_dict() for item in valid[:10]],
            relaxation_candidates=relaxation_candidates,
            next_action=next_action,
        )

    def _resolve(self, request: SchedulingRequest) -> dict[str, Resolution]:
        appointment = (
            self.catalog.resolve_appointment_type(request.appointment_type.raw_text)
            if request.appointment_type
            else Resolution("NOT_REQUESTED", "", None, [])
        )
        appointment_id = appointment.selected["id"] if appointment.status == "RESOLVED" else None
        provider = (
            self.catalog.resolve_provider(request.provider.raw_text, appointment_id)
            if request.provider
            else Resolution("NOT_REQUESTED", "", None, [])
        )
        location = (
            self.catalog.resolve_location(request.location.raw_text)
            if request.location
            else Resolution("NOT_REQUESTED", "", None, [])
        )
        return {"appointment_type": appointment, "provider": provider, "location": location}

    @staticmethod
    def _identity_problem(resolutions: dict[str, Resolution]) -> list[dict[str, Any]]:
        appointment = resolutions["appointment_type"]
        if appointment.status == "NOT_REQUESTED":
            return [{"field": "appointment_type", "kind": "MISSING"}]
        if appointment.status != "RESOLVED":
            return [{"field": "appointment_type", "kind": appointment.status, "candidates": appointment.candidates}]
        for field_name in ("provider", "location"):
            resolution = resolutions[field_name]
            if resolution.status not in {"NOT_REQUESTED", "RESOLVED"}:
                return [{"field": field_name, "kind": resolution.status, "candidates": resolution.candidates}]
        return []

    def _next_relevant_unknown(
        self, request: SchedulingRequest, appointment_id: str
    ) -> str | None:
        appointment = self.catalog.appointment_types[appointment_id]
        providers = [
            item
            for item in self.catalog.providers.values()
            if appointment_id in item["appointment_type_ids"]
        ]
        if request.patient_status in {PatientStatus.UNKNOWN, PatientStatus.CONFLICTING}:
            outcomes = {
                self._patient_outcome(status, appointment, providers)
                for status in (PatientStatus.NEW, PatientStatus.EXISTING)
            }
            if len(outcomes) > 1:
                return "patient_status"
        if (
            appointment["requires_referral"]
            and request.referral_status in {ReferralStatus.UNKNOWN, ReferralStatus.CONFLICTING}
        ):
            return "referral_status"
        return None

    @staticmethod
    def _patient_outcome(
        status: PatientStatus,
        appointment: dict[str, Any],
        providers: list[dict[str, Any]],
    ) -> tuple[bool, tuple[str, ...]]:
        if status == PatientStatus.NEW and not appointment["new_patients_allowed"]:
            return False, ()
        eligible = tuple(
            sorted(
                item["id"]
                for item in providers
                if status != PatientStatus.NEW or item["accepting_new_patients"]
            )
        )
        return bool(eligible), eligible

    @staticmethod
    def _decisive_patient_failure(
        request: SchedulingRequest, appointment: dict[str, Any]
    ) -> RuleResult | None:
        if request.patient_status == PatientStatus.NEW and not appointment["new_patients_allowed"]:
            return RuleResult(
                rule="APPOINTMENT_ALLOWS_NEW_PATIENTS",
                status=RuleStatus.FAIL,
                field="patient_status",
                reason="This appointment type is available only to existing patients.",
                recoverable=False,
            )
        return None

    @staticmethod
    def _referral_failure(
        request: SchedulingRequest, appointment: dict[str, Any]
    ) -> RuleResult | None:
        if appointment["requires_referral"] and request.referral_status != ReferralStatus.ON_FILE:
            return RuleResult(
                rule="REFERRAL_REQUIRED",
                status=RuleStatus.FAIL,
                field="referral_status",
                reason="This appointment requires a referral on file.",
                recoverable=False,
            )
        return None

    def _requested_combination_rules(
        self,
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
        appointment_id: str,
    ) -> list[RuleResult]:
        rules: list[RuleResult] = []
        provider = (
            self.catalog.providers[resolutions["provider"].selected["id"]]
            if resolutions["provider"].status == "RESOLVED"
            else None
        )
        location = (
            self.catalog.locations[resolutions["location"].selected["id"]]
            if resolutions["location"].status == "RESOLVED"
            else None
        )
        appointment = self.catalog.appointment_types[appointment_id]
        if provider:
            passes = appointment_id in provider["appointment_type_ids"]
            rules.append(
                RuleResult(
                    rule="PROVIDER_OFFERS_APPOINTMENT",
                    status=RuleStatus.PASS if passes else RuleStatus.FAIL,
                    field="provider",
                    reason=(
                        f"{provider['name']} offers {appointment['name']}."
                        if passes
                        else f"{provider['name']} does not offer {appointment['name']}."
                    ),
                    recoverable=not passes,
                )
            )
        if provider and location:
            passes = location["id"] in provider["location_ids"]
            rules.append(
                RuleResult(
                    rule="PROVIDER_PRACTICES_AT_LOCATION",
                    status=RuleStatus.PASS if passes else RuleStatus.FAIL,
                    field="location",
                    reason=(
                        f"{provider['name']} practices at {location['name']}."
                        if passes
                        else f"{provider['name']} does not practice at {location['name']}."
                    ),
                    recoverable=not passes,
                )
            )
        if location:
            capability = appointment.get("required_capability")
            passes = not capability or capability in location["capabilities"]
            rules.append(
                RuleResult(
                    rule="LOCATION_HAS_REQUIRED_CAPABILITY",
                    status=(
                        RuleStatus.NOT_APPLICABLE
                        if not capability
                        else RuleStatus.PASS if passes else RuleStatus.FAIL
                    ),
                    field="location",
                    reason=(
                        "This appointment has no location capability requirement."
                        if not capability
                        else f"{location['name']} has the required {capability} capability."
                        if passes
                        else f"{location['name']} does not have the required {capability} capability."
                    ),
                    recoverable=not passes,
                )
            )
        return rules

    def _construct_and_evaluate_candidates(
        self,
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
        appointment_id: str,
    ) -> list[tuple[Candidate, list[RuleResult]]]:
        requested_provider = resolutions["provider"].selected["id"] if resolutions["provider"].status == "RESOLVED" else None
        requested_location = resolutions["location"].selected["id"] if resolutions["location"].status == "RESOLVED" else None
        appointment = self.catalog.appointment_types[appointment_id]
        results: list[tuple[Candidate, list[RuleResult]]] = []
        for provider in self.catalog.providers.values():
            if appointment_id not in provider["appointment_type_ids"]:
                continue
            for location_id in provider["location_ids"]:
                location = self.catalog.locations[location_id]
                preference = {
                    "provider_match": not requested_provider or provider["id"] == requested_provider,
                    "location_match": not requested_location or location_id == requested_location,
                    "primary_priority": request.primary_priority.value,
                    "timezone": self.catalog.timezone_for_location(location_id),
                }
                candidate = Candidate(
                    appointment_id,
                    provider["id"],
                    location_id,
                    provider["name"],
                    location["name"],
                    preference,
                )
                rules = self._candidate_rules(
                    request, candidate, appointment, provider, location, requested_provider, requested_location
                )
                results.append((candidate, rules))
        return sorted(results, key=lambda pair: self._candidate_rank(pair[0], pair[1], request))

    def _candidate_rules(
        self,
        request: SchedulingRequest,
        candidate: Candidate,
        appointment: dict[str, Any],
        provider: dict[str, Any],
        location: dict[str, Any],
        requested_provider: str | None,
        requested_location: str | None,
    ) -> list[RuleResult]:
        rules: list[RuleResult] = []
        accepts = request.patient_status != PatientStatus.NEW or provider["accepting_new_patients"]
        rules.append(RuleResult(
            "PROVIDER_ACCEPTS_NEW_PATIENTS",
            RuleStatus.PASS if accepts else RuleStatus.FAIL,
            "Provider is eligible for this patient status." if accepts else f"{provider['name']} is not accepting new patients.",
            "patient_status", candidate.id, False,
        ))
        capability = appointment.get("required_capability")
        has_capability = not capability or capability in location["capabilities"]
        rules.append(RuleResult(
            "LOCATION_HAS_REQUIRED_CAPABILITY",
            RuleStatus.NOT_APPLICABLE if not capability else RuleStatus.PASS if has_capability else RuleStatus.FAIL,
            "No capability requirement." if not capability else "Location capability satisfied." if has_capability else f"{location['name']} lacks {capability}.",
            "location", candidate.id, not has_capability,
        ))
        provider_required = bool(request.provider and request.provider.requirement == Requirement.REQUIRED)
        provider_ok = not provider_required or provider["id"] == requested_provider
        rules.append(RuleResult(
            "REQUIRED_PROVIDER_SATISFIED",
            RuleStatus.NOT_APPLICABLE if not provider_required else RuleStatus.PASS if provider_ok else RuleStatus.FAIL,
            "No required provider." if not provider_required else "Required provider satisfied." if provider_ok else "Candidate changes the required provider.",
            "provider", candidate.id, not provider_ok,
        ))
        location_required = bool(request.location and request.location.requirement == Requirement.REQUIRED)
        location_ok = not location_required or location["id"] == requested_location
        rules.append(RuleResult(
            "REQUIRED_LOCATION_SATISFIED",
            RuleStatus.NOT_APPLICABLE if not location_required else RuleStatus.PASS if location_ok else RuleStatus.FAIL,
            "No required location." if not location_required else "Required location satisfied." if location_ok else "Candidate changes the required location.",
            "location", candidate.id, not location_ok,
        ))
        return rules

    @staticmethod
    def _candidate_rank(
        candidate: Candidate, rules: list[RuleResult], request: SchedulingRequest
    ) -> tuple[Any, ...]:
        failures = sum(rule.status == RuleStatus.FAIL for rule in rules)
        provider_miss = not candidate.preference_breakdown["provider_match"]
        location_miss = not candidate.preference_breakdown["location_match"]
        if request.primary_priority.value == "LOCATION":
            preference = (location_miss, provider_miss)
        else:
            preference = (provider_miss, location_miss)
        return (failures, *preference, candidate.provider_name, candidate.location_name, candidate.id)

    @staticmethod
    def _has_failure(rules: list[RuleResult]) -> bool:
        return any(rule.status == RuleStatus.FAIL for rule in rules)

    @staticmethod
    def _matches_named_request(
        candidate: Candidate,
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
    ) -> bool:
        if request.provider and candidate.provider_id != resolutions["provider"].selected["id"]:
            return False
        if request.location and candidate.location_id != resolutions["location"].selected["id"]:
            return False
        return True

    @staticmethod
    def _requires_permission_for_alternatives(
        request: SchedulingRequest,
        valid: list[Candidate],
        resolutions: dict[str, Resolution],
        named_option_unsatisfied: bool,
    ) -> bool:
        if not named_option_unsatisfied:
            return False
        if request.provider:
            requested = resolutions["provider"].selected["id"]
            provider_is_unsatisfied = not any(item.provider_id == requested for item in valid)
            if provider_is_unsatisfied and request.provider.requirement in {
                Requirement.REQUIRED,
                Requirement.UNSPECIFIED,
            }:
                return True
        if request.location:
            requested = resolutions["location"].selected["id"]
            location_is_unsatisfied = not any(item.location_id == requested for item in valid)
            if location_is_unsatisfied and request.location.requirement in {
                Requirement.REQUIRED,
                Requirement.UNSPECIFIED,
            }:
                return True
        return False

    @staticmethod
    def _relaxable_candidates(
        evaluated: list[tuple[Candidate, list[RuleResult]]]
    ) -> list[Candidate]:
        relaxable_rules = {"REQUIRED_PROVIDER_SATISFIED", "REQUIRED_LOCATION_SATISFIED"}
        output = []
        for candidate, rules in evaluated:
            failures = {rule.rule for rule in rules if rule.status == RuleStatus.FAIL}
            if failures and failures <= relaxable_rules:
                output.append(candidate)
        return output

    @classmethod
    def _minimal_change_candidates(
        cls,
        candidates: list[Candidate],
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
    ) -> list[Candidate]:
        """Keep alternatives that change the fewest named patient choices."""

        if not candidates:
            return []
        candidates_with_changes = [
            (candidate, cls._changed_named_fields(candidate, request, resolutions))
            for candidate in candidates
        ]
        minimum_change_count = min(
            len(changes) for _, changes in candidates_with_changes
        )
        return [
            candidate
            for candidate, changes in candidates_with_changes
            if len(changes) == minimum_change_count
        ]

    @staticmethod
    def _changed_named_fields(
        candidate: Candidate,
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
    ) -> list[dict[str, Any]]:
        changes = []
        if request.provider and candidate.provider_id != resolutions["provider"].selected["id"]:
            changes.append({"field": "provider", "from": resolutions["provider"].selected["id"], "to": candidate.provider_id})
        if request.location and candidate.location_id != resolutions["location"].selected["id"]:
            changes.append({"field": "location", "from": resolutions["location"].selected["id"], "to": candidate.location_id})
        return changes

    @staticmethod
    def _unknown_reason(field: str) -> str:
        if field == "patient_status":
            return "New and existing patients can have different eligible appointments."
        return "Referral status changes whether this appointment can proceed."

    @staticmethod
    def _blocker(rule: RuleResult) -> dict[str, Any]:
        blocker_codes = {
            "LOCATION_HAS_REQUIRED_CAPABILITY": "LOCATION_MISSING_CAPABILITY",
        }
        return {
            "code": blocker_codes.get(rule.rule, rule.rule),
            "field": rule.field,
            "reason": rule.reason,
            "recoverable": rule.recoverable,
        }

    def _result(
        self,
        request: SchedulingRequest,
        resolutions: dict[str, Resolution],
        *,
        status: str,
        blockers: list[dict[str, Any]],
        rule_results: list[RuleResult],
        next_action: dict[str, Any],
        valid_candidates: list[dict[str, Any]] | None = None,
        relaxation_candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog.version,
            "request_fingerprint": request.fingerprint(),
            "resolution": {key: value.to_dict() for key, value in resolutions.items()},
            "decision": {"status": status},
            "blockers": blockers,
            "rule_results": [item.to_dict() for item in rule_results],
            "valid_candidates": valid_candidates or [],
            "relaxation_candidates": relaxation_candidates or [],
            "next_action": next_action,
        }
