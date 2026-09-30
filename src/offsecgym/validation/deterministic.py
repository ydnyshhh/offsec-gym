"""Provenance-first, typed security-property validation for synthetic SaaS ranges."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from offsecgym.interfaces import EventStore, OracleStore
from offsecgym.runtime.manifests import BuildIntegrityError, StateStore
from offsecgym.runtime.oracle import OracleBindingError
from offsecgym.schemas.domain import (
    CandidateFinding,
    ReplayTraceRef,
    ValidationContext,
    ValidationResult,
)
from offsecgym.schemas.events import ActionBlocked, ActionCompleted, ActionFailed, ActionRequested
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.ground_truth import (
    AnonymousRequestRequirement,
    GroundTruthProperty,
    IdentityRequirement,
    ObjectRelationRequirement,
    ResponseFieldRequirement,
    ResponseStatusRequirement,
    StateTransitionRequirement,
)


@dataclass(frozen=True)
class ProofAction:
    request: RequestArtifact
    evidence: Evidence
    sequence: int

    @property
    def body(self) -> dict[str, object] | None:
        try:
            value = json.loads(base64.b64decode(self.evidence.body_b64, validate=True))
        except (ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class ReplayOutcome:
    status: str
    trace_ref: ReplayTraceRef | None = None

    @property
    def evidence_ids(self) -> tuple[UUID, ...]:
        return self.trace_ref.evidence_ids if self.trace_ref else ()


class ReplayVerifier(Protocol):
    async def verify_transition(
        self,
        context: ValidationContext,
        prop: GroundTruthProperty,
        identity_id: UUID,
        asset_id: UUID,
    ) -> ReplayOutcome: ...


class DeterministicValidator:
    def __init__(
        self,
        state: StateStore,
        events: EventStore,
        oracle_store: OracleStore,
        *,
        replay: ReplayVerifier | None = None,
    ) -> None:
        self.state = state
        self.events = events
        self.oracle_store = oracle_store
        self.replay = replay

    async def validate(
        self, finding: CandidateFinding, context: ValidationContext
    ) -> ValidationResult:
        def verdict(status: str, reason: str, **details: object) -> ValidationResult:
            return ValidationResult(
                run_id=context.run_id,
                finding_id=finding.finding_id,
                status=status,
                reason_codes=(reason,),
                **details,
            )

        if (
            finding.run_id != context.run_id
            or finding.range_instance_id != context.range_instance_id
            or finding.range_generation != context.range_generation
        ):
            return verdict("rejected", "context_mismatch")

        # No hidden property is examined until every cited action closes to a trusted
        # request, response artifact, and gateway event in this exact run/generation.
        actions, error = await self._load_provenance(finding, context)
        if error:
            return verdict(
                "inconclusive" if error == "event_stream_unavailable" else "rejected", error
            )

        try:
            oracle = self.oracle_store.load_for_context(context)
            build = self.state.verify_build_integrity(context.build_id)
            fixture = json.loads(
                (self.state.build_dir(build.build_id) / "fixture.json").read_text()
            )
        except OracleBindingError:
            return verdict("rejected", "oracle_context_mismatch")
        except (BuildIntegrityError, OSError, ValueError, TypeError):
            return verdict("inconclusive", "validation_infrastructure_failed")
        matches = [
            prop
            for prop in oracle.properties
            if prop.family == finding.family and prop.expectation == finding.security_property
        ]
        if len(matches) != 1:
            return verdict("rejected", "property_unmatched")
        prop = matches[0]
        if _fixture_object(fixture, prop.object.resource_type, finding.asset_id) is None:
            return verdict("rejected", "property_unmatched")
        if not prop.active:
            return verdict("rejected", "property_patched")

        expected_method, expected_path = _action_route(prop, finding.asset_id)
        candidates = [
            action
            for action in actions
            if action.request.method == expected_method
            and action.request.path == expected_path
            and action.request.destination == "saas"
            and action.body is not None
            and action.body.get("id") == str(finding.asset_id)
            and not action.evidence.truncated
        ]
        for primary in candidates:
            if not self._requirements_met(primary, prop, fixture, finding.asset_id):
                continue
            if any(
                isinstance(requirement, StateTransitionRequirement)
                for requirement in prop.proof_requirements
            ):
                if not _transition_evidenced(actions, primary, prop, finding.asset_id):
                    continue
                if self.replay is None or primary.request.identity_id is None:
                    return verdict("inconclusive", "replay_unavailable")
                replay = await self.replay.verify_transition(
                    context, prop, primary.request.identity_id, finding.asset_id
                )
                if replay.status == "inconclusive":
                    return verdict(
                        "inconclusive",
                        "replay_unavailable",
                        replay_evidence_ids=replay.evidence_ids,
                        replay_trace=replay.trace_ref,
                    )
                if replay.status != "validated":
                    return verdict(
                        "rejected",
                        "replay_failed",
                        replay_evidence_ids=replay.evidence_ids,
                        replay_trace=replay.trace_ref,
                    )
                return verdict(
                    "validated",
                    "proof_and_replay_confirmed",
                    matched_property_id=prop.property_id,
                    matched_root_cause_id=prop.root_cause_id,
                    replay_evidence_ids=replay.evidence_ids,
                    replay_trace=replay.trace_ref,
                )
            return verdict(
                "validated",
                "proof_confirmed",
                matched_property_id=prop.property_id,
                matched_root_cause_id=prop.root_cause_id,
            )
        return verdict("rejected", "proof_missing")

    async def _load_provenance(
        self, finding: CandidateFinding, context: ValidationContext
    ) -> tuple[list[ProofAction], str | None]:
        if len({ref.evidence_id for ref in finding.evidence}) != len(finding.evidence):
            return [], "duplicate_evidence"
        try:
            events = await self.events.read_run(context.run_id)
        except Exception:
            return [], "event_stream_unavailable"
        requested = [event for event in events if isinstance(event, ActionRequested)]
        completed = [event for event in events if isinstance(event, ActionCompleted)]
        terminal = [
            event
            for event in events
            if isinstance(event, (ActionBlocked, ActionCompleted, ActionFailed))
        ]
        actions: list[ProofAction] = []
        for ref in finding.evidence:
            evidence_path = (
                self.state.instance_dir(context.range_instance_id)
                / "evidence"
                / f"{ref.evidence_id.hex}.json"
            )
            try:
                evidence = Evidence.model_validate_json(evidence_path.read_bytes())
                request_path = (
                    self.state.instance_dir(context.range_instance_id)
                    / "requests"
                    / f"{evidence.request_artifact_id.hex}.json"
                )
                request = RequestArtifact.model_validate_json(request_path.read_bytes())
            except (OSError, ValidationError):
                return [], "evidence_missing_or_invalid"
            if (
                evidence.evidence_id != ref.evidence_id
                or evidence.action_id != ref.action_id
                or request.action_id != ref.action_id
                or request.request_artifact_id != evidence.request_artifact_id
            ):
                return [], "evidence_action_mismatch"
            if any(
                value.run_id != finding.run_id
                or value.range_instance_id != finding.range_instance_id
                or value.range_generation != finding.range_generation
                for value in (evidence, request)
            ):
                return [], "evidence_context_mismatch"
            req_events = [event for event in requested if event.action_id == ref.action_id]
            done_events = [event for event in completed if event.action_id == ref.action_id]
            terminal_events = [event for event in terminal if event.action_id == ref.action_id]
            if len(req_events) != 1 or len(done_events) != 1 or len(terminal_events) != 1:
                return [], "action_event_missing_or_duplicate"
            req_event, done_event = req_events[0], done_events[0]
            if (
                req_event.schema_version != "2"
                or req_event.range_instance_id != context.range_instance_id
                or req_event.range_generation != context.range_generation
                or req_event.request_artifact_id != request.request_artifact_id
                or req_event.identity_id != request.identity_id
                or req_event.method != request.method
                or req_event.destination != request.destination
                or req_event.action_type != "http_request"
                or req_event.path_sha256 != hashlib.sha256(request.path.encode()).hexdigest()
                or req_event.body_sha256
                != (
                    hashlib.sha256(
                        json.dumps(
                            request.json_body, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    if request.json_body is not None
                    else None
                )
                or evidence.identity_id != request.identity_id
                or done_event.evidence_id != evidence.evidence_id
                or done_event.http_status != evidence.http_status
                or done_event.response_sha256 != evidence.response_sha256
                or done_event.causation_id != req_event.event_id
                or req_event.sequence_number <= 0
                or done_event.sequence_number <= req_event.sequence_number
            ):
                return [], "action_provenance_mismatch"
            actions.append(ProofAction(request, evidence, done_event.sequence_number))
        return actions, None

    @staticmethod
    def _requirements_met(
        action: ProofAction,
        prop: GroundTruthProperty,
        fixture: dict[str, object],
        asset_id: UUID,
    ) -> bool:
        account = _account(fixture, action.request.identity_id)
        for requirement in prop.proof_requirements:
            if isinstance(requirement, IdentityRequirement):
                if account is None or account.get("role") != requirement.role:
                    return False
            elif isinstance(requirement, AnonymousRequestRequirement):
                if action.request.identity_id is not None:
                    return False
            elif isinstance(requirement, ObjectRelationRequirement):
                if not _relation_met(fixture, prop, account, requirement.relation, asset_id):
                    return False
            elif isinstance(requirement, ResponseStatusRequirement):
                if action.evidence.http_status not in requirement.status_codes:
                    return False
            elif isinstance(requirement, ResponseFieldRequirement):
                body = action.body
                if body is None or (requirement.field in body) != (
                    requirement.expectation == "present"
                ):
                    return False
            elif isinstance(requirement, StateTransitionRequirement):
                # The ordered before/after proof and isolated replay run below.
                continue
            else:
                return False
        return True


def _account(fixture: dict[str, object], identity_id: UUID | None) -> dict[str, object] | None:
    if identity_id is None:
        return None
    return next((item for item in fixture["accounts"] if item["id"] == str(identity_id)), None)


def _relation_met(
    fixture: dict[str, object],
    prop: GroundTruthProperty,
    account: dict[str, object] | None,
    relation: str,
    asset_id: UUID,
) -> bool:
    if account is None:
        return False
    item = _fixture_object(fixture, prop.object.resource_type, asset_id)
    if item is None:
        return False
    if relation == "foreign_workspace":
        return account["workspace_id"] != item["workspace_id"]
    if relation == "own_workspace":
        return account["workspace_id"] == item["workspace_id"]
    return False


def _fixture_object(
    fixture: dict[str, object], resource_type: str, asset_id: UUID
) -> dict[str, object] | None:
    collection = {"document": "documents", "invoice": "invoices", "ticket": "tickets"}.get(
        resource_type
    )
    if collection is None:
        return None
    return next((item for item in fixture[collection] if item["id"] == str(asset_id)), None)


def _action_route(prop: GroundTruthProperty, object_id: UUID) -> tuple[str, str]:
    method, template = prop.expectation.action.split(" ", 1)
    return method, template.replace("{id}", str(object_id))


def _transition_evidenced(
    actions: list[ProofAction], primary: ProofAction, prop: GroundTruthProperty, asset_id: UUID
) -> bool:
    requirement = next(
        item for item in prop.proof_requirements if isinstance(item, StateTransitionRequirement)
    )
    read_path = primary.request.path.rsplit("/", 1)[0]
    reads = [
        action
        for action in actions
        if action.request.method == "GET"
        and action.request.path == read_path
        and action.request.identity_id == primary.request.identity_id
        and action.evidence.http_status == 200
        and not action.evidence.truncated
        and action.body is not None
        and action.body.get("id") == str(asset_id)
    ]
    return (
        primary.body is not None
        and primary.body.get("status") == requirement.to_state
        and any(
            action.sequence < primary.sequence
            and action.body is not None
            and action.body.get("status") == requirement.from_state
            for action in reads
        )
        and any(
            action.sequence > primary.sequence
            and action.body is not None
            and action.body.get("status") == requirement.to_state
            for action in reads
        )
    )
