"""Milestone 2.5.1 lifecycle, oracle-binding, and semantic-proof contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import (
    BuildIntegrityError,
    StateStore,
    UnsupportedManifestVersionError,
    artifact_digest,
    utc_now,
    write_json_atomic,
)
from offsecgym.runtime.oracle import OracleBindingError, StateOracleStore
from offsecgym.runtime.saas import PROPERTY_SLUGS, SaasRangeCompiler
from offsecgym.schemas.domain import RangeInstanceStatus, ValidationContext, agent_visible_context
from offsecgym.schemas.events import RangeStarted, parse_event
from offsecgym.schemas.ground_truth import GroundTruthManifest, ProofRequirement
from offsecgym.schemas.specs import RangeSpec


def saas_spec() -> RangeSpec:
    path = Path(__file__).parents[2] / "examples" / "saas-range.yaml"
    return RangeSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_range_started_v2_has_explicit_identity_and_legacy_v1_remains_readable() -> None:
    event = RangeStarted(
        run_id=uuid4(),
        actor="controller",
        build_id=uuid4(),
        range_instance_id=uuid4(),
        range_generation=0,
    )
    assert parse_event(event.model_dump(mode="json")) == event
    assert event.schema_version == "2" and event.range_id is None
    legacy = RangeStarted(schema_version="1", run_id=uuid4(), actor="controller", range_id=uuid4())
    assert parse_event(legacy.model_dump(mode="json")) == legacy
    with pytest.raises(ValidationError, match="requires build, instance, and generation"):
        RangeStarted(run_id=uuid4(), actor="controller", build_id=uuid4())
    with pytest.raises(ValidationError, match="requires only legacy range_id"):
        RangeStarted(
            schema_version="1",
            run_id=uuid4(),
            actor="controller",
            range_id=uuid4(),
            build_id=uuid4(),
        )


def test_proof_requirements_are_typed_and_scenario_independent(tmp_path: Path) -> None:
    adapter = TypeAdapter(ProofRequirement)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "foreign_document_id"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "response_field", "field": "billing_email"})
    manifest = SaasRangeCompiler(StateStore(tmp_path)).build(saas_spec())
    oracle = StateOracleStore(StateStore(tmp_path)).load_ground_truth(manifest.build_id)
    assert oracle.schema_version == "3"
    kinds = {item.kind for prop in oracle.properties for item in prop.proof_requirements}
    assert kinds == {
        "identity",
        "object_relation",
        "response_status",
        "state_transition",
        "anonymous_request",
        "response_field",
    }
    with pytest.raises(ValidationError):
        GroundTruthManifest.model_validate(
            {**oracle.model_dump(mode="python"), "schema_version": "2"}
        )


def test_oracle_store_uses_build_and_instance_generation(tmp_path: Path) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = asyncio.run(runtime.build(saas_spec()))
    instance_id = asyncio.run(runtime.create_instance(build_id))
    context = ValidationContext(
        run_id=uuid4(),
        build_id=build_id,
        range_instance_id=instance_id,
        range_generation=0,
    )
    store = StateOracleStore(runtime.state)
    assert store.load_for_context(context).build_id == build_id
    with pytest.raises(OracleBindingError, match="build"):
        store.load_for_context(context.model_copy(update={"build_id": uuid4()}))
    with pytest.raises(OracleBindingError, match="generation"):
        store.load_for_context(context.model_copy(update={"range_generation": 1}))
    oracle_path = tmp_path / "oracles" / build_id.hex / "ground_truth.json"
    oracle_path.write_bytes(oracle_path.read_bytes() + b"\n")
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        store.load_for_context(context)


def test_compiler_identity_version_binds_oracle_semantics(tmp_path: Path, monkeypatch) -> None:
    import offsecgym.runtime.saas as saas

    compiler = SaasRangeCompiler(StateStore(tmp_path))
    first = compiler.build(saas_spec())
    first_oracle = StateOracleStore(compiler.state).load_ground_truth(first.build_id)
    monkeypatch.setattr(saas, "SAAS_COMPILER_VERSION", "tenant-boundary-v1/compiler-next")
    second = compiler.build(saas_spec())
    second_oracle = StateOracleStore(compiler.state).load_ground_truth(second.build_id)
    assert first.build_id != second.build_id
    assert first.pair_id == second.pair_id
    assert {item.property_id for item in first_oracle.properties} == {
        item.property_id for item in second_oracle.properties
    }


def test_old_oracle_version_fails_build_integrity_check(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    manifest = SaasRangeCompiler(state).build(saas_spec())
    path = tmp_path / "oracles" / manifest.build_id.hex / "ground_truth.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "2"
    write_json_atomic(path, payload)
    old_digest = artifact_digest(path.read_bytes())
    old_manifest = manifest.model_copy(
        update={
            "oracle_artifact_digests": {
                **manifest.oracle_artifact_digests,
                "ground_truth.json": old_digest,
            }
        }
    )
    write_json_atomic(
        state.build_dir(manifest.build_id) / "manifest.json", old_manifest.model_dump(mode="json")
    )
    with pytest.raises(UnsupportedManifestVersionError, match="rebuild"):
        state.verify_build_integrity(manifest.build_id)


@pytest.mark.asyncio
async def test_controller_metadata_uses_effective_patch_set(tmp_path: Path, monkeypatch) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    spec = saas_spec().model_copy(update={"patched_properties": tuple(sorted(PROPERTY_SLUGS))})
    build_id = await runtime.build(spec)
    instance_id = await runtime.create_instance(build_id)

    async def status(_: object) -> RangeInstanceStatus:
        return RangeInstanceStatus(
            instance_id=instance_id,
            build_id=build_id,
            generation=0,
            state="stopped",
            checked_at=utc_now(),
        )

    monkeypatch.setattr(runtime, "instance_status", status)
    metadata = await runtime.snapshot_metadata(instance_id)
    assert metadata.variant == "patched"
    assert metadata.patched_properties == tuple(sorted(PROPERTY_SLUGS))
    visible = agent_visible_context(metadata)
    assert "variant" not in visible.model_dump()
    assert "patched_properties" not in visible.model_dump()
