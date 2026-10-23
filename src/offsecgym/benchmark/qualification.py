"""No-model compiler qualification with conservative readiness gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.benchmark.difficulty import existing_factors
from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.registry import ARCHETYPES
from offsecgym.benchmark.stages import StageResult, unqualified_stage_vector
from offsecgym.benchmark.tasks import TaskManifest, canonical_sha256
from offsecgym.runtime.enterprise import EnterpriseRangeCompiler
from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.ground_truth import GroundTruthManifest

GateStatus = Literal["pass", "fail", "unknown", "not_evaluated"]
Readiness = Literal["qualified", "partially_qualified", "blocked", "not_evaluated"]


class GateResult(StrictModel):
    status: GateStatus
    reason_code: str = Field(min_length=1)
    evidence_reference: str | None = None

    @model_validator(mode="after")
    def passing_gate_has_evidence(self) -> GateResult:
        if self.status == "pass" and not self.evidence_reference:
            raise ValueError("passing gate requires evidence reference")
        return self


class QualificationRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    task_id: str
    archetype_id: str
    range_family: str
    scenario: str
    root_slug: str
    task_mode: str
    variant: str
    qualification_type: Literal["static_no_model"] = "static_no_model"
    gates: dict[str, GateResult]
    readiness: Readiness
    public_only_solvability: Literal["demonstrated", "unknown", "blocked"]
    positive_control_status: GateStatus
    negative_control_status: GateStatus
    real_docker_used: Literal[False] = False
    gateway_used: Literal[False] = False
    stage_results: tuple[StageResult, ...]
    source_sha256: dict[str, str]
    blocking_reasons: tuple[str, ...]

    @model_validator(mode="after")
    def no_unearned_readiness(self) -> QualificationRecord:
        if set(self.gates) != {f"Q{i}" for i in range(1, 10)}:
            raise ValueError("Q1–Q9 gates are required")
        if (
            self.gates["Q9"].status != "pass"
            or self.gates["Q9"].reason_code != f"readiness_{self.readiness}"
        ):
            raise ValueError("Q9 must record the supported readiness classification")
        if self.readiness == "qualified" and any(
            self.gates[f"Q{i}"].status != "pass" for i in range(1, 9)
        ):
            raise ValueError("qualified task requires all gates to pass")
        if self.public_only_solvability == "demonstrated" and (
            self.gates["Q3"].status != "pass" or self.gates["Q4"].status != "pass"
        ):
            raise ValueError("public-only solvability requires Q3 and Q4 proof")
        return self


def _oracle(state: StateStore, build_id: object) -> GroundTruthManifest:
    path = state.root / "oracles" / build_id.hex / "ground_truth.json"
    return GroundTruthManifest.model_validate_json(path.read_bytes())


def _gate(status: GateStatus, reason: str, evidence: str | None = None) -> GateResult:
    return GateResult(status=status, reason_code=reason, evidence_reference=evidence)


def qualify_static(manifest: TaskManifest, state_root: Path) -> QualificationRecord:
    """Build disposable artifacts and verify patch isolation; never start services."""
    state = StateStore(state_root)
    spec = manifest.private.range_spec
    compiler = SaasRangeCompiler(state) if spec.family == "saas" else EnterpriseRangeCompiler(state)
    gates = {
        f"Q{i}": _gate("not_evaluated", "requires_runtime_or_public_path_qualification")
        for i in range(1, 9)
    }
    try:
        if manifest.private.source_sha256 != source_pins():
            raise ValueError("source_sha_mismatch")
        build = compiler.build(spec)
        repeat = compiler.build(spec)
        state.verify_build_integrity(build.build_id)
        oracle = _oracle(state, build.build_id)
        active = tuple(sorted(prop.slug for prop in oracle.properties if prop.active))
        expected = tuple(sorted(manifest.task.active_property_slugs))
        if build != repeat or active != expected:
            raise ValueError("compiler regeneration or active-root isolation mismatch")
        sibling = compiler.build(
            spec.model_copy(update={"patched": True, "patched_properties": ()})
        )
        state.verify_build_integrity(sibling.build_id)
        sibling_oracle = _oracle(state, sibling.build_id)
        if any(prop.active for prop in sibling_oracle.properties):
            raise ValueError("patched sibling contains active root")
        if build.pair_id != sibling.pair_id:
            raise ValueError("vulnerable/patched pair identity mismatch")
        fixture_a = state.build_dir(build.build_id) / "fixture.json"
        fixture_b = state.build_dir(sibling.build_id) / "fixture.json"
        if (
            hashlib.sha256(fixture_a.read_bytes()).digest()
            != hashlib.sha256(fixture_b.read_bytes()).digest()
        ):
            raise ValueError("paired fixture mismatch")
        fixture = json.loads(fixture_a.read_text(encoding="utf-8"))
        fixture_entities = sum(len(value) for value in fixture.values() if isinstance(value, list))
        measured = existing_factors(
            fixture_entities,
            sum(spec.identities.values()),
            sum(spec.topology.values()),
        )
        gates["Q1"] = _gate(
            "pass", "deterministic_build_and_patch_isolation", "verified_build_and_oracle_digests"
        )
        gates["Q7"] = (
            _gate(
                "pass", "measured_features_match_fixture_and_spec", "fixture_and_range_spec_counts"
            )
            if manifest.task.difficulty_factors == measured
            else _gate("fail", "difficulty_feature_mismatch")
        )
    except (OSError, ValueError) as exc:
        reason = "source_sha_mismatch" if str(exc) == "source_sha_mismatch" else type(exc).__name__
        gates["Q1"] = _gate("fail", reason)
        gates["Q7"] = _gate("unknown", "build_integrity_not_established")
    public_payload = manifest.public.model_dump(mode="json")
    forbidden = {
        "expected_root_slug",
        "range_spec",
        "source_sha256",
        "active_property_slugs",
        "patched_properties",
        "reference_policy",
        "oracle",
    }
    if not (
        forbidden & set(public_payload)
    ) and manifest.private.expected_root_slug not in json.dumps(public_payload):
        gates["Q8"] = _gate(
            "pass", "public_schema_excludes_private_fields", "public_envelope_schema_v1"
        )
    else:
        gates["Q8"] = _gate("fail", "private_data_in_public_envelope")
    readiness: Readiness = (
        "partially_qualified"
        if all(gates[name].status == "pass" for name in ("Q1", "Q7", "Q8"))
        else "blocked"
    )
    gates["Q9"] = _gate("pass", f"readiness_{readiness}", "gate_status_projection")
    return QualificationRecord(
        task_id=manifest.task.task_id,
        archetype_id=manifest.task.archetype_id,
        range_family=spec.family,
        scenario=spec.scenario,
        root_slug=manifest.private.expected_root_slug,
        task_mode=manifest.task.task_mode,
        variant=manifest.task.variant,
        gates=gates,
        readiness=readiness,
        public_only_solvability="unknown",
        positive_control_status="not_evaluated",
        negative_control_status="not_evaluated",
        stage_results=unqualified_stage_vector(),
        source_sha256=manifest.private.source_sha256,
        blocking_reasons=(
            "Q2–Q6 require independent runtime, public-path, control, and scoring evidence",
        ),
    )


def default_registry_manifests() -> tuple[TaskManifest, ...]:
    return tuple(
        make_task_manifest(
            archetype_id,
            task_mode="scoped_audit",
            variant="vulnerable",
            seed=42,
        )
        for archetype_id in sorted(ARCHETYPES)
    )


def qualification_aggregate(records: tuple[QualificationRecord, ...]) -> dict[str, object]:
    if len(records) != 8 or len({r.archetype_id for r in records}) != 8:
        raise ValueError("qualification aggregate requires exactly eight archetypes")
    payload: dict[str, object] = {
        "milestone": "T0",
        "benchmark_version": "0.1.0-draft",
        "scope": "task_contract_and_static_no_model_qualification",
        "registered_archetypes": 8,
        "range_families_implemented": 2,
        "new_range_families_implemented": 0,
        "task_qualification": {r.archetype_id: r.model_dump(mode="json") for r in records},
        "provider_calls": 0,
        "prospective_model_results": False,
        "historical_results_modified": False,
    }
    payload["record_set_sha256"] = canonical_sha256(payload)
    return payload
