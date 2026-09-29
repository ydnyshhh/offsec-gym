"""Persistent build and range-instance metadata."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import RangeSpec


class BuildManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    build_id: UUID
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_name: str
    spec: RangeSpec


class InstanceManifest(StrictModel):
    schema_version: Literal["1"] = "1"
    instance_id: UUID
    build_id: UUID
    project_name: str
    nonce: UUID
    state: Literal["starting", "healthy", "unhealthy", "stopped", "destroyed"]
    created_at: datetime
    updated_at: datetime
    image_id: str | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def build_dir(self, build_id: UUID) -> Path:
        return self.root / "builds" / build_id.hex

    def instance_dir(self, instance_id: UUID) -> Path:
        return self.root / "instances" / instance_id.hex

    def load_build(self, build_id: UUID) -> BuildManifest:
        path = self.build_dir(build_id) / "manifest.json"
        if not path.is_file():
            raise ValueError(f"unknown range build: {build_id}")
        manifest = BuildManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.build_id != build_id:
            raise ValueError("range build ID does not match its manifest path")
        return manifest

    def load_instance(self, instance_id: UUID) -> InstanceManifest:
        path = self.instance_dir(instance_id) / "manifest.json"
        if not path.is_file():
            raise ValueError(f"unknown range instance: {instance_id}")
        manifest = InstanceManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            manifest.instance_id != instance_id
            or manifest.project_name != f"offsecgym_{instance_id.hex}"
        ):
            raise ValueError("range instance identity does not match its manifest path")
        return manifest

    def save_instance(self, manifest: InstanceManifest) -> None:
        write_json_atomic(
            self.instance_dir(manifest.instance_id) / "manifest.json",
            manifest.model_dump(mode="json"),
        )
