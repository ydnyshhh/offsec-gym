"""Detached T0 task contracts and conservative static qualification."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from offsecgym.benchmark.difficulty import FACTOR_NAMES, DifficultyFactor, DifficultyFactors
from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.qualification import qualify_static
from offsecgym.benchmark.registry import ARCHETYPES
from offsecgym.benchmark.stages import StageResult, unqualified_stage_vector
from offsecgym.benchmark.tasks import (
    BenchmarkTaskSpec,
    PublicTaskEnvelope,
    TaskManifest,
    canonical_bytes,
)
from offsecgym.cli.main import app
from offsecgym.runtime.enterprise import PROPERTY_SLUGS as B_ROOTS
from offsecgym.runtime.saas import PROPERTY_SLUGS as A_ROOTS


def test_registry_matches_compilers() -> None:
    assert len(ARCHETYPES) == 8
    assert {row.root_slug for row in ARCHETYPES.values()} == A_ROOTS | B_ROOTS


@pytest.mark.parametrize("archetype_id", sorted(ARCHETYPES))
def test_scoped_and_unguided_manifests_are_distinct_and_public_safe(archetype_id: str) -> None:
    scoped = make_task_manifest(
        archetype_id, task_mode="scoped_audit", variant="vulnerable", seed=42
    )
    repeat = make_task_manifest(
        archetype_id, task_mode="scoped_audit", variant="vulnerable", seed=42
    )
    unguided = make_task_manifest(
        archetype_id, task_mode="unguided_audit", variant="vulnerable", seed=42
    )
    patched = make_task_manifest(archetype_id, task_mode="scoped_audit", variant="patched", seed=42)
    assert canonical_bytes(scoped) == canonical_bytes(repeat)
    assert len({scoped.task.task_id, unguided.task.task_id, patched.task.task_id}) == 3
    assert scoped.task.active_property_slugs == (scoped.private.expected_root_slug,)
    assert not patched.task.active_property_slugs
    assert len(unguided.task.active_property_slugs) == (5 if archetype_id[0] == "A" else 3)
    public = json.dumps(scoped.public.model_dump(mode="json"), sort_keys=True)
    assert scoped.private.expected_root_slug not in public
    assert "patched_properties" not in public
    assert "oracle" not in public
    assert "source_sha256" not in public
    assert "reference_policy" not in public
    assert "task_mode" in public
    assert "document" not in unguided.public.public_objective.lower()
    assert (
        "change-control" not in unguided.public.public_objective.lower() or archetype_id[0] == "B"
    )


def test_schema_rejects_unknowns_collisions_and_private_public_fields() -> None:
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=3)
    with pytest.raises(ValueError, match="unknown benchmark archetype"):
        make_task_manifest("Z9", task_mode="scoped_audit", variant="vulnerable", seed=3)
    payload = manifest.task.model_dump(mode="json")
    payload["scenario"] = "enterprise_change_control_v1"
    with pytest.raises(ValidationError):
        BenchmarkTaskSpec.model_validate(payload)
    payload = manifest.task.model_dump(mode="json")
    payload["seed"] = 4
    with pytest.raises(ValidationError, match="identity"):
        BenchmarkTaskSpec.model_validate(payload)
    payload = manifest.task.model_dump(mode="json")
    payload["active_property_slugs"] = ["MEMBER-REFUND"]
    with pytest.raises(ValidationError):
        BenchmarkTaskSpec.model_validate(payload)
    public = manifest.public.model_dump(mode="json")
    public["expected_root_slug"] = manifest.private.expected_root_slug
    with pytest.raises(ValidationError):
        PublicTaskEnvelope.model_validate(public)
    public = manifest.public.model_dump(mode="json")
    public["public_objective"] = "The target root is DOC-CROSS-TENANT-READ"
    with pytest.raises(ValidationError):
        PublicTaskEnvelope.model_validate(public)
    private = manifest.private.model_dump(mode="json")
    private["expected_root_slug"] = "MEMBER-REFUND"
    whole = manifest.model_dump(mode="json")
    whole["private"] = private
    with pytest.raises(ValidationError):
        TaskManifest.model_validate(whole)


def test_difficulty_is_seven_axis_uncalibrated_and_unknown_is_honest() -> None:
    axes = tuple(DifficultyFactor(factor_id=axis) for axis in FACTOR_NAMES)
    factors = DifficultyFactors(axes=axes, design_level="L1-existing")
    assert not factors.empirically_calibrated
    assert all(not axis.measured_features for axis in factors.axes)
    with pytest.raises(ValidationError):
        DifficultyFactors(axes=axes, design_level="L2-proposed")
    with pytest.raises(ValidationError):
        DifficultyFactor(factor_id="D2", measured_features={"hops": 3})


def test_stage_success_cannot_be_inferred_from_model_text_or_http_200() -> None:
    assert all(row.status == "unknown" for row in unqualified_stage_vector())
    with pytest.raises(ValidationError):
        StageResult(stage_id="S5", status="true", reason_code="model_claim")
    with pytest.raises(ValidationError):
        StageResult(
            stage_id="S5",
            status="true",
            evidence_source="http_200",
            evidence_reference="response-1",
            reason_code="insufficient",
        )


def test_static_qualification_checks_compiler_but_does_not_claim_public_path(tmp_path) -> None:
    manifest = make_task_manifest("B2", task_mode="scoped_audit", variant="vulnerable", seed=42)
    record = qualify_static(manifest, tmp_path)
    assert record.readiness == "partially_qualified"
    assert record.gates["Q1"].status == "pass"
    assert record.gates["Q7"].status == "pass"
    assert record.gates["Q8"].status == "pass"
    assert record.gates["Q9"].status == "pass"
    assert record.gates["Q9"].reason_code == "readiness_partially_qualified"
    assert all(record.gates[f"Q{i}"].status == "not_evaluated" for i in range(2, 7))
    assert record.public_only_solvability == "unknown"
    assert record.positive_control_status == "not_evaluated"
    assert not record.real_docker_used and not record.gateway_used


def test_source_pin_drift_blocks_admission(tmp_path) -> None:
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    private = manifest.private.model_copy(update={"source_sha256": {"registry": "0" * 64}})
    tampered = manifest.model_copy(update={"private": private})
    record = qualify_static(tampered, tmp_path)
    assert record.readiness == "blocked"
    assert record.gates["Q9"].reason_code == "readiness_blocked"
    assert record.gates["Q1"].reason_code == "source_sha_mismatch"
    assert not list(tmp_path.iterdir())


def test_difficulty_measurement_must_match_generated_fixture(tmp_path) -> None:
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant="vulnerable", seed=42)
    factors = manifest.task.difficulty_factors
    axes = list(factors.axes)
    axes[0] = axes[0].model_copy(update={"measured_features": {"fixture_entities": 9999}})
    task = manifest.task.model_copy(
        update={"difficulty_factors": factors.model_copy(update={"axes": tuple(axes)})}
    )
    altered = manifest.model_copy(update={"task": task})
    record = qualify_static(altered, tmp_path)
    assert record.gates["Q1"].status == "pass"
    assert record.gates["Q7"].reason_code == "difficulty_feature_mismatch"
    assert record.readiness == "blocked"


def test_cli_inspection_only_prints_public_envelope() -> None:
    manifest = make_task_manifest("B3", task_mode="scoped_audit", variant="vulnerable", seed=42)
    result = CliRunner().invoke(app, ["benchmark", "tasks", "inspect", manifest.task.task_id])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == manifest.public.model_dump(mode="json")
    assert manifest.private.expected_root_slug not in result.stdout
    assert "patched_properties" not in result.stdout
