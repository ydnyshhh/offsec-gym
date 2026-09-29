from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from offsecgym.runtime.compiler import HelloRangeCompiler
from offsecgym.runtime.compose import DockerCommandError, _check_owner
from offsecgym.runtime.manifests import InstanceManifest, StateStore, utc_now
from offsecgym.schemas.specs import RangeSpec


def test_hello_build_is_reproducible_and_unpublished(tmp_path: Path) -> None:
    compiler = HelloRangeCompiler(StateStore(tmp_path))
    spec = RangeSpec(family="hello", scenario="health_check", seed=42, topology={"hello": True})
    first = compiler.build(spec)
    second = compiler.build(spec)
    assert first == second
    assert compiler.state.verify_build_integrity(first.build_id) == first
    assert set(first.artifact_digests) == {
        "Dockerfile",
        "compose.yaml",
        "gateway_idle.py",
        "hello_service.py",
        "http_worker.py",
    }
    assert first.oracle_artifact_digests == {}
    bundle = tmp_path / "builds" / first.build_id.hex
    compose = yaml.safe_load((bundle / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["networks"]["range"]["internal"] is True
    assert set(compose["services"]) == {"hello", "gateway"}
    assert all("ports" not in service for service in compose["services"].values())
    assert all(service["read_only"] for service in compose["services"].values())
    assert compiler.build(spec.model_copy(update={"seed": 43})).build_id != first.build_id


def test_hello_compiler_rejects_unsupported_surface(tmp_path: Path) -> None:
    compiler = HelloRangeCompiler(StateStore(tmp_path))
    spec = RangeSpec(
        family="hello",
        scenario="health_check",
        seed=42,
        topology={"hello": True, "internet": True},
    )
    with pytest.raises(ValueError, match="exactly one"):
        compiler.build(spec)


def test_lifecycle_rejects_resource_with_foreign_owner() -> None:
    instance_id = uuid4()
    instance = InstanceManifest(
        instance_id=instance_id,
        build_id=uuid4(),
        project_name=f"offsecgym_{instance_id.hex}",
        nonce=uuid4(),
        state="healthy",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    labels = {"org.offsecgym.managed": "true", "org.offsecgym.instance_id": str(instance_id)}
    _check_owner(labels, instance)
    with pytest.raises(DockerCommandError, match="not owned"):
        _check_owner({**labels, "org.offsecgym.instance_id": str(uuid4())}, instance)
