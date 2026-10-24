"""Shared disposable, no-provider A1 qualification runtime primitives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from offsecgym.benchmark.runtime_prerequisites import _project_removed
from offsecgym.runtime.compose import ComposeRangeRuntime


class MemoryEvents:
    def __init__(self) -> None:
        self.items: list[object] = []

    async def append(self, event):
        saved = event.model_copy(update={"sequence_number": len(self.items) + 1})
        self.items.append(saved)
        return saved

    async def read_run(self, run_id: UUID):
        return tuple(item for item in self.items if item.run_id == run_id)


def private_write(path: Path, payload: object) -> None:
    """Create exactly once, refusing links and preserving mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    data = (json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def require_private_root(path: Path, checkout: Path) -> Path:
    """Constrain sensitive qualification artifacts to this checkout's ignored root."""
    allowed = (checkout / ".offsecgym").resolve()
    resolved = path.resolve()
    if path.is_symlink() or os.path.commonpath((allowed, resolved)) != str(allowed):
        raise ValueError("private output must be inside this checkout's .offsecgym")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


QUALIFICATION_SOURCE_FILES = (
    "src/offsecgym/benchmark/a1_admission.py",
    "src/offsecgym/benchmark/a1_public.py",
    "src/offsecgym/benchmark/a1_reference_record.py",
    "src/offsecgym/benchmark/a1_runtime.py",
    "src/offsecgym/benchmark/a1_scoring.py",
    "src/offsecgym/benchmark/a1_stages.py",
    "src/offsecgym/benchmark/q2_runtime_qualification.py",
    "research_ops/benchmark_t11b_q2_qualify.py",
    "research_ops/benchmark_t11c_q3.py",
    "research_ops/benchmark_t11d_conformance.py",
    "research_ops/benchmark_t11e_reference.py",
    "research_ops/benchmark_t11f_admission.py",
)


def qualification_source_pins(checkout: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((checkout / name).read_bytes()).hexdigest()
        for name in QUALIFICATION_SOURCE_FILES
    }


async def destroy_owned(runtime: ComposeRangeRuntime, instance_id: UUID, project: str) -> bool:
    status = await runtime.destroy_instance(instance_id)
    return status.state == "destroyed" and await _project_removed(project)
