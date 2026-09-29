"""Paired fixture and hidden oracle compiler checks."""

import json
from pathlib import Path

import pytest
import yaml

from offsecgym.runtime.manifests import BuildIntegrityError, StateStore
from offsecgym.runtime.saas import SaasRangeCompiler, fixture_for_seed
from offsecgym.schemas.attack_graph import AttackGraphManifest
from offsecgym.schemas.ground_truth import GroundTruthManifest
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
    selective = compiler.build(
        load_spec().model_copy(update={"patched_properties": ("DOC-CROSS-TENANT-READ",)})
    )
    assert compiler.build(load_spec()) == vulnerable
    assert len({vulnerable.build_id, selective.build_id, patched.build_id}) == 3
    assert vulnerable.pair_id == selective.pair_id == patched.pair_id
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
        ground_truth = GroundTruthManifest.model_validate_json(
            (oracle_dir / "ground_truth.json").read_bytes()
        )
        assert len(ground_truth.properties) == 5
        assert sum(item.active for item in ground_truth.properties) == (
            0 if manifest.spec.patched else 5
        )
        graph = AttackGraphManifest.model_validate_json(
            (oracle_dir / "attack_graph.json").read_bytes()
        )
        graph.validate_against_ground_truth(ground_truth)
        assert len(graph.edges) >= 10
        assert {item.property_id for item in ground_truth.properties} <= {
            edge.property_id for edge in graph.edges
        }


def test_saas_compiler_rejects_unsupported_surface(tmp_path: Path) -> None:
    compiler = SaasRangeCompiler(StateStore(tmp_path))
    with pytest.raises(ValueError, match="documented topology"):
        compiler.build(load_spec().model_copy(update={"topology": {"saas": True, "redis": True}}))
    with pytest.raises(ValueError, match="five declared"):
        compiler.build(load_spec().model_copy(update={"vulnerabilities": ()}))


@pytest.mark.parametrize(
    "relative_path",
    (
        "builds/fixture.json",
        "builds/compose.yaml",
        "oracles/ground_truth.json",
        "oracles/attack_graph.json",
    ),
)
def test_saas_reuse_fails_closed_on_modified_artifact(tmp_path: Path, relative_path: str) -> None:
    state = StateStore(tmp_path)
    compiler = SaasRangeCompiler(state)
    spec = load_spec()
    manifest = compiler.build(spec)
    category, name = relative_path.split("/")
    directory = (
        state.build_dir(manifest.build_id)
        if category == "builds"
        else (tmp_path / "oracles" / manifest.build_id.hex)
    )
    artifact = directory / name
    tampered = artifact.read_bytes() + b"\n"
    artifact.write_bytes(tampered)
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        state.verify_build_integrity(manifest.build_id)
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        compiler.build(spec)
    assert artifact.read_bytes() == tampered
