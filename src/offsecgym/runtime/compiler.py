"""Deterministic compilation of the initial hello range."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid5

import yaml

from offsecgym.runtime.manifests import (
    BuildIntegrityError,
    BuildManifest,
    StateStore,
    artifact_digest,
    write_json_atomic,
)
from offsecgym.schemas.specs import RangeSpec

BUILD_NAMESPACE = UUID("935933a5-2f8b-4b33-8d68-b4a54ba2327d")
TEMPLATE_NAMES = ("Dockerfile", "gateway_idle.py", "hello_service.py", "http_worker.py")


def validate_hello_spec(spec: RangeSpec) -> None:
    if spec.family != "hello" or spec.scenario != "health_check":
        raise ValueError("Milestone 1 supports only hello/health_check")
    if spec.topology != {"hello": True}:
        raise ValueError("hello range requires exactly one enabled hello service")
    if spec.identities or spec.vulnerabilities or spec.patched or spec.patched_properties:
        raise ValueError("hello range has no identities, vulnerabilities, or patched variant")


def _template_bytes() -> dict[str, bytes]:
    root = files("offsecgym.runtime").joinpath("templates", "hello")
    return {name: root.joinpath(name).read_bytes() for name in TEMPLATE_NAMES}


def _compose_config(spec: RangeSpec, image_name: str) -> dict[str, object]:
    labels = {
        "org.offsecgym.managed": "true",
        "org.offsecgym.instance_id": "${OFFSECGYM_INSTANCE_ID}",
    }
    hardening = {
        "read_only": True,
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=16m"],
        "pids_limit": 64,
        "mem_limit": "128m",
        "cpus": 0.5,
        "networks": ["range"],
        "labels": labels,
        "restart": "no",
    }
    hello = {
        **hardening,
        "labels": dict(labels),
        "image": image_name,
        "build": {"context": "."},
        "command": ["python", "-u", "/app/hello_service.py"],
        "environment": {"RANGE_SEED": str(spec.seed)},
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; "
                "assert urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200",
            ],
            "interval": "2s",
            "timeout": "3s",
            "retries": 15,
            "start_period": "2s",
        },
    }
    gateway = {
        **hardening,
        "labels": dict(labels),
        "image": image_name,
        "build": {"context": "."},
        "command": ["python", "-u", "/app/gateway_idle.py"],
    }
    return {
        "services": {"gateway": gateway, "hello": hello},
        "networks": {"range": {"internal": True, "labels": dict(labels)}},
    }


class HelloRangeCompiler:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    def build(self, spec: RangeSpec) -> BuildManifest:
        validate_hello_spec(spec)
        spec_bytes = json.dumps(
            spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        spec_hash = hashlib.sha256(spec_bytes).hexdigest()
        templates = _template_bytes()
        compose_template = yaml.safe_dump(
            _compose_config(spec, "offsecgym-hello:placeholder"), sort_keys=True
        ).encode("utf-8")
        template_hash = hashlib.sha256(
            b"".join(name.encode() + b"\0" + templates[name] for name in TEMPLATE_NAMES)
            + compose_template
        ).hexdigest()
        build_id = uuid5(BUILD_NAMESPACE, f"1:{spec_hash}:{template_hash}")
        image_name = f"offsecgym-hello:{build_id.hex[:16]}"
        compose_bytes = yaml.safe_dump(_compose_config(spec, image_name), sort_keys=True).encode(
            "utf-8"
        )
        manifest = BuildManifest(
            build_id=build_id,
            spec_sha256=spec_hash,
            template_sha256=template_hash,
            image_name=image_name,
            spec=spec,
            artifact_digests={
                **{name: artifact_digest(content) for name, content in templates.items()},
                "compose.yaml": artifact_digest(compose_bytes),
            },
        )
        destination = self.state.build_dir(build_id)
        if destination.exists():
            if self.state.verify_build_integrity(build_id) != manifest:
                raise BuildIntegrityError(
                    "existing range build does not match deterministic manifest"
                )
            return manifest
        builds = destination.parent
        builds.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=builds))
        try:
            for name, content in templates.items():
                (temporary / name).write_bytes(content)
            (temporary / "compose.yaml").write_bytes(compose_bytes)
            write_json_atomic(temporary / "manifest.json", manifest.model_dump(mode="json"))
            try:
                os.replace(temporary, destination)
            except OSError:
                if (
                    not destination.exists()
                    or self.state.verify_build_integrity(build_id) != manifest
                ):
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self.state.verify_build_integrity(build_id)
        return manifest
