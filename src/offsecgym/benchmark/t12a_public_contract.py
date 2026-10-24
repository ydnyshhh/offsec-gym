"""Shared, no-model visibility and passive observation checks for T1.2 tasks."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from offsecgym.benchmark.a1_public import (
    PublicInputs,
    PublicObservationGraph,
    digest,
    rendered_model_projection,
)
from offsecgym.benchmark.tasks import TaskManifest
from offsecgym.schemas.domain import AgentContext, AgentTask
from offsecgym.solver.monolithic import build_context, model_tools
from offsecgym.solver.range_surface import range_surface

CONTRACT_VERSION = "t12a-public-visibility-v1"
ARCHETYPES = frozenset({"A2", "A3", "A4"})


@dataclass(frozen=True)
class PublicVisibility:
    version: str
    task_id: str
    archetype_id: str
    projection_sha256: str
    rendered_context_sha256: str
    read_routes: tuple[str, ...]
    identity_ids: tuple[UUID, ...]


def freeze_public_visibility(
    manifest: TaskManifest, task: AgentTask, context: AgentContext
) -> PublicVisibility:
    """Compare the reference's inputs to the effective rendered model context."""
    if manifest.task.archetype_id not in ARCHETYPES or manifest.task.task_mode != "scoped_audit":
        raise ValueError("unsupported T1.2 scoped archetype")
    if context.range is None or context.range.family != "saas":
        raise ValueError("T1.2 visibility requires a SaaS context")
    if task.goal != manifest.public.public_objective or context.objective != task.goal:
        raise ValueError("model objective differs from the public task")
    instructions, items = build_context(task, context)
    tools = model_tools(family="saas", structured=False)
    rendered = {"instructions": instructions, "items": items, "tools": tools}
    effective = rendered_model_projection(rendered)
    reference = PublicInputs(
        manifest.public.public_objective,
        context.range.identity_ids,
        range_surface("saas").routes,
    ).projection()
    if effective != reference:
        raise ValueError("reference inputs exceed or differ from effective model exposure")
    return PublicVisibility(
        version=CONTRACT_VERSION,
        task_id=manifest.task.task_id,
        archetype_id=manifest.task.archetype_id,
        projection_sha256=digest(effective),
        rendered_context_sha256=digest(rendered),
        read_routes=tuple(route for route in effective["routes"] if route.startswith("GET ")),
        identity_ids=context.range.identity_ids,
    )


def passive_field_observed(
    graph: PublicObservationGraph,
    *,
    action_id: UUID,
    evidence_id: UUID,
    sequence_number: int,
    field: str,
    value: str,
) -> bool:
    """Check a claim against an already audited read-only observation graph."""
    if not field or field.startswith("_") or "." in field or "[" in field:
        raise ValueError("only a top-level public response field is supported")
    return any(
        observation.action_id == action_id
        and observation.evidence_id == evidence_id
        and observation.sequence_number == sequence_number
        and observation.run_id == graph.run_id
        and observation.instance_id == graph.instance_id
        and observation.generation == graph.generation
        and observation.method == "GET"
        and observation.response.get(field) == value
        for observation in graph.observations
    )
