"""Paired fixture and hidden oracle compiler checks."""

import json
from pathlib import Path

import pytest
import yaml

from offsecgym.runtime.manifests import StateStore
from offsecgym.runtime.saas import SaasRangeCompiler, fixture_for_seed
from offsecgym.schemas.specs import RangeSpec


def load_spec(patched: bool = False) -> RangeSpec:
    name = "saas-range-patched.yaml" if patched else "saas-range.yaml"
    path = Path(__file__).parents[2] / "examples" / name
    return RangeSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_saas_paired_builds_share_fixture_and_hide_oracle(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    compiler = SaasRangeCompiler(state)
    vulnerable = compiler.build(load_spec())
    patched = compiler.build(load_spec(patched=True))
    assert compiler.build(load_spec()) == vulnerable
    assert vulnerable.build_id != patched.build_id
    assert vulnerable.pair_id == patched.pair_id
    fixture_path = state.build_dir(vulnerable.build_id) / "fixture.json"
    patched_fixture = state.build_dir(patched.build_id) / "fixture.json"
    assert fixture_path.read_bytes() == patched_fixture.read_bytes()
    assert json.loads(fixture_path.read_text()) == fixture_for_seed(42)
    assert fixture_for_seed(43) != fixture_for_seed(42)
    for manifest in (vulnerable, patched):
        bundle = state.build_dir(manifest.build_id)
        oracle_dir = tmp_path / "oracles" / manifest.build_id.hex
        assert not (bundle / "ground_truth.json").exists()
        assert not (bundle / "attack_graph.json").exists()
        assert (oracle_dir / "ground_truth.json").stat().st_mode & 0o777 == 0o600
        ground_truth = json.loads((oracle_dir / "ground_truth.json").read_text())
        assert len(ground_truth["properties"]) == 5
        assert len(ground_truth["active_property_ids"]) == (0 if manifest.spec.patched else 5)
        graph = json.loads((oracle_dir / "attack_graph.json").read_text())
        assert len(graph["edges"]) >= 5
        assert {item["id"] for item in ground_truth["properties"]} <= {
            edge["property_id"] for edge in graph["edges"]
        }


def test_saas_compiler_rejects_unsupported_surface(tmp_path: Path) -> None:
    compiler = SaasRangeCompiler(StateStore(tmp_path))
    with pytest.raises(ValueError, match="documented topology"):
        compiler.build(load_spec().model_copy(update={"topology": {"saas": True, "redis": True}}))
    with pytest.raises(ValueError, match="five declared"):
        compiler.build(load_spec().model_copy(update={"vulnerabilities": ()}))
