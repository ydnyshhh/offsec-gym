"""Restricted, verifiable records of the exact provider JSON exchanged by a run."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from offsecgym.runtime.manifests import write_json_atomic


def payload_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ModelCallArtifacts:
    def __init__(self, state_root: Path) -> None:
        self.root = state_root / "model_calls"

    def _path(self, run_id: UUID, call_id: UUID, kind: Literal["request", "response"]) -> Path:
        return self.root / run_id.hex / call_id.hex / f"{kind}.json"

    def write(
        self,
        run_id: UUID,
        call_id: UUID,
        kind: Literal["request", "response"],
        payload: dict[str, object],
    ) -> tuple[UUID, str]:
        path = self._path(run_id, call_id, kind)
        for directory in (self.root, path.parent.parent, path.parent):
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
                raise OSError(f"model artifact directory is not private: {directory}")
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"model artifact already exists: {path}")
        artifact_id = uuid4()
        digest = payload_sha256(payload)
        write_json_atomic(
            path,
            {
                "artifact_id": str(artifact_id),
                "run_id": str(run_id),
                "call_id": str(call_id),
                "kind": kind,
                "payload": payload,
            },
        )
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise OSError(f"model artifact is not private: {path}")
        return artifact_id, digest

    def read_verified(
        self,
        run_id: UUID,
        call_id: UUID,
        kind: Literal["request", "response"],
        artifact_id: UUID,
        expected_sha256: str,
    ) -> dict[str, object]:
        path = self._path(run_id, call_id, kind)
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise OSError(f"model artifact is missing or unsafe: {path}")
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or set(record) != {"artifact_id", "run_id", "call_id", "kind", "payload"}
            or any(
                record.get(key) != expected
                for key, expected in (
                    ("artifact_id", str(artifact_id)),
                    ("run_id", str(run_id)),
                    ("call_id", str(call_id)),
                    ("kind", kind),
                )
            )
        ):
            raise ValueError("model artifact provenance mismatch")
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload_sha256(payload) != expected_sha256:
            raise ValueError("model artifact digest mismatch")
        return payload
