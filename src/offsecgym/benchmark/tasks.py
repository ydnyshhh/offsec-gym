"""Strict task identities and structurally separate public/private artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.benchmark.difficulty import DifficultyFactors
from offsecgym.benchmark.registry import (
    ARCHETYPES,
    ENTERPRISE_ROOTS,
    SAAS_ROOTS,
    UNGUIDED_OBJECTIVES,
    get_archetype,
)
from offsecgym.runtime import enterprise, saas
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import RangeSpec

BENCHMARK_VERSION = "0.1.0-draft"
TaskMode = Literal["scoped_audit", "unguided_audit"]
TaskVariant = Literal["vulnerable", "patched"]
BenchmarkSplit = Literal["development", "calibration", "held_out"]


def canonical_bytes(value: StrictModel | dict[str, object]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, StrictModel) else value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_sha256(value: StrictModel | dict[str, object]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class BenchmarkTaskSpec(StrictModel):
    schema_version: Literal["1"] = "1"
    benchmark_version: Literal["0.1.0-draft"] = BENCHMARK_VERSION
    task_id: str = Field(pattern=r"^t0-[0-9a-f]{64}$")
    archetype_id: str
    range_family: str
    scenario: str
    range_compiler_version: str = Field(min_length=1)
    task_mode: TaskMode
    difficulty_config_id: Literal["L1-existing"]
    seed: int = Field(ge=0)
    variant: TaskVariant
    active_property_slugs: tuple[str, ...]
    public_objective_id: Literal["scoped-v1", "unguided-v1"]
    public_surface_id: Literal["saas-public-v1", "enterprise-public-v1"]
    qualification_policy_id: Literal["t0-q1-q9-v1"] = "t0-q1-q9-v1"
    verifier_contract_id: Literal["canonical-v1"] = "canonical-v1"
    stage_contract_id: Literal["t0-stages-v1"] = "t0-stages-v1"
    difficulty_factors: DifficultyFactors
    benchmark_split: BenchmarkSplit
    semantic_template_group: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_definition(self) -> BenchmarkTaskSpec:
        archetype = get_archetype(self.archetype_id)
        if (self.range_family, self.scenario) != (
            archetype.range_family,
            archetype.scenario,
        ):
            raise ValueError("archetype and range/scenario disagree")
        expected_compiler = (
            saas.SAAS_V2_COMPILER_VERSION
            if self.range_family == "saas"
            else enterprise.COMPILER_VERSION
        )
        if self.range_compiler_version != expected_compiler:
            raise ValueError("range compiler version disagrees with implementation")
        roots = SAAS_ROOTS if self.range_family == "saas" else ENTERPRISE_ROOTS
        expected = (
            ()
            if self.variant == "patched"
            else (
                (archetype.root_slug,) if self.task_mode == "scoped_audit" else tuple(sorted(roots))
            )
        )
        if self.active_property_slugs != expected:
            raise ValueError("active properties disagree with mode, variant, or archetype")
        if self.public_objective_id != (
            "scoped-v1" if self.task_mode == "scoped_audit" else "unguided-v1"
        ):
            raise ValueError("objective ID disagrees with task mode")
        if self.public_surface_id != (
            "saas-public-v1" if self.range_family == "saas" else "enterprise-public-v1"
        ):
            raise ValueError("surface ID disagrees with range")
        if (
            self.semantic_template_group
            != f"{self.range_family}:{self.scenario}:{self.archetype_id}"
        ):
            raise ValueError("semantic template group disagrees with archetype")
        if self.difficulty_factors.design_level != self.difficulty_config_id:
            raise ValueError("difficulty metadata disagrees with configuration")
        if self.task_id != self.derived_task_id():
            raise ValueError("task identity does not match canonical definition")
        return self

    def derived_task_id(self) -> str:
        return "t0-" + canonical_sha256(self.model_dump(mode="json", exclude={"task_id"}))


class PublicTaskEnvelope(StrictModel):
    """Only this type may be used for agent-visible task metadata."""

    schema_version: Literal["1"] = "1"
    benchmark_version: Literal["0.1.0-draft"] = BENCHMARK_VERSION
    task_id: str = Field(pattern=r"^t0-[0-9a-f]{64}$")
    task_mode: TaskMode
    range_family: Literal["saas", "enterprise_change_control_v1"]
    public_objective: str = Field(min_length=1)
    public_surface_id: Literal["saas-public-v1", "enterprise-public-v1"]

    @model_validator(mode="after")
    def registered_objective_only(self) -> PublicTaskEnvelope:
        if self.task_mode == "unguided_audit":
            valid = self.public_objective == UNGUIDED_OBJECTIVES[self.range_family]
        else:
            valid = self.public_objective in {
                row.scoped_objective
                for row in ARCHETYPES.values()
                if row.range_family == self.range_family
            }
        if not valid:
            raise ValueError("public objective is not registered for mode and range")
        return self


class ControllerPrivateTaskBinding(StrictModel):
    """Never serialize this binding into model prompts or ordinary tool responses."""

    schema_version: Literal["1"] = "1"
    task_id: str
    expected_root_slug: str
    range_spec: RangeSpec
    source_sha256: dict[str, str]
    verifier_contract_id: Literal["canonical-v1"] = "canonical-v1"

    @model_validator(mode="after")
    def valid_source_hashes(self) -> ControllerPrivateTaskBinding:
        if not self.source_sha256 or any(
            len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            for digest in self.source_sha256.values()
        ):
            raise ValueError("private binding requires valid source SHA-256 pins")
        return self


class TaskManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    task: BenchmarkTaskSpec
    public: PublicTaskEnvelope
    private: ControllerPrivateTaskBinding

    @model_validator(mode="after")
    def consistent_identity(self) -> TaskManifest:
        if len({self.task.task_id, self.public.task_id, self.private.task_id}) != 1:
            raise ValueError("task identity mismatch across artifacts")
        if self.public.task_mode != self.task.task_mode:
            raise ValueError("public mode does not match private task")
        if self.private.range_spec.seed != self.task.seed:
            raise ValueError("range seed does not match task")
        archetype = get_archetype(self.task.archetype_id)
        if self.private.expected_root_slug != archetype.root_slug:
            raise ValueError("private root does not match archetype")
        if (self.private.range_spec.family, self.private.range_spec.scenario) != (
            self.task.range_family,
            self.task.scenario,
        ):
            raise ValueError("private range does not match task")
        roots = SAAS_ROOTS if self.task.range_family == "saas" else ENTERPRISE_ROOTS
        expected_patches = (
            ()
            if self.task.variant == "patched" or self.task.task_mode == "unguided_audit"
            else tuple(sorted(roots - {archetype.root_slug}))
        )
        if (
            self.private.range_spec.patched != (self.task.variant == "patched")
            or self.private.range_spec.patched_properties != expected_patches
        ):
            raise ValueError("private patch selection does not match task")
        if (
            self.public.range_family != self.task.range_family
            or self.public.public_surface_id != self.task.public_surface_id
        ):
            raise ValueError("public range or surface does not match task")
        return self
