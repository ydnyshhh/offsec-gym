"""Fail-closed A1 visibility, public grounding, stage, and admission contracts."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from offsecgym.benchmark.a1_admission import SEEDS, VARIANTS, admit_a1
from offsecgym.benchmark.a1_public import (
    PublicCandidate,
    PublicObservation,
    PublicObservationGraph,
    freeze_visibility,
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
    assert all(cell["gates"]["Q4"] == "not_evaluated" for cell in result["cells"])
