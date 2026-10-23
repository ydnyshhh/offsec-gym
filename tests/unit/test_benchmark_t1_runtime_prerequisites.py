"""Conservative status contract for benign runtime prerequisite checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from offsecgym.benchmark import runtime_prerequisites as module
from offsecgym.benchmark.runtime_prerequisites import CHECKS, RuntimePrerequisiteRecord


def _record(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": "t0-" + "0" * 64,
        "archetype_id": "A1",
        "seed": 42,
        "variant": "vulnerable",
        "checks": {name: True for name in CHECKS},
        "status": "pass",
        "real_docker_started": True,
    }
    payload.update(changes)
    return payload


def test_prerequisite_pass_never_upgrades_q2_or_claims_model_work() -> None:
    record = RuntimePrerequisiteRecord.model_validate(_record())
    assert record.status == "pass"
    assert record.q2_gate == "not_evaluated"
    assert record.provider_calls == 0
    assert record.q2_missing


def test_prerequisite_pass_requires_every_check_and_no_error() -> None:
    incomplete = {name: True for name in CHECKS}
    incomplete["teardown"] = False
    with pytest.raises(ValidationError):
        RuntimePrerequisiteRecord.model_validate(_record(checks=incomplete))
    with pytest.raises(ValidationError):
        RuntimePrerequisiteRecord.model_validate(_record(failure_class="DockerCommandError"))
    with pytest.raises(ValidationError):
        RuntimePrerequisiteRecord.model_validate(_record(q2_gate="pass"))
    with pytest.raises(ValidationError):
        RuntimePrerequisiteRecord.model_validate(_record(provider_calls=1))


@pytest.mark.parametrize(
    ("container_ids", "port_bindings", "expected"),
    [
        ("", "{}", False),
        ("container-1\n", '{"8080/tcp":null}', True),
        ("container-1\n", '{"8080/tcp":[{"HostIp":"0.0.0.0","HostPort":"8080"}]}', False),
    ],
)
async def test_network_check_rejects_missing_or_published_containers(
    monkeypatch: pytest.MonkeyPatch,
    container_ids: str,
    port_bindings: str,
    expected: bool,
) -> None:
    async def fake_run_command(arguments: list[str], **_kwargs: object) -> str:
        if arguments[:3] == ["docker", "network", "inspect"]:
            return "true\n"
        if arguments[:2] == ["docker", "ps"]:
            return container_ids
        if arguments[:2] == ["docker", "inspect"]:
            return port_bindings
        raise AssertionError("unexpected Docker command")

    monkeypatch.setattr(module, "run_command", fake_run_command)
    assert await module._contained_project("synthetic") is expected
