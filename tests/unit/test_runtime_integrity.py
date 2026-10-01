"""Generated artifacts are immutable inputs to the runtime."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from offsecgym.runtime.compiler import HelloRangeCompiler
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import (
    BuildIntegrityError,
    StateStore,
    UnknownInstanceError,
    UnsupportedManifestVersionError,
)
from offsecgym.schemas.specs import RangeSpec


def _hold_process_lock(root: str, instance_id: UUID, entered, release) -> None:
    async def hold() -> None:
        runtime = ComposeRangeRuntime(Path(root))
        async with runtime.get_instance_guard(instance_id):
            entered.set()
            await asyncio.to_thread(release.wait)

    asyncio.run(hold())


def _crash_with_process_lock(root: str, instance_id: UUID, entered) -> None:
    async def hold() -> None:
        runtime = ComposeRangeRuntime(Path(root))
        async with runtime.get_instance_guard(instance_id):
            entered.set()
            os._exit(0)

    asyncio.run(hold())


def hello_spec() -> RangeSpec:
    return RangeSpec(family="hello", scenario="health_check", seed=42, topology={"hello": True})


@pytest.mark.asyncio
async def test_instance_guard_waits_for_other_process_and_survives_crash(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    instance_id = uuid4()
    entered, release = context.Event(), context.Event()
    holder = context.Process(
        target=_hold_process_lock,
        args=(str(tmp_path), instance_id, entered, release),
    )
    holder.start()
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        runtime = ComposeRangeRuntime(tmp_path)
        acquired = asyncio.Event()

        async def compete() -> None:
            async with runtime.get_instance_guard(instance_id):
                acquired.set()

        waiting = asyncio.create_task(compete())
        await asyncio.sleep(0.1)
        assert not acquired.is_set()
        release.set()
        await asyncio.wait_for(waiting, 10)
        assert acquired.is_set()
    finally:
        release.set()
        await asyncio.to_thread(holder.join, 10)
        if holder.is_alive():
            holder.terminate()
            await asyncio.to_thread(holder.join, 5)
    assert holder.exitcode == 0

    crash_entered = context.Event()
    crashed = context.Process(
        target=_crash_with_process_lock,
        args=(str(tmp_path), instance_id, crash_entered),
    )
    crashed.start()
    await asyncio.to_thread(crashed.join, 10)
    assert crashed.exitcode == 0
    async with ComposeRangeRuntime(tmp_path).get_instance_guard(instance_id):
        pass


@pytest.mark.parametrize("name", ["compose.yaml", "hello_service.py", "Dockerfile"])
def test_hello_tamper_blocks_reuse_and_instance_creation(tmp_path: Path, name: str) -> None:
    state = StateStore(tmp_path)
    compiler = HelloRangeCompiler(state)
    manifest = compiler.build(hello_spec())
    artifact = state.build_dir(manifest.build_id) / name
    artifact.write_bytes(artifact.read_bytes() + b"\n# tampered\n")
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        state.verify_build_integrity(manifest.build_id)
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        compiler.build(hello_spec())


@pytest.mark.parametrize("name", ["compose.yaml", "hello_service.py"])
async def test_tamper_blocks_start_before_docker(tmp_path: Path, name: str, monkeypatch) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(hello_spec())
    instance_id = await runtime.create_instance(build_id)
    artifact = runtime.state.build_dir(build_id) / name
    artifact.write_bytes(artifact.read_bytes() + b"\n# tampered\n")

    async def docker_must_not_run(*_args, **_kwargs):
        raise AssertionError("Docker was invoked before integrity verification")

    monkeypatch.setattr("offsecgym.runtime.compose.run_command", docker_must_not_run)
    with pytest.raises(BuildIntegrityError, match="digest mismatch"):
        await runtime.start_instance(instance_id)


async def test_build_and_instance_ids_have_distinct_operations(tmp_path: Path) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(hello_spec())
    instance_id = await runtime.create_instance(build_id)
    assert instance_id != build_id
    manifest = runtime.state.load_instance(instance_id)
    assert manifest.build_id == build_id
    assert manifest.generation == 0
    with pytest.raises(UnknownInstanceError):
        await runtime.instance_status(build_id)


async def test_generation_advances_only_on_reset(tmp_path: Path, monkeypatch) -> None:
    runtime = ComposeRangeRuntime(tmp_path)
    build_id = await runtime.build(hello_spec())
    instance_id = await runtime.create_instance(build_id)
    simulated = {"running": False}

    async def compose(_instance, *arguments, **_kwargs):
        if arguments[0] == "up":
            simulated["running"] = True
        elif arguments[0] in {"stop", "down"}:
            simulated["running"] = False
        elif arguments[0] == "ps":
            if simulated["running"]:
                return json.dumps(
                    [
                        {"Service": "hello", "State": "running", "Health": "healthy"},
                        {"Service": "gateway", "State": "running"},
                    ]
                )
            return "[]"
        return ""

    async def owned(_instance):
        return None

    async def command(*_args, **_kwargs):
        return "sha256:test-image\n"

    monkeypatch.setattr(runtime, "_compose", compose)
    monkeypatch.setattr(runtime, "_assert_owned", owned)
    monkeypatch.setattr("offsecgym.runtime.compose.run_command", command)
    started = await runtime.start_instance(instance_id)
    assert (started.state, started.generation) == ("healthy", 0)
    stopped = await runtime.stop_instance(instance_id)
    assert (stopped.state, stopped.generation) == ("stopped", 0)
    resumed = await runtime.start_instance(instance_id)
    assert (resumed.state, resumed.generation) == ("healthy", 0)
    reset = await runtime.reset_instance(instance_id)
    assert (reset.state, reset.generation) == ("healthy", 1)
    destroyed = await runtime.destroy_instance(instance_id)
    assert (destroyed.state, destroyed.generation) == ("destroyed", 1)
    repeated = await runtime.destroy_instance(instance_id)
    assert (repeated.state, repeated.generation) == ("destroyed", 1)


def test_old_build_manifest_requires_rebuild(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    manifest = HelloRangeCompiler(state).build(hello_spec())
    path = state.build_dir(manifest.build_id) / "manifest.json"
    raw = json.loads(path.read_text())
    raw["schema_version"] = "1"
    path.write_text(json.dumps(raw))
    with pytest.raises(UnsupportedManifestVersionError, match="rebuild"):
        state.verify_build_integrity(manifest.build_id)


def test_extra_build_file_is_rejected(tmp_path: Path) -> None:
    state = StateStore(tmp_path)
    manifest = HelloRangeCompiler(state).build(hello_spec())
    (state.build_dir(manifest.build_id) / "unexpected.txt").write_text("not part of build")
    with pytest.raises(BuildIntegrityError, match="artifact set differs"):
        state.verify_build_integrity(manifest.build_id)
