"""Frozen, detached R0 pilot plan. This module never starts a range or provider."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.benchmark.a1_public import digest
from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.registry import get_archetype
from offsecgym.benchmark.tasks import canonical_sha256
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import Budget, ExperimentSpec, ModelSpec
from offsecgym.solver.monolithic import model_tools
from offsecgym.solver.range_surface import range_surface

PROTOCOL_VERSION = "r0-a1-model-pilot-v1"
SEEDS = (42, 43, 44)
VARIANTS = ("vulnerable", "patched")
REQUIRED_LIMITS = (
    "max_model_calls",
    "max_total_tokens",
    "max_output_tokens_per_call",
    "max_actions",
    "max_http_requests",
    "max_wall_seconds",
    "max_cost_usd",
)


class PilotProtocol(StrictModel):
    version: Literal["r0-a1-model-pilot-v1"] = PROTOCOL_VERSION
    archetype_id: Literal["A1"] = "A1"
    scenario: Literal["tenant_boundary_v2"] = "tenant_boundary_v2"
    seeds: tuple[int, int, int] = SEEDS
    variants: tuple[Literal["vulnerable", "patched"], ...] = VARIANTS
    public_objective: str = Field(min_length=1)
    account_visibility: Literal["white_box_accounts"] = "white_box_accounts"
    surface_visibility: Literal["known_routes"] = "known_routes"
    visibility_contract_id: Literal["t11c-a1-public-visibility-v2"] = "t11c-a1-public-visibility-v2"
    route_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    orchestrator: Literal["monolithic"] = "monolithic"
    memory: Literal["none"] = "none"
    validation: Literal["deterministic"] = "deterministic"
    repetitions: Literal[1] = 1
    budget: Budget
    models: tuple[ModelSpec, ...] = ()
    source_pin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def frozen_contract(self) -> PilotProtocol:
        if self.seeds != SEEDS or self.variants != VARIANTS:
            raise ValueError("R0 development cells must be exactly seeds 42–44 by both variants")
        if self.public_objective != get_archetype("A1").scoped_objective:
            raise ValueError("pilot objective differs from frozen A1 objective")
        if any(getattr(self.budget, field) is None for field in REQUIRED_LIMITS):
            raise ValueError("all seven pilot budget ceilings must be set")
        if self.source_pin_sha256 != canonical_sha256(source_pins()):
            raise ValueError("pilot source pins differ from this checkout")
        if self.route_inventory_sha256 != digest(range_surface("saas").routes):
            raise ValueError("pilot route inventory differs from frozen visibility")
        if self.tool_schema_sha256 != digest(model_tools(family="saas", structured=False)):
            raise ValueError("pilot tool schema differs from frozen visibility")
        identities = [(model.provider, model.name, model.reasoning) for model in self.models]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate model configuration")
        return self

    def experiment_specs(self) -> tuple[ExperimentSpec, ...]:
        """Validate proposed specs without executing them."""
        return tuple(
            ExperimentSpec(
                name=f"{self.version}:{seed}:{variant}:{model.provider}:{model.name}",
                seed=seed,
                range=manifest.private.range_spec,
                budget=self.budget,
                orchestrator=self.orchestrator,
                memory=self.memory,
                validation=self.validation,
                surface_visibility=self.surface_visibility,
                model=model,
            )
            for model in self.models
            for seed in self.seeds
            for variant in self.variants
            for manifest in (
                make_task_manifest("A1", task_mode="scoped_audit", seed=seed, variant=variant),
            )
        )


def load_protocol(path: Path) -> PilotProtocol:
    import yaml

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("pilot protocol must be a mapping")
    return PilotProtocol.model_validate(data)


def admission_baseline(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    report = json.loads(raw)
    normalized = report.pop("normalized_sha256", None)
    if normalized != canonical_sha256(report):
        raise ValueError("A1 admission digest mismatch")
    if report.get("admitted") is not True or report.get("source_sha256") != source_pins():
        raise ValueError("A1 admission or source pins are not current")
    expected = {
        (seed, variant): make_task_manifest(
            "A1", task_mode="scoped_audit", seed=seed, variant=variant
        ).task.task_id
        for seed in SEEDS
        for variant in VARIANTS
    }
    observed = {(cell["seed"], cell["variant"]): cell["task_id"] for cell in report["cells"]}
    if observed != expected:
        raise ValueError("A1 admission task identities differ")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "cells": len(observed)}


def dry_run(protocol: PilotProtocol, admission_path: Path) -> dict[str, object]:
    baseline = admission_baseline(admission_path)
    cells = [
        {
            "seed": seed,
            "variant": variant,
            "task_id": make_task_manifest(
                "A1", task_mode="scoped_audit", seed=seed, variant=variant
            ).task.task_id,
        }
        for seed in protocol.seeds
        for variant in protocol.variants
    ]
    # The existing controller supplies a generic range-wide goal. Offline planning
    # must not claim that it ran the scoped objective.
    return {
        "protocol_version": protocol.version,
        "protocol_sha256": canonical_sha256(protocol),
        "admission": baseline,
        "cells": cells,
        "model_count": len(protocol.models),
        "proposed_run_count": len(protocol.experiment_specs()),
        "models": [
            {"provider": row.provider, "name": row.name, "reasoning": row.reasoning}
            for row in protocol.models
        ],
        "budget": protocol.budget.model_dump(mode="json"),
        "route_inventory_sha256": protocol.route_inventory_sha256,
        "tool_schema_sha256": protocol.tool_schema_sha256,
        "visibility_contract_id": protocol.visibility_contract_id,
        "scoped_task_parity": False,
        "controller_memory_supported": False,
        "rendered_model_context_parity": "not_evaluated",
        "execution_blockers": [
            "generic_controller_objective",
            "unsupported_monolithic_memory_none",
        ],
        "execution_status": "blocked_generic_controller_objective",
        "provider_calls": 0,
    }
