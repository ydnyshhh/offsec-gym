"""Persistent build and range-instance metadata."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, field_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import RangeSpec

Digest = str
EXPECTED_BUNDLE_FILES = {
    "hello": frozenset(
        {"Dockerfile", "compose.yaml", "gateway_idle.py", "hello_service.py", "http_worker.py"}
    ),
    "saas": frozenset(
        {
            "Dockerfile",
            "compose.yaml",
            "fixture.json",
            "gateway_idle.py",
            "http_worker.py",
            "saas_service.py",
        }
    ),
    "enterprise_change_control_v1": frozenset(
        {
            "Dockerfile",
            "compose.yaml",
            "fixture.json",
            "implementation.json",
            "schema.sql",
            "init_db.py",
            "service_common.py",
            "pg_client.py",
            "cache_client.py",
            "cache_service.py",
            "identity_service.py",
            "change_service.py",
            "worker_service.py",
            "gateway_idle.py",
            "http_worker.py",
        }
    ),
}
EXPECTED_ORACLE_FILES = {
    "hello": frozenset(),
    "saas": frozenset({"ground_truth.json", "attack_graph.json"}),
    "enterprise_change_control_v1": frozenset(
        {"ground_truth.json", "attack_graph.json", "topology.json", "provenance.json"}
    ),
}


class BuildIntegrityError(ValueError):
    """A generated build cannot be trusted or used."""


class UnknownBuildError(ValueError):
    """The requested build does not exist."""


class UnknownInstanceError(ValueError):
    """The requested instance does not exist."""


class UnsupportedManifestVersionError(BuildIntegrityError):
    """A persisted manifest must be rebuilt with this version of OffSecGym."""


def artifact_digest(content: bytes) -> Digest:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def digest_files(directory: Path, names: tuple[str, ...]) -> dict[str, Digest]:
    return {name: artifact_digest((directory / name).read_bytes()) for name in names}


def _check_digest_map(value: dict[str, Digest]) -> dict[str, Digest]:
    for name, digest in value.items():
        if name in {"", ".", "..", "manifest.json"} or Path(name).name != name:
            raise ValueError(f"invalid artifact name: {name}")
        if (
            not digest.startswith("sha256:")
            or len(digest) != 71
            or any(char not in "0123456789abcdef" for char in digest[7:])
        ):
            raise ValueError(f"invalid SHA-256 digest for {name}")
    return value


class BuildManifest(StrictModel):
    schema_version: Literal["2"] = "2"
    build_id: UUID
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_name: str
    spec: RangeSpec
    pair_id: UUID | None = None
    artifact_digests: dict[str, Digest] = Field(min_length=1)
    oracle_artifact_digests: dict[str, Digest] = Field(default_factory=dict)

    @field_validator("artifact_digests", "oracle_artifact_digests")
    @classmethod
    def valid_digests(cls, value: dict[str, Digest]) -> dict[str, Digest]:
        return _check_digest_map(value)


class InstanceManifest(StrictModel):
    schema_version: Literal["2"] = "2"
    instance_id: UUID
    build_id: UUID
    project_name: str
    nonce: UUID
    state: Literal["starting", "healthy", "unhealthy", "stopped", "destroyed"]
    created_at: datetime
    updated_at: datetime
    image_id: str | None = None
    generation: int = Field(default=0, ge=0)


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
        if not path.exists():
            raise UnknownBuildError(f"unknown range build: {build_id}")
        if path.is_symlink() or not path.is_file():
            raise BuildIntegrityError(f"range build manifest is unsafe: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != "2":
                raise UnsupportedManifestVersionError(
                    "range build uses an old manifest version; stop old instances and rebuild"
                )
            manifest = BuildManifest.model_validate(raw)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            AttributeError,
            ValidationError,
        ) as exc:
            raise BuildIntegrityError(f"invalid range build manifest: {path}") from exc
        if manifest.build_id != build_id:
            raise BuildIntegrityError("range build ID does not match its manifest path")
        family = manifest.spec.family
        if family not in EXPECTED_BUNDLE_FILES:
            raise BuildIntegrityError(f"unsupported range build family: {family}")
        if (
            set(manifest.artifact_digests) != EXPECTED_BUNDLE_FILES[family]
            or set(manifest.oracle_artifact_digests) != EXPECTED_ORACLE_FILES[family]
        ):
            raise BuildIntegrityError("range build manifest artifact inventory is incomplete")
        spec_bytes = json.dumps(
            manifest.spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(spec_bytes).hexdigest() != manifest.spec_sha256:
            raise BuildIntegrityError("range build specification digest mismatch")
        return manifest

    def verify_build_integrity(self, build_id: UUID) -> BuildManifest:
        manifest = self.load_build(build_id)
        self._verify_files(self.build_dir(build_id), manifest.artifact_digests, "build")
        if manifest.oracle_artifact_digests:
            self._verify_files(
                self.root / "oracles" / build_id.hex,
                manifest.oracle_artifact_digests,
                "hidden oracle",
            )
            self._verify_oracle_versions(build_id)
        return manifest

    def _verify_oracle_versions(self, build_id: UUID) -> None:
        oracle_dir = self.root / "oracles" / build_id.hex
        for name, version in (("ground_truth.json", "3"), ("attack_graph.json", "2")):
            path = oracle_dir / name
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise BuildIntegrityError(f"hidden oracle is invalid: {name}") from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != version:
                raise UnsupportedManifestVersionError(
                    f"hidden oracle {name} uses an unsupported version; rebuild this SaaS range"
                )

    @staticmethod
    def _verify_files(directory: Path, digests: dict[str, Digest], purpose: str) -> None:
        if not directory.is_dir() or directory.is_symlink():
            raise BuildIntegrityError(f"{purpose} directory is missing or unsafe: {directory}")
        expected = set(digests)
        if purpose == "build":
            expected.add("manifest.json")
        actual = {path.name for path in directory.iterdir()}
        if actual != expected:
            raise BuildIntegrityError(
                f"{purpose} artifact set differs: missing={sorted(expected - actual)}, "
                f"unexpected={sorted(actual - expected)}"
            )
        for name, digest in digests.items():
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise BuildIntegrityError(f"{purpose} artifact missing or unsafe: {name}")
            try:
                actual_digest = artifact_digest(path.read_bytes())
            except OSError as exc:
                raise BuildIntegrityError(f"{purpose} artifact unreadable: {name}") from exc
            if actual_digest != digest:
                raise BuildIntegrityError(f"{purpose} artifact digest mismatch: {name}")

    def load_instance(self, instance_id: UUID) -> InstanceManifest:
        path = self.instance_dir(instance_id) / "manifest.json"
        if not path.exists():
            raise UnknownInstanceError(f"unknown range instance: {instance_id}")
        if path.is_symlink() or not path.is_file():
            raise BuildIntegrityError(f"range instance manifest is unsafe: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BuildIntegrityError(f"invalid range instance manifest: {path}") from exc
        if not isinstance(raw, dict):
            raise BuildIntegrityError(f"invalid range instance manifest: {path}")
        if raw.get("schema_version") != "2":
            raise UnsupportedManifestVersionError(
                "range instance uses an old manifest version; "
                "stop it with the old version and rebuild"
            )
        try:
            manifest = InstanceManifest.model_validate(raw)
        except ValidationError as exc:
            raise BuildIntegrityError(f"invalid range instance manifest: {path}") from exc
        if (
            manifest.instance_id != instance_id
            or manifest.project_name != f"offsecgym_{instance_id.hex}"
        ):
            raise BuildIntegrityError("range instance identity does not match its manifest path")
        return manifest

    def save_instance(self, manifest: InstanceManifest) -> None:
        write_json_atomic(
            self.instance_dir(manifest.instance_id) / "manifest.json",
            manifest.model_dump(mode="json"),
        )
