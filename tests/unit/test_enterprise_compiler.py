"""Range B determinism, selective patching, provenance, and static containment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.enterprise import (
    FAMILY,
    PROPERTY_SLUGS,
    fixture_for_seed,
)
from offsecgym.schemas.specs import RangeSpec


def spec() -> RangeSpec:
    return RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "examples/enterprise-change-control.yaml").read_text()
        )
    )


def test_seeded_fixture_is_realistic_and_semantically_varied() -> None:
    first, anchors = fixture_for_seed(42)
    repeated, repeated_anchors = fixture_for_seed(42)
    other, _ = fixture_for_seed(43)
    assert first == repeated and anchors == repeated_anchors
    assert first != other
    assert len(first["organizations"]) == 3
    assert len(first["projects"]) == 6
    assert len(first["users"]) == 18
    assert len(first["environments"]) == 18
    assert len(first["role_assignments"]) >= 20
    assert len(first["access_requests"]) >= 10
    assert len(first["changes"]) >= 10
    assert len(first["jobs"]) >= 5
    assert not any(
        "attacker" in user["username"] or "victim" in user["username"] for user in first["users"]
    )
    assert anchors["b2_actor"] != anchors["b2_admin"]
    assert set(anchors) == {
        "b1_actor",
        "b1_request",
        "b1_project",
        "b2_actor",
        "b2_admin",
        "b2_change",
        "b2_project",
        "b3_actor",
        "b3_change",
        "b3_environment",
    }


def test_pair_build_determinism_and_selective_patches(tmp_path: Path) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    base = spec()
    variants = [base, base.model_copy(update={"patched": True})]
    variants += [
        base.model_copy(update={"patched_properties": (slug,)}) for slug in sorted(PROPERTY_SLUGS)
    ]
    manifests = [runtime.enterprise_compiler.build(variant) for variant in variants]
    assert len({item.build_id for item in manifests}) == 5
    assert len({item.pair_id for item in manifests}) == 1
    assert [runtime.enterprise_compiler.build(variant) for variant in variants] == manifests
    fixture_hashes = {
        hashlib.sha256(
            (runtime.state.build_dir(item.build_id) / "fixture.json").read_bytes()
        ).hexdigest()
        for item in manifests
    }
    assert len(fixture_hashes) == 1
    assert all(runtime.state.verify_build_integrity(item.build_id) == item for item in manifests)
    for item, variant in zip(manifests, variants, strict=True):
        oracle = json.loads(
            (runtime.state.root / "oracles" / item.build_id.hex / "ground_truth.json").read_text()
        )
        graph = json.loads(
            (runtime.state.root / "oracles" / item.build_id.hex / "attack_graph.json").read_text()
        )
        topology = json.loads(
            (runtime.state.root / "oracles" / item.build_id.hex / "topology.json").read_text()
        )
        provenance = json.loads(
            (runtime.state.root / "oracles" / item.build_id.hex / "provenance.json").read_text()
        )
        inactive = {prop["slug"] for prop in oracle["properties"] if not prop["active"]}
        expected = PROPERTY_SLUGS if variant.patched else set(variant.patched_properties)
        assert inactive == expected
        assert (
            len(oracle["properties"])
            == len(graph["edges"]) // 3
            == len(topology["properties"])
            == 3
        )
        assert provenance["fixture_sha256"] in fixture_hashes
        assert provenance["pair_id"] == str(item.pair_id)
        assert provenance["build_id"] == str(item.build_id)
        assert provenance["compiler_version"]
        assert provenance["route_schema_sha256"]
        assert provenance["compose_sha256"]
    altered = base.model_copy(update={"seed": 43})
    other = runtime.enterprise_compiler.build(altered)
    assert other.pair_id != manifests[0].pair_id
    other_oracle = json.loads(
        (runtime.state.root / "oracles" / other.build_id.hex / "ground_truth.json").read_text()
    )
    assert {p["slug"] for p in other_oracle["properties"]} == PROPERTY_SLUGS


def test_no_hidden_property_leak_and_static_containment(tmp_path: Path) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    built = runtime.enterprise_compiler.build(spec())
    build_dir = runtime.state.build_dir(built.build_id)
    fixture = (build_dir / "fixture.json").read_text()
    compose = yaml.safe_load((build_dir / "compose.yaml").read_text())
    assert all(slug not in fixture for slug in PROPERTY_SLUGS)
    assert "hidden_root" not in fixture and "is_vulnerable" not in fixture
    assert compose["networks"]["range"]["internal"] is True
    assert set(compose["services"]) == {
        "postgres",
        "cache",
        "init",
        "identity",
        "change",
        "worker",
        "gateway",
    }
    for service in compose["services"].values():
        assert not service.get("privileged", False)
        assert not service.get("ports")
        assert not service.get("network_mode") == "host"
        assert all(
            "docker.sock" not in str(value) and "/var/run" not in str(value)
            for value in service.get("volumes", [])
        )
        assert service["networks"] == ["range"]
        assert all(
            "B1" not in str(value) and "B2" not in str(value) and "B3" not in str(value)
            for value in service.get("environment", {}).values()
        )


def test_invalid_enterprise_spec_is_rejected(tmp_path: Path) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    with pytest.raises(ValueError, match="three declared"):
        runtime.enterprise_compiler.build(spec().model_copy(update={"vulnerabilities": ()}))
    with pytest.raises(ValueError, match="unknown"):
        runtime.enterprise_compiler.build(
            spec().model_copy(update={"patched_properties": ("UNKNOWN",)})
        )
    assert spec().family == FAMILY
