"""T1.2 visibility parity and passive evidence checks need no runtime actions."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from offsecgym.benchmark import t12a_public_contract
from offsecgym.benchmark.a1_public import PublicObservation, PublicObservationGraph
from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.t12a_public_contract import (
    freeze_public_visibility,
    passive_field_observed,
)
from offsecgym.schemas.domain import AgentContext, AgentTask, AgentVisibleRangeContext
from offsecgym.schemas.specs import Budget


def _inputs(archetype: str):
    manifest = make_task_manifest(
        archetype, task_mode="scoped_audit", variant="vulnerable", seed=42
    )
    task = AgentTask(
        task_id=uuid4(),
        goal=manifest.public.public_objective,
        budget=Budget(max_actions=24, max_total_tokens=4096),
    )
    context = AgentContext(
        run_id=uuid4(),
        objective=task.goal,
        range=AgentVisibleRangeContext(
            range_instance_id=uuid4(),
            family="saas",
            visibility_policy="white_box_accounts",
            identity_ids=(uuid4(),),
            known_roles=("member", "platform_admin"),
        ),
    )
    return manifest, task, context


@pytest.mark.parametrize("archetype", ("A2", "A3", "A4"))
def test_effective_model_visibility_is_shared_across_t12_archetypes(archetype: str) -> None:
    manifest, task, context = _inputs(archetype)
    contract = freeze_public_visibility(manifest, task, context)
    assert contract.task_id == manifest.task.task_id
    assert contract.archetype_id == archetype
    assert contract.identity_ids == context.range.identity_ids
    assert contract.read_routes
    assert all(route.startswith("GET ") for route in contract.read_routes)


def test_extra_reference_route_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, task, context = _inputs("A3")
    original = t12a_public_contract.range_surface

    def extra_route(family: str):
        surface = original(family)
        return replace(surface, routes=(*surface.routes, "GET /reference-only"))

    monkeypatch.setattr(t12a_public_contract, "range_surface", extra_route)
    with pytest.raises(ValueError, match="effective model exposure"):
        freeze_public_visibility(manifest, task, context)


def test_extra_model_bootstrap_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, task, context = _inputs("A3")
    original = t12a_public_contract.build_context

    def extra_bootstrap(task: AgentTask, context: AgentContext):
        instructions, items = original(task, context)
        items[0]["content"] += " The controller already checked public facts."
        return instructions, items

    monkeypatch.setattr(t12a_public_contract, "build_context", extra_bootstrap)
    with pytest.raises(ValueError, match="effective model exposure"):
        freeze_public_visibility(manifest, task, context)


def test_passive_claim_requires_exact_public_observation_lineage() -> None:
    run_id, instance_id, action_id, evidence_id = (uuid4() for _ in range(4))
    observation = PublicObservation(
        action_id=action_id,
        evidence_id=evidence_id,
        run_id=run_id,
        instance_id=instance_id,
        generation=2,
        identity_id=uuid4(),
        method="GET",
        path="/api/documents/own",
        response_sha256="0" * 64,
        response={"reference": "benign-record-7"},
        sequence_number=8,
    )
    graph = PublicObservationGraph(
        task_id="synthetic",
        run_id=run_id,
        instance_id=instance_id,
        generation=2,
        visibility_contract_id="test",
        observations=(observation,),
        candidate=None,
        actor_id=None,
        actor_workspace_id=None,
    )
    claim = dict(
        action_id=action_id,
        evidence_id=evidence_id,
        sequence_number=8,
        field="reference",
        value="benign-record-7",
    )
    assert passive_field_observed(graph, **claim)
    assert not passive_field_observed(graph, **{**claim, "evidence_id": uuid4()})
    assert not passive_field_observed(graph, **{**claim, "sequence_number": 9})
    assert not passive_field_observed(graph, **{**claim, "value": "other"})
    with pytest.raises(ValueError, match="top-level"):
        passive_field_observed(graph, **{**claim, "field": "private.secret"})
