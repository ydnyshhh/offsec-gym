"""Deterministic sequential task decomposition over the shared M6 controller."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from offsecgym.interfaces import EventStore, ToolRegistry
from offsecgym.providers.base import ModelProvider
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import AgentContext, AgentResult, AgentTask, CoverageClaim, EntityRef
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    ActionReservationAcquired,
    CoverageUpdated,
    FindingSubmitted,
    WorkerBlocked,
    WorkerContractViolated,
    WorkerDebriefed,
    WorkerObjectiveAction,
    WorkerOriented,
    WorkerPacketPrepared,
    WorldFactSubmitted,
)
from offsecgym.schemas.evidence import RequestArtifact
from offsecgym.schemas.specs import Budget, ModelSpec
from offsecgym.schemas.workers import (
    CheckedAction,
    WorkerDebrief,
    WorkerEntity,
    WorkerEvidence,
    WorkerTaskContract,
    WorkerTaskPacket,
)
from offsecgym.solver.monolithic import MonolithicSaasAgent
from offsecgym.solver.scripted import (
    AgentBudgetExhausted,
    ExperimentInfrastructureError,
    FindingSink,
)
from offsecgym.storage.controller import PostgresControllerState, cost_microusd
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.worldview import EventWorldState

WORKER_OBJECTIVES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("identity", "Map identities, roles, and workspace membership", ("identity", "workspace")),
    ("document", "Test document object authorization", ("document", "workspace", "identity")),
    ("invoice", "Test invoice object authorization", ("invoice", "workspace", "identity")),
    ("ticket", "Test support-ticket object authorization", ("ticket", "workspace", "identity")),
    ("public", "Test anonymous public invoice exposure", ("invoice", "identity")),
    ("refund", "Test invoice refund transition authorization", ("invoice", "identity")),
)
OBJECTIVE_ROUTES = {
    "identity": "GET /api/me",
    "document": "GET /api/documents/{id}",
    "invoice": "GET /api/invoices/{id}",
    "ticket": "GET /api/support/tickets/{id}",
    "public": "GET /api/public/invoices/{id}/preview",
    "refund": "POST /api/invoices/{id}/refund",
}
MIN_MEANINGFUL_INPUT_TOKENS = 15500
WORKER_OUTPUT_CAP_TOKENS = 4000


def _path_relevance(kind: str, path: str) -> int:
    if kind == "identity":
        return int(path == "/api/me" or "/api/workspaces/" in path)
    if kind == "document":
        return int("/documents" in path)
    if kind == "invoice":
        return int("/invoices" in path and "/public/" not in path and "/refund" not in path)
    if kind == "ticket":
        return int("/tickets" in path)
    if kind == "public":
        return int("/public/invoices/" in path)
    return int("/refund" in path)


@dataclass(frozen=True)
class FutureWorkerFloor:
    tokens: int = 0
    model_calls: int = 0
    cost_microusd: int = 0
    actions: int = 0
    http_requests: int = 0


def future_worker_floor(
    global_budget: Budget, index: int, model: ModelSpec | None = None
) -> FutureWorkerFloor:
    count = len(WORKER_OBJECTIVES)
    if not 0 <= index < count:
        raise ValueError("worker index is outside the fixed decomposition")
    future = count - index - 1
    output = min(
        global_budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
        WORKER_OUTPUT_CAP_TOKENS,
    )
    turn_tokens = MIN_MEANINGFUL_INPUT_TOKENS + output
    turn_cost = 0
    if model is not None and model.input_usd_per_million_tokens is not None:
        assert model.output_usd_per_million_tokens is not None
        turn_cost = cost_microusd(
            (
                MIN_MEANINGFUL_INPUT_TOKENS * model.input_usd_per_million_tokens
                + output * model.output_usd_per_million_tokens
            )
            / 1_000_000
        )
    return FutureWorkerFloor(
        tokens=future * turn_tokens if global_budget.max_total_tokens is not None else 0,
        model_calls=future,
        cost_microusd=future * turn_cost if global_budget.max_cost_usd is not None else 0,
        actions=future if global_budget.max_actions is not None else 0,
        http_requests=future if global_budget.max_http_requests is not None else 0,
    )


def _spendable(limit: int | None, used: int, floor: int) -> int | None:
    if limit is None:
        return None
    available = limit - used - floor
    if available <= 0:
        raise AgentBudgetExhausted("future_worker_floor_unfunded")
    return available


def worker_budget(
    global_budget: Budget,
    index: int,
    usage: dict[str, object] | None = None,
    model: ModelSpec | None = None,
) -> Budget:
    usage = usage or {}
    floor = future_worker_floor(global_budget, index, model)
    cost_limit = cost_microusd(global_budget.max_cost_usd)
    cost_available = (
        _spendable(cost_limit, int(usage.get("used_cost_microusd", 0)), floor.cost_microusd)
        if global_budget.max_cost_usd is not None
        else None
    )
    return Budget(
        max_total_tokens=_spendable(
            global_budget.max_total_tokens, int(usage.get("used_tokens", 0)), floor.tokens
        ),
        max_output_tokens_per_call=min(
            global_budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
            WORKER_OUTPUT_CAP_TOKENS,
        ),
        max_model_calls=_spendable(
            global_budget.max_model_calls,
            int(usage.get("used_model_calls", 0)),
            floor.model_calls,
        ),
        max_actions=_spendable(
            global_budget.max_actions, int(usage.get("used_actions", 0)), floor.actions
        ),
        max_http_requests=_spendable(
            global_budget.max_http_requests,
            int(usage.get("used_http_requests", 0)),
            floor.http_requests,
        ),
        max_cost_usd=cost_available / 1_000_000 if cost_available is not None else None,
    )


class WorkerPacketBuilder:
    def __init__(self, events: EventStore, state_root: Path) -> None:
        self.events = events
        self.world = EventWorldState(events)
        self.state_root = state_root

    def _request(self, context: AgentContext, event: ActionRequested) -> RequestArtifact:
        if context.range is None or event.request_artifact_id is None:
            raise ExperimentInfrastructureError("worker handoff request provenance is missing")
        path = (
            self.state_root
            / "instances"
            / context.range.range_instance_id.hex
            / "requests"
            / f"{event.request_artifact_id.hex}.json"
        )
        try:
            request = RequestArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ExperimentInfrastructureError(
                "worker handoff request artifact unavailable"
            ) from exc
        if (
            request.run_id != context.run_id
            or request.action_id != event.action_id
            or request.request_artifact_id != event.request_artifact_id
            or request.range_instance_id != context.range.range_instance_id
            or request.range_generation != context.range.range_generation
            or request.method != event.method
            or request.identity_id != event.identity_id
            or request.destination != event.destination
        ):
            raise ExperimentInfrastructureError("worker handoff request artifact mismatch")
        if (
            "*" not in request.path
            and event.path_sha256 != hashlib.sha256(request.path.encode("utf-8")).hexdigest()
        ):
            raise ExperimentInfrastructureError("worker handoff request path hash mismatch")
        return request

    async def build(
        self,
        context: AgentContext,
        task_id: UUID,
        worker_id: UUID,
        objective: str,
        relevant_types: tuple[str, ...],
        budget: Budget,
        floor: FutureWorkerFloor | None = None,
    ) -> WorkerTaskPacket:
        floor = floor or FutureWorkerFloor()
        objective_kind = next(
            (kind for kind, text, _ in WORKER_OBJECTIVES if text == objective), "identity"
        )
        trace = await self.events.read_run(context.run_id)
        facts = await self.world.query(context.run_id)
        coverage = await self.world.coverage(context.run_id)
        grouped: dict[tuple[str, UUID], list[str]] = defaultdict(list)
        evidence: list[WorkerEvidence] = []
        seen_evidence: set[tuple[UUID, UUID]] = set()
        recency: dict[UUID, int] = {}
        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        identity_workspace: dict[UUID, UUID] = {}
        identity_role: dict[UUID, str] = {}
        completed = {item.action_id: item for item in trace if isinstance(item, ActionCompleted)}
        for index, fact in enumerate(facts):
            if fact.subject.entity_type not in relevant_types:
                continue
            key = (fact.subject.entity_type, fact.subject.entity_id)
            recency[fact.subject.entity_id] = index
            if fact.subject.entity_type == "identity":
                if fact.predicate == "member_of" and isinstance(fact.object_value, EntityRef):
                    identity_workspace[fact.subject.entity_id] = fact.object_value.entity_id
                elif fact.predicate == "role" and isinstance(fact.object_value, str):
                    identity_role[fact.subject.entity_id] = fact.object_value
            if isinstance(fact.object_value, EntityRef):
                adjacency[fact.subject.entity_id].add(fact.object_value.entity_id)
                adjacency[fact.object_value.entity_id].add(fact.subject.entity_id)
            value = str(fact.object_value)
            detail = f"{fact.predicate}={value[:100]} [{fact.status}]"
            if detail not in grouped[key]:
                grouped[key].append(detail[:160])
            for action_id in fact.source_action_ids:
                source = completed.get(action_id)
                if (
                    source is None
                    or source.evidence_id not in fact.evidence_ids
                    or (fact.subject.entity_id, source.evidence_id) in seen_evidence
                ):
                    continue
                evidence_id = source.evidence_id
                evidence.append(
                    WorkerEvidence(
                        entity_id=fact.subject.entity_id,
                        action_id=action_id,
                        evidence_id=evidence_id,
                        source_worker_id=source.worker_id,
                    )
                )
                seen_evidence.add((fact.subject.entity_id, evidence_id))
        entities_all = [
            WorkerEntity(entity_type=kind, entity_id=entity_id, details=tuple(values[-6:]))
            for (kind, entity_id), values in grouped.items()
        ]
        primary_ids = {
            item.entity_id for item in entities_all if item.entity_type == relevant_types[0]
        }
        workspace_ids = {
            item.entity_id for item in entities_all if item.entity_type == "workspace"
        } | set(identity_workspace.values())
        target_workspaces = {
            related
            for entity_id in primary_ids
            for related in adjacency[entity_id]
            if related in workspace_ids
        }
        evidence_count: dict[UUID, int] = defaultdict(int)
        for item in evidence:
            evidence_count[item.entity_id] += 1

        def entity_score(item: WorkerEntity) -> tuple[int, int, int, str]:
            linked = len(adjacency[item.entity_id] & primary_ids)
            membership = identity_workspace.get(item.entity_id)
            role = identity_role.get(item.entity_id)
            return (
                linked * 8
                + evidence_count[item.entity_id] * 4
                + int(membership in target_workspaces) * 30
                + int(membership is not None) * 15
                + int(role in {"member", "workspace_admin"}) * 10,
                recency.get(item.entity_id, -1),
                len(item.details),
                str(item.entity_id),
            )

        buckets = {
            kind: sorted(
                (item for item in entities_all if item.entity_type == kind),
                key=entity_score,
                reverse=True,
            )
            for kind in relevant_types
        }
        entities: list[WorkerEntity] = []
        for kind in relevant_types[1:]:
            if kind == "identity":
                diverse: list[WorkerEntity] = []
                seen_workspaces: set[UUID] = set()
                for item in buckets[kind]:
                    workspace_id = identity_workspace.get(item.entity_id)
                    if workspace_id is not None and workspace_id not in seen_workspaces:
                        diverse.append(item)
                        seen_workspaces.add(workspace_id)
                    if len(diverse) == 2:
                        break
                diverse.extend(item for item in buckets[kind] if item not in diverse)
                entities.extend(diverse[:2])
            else:
                entities.extend(buckets[kind][:2])
        entities.extend(buckets[relevant_types[0]][: max(0, 8 - len(entities))])
        if len(entities) < 8:
            selected_ids = {item.entity_id for item in entities}
            extras = sorted(
                (item for item in entities_all if item.entity_id not in selected_ids),
                key=entity_score,
                reverse=True,
            )
            entities.extend(extras[: 8 - len(entities)])
        selected_ids = {item.entity_id for item in entities}
        evidence_by_entity: dict[UUID, list[WorkerEvidence]] = defaultdict(list)
        for item in evidence:
            if item.entity_id in selected_ids:
                evidence_by_entity[item.entity_id].append(item)
        ranked_evidence: list[WorkerEvidence] = []
        for item in entities:
            ranked_evidence.extend(
                sorted(
                    evidence_by_entity[item.entity_id],
                    key=lambda row: completed[row.action_id].sequence_number,
                    reverse=True,
                )[:1]
            )
        remaining_evidence = sorted(
            (
                item
                for item in evidence
                if item.entity_id in selected_ids and item not in ranked_evidence
            ),
            key=lambda row: completed[row.action_id].sequence_number,
            reverse=True,
        )
        ranked_evidence.extend(remaining_evidence)
        requested = {item.action_id: item for item in trace if isinstance(item, ActionRequested)}
        fingerprints = {
            item.action_id: item.fingerprint
            for item in trace
            if isinstance(item, ActionReservationAcquired)
        }
        checked: list[CheckedAction] = []
        for item in trace:
            if not isinstance(item, ActionCompleted) or item.action_id not in fingerprints:
                continue
            source = requested.get(item.action_id)
            if source is None or source.worker_id is None:
                continue
            request = self._request(context, source)
            body_json = (
                json.dumps(
                    request.json_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                if request.json_body is not None
                else None
            )
            checked.append(
                CheckedAction(
                    action_id=item.action_id,
                    fingerprint=fingerprints[item.action_id],
                    method=request.method,
                    path=request.path,
                    identity_id=request.identity_id,
                    body_sha256=source.body_sha256,
                    body_json=body_json
                    if body_json is not None and len(body_json) <= 512
                    else None,
                    body_summary=(
                        f"keys={','.join(sorted(request.json_body)[:12])};chars={len(body_json)}"
                        if body_json is not None and len(body_json) > 512
                        else None
                    ),
                )
            )
        pinned = [item for item in checked if item.path == "/api/me"][-12:]
        recent = sorted(
            (item for item in checked if item.path != "/api/me"),
            key=lambda item: (_path_relevance(objective_kind, item.path), checked.index(item)),
            reverse=True,
        )[:12]
        hypotheses = [
            f"{item.subject.entity_type}:{item.subject.entity_id} "
            f"{item.predicate}={str(item.object_value)[:100]} [{item.status}]"
            for item in facts
            if item.kind in {"hypothesis", "open_question"}
        ]
        active_coverage = [
            f"{item.claim_id} {item.component}: {item.objective[:100]}"
            for item in coverage
            if item.status == "active"
        ]
        completed_coverage = [
            f"{item.claim_id} {item.component}: {item.objective[:100]}"
            for item in coverage
            if item.status == "completed"
        ]
        omitted = {
            "omitted_entities": max(0, len(entities_all) - len(entities)),
            "omitted_evidence": max(0, len(evidence) - min(len(ranked_evidence), 12)),
            "omitted_checked_actions": len(checked) - len(pinned) - len(recent),
            "omitted_coverage": (
                max(0, len(active_coverage) - 8) + max(0, len(completed_coverage) - 12)
            ),
            "omitted_hypotheses": max(0, len(hypotheses) - 12),
            "omitted_entity_details": 0,
        }
        evidence = ranked_evidence[:12]
        active_coverage = active_coverage[-8:]
        completed_coverage = completed_coverage[-12:]
        hypotheses = hypotheses[-12:]
        while True:
            try:
                return WorkerTaskPacket(
                    task_id=task_id,
                    worker_id=worker_id,
                    objective=objective,
                    contract=WorkerTaskContract(
                        objective=objective,
                        route_family=OBJECTIVE_ROUTES[objective_kind],
                        target_entity_types=relevant_types,
                        permitted_methods=("GET", "POST")
                        if objective_kind == "refund"
                        else ("GET",),
                        state_change_authorized=objective_kind == "refund",
                    ),
                    budget_slice=budget,
                    protected_future_tokens=floor.tokens,
                    protected_future_model_calls=floor.model_calls,
                    protected_future_cost_microusd=floor.cost_microusd,
                    protected_future_actions=floor.actions,
                    protected_future_http_requests=floor.http_requests,
                    relevant_entities=tuple(entities),
                    relevant_evidence=tuple(evidence),
                    prior_checked_actions=tuple(pinned + recent),
                    active_coverage=tuple(active_coverage),
                    completed_coverage=tuple(completed_coverage),
                    known_hypotheses=tuple(hypotheses),
                    **omitted,
                )
            except ValidationError as exc:
                if "worker packet exceeds 10000 characters" not in str(exc):
                    raise ExperimentInfrastructureError("worker packet validation failed") from exc
            if hypotheses:
                hypotheses.pop(0)
                omitted["omitted_hypotheses"] += 1
            elif completed_coverage:
                completed_coverage.pop(0)
                omitted["omitted_coverage"] += 1
            elif any(len(item.details) > 3 for item in entities):
                index = next(
                    index
                    for index in reversed(range(len(entities)))
                    if len(entities[index].details) > 3
                )
                item = entities[index]
                entities[index] = item.model_copy(update={"details": item.details[1:]})
                omitted["omitted_entity_details"] += 1
            elif recent:
                recent.pop()
                omitted["omitted_checked_actions"] += 1
            elif evidence:
                evidence.pop()
                omitted["omitted_evidence"] += 1
            elif entities:
                removed = entities.pop()
                omitted["omitted_entities"] += 1
                omitted["omitted_evidence"] += sum(
                    item.entity_id == removed.entity_id for item in evidence
                )
                evidence = [item for item in evidence if item.entity_id != removed.entity_id]
            elif pinned:
                pinned.pop(0)
                omitted["omitted_checked_actions"] += 1
            elif active_coverage:
                active_coverage.pop(0)
                omitted["omitted_coverage"] += 1
            else:
                raise ExperimentInfrastructureError("worker packet cannot fit bounded context")


class _WorkerTools:
    def __init__(
        self,
        tools: ToolRegistry,
        task_id: UUID,
        worker_id: UUID,
        budget: Budget,
        controller: PostgresControllerState,
        run_id: UUID,
        global_budget: Budget,
        floor: FutureWorkerFloor,
    ):
        self.tools = tools
        self.task_id = task_id
        self.worker_id = worker_id
        self.budget = budget
        self.used_actions = 0
        self.controller = controller
        self.run_id = run_id
        self.global_budget = global_budget
        self.floor = floor

    async def execute(self, action: ActionRequest) -> ActionResult:
        if action.task_id != self.task_id or action.worker_id != self.worker_id:
            raise ValueError("worker tool action has different ownership")
        limits = [
            item
            for item in (self.budget.max_actions, self.budget.max_http_requests)
            if item is not None
        ]
        if limits and self.used_actions >= min(limits):
            raise AgentBudgetExhausted("worker_action_slice_exhausted")
        usage = await self.controller.snapshot(self.run_id)
        if (
            self.global_budget.max_actions is not None
            and int(usage["used_actions"]) + 1 + self.floor.actions > self.global_budget.max_actions
        ):
            raise AgentBudgetExhausted("future_worker_action_floor")
        if (
            self.global_budget.max_http_requests is not None
            and int(usage["used_http_requests"]) + 1 + self.floor.http_requests
            > self.global_budget.max_http_requests
        ):
            raise AgentBudgetExhausted("future_worker_http_floor")
        self.used_actions += 1
        return await self.tools.execute(action)


class SequentialWorkerCoordinator:
    def __init__(
        self,
        provider: ModelProvider,
        model: ModelSpec,
        findings: FindingSink,
        events: EventStore,
        state_root: Path,
    ) -> None:
        if not isinstance(events, PostgresEventStore):
            raise ValueError("sequential workers require PostgreSQL controller state")
        self.provider = provider
        self.model = model
        self.findings = findings
        self.events = events
        self.state_root = state_root
        self.controller = PostgresControllerState(events)
        self.world = EventWorldState(events)
        self.packet_builder = WorkerPacketBuilder(events, state_root)

    async def _heartbeat_worker(
        self, run_id: UUID, worker_id: UUID, task_id: UUID, owner: asyncio.Task
    ) -> None:
        while True:
            await asyncio.sleep(self.controller.WORKER_HEARTBEAT_SECONDS)
            try:
                await self.controller.heartbeat_worker(run_id, worker_id, task_id)
            except Exception:
                owner.cancel()
                raise

    async def _debrief(
        self, run_id: UUID, task_id: UUID, worker_id: UUID, after: int
    ) -> WorkerDebrief:
        delta = [
            item for item in await self.events.read_run(run_id) if item.sequence_number > after
        ]
        fact_ids = tuple(
            item.fact.fact_id for item in delta if isinstance(item, WorldFactSubmitted)
        )
        finding_ids = tuple(
            item.finding.finding_id for item in delta if isinstance(item, FindingSubmitted)
        )
        completed_ids = tuple(
            item.claim_id
            for item in delta
            if isinstance(item, CoverageUpdated) and item.status == "completed"
        )
        questions = tuple(
            f"{item.fact.predicate}: {str(item.fact.object_value)[:120]}"
            for item in delta
            if isinstance(item, WorldFactSubmitted) and item.fact.kind == "open_question"
        )[:12]
        return WorkerDebrief(
            task_id=task_id,
            worker_id=worker_id,
            new_fact_ids=fact_ids[:100],
            candidate_finding_ids=finding_ids[:30],
            completed_coverage_ids=completed_ids[:30],
            open_questions=questions,
            recommended_followups=questions[:6],
        )

    async def run(self, task: AgentTask, context: AgentContext, tools: ToolRegistry) -> AgentResult:
        if context.global_budget is None or context.range is None:
            raise ValueError("workers require a visible range and global budget")
        if context.global_budget.max_model_calls is None:
            raise ValueError("workers require an explicit global model-call budget")
        initial_floor = future_worker_floor(context.global_budget, 0, self.model)
        minimum_turn = MIN_MEANINGFUL_INPUT_TOKENS + min(
            context.global_budget.max_output_tokens_per_call or WORKER_OUTPUT_CAP_TOKENS,
            WORKER_OUTPUT_CAP_TOKENS,
        )
        if (
            context.global_budget.max_total_tokens is not None
            and context.global_budget.max_total_tokens < len(WORKER_OBJECTIVES) * minimum_turn
        ):
            raise ValueError("global token budget cannot fund one reserved turn per worker")
        if context.global_budget.max_cost_usd is not None and cost_microusd(
            context.global_budget.max_cost_usd
        ) < len(WORKER_OBJECTIVES) * (initial_floor.cost_microusd // (len(WORKER_OBJECTIVES) - 1)):
            raise ValueError("global cost budget cannot fund one reserved turn per worker")
        observations: list[UUID] = []
        findings: list[UUID] = []
        any_failed = False
        globally_exhausted = False
        for index, (kind, objective, relevant_types) in enumerate(WORKER_OBJECTIVES):
            usage_before = await self.controller.snapshot(context.run_id)
            floor = future_worker_floor(context.global_budget, index, self.model)
            budget = worker_budget(context.global_budget, index, usage_before, self.model)
            task_id, worker_id = uuid4(), uuid4()
            claim = CoverageClaim(
                claim_id=uuid4(),
                run_id=context.run_id,
                task_id=task_id,
                component=kind,
                objective=objective,
            )
            guard = self.controller.worker_guard(context.run_id, worker_id)
            await guard.__aenter__()
            claimed = False
            spawned = False
            try:
                reason = await self.controller.spawn_worker(
                    context.run_id, worker_id, task_id, objective, context.global_budget
                )
                if reason:
                    raise AgentBudgetExhausted(reason)
                spawned = True
                await self.world.claim_coverage(claim)
                claimed = True
                packet = await self.packet_builder.build(
                    context, task_id, worker_id, objective, relevant_types, budget, floor
                )
                packet_event = await self.events.append(
                    WorkerPacketPrepared(
                        run_id=context.run_id,
                        actor="coordinator",
                        worker_id=worker_id,
                        task_id=task_id,
                        packet=packet,
                    )
                )
                reason = await self.controller.start_worker(
                    context.run_id, worker_id, task_id, context.global_budget
                )
                if reason:
                    raise ExperimentInfrastructureError(reason)
            except BaseException:
                try:
                    if claimed:
                        await self.world.update_coverage(
                            context.run_id, claim.claim_id, task_id, "released"
                        )
                    if spawned:
                        await self.controller.abort_spawned_worker(
                            context.run_id, worker_id, task_id, "worker_setup_failed"
                        )
                finally:
                    await guard.__aexit__(None, None, None)
                raise
            start_sequence = (await self.events.read_run(context.run_id))[-1].sequence_number
            worker_task = AgentTask(
                task_id=task_id,
                worker_id=worker_id,
                goal=objective,
                allowed_services=task.allowed_services,
                budget=budget,
            )
            worker_context = context.model_copy(
                update={
                    "objective": objective,
                    "worker_packet": packet,
                    "worker_packet_sequence": packet_event.sequence_number,
                }
            )
            agent = MonolithicSaasAgent(
                self.provider,
                self.model,
                self.findings,
                self.events,
                self.state_root,
                memory="structured",
            )
            status = "completed"
            heartbeat: asyncio.Task | None = None
            try:
                owner = asyncio.current_task()
                assert owner is not None
                heartbeat = asyncio.create_task(
                    self._heartbeat_worker(context.run_id, worker_id, task_id, owner)
                )
                result = await agent.run(
                    worker_task,
                    worker_context,
                    _WorkerTools(
                        tools,
                        task_id,
                        worker_id,
                        budget,
                        self.controller,
                        context.run_id,
                        context.global_budget,
                        floor,
                    ),
                )
                status = result.status
                observations.extend(result.observation_ids)
                findings.extend(result.candidate_finding_ids)
            except AgentBudgetExhausted:
                status = "budget_exhausted"
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except Exception:
                status = "failed"
                raise
            finally:
                try:
                    if heartbeat is not None:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
                    worker_delta = [
                        item
                        for item in await self.events.read_run(context.run_id)
                        if item.sequence_number > start_sequence
                    ]
                    outcomes = [
                        item
                        for item in worker_delta
                        if isinstance(item, (WorkerObjectiveAction, WorkerBlocked))
                        and item.worker_id == worker_id
                    ]
                    if not outcomes and status != "cancelled":
                        await self.events.append(
                            WorkerContractViolated(
                                run_id=context.run_id,
                                actor="controller",
                                worker_id=worker_id,
                                task_id=task_id,
                                reason_code=(
                                    "task_block_required"
                                    if any(
                                        isinstance(item, WorkerOriented)
                                        and item.worker_id == worker_id
                                        for item in worker_delta
                                    )
                                    else f"objective_unattempted_{status}"
                                ),
                            )
                        )
                        status = "failed"
                    await self.world.update_coverage(
                        context.run_id,
                        claim.claim_id,
                        task_id,
                        "completed"
                        if any(isinstance(item, WorkerObjectiveAction) for item in outcomes)
                        else "released",
                    )
                    for claim in await self.world.coverage(context.run_id):
                        if claim.task_id == task_id and claim.status == "active":
                            await self.world.update_coverage(
                                context.run_id, claim.claim_id, task_id, "released"
                            )
                    debrief = await self._debrief(
                        context.run_id, task_id, worker_id, start_sequence
                    )
                    await self.events.append(
                        WorkerDebriefed(
                            run_id=context.run_id,
                            actor="coordinator",
                            worker_id=worker_id,
                            task_id=task_id,
                            new_fact_ids=debrief.new_fact_ids,
                            candidate_finding_ids=debrief.candidate_finding_ids,
                            coverage_claim_ids=debrief.completed_coverage_ids,
                            open_questions=debrief.open_questions,
                            recommended_followups=debrief.recommended_followups,
                        )
                    )
                    await self.controller.finish_worker(context.run_id, worker_id, task_id, status)
                finally:
                    await guard.__aexit__(None, None, None)
            any_failed |= status == "failed"
            usage = await self.controller.snapshot(context.run_id)
            global_budget = context.global_budget
            if (
                usage["used_model_calls"] >= global_budget.max_model_calls
                or (
                    global_budget.max_total_tokens is not None
                    and usage["used_tokens"] >= global_budget.max_total_tokens
                )
                or (
                    global_budget.max_actions is not None
                    and usage["used_actions"] >= global_budget.max_actions
                )
                or (
                    global_budget.max_http_requests is not None
                    and usage["used_http_requests"] >= global_budget.max_http_requests
                )
                or (
                    global_budget.max_cost_usd is not None
                    and usage["used_cost_microusd"] >= cost_microusd(global_budget.max_cost_usd)
                )
            ):
                globally_exhausted = index < len(WORKER_OBJECTIVES) - 1
                break
        return AgentResult(
            task_id=task.task_id,
            status="failed"
            if any_failed
            else "budget_exhausted"
            if globally_exhausted
            else "completed",
            observation_ids=tuple(observations),
            candidate_finding_ids=tuple(findings),
        )
