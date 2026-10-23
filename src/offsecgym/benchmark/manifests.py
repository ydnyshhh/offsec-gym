"""Deterministic, non-executing task manifest generation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from offsecgym.benchmark.difficulty import existing_factors
from offsecgym.benchmark.registry import (
    ENTERPRISE_ROOTS,
    SAAS_ROOTS,
    UNGUIDED_OBJECTIVES,
    get_archetype,
)
from offsecgym.benchmark.tasks import (
    BenchmarkSplit,
    BenchmarkTaskSpec,
    ControllerPrivateTaskBinding,
    PublicTaskEnvelope,
    TaskManifest,
    TaskMode,
    TaskVariant,
)
from offsecgym.runtime import enterprise, saas
from offsecgym.schemas import ground_truth, specs
from offsecgym.schemas.specs import RangeSpec, VulnerabilitySpec
from offsecgym.validation import deterministic


def source_pins() -> dict[str, str]:
    paths = {
        "saas_compiler": Path(saas.__file__),
        "enterprise_compiler": Path(enterprise.__file__),
        "range_schema": Path(specs.__file__),
        "ground_truth_schema": Path(ground_truth.__file__),
        "deterministic_validator": Path(deterministic.__file__),
        "registry": Path(__file__).with_name("registry.py"),
        "task_schema": Path(__file__).with_name("tasks.py"),
        "difficulty_schema": Path(__file__).with_name("difficulty.py"),
        "stage_schema": Path(__file__).with_name("stages.py"),
    }
    for family in ("saas", "enterprise"):
        template_dir = Path(saas.__file__).parent / "templates" / family
        for path in sorted(template_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                paths[f"{family}_template_{path.name}"] = path
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(paths.items())
    }


def range_spec_for(archetype_id: str, mode: TaskMode, variant: TaskVariant, seed: int) -> RangeSpec:
    archetype = get_archetype(archetype_id)
    if archetype.range_family == "saas":
        roots, topology, identities, vulnerabilities = (
            SAAS_ROOTS,
            saas.TOPOLOGY,
            saas.IDENTITIES,
            saas.VULNERABILITIES,
        )
    else:
        roots, topology, identities, vulnerabilities = (
            ENTERPRISE_ROOTS,
            enterprise.TOPOLOGY,
            enterprise.IDENTITIES,
            enterprise.VULNERABILITIES,
        )
    patched = variant == "patched"
    patches = (
        () if patched or mode == "unguided_audit" else tuple(sorted(roots - {archetype.root_slug}))
    )
    spec = RangeSpec(
        family=archetype.range_family,
        scenario=archetype.scenario,
        seed=seed,
        patched=patched,
        patched_properties=patches,
        topology=topology,
        identities=identities,
        vulnerabilities=tuple(
            VulnerabilitySpec(family=family, component=component, variant=name)
            for family, component, name in sorted(vulnerabilities)
        ),
    )
    if archetype.range_family == "saas":
        saas.validate_saas_spec(spec)
    else:
        enterprise.validate_spec(spec)
    return spec


def _fixture_counts(family: str, seed: int) -> tuple[int, int, int]:
    if family == "saas":
        fixture = saas.fixture_for_seed_v2(seed)
        identities = sum(saas.IDENTITIES.values())
        services = sum(saas.TOPOLOGY.values())
    else:
        fixture, _anchors = enterprise.fixture_for_seed(seed)
        identities = sum(enterprise.IDENTITIES.values())
        services = sum(enterprise.TOPOLOGY.values())
    entities = sum(len(value) for value in fixture.values() if isinstance(value, list))
    return entities, identities, services


def make_task_manifest(
    archetype_id: str,
    *,
    task_mode: TaskMode,
    variant: TaskVariant,
    seed: int,
    benchmark_split: BenchmarkSplit = "development",
) -> TaskManifest:
    """Build metadata only; no Docker, gateway, provider, or model is started."""

    archetype = get_archetype(archetype_id)
    range_spec = range_spec_for(archetype_id, task_mode, variant, seed)
    roots = SAAS_ROOTS if archetype.range_family == "saas" else ENTERPRISE_ROOTS
    active = (
        ()
        if variant == "patched"
        else ((archetype.root_slug,) if task_mode == "scoped_audit" else tuple(sorted(roots)))
    )
    entities, identities, services = _fixture_counts(archetype.range_family, seed)
    fields = dict(
        archetype_id=archetype_id,
        range_family=archetype.range_family,
        scenario=archetype.scenario,
        range_compiler_version=(
            saas.SAAS_V2_COMPILER_VERSION
            if archetype.range_family == "saas"
            else enterprise.COMPILER_VERSION
        ),
        task_mode=task_mode,
        difficulty_config_id="L1-existing",
        seed=seed,
        variant=variant,
        active_property_slugs=active,
        public_objective_id="scoped-v1" if task_mode == "scoped_audit" else "unguided-v1",
        public_surface_id=(
            "saas-public-v1" if archetype.range_family == "saas" else "enterprise-public-v1"
        ),
        difficulty_factors=existing_factors(entities, identities, services),
        benchmark_split=benchmark_split,
        semantic_template_group=(f"{archetype.range_family}:{archetype.scenario}:{archetype_id}"),
    )
    provisional = BenchmarkTaskSpec.model_construct(task_id="", **fields)
    task = BenchmarkTaskSpec(task_id=provisional.derived_task_id(), **fields)
    objective = (
        archetype.scoped_objective
        if task_mode == "scoped_audit"
        else UNGUIDED_OBJECTIVES[archetype.range_family]
    )
    public = PublicTaskEnvelope(
        task_id=task.task_id,
        task_mode=task_mode,
        range_family=archetype.range_family,
        public_objective=objective,
        public_surface_id=task.public_surface_id,
    )
    private = ControllerPrivateTaskBinding(
        task_id=task.task_id,
        expected_root_slug=archetype.root_slug,
        range_spec=range_spec,
        source_sha256=source_pins(),
    )
    return TaskManifest(task=task, public=public, private=private)
