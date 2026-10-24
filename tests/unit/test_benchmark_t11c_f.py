"""Fail-closed A1 visibility, public grounding, stage, and admission contracts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from offsecgym.benchmark import a1_public
from offsecgym.benchmark.a1_admission import SEEDS, VARIANTS, admit_a1
from offsecgym.benchmark.a1_public import (
    PublicCandidate,
    PublicObservation,
    PublicObservationGraph,
    freeze_visibility,
    rendered_model_projection,
)
from offsecgym.benchmark.a1_stages import extract_a1_stages
from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.schemas.domain import (
    AgentContext,
    AgentTask,
    AgentVisibleRangeContext,
    ValidationResult,
)
from offsecgym.schemas.specs import Budget


def _visibility():
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    actor = uuid4()
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
            identity_ids=(actor,),
            known_roles=("member", "platform_admin"),
        ),
    )
    return freeze_visibility(manifest.public, task, context, manifest.private.source_sha256)


def test_real_model_renderer_and_reference_share_effective_public_projection() -> None:
    contract, public, rendered = _visibility()
    assert contract.visibility_parity
    assert contract.model_exposed_role_policy == "discover_via_api_me"
    assert "Discover roles through /api/me" in rendered["items"][0]["content"]
    assert "member" not in rendered["items"][0]["content"]
    assert public.identity_ids and public.routes
    assert rendered_model_projection(rendered) == public.projection()


def test_reference_visibility_rejects_differential_exposure() -> None:
    _, public, rendered = _visibility()
    model_projection = rendered_model_projection(rendered)
    assert model_projection != {
        **public.projection(),
        "identity_ids": (*public.projection()["identity_ids"], str(uuid4())),
    }
    assert model_projection != {
        **public.projection(),
        "routes": (*public.routes, "GET /private-only"),
    }
    hidden_roster = deepcopy(rendered)
    hidden_roster["items"][0]["content"] = hidden_roster["items"][0]["content"].replace(
        str(public.identity_ids[0]), str(uuid4())
    )
    assert rendered_model_projection(hidden_roster) != public.projection()
    hidden_route = deepcopy(rendered)
    hidden_route["instructions"] = hidden_route["instructions"].replace(
        public.routes[0], "GET /different-route"
    )
    assert rendered_model_projection(hidden_route) != public.projection()
    with_bootstrap = deepcopy(rendered)
    with_bootstrap["items"][0]["content"] += " The controller already checked public facts."
    assert rendered_model_projection(with_bootstrap) != public.projection()
    with_memory = deepcopy(rendered)
    with_memory["instructions"] += " Your memory is a structured worldview."
    assert rendered_model_projection(with_memory) != public.projection()
    missing_role_rule = deepcopy(rendered)
    missing_role_rule["items"][0]["content"] = missing_role_rule["items"][0]["content"].replace(
        "Discover roles through /api/me. ", ""
    )
    with pytest.raises(ValueError, match="role discovery"):
        rendered_model_projection(missing_role_rule)


def test_freeze_visibility_blocks_reference_policy_on_harness_drift(monkeypatch) -> None:
    original = a1_public.build_context

    def changed_context(task, context):
        instructions, items = original(task, context)
        items[0]["content"] = items[0]["content"].replace(
            str(context.range.identity_ids[0]), str(uuid4())
        )
        return instructions, items

    monkeypatch.setattr(a1_public, "build_context", changed_context)
    with pytest.raises(ValueError, match="effective model exposure"):
        _visibility()


def test_freeze_visibility_blocks_reference_only_route(monkeypatch) -> None:
    original = a1_public.range_surface

    def expanded_reference_surface(family):
        surface = original(family)
        return replace(surface, routes=(*surface.routes, "GET /reference-only"))

    monkeypatch.setattr(a1_public, "range_surface", expanded_reference_surface)
    with pytest.raises(ValueError, match="effective model exposure"):
        _visibility()


def test_public_candidate_requires_exact_response_field_and_sequence() -> None:
    run_id, instance_id, action_id, evidence_id, candidate_id = (uuid4() for _ in range(5))
    workspace_id = uuid4()
    observation = PublicObservation(
        action_id=action_id,
        evidence_id=evidence_id,
        run_id=run_id,
        instance_id=instance_id,
        generation=0,
        identity_id=uuid4(),
        method="GET",
        path="/api/documents/own",
        response_sha256="0" * 64,
        response={"reference_document_id": str(candidate_id), "workspace_id": str(workspace_id)},
        sequence_number=4,
    )
    candidate = PublicCandidate(
        document_id=candidate_id,
        source_action_id=action_id,
        source_evidence_id=evidence_id,
        source_field_path="$.reference_document_id",
        source_sequence_number=4,
    )
    base = dict(
        task_id="t0-test",
        run_id=run_id,
        instance_id=instance_id,
        generation=0,
        visibility_contract_id="test",
        observations=(observation,),
        candidate=candidate,
        actor_id=None,
        actor_workspace_id=workspace_id,
    )
    PublicObservationGraph(**base)
    with pytest.raises(ValidationError):
        PublicObservationGraph(
            **{**base, "candidate": candidate.model_copy(update={"document_id": uuid4()})}
        )
    with pytest.raises(ValidationError):
        PublicObservationGraph(
            **{**base, "candidate": candidate.model_copy(update={"source_sequence_number": 3})}
        )
    for mismatch in (
        {"run_id": uuid4()},
        {"instance_id": uuid4()},
        {"generation": 1},
    ):
        with pytest.raises(ValidationError):
            PublicObservationGraph(
                **{**base, "observations": (observation.model_copy(update=mismatch),)}
            )


def test_s6_needs_matching_canonical_verdict_in_same_run() -> None:
    graph = PublicObservationGraph(
        task_id="t0-test",
        run_id=uuid4(),
        instance_id=uuid4(),
        generation=0,
        visibility_contract_id="test",
        observations=(),
        candidate=None,
        actor_id=None,
        actor_workspace_id=None,
    )
    verdict = ValidationResult(
        run_id=graph.run_id,
        finding_id=uuid4(),
        status="validated",
    )
    stages = extract_a1_stages(
        initialized=True,
        graph=graph,
        decisive=None,
        decisive_response=None,
        verdict=verdict,
        matched_expected_root=False,
    )
    assert stages[0].status == "true"
    assert stages[6].status == "unknown"
    assert stages[6].evidence_reference is None


def test_admission_cannot_pass_with_missing_six_cell_evidence() -> None:
    result = admit_a1(static={}, q2={}, reference={}, conformance={})
    assert result["q9_readiness"] == "not_evaluated"
    assert not result["admitted"]
    assert len(result["cells"]) == len(SEEDS) * len(VARIANTS)
    assert all(
        cell["gates"]["Q4"]
        == ("not_evaluated" if cell["variant"] == "vulnerable" else "not_applicable")
        for cell in result["cells"]
    )
    assert all(
        cell["patched_control_status"]
        == ("not_applicable" if cell["variant"] == "vulnerable" else "not_evaluated")
        for cell in result["cells"]
    )


def test_offline_canonical_validator_controls_and_score_replay(tmp_path) -> None:
    from research_ops.benchmark_t11d_conformance import execute

    result = asyncio.run(execute(tmp_path))
    assert result["positive_control_pass"] is True
    assert set(result["negative_controls"]) == {
        "positive",
        *(f"NC{number}" for number in range(1, 16)),
    }
    assert all(control["pass"] for control in result["negative_controls"].values())
    assert result["q5_status"] == "pass"
    assert result["q6_status"] == "pass"
