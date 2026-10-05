"""Local Docker Compose runtime for generated synthetic ranges."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

from offsecgym.runtime.compiler import HelloRangeCompiler
from offsecgym.runtime.enterprise import (
    FAMILY as ENTERPRISE_FAMILY,
)
from offsecgym.runtime.enterprise import (
    PROPERTY_SLUGS as ENTERPRISE_PROPERTIES,
)
from offsecgym.runtime.enterprise import (
    EnterpriseRangeCompiler,
)
from offsecgym.runtime.enterprise import (
    patched_properties as enterprise_patched_properties,
)
from offsecgym.runtime.manifests import InstanceManifest, StateStore, utc_now
from offsecgym.runtime.saas import PROPERTY_SLUGS, SaasRangeCompiler, patched_properties
from offsecgym.schemas.common import JsonValue
from offsecgym.schemas.domain import (
    RangeControllerMetadata,
    RangeIdentity,
    RangeInstanceStatus,
)
from offsecgym.schemas.specs import RangeSpec


class DockerCommandError(RuntimeError):
    """A bounded Docker operation failed."""


class _InstanceGuard:
    """Process-shared instance fence; the manifest generation is checked while held."""

    def __init__(self, path: Path, local_lock: asyncio.Lock) -> None:
        self.path = path
        self.local_lock = local_lock
        self.descriptor: int | None = None

    async def __aenter__(self) -> _InstanceGuard:
        await self.local_lock.acquire()
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (
                self.path.parent.is_symlink()
                or not self.path.parent.is_dir()
                or stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077
            ):
                raise OSError("instance lock directory is unsafe")
            descriptor = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            self.descriptor = descriptor
            lock_mode = os.fstat(descriptor).st_mode
            if not stat.S_ISREG(lock_mode) or stat.S_IMODE(lock_mode) & 0o077:
                raise OSError("instance lock is not a regular file")
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK}:
                        raise
                    await asyncio.sleep(0.02)
            return self
        except BaseException:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            self.local_lock.release()
            raise

    async def __aexit__(self, *_exc: object) -> None:
        try:
            assert self.descriptor is not None
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None
        finally:
            self.local_lock.release()


async def run_command(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout_seconds: int = 180,
) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=(
                asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise DockerCommandError(f"could not start Docker: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input_bytes), timeout=timeout_seconds
        )
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-2000:].strip()
        raise DockerCommandError(f"Docker command failed ({process.returncode}): {detail}")
    return stdout.decode("utf-8", errors="replace")


class ComposeRangeRuntime:
    def __init__(self, state_dir: Path) -> None:
        self.state = StateStore(state_dir)
        self.compiler = HelloRangeCompiler(self.state)
        self.saas_compiler = SaasRangeCompiler(self.state)
        self.enterprise_compiler = EnterpriseRangeCompiler(self.state)
        self._instance_locks: dict[UUID, asyncio.Lock] = {}

    def get_instance_guard(self, instance_id: UUID) -> _InstanceGuard:
        """Serialize dispatch and lifecycle across processes sharing this state root."""

        return _InstanceGuard(
            self.state.root / "instance_locks" / f"{instance_id.hex}.lock",
            self._instance_locks.setdefault(instance_id, asyncio.Lock()),
        )

    async def build(self, spec: RangeSpec) -> UUID:
        if spec.family == "hello":
            return self.compiler.build(spec).build_id
        if spec.family == "saas":
            return self.saas_compiler.build(spec).build_id
        if spec.family == ENTERPRISE_FAMILY:
            return self.enterprise_compiler.build(spec).build_id
        raise ValueError(f"unsupported range family: {spec.family}")

    def _compose_command(self, instance: InstanceManifest, *arguments: str) -> list[str]:
        build_dir = self.state.build_dir(instance.build_id)
        return [
            "docker",
            "compose",
            "--ansi",
            "never",
            "--project-name",
            instance.project_name,
            "--file",
            str(build_dir / "compose.yaml"),
            *arguments,
        ]

    @staticmethod
    def _environment(instance: InstanceManifest) -> dict[str, str]:
        return {
            **os.environ,
            "OFFSECGYM_INSTANCE_ID": str(instance.instance_id),
            "OFFSECGYM_INSTANCE_SECRET": instance.nonce.hex,
        }

    async def _compose(
        self,
        instance: InstanceManifest,
        *arguments: str,
        input_bytes: bytes | None = None,
        timeout_seconds: int = 180,
    ) -> str:
        return await run_command(
            self._compose_command(instance, *arguments),
            environment=self._environment(instance),
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
        )

    async def _assert_owned(self, instance: InstanceManifest) -> None:
        """Refuse a mutation if a project contains resources lacking this instance label."""

        container_ids = (
            await run_command(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    f"label=com.docker.compose.project={instance.project_name}",
                    "--format",
                    "{{.ID}}",
                ],
                timeout_seconds=20,
            )
        ).splitlines()
        network_ids = (
            await run_command(
                [
                    "docker",
                    "network",
                    "ls",
                    "--filter",
                    f"label=com.docker.compose.project={instance.project_name}",
                    "--format",
                    "{{.ID}}",
                ],
                timeout_seconds=20,
            )
        ).splitlines()
        for resource_id in container_ids:
            labels = json.loads(
                await run_command(
                    ["docker", "inspect", resource_id, "--format", "{{json .Config.Labels}}"],
                    timeout_seconds=20,
                )
            )
            _check_owner(labels, instance)
        for resource_id in network_ids:
            labels = json.loads(
                await run_command(
                    [
                        "docker",
                        "network",
                        "inspect",
                        resource_id,
                        "--format",
                        "{{json .Labels}}",
                    ],
                    timeout_seconds=20,
                )
            )
            _check_owner(labels, instance)

    async def _down(self, instance: InstanceManifest) -> None:
        await self._assert_owned(instance)
        await self._compose(instance, "down", "--volumes")

    async def create_instance(self, build_id: UUID) -> UUID:
        self.state.verify_build_integrity(build_id)
        instance_id = uuid4()
        now = utc_now()
        instance = InstanceManifest(
            instance_id=instance_id,
            build_id=build_id,
            project_name=f"offsecgym_{instance_id.hex}",
            nonce=uuid4(),
            state="stopped",
            created_at=now,
            updated_at=now,
        )
        self.state.save_instance(instance)
        return instance_id

    def _save_state(
        self, instance: InstanceManifest, state: str, **updates: object
    ) -> InstanceManifest:
        changed = instance.model_copy(update={"state": state, "updated_at": utc_now(), **updates})
        self.state.save_instance(changed)
        return changed

    async def start_instance(self, instance_id: UUID) -> RangeInstanceStatus:
        async with self.get_instance_guard(instance_id):
            return await self._start_instance_unlocked(instance_id)

    async def _start_instance_unlocked(self, instance_id: UUID) -> RangeInstanceStatus:
        instance = self.state.load_instance(instance_id)
        self.state.verify_build_integrity(instance.build_id)
        if instance.state == "destroyed":
            raise ValueError("destroyed range instances cannot be restarted")
        current = await self.instance_status(instance.instance_id)
        if current.state == "healthy":
            return current
        instance = self._save_state(instance, "starting")
        try:
            await self._assert_owned(instance)
            await self._compose(instance, "up", "--build", "--wait", "--wait-timeout", "90")
            build = self.state.verify_build_integrity(instance.build_id)
            image_id = (
                await run_command(
                    ["docker", "image", "inspect", build.image_name, "--format", "{{.Id}}"],
                    timeout_seconds=20,
                )
            ).strip()
            instance = self._save_state(instance, "healthy", image_id=image_id)
            status = await self.instance_status(instance.instance_id)
            if status.state != "healthy":
                raise DockerCommandError("range did not become healthy")
            return status
        except (DockerCommandError, TimeoutError, asyncio.CancelledError):
            try:
                await self._down(instance)
                self._save_state(instance, "stopped")
            except DockerCommandError:
                self._save_state(instance, "unhealthy")
            raise

    async def instance_status(self, instance_id: UUID) -> RangeInstanceStatus:
        instance = self.state.load_instance(instance_id)
        if instance.state == "destroyed":
            return self._status(instance, "destroyed")
        build = self.state.verify_build_integrity(instance.build_id)
        output = await self._compose(
            instance, "ps", "--all", "--format", "json", timeout_seconds=20
        )
        records = _parse_compose_ps(output)
        services = {record.get("Service"): record for record in records}
        target_service = (
            "hello"
            if build.spec.family == "hello"
            else "change"
            if build.spec.family == ENTERPRISE_FAMILY
            else "saas"
        )
        target = services.get(target_service, {})
        gateway = services.get("gateway", {})
        dependencies_healthy = (
            all(
                services.get(name, {}).get("State") == "running"
                and services.get(name, {}).get("Health") == "healthy"
                for name in ("postgres", "cache", "identity", "worker")
            )
            if build.spec.family == ENTERPRISE_FAMILY
            else True
        )
        if (
            target.get("State") == "running"
            and target.get("Health") == "healthy"
            and gateway.get("State") == "running"
            and dependencies_healthy
        ):
            state = "healthy"
        elif any(record.get("State") == "running" for record in records):
            state = "unhealthy"
        else:
            state = "stopped"
        if state != instance.state:
            instance = self._save_state(instance, state)
        return self._status(instance, state)

    @staticmethod
    def _status(instance: InstanceManifest, state: str) -> RangeInstanceStatus:
        return RangeInstanceStatus(
            instance_id=instance.instance_id,
            build_id=instance.build_id,
            generation=instance.generation,
            state=state,
            checked_at=utc_now(),
        )

    async def wait_until_healthy(self, instance_id: UUID) -> RangeInstanceStatus:
        deadline = asyncio.get_running_loop().time() + 90
        while asyncio.get_running_loop().time() < deadline:
            status = await self.instance_status(instance_id)
            if status.state == "healthy":
                return status
            await asyncio.sleep(1)
        raise TimeoutError("range health check timed out")

    async def snapshot_metadata(self, instance_id: UUID) -> RangeControllerMetadata:
        instance = self.state.load_instance(instance_id)
        build = self.state.verify_build_integrity(instance.build_id)
        status = await self.instance_status(instance_id)
        patch_set = (
            patched_properties(build.spec)
            if build.spec.family == "saas"
            else enterprise_patched_properties(build.spec)
            if build.spec.family == ENTERPRISE_FAMILY
            else frozenset()
        )
        all_properties = (
            PROPERTY_SLUGS
            if build.spec.family == "saas"
            else ENTERPRISE_PROPERTIES
            if build.spec.family == ENTERPRISE_FAMILY
            else frozenset()
        )
        security_variant = (
            None
            if build.spec.family not in {"saas", ENTERPRISE_FAMILY}
            else "vulnerable"
            if not patch_set
            else "patched"
            if patch_set == all_properties
            else "selective"
        )
        return RangeControllerMetadata(
            instance_id=instance.instance_id,
            build_id=instance.build_id,
            project_name=instance.project_name,
            spec_sha256=build.spec_sha256,
            seed=build.spec.seed,
            image_id=instance.image_id,
            state=status.state,
            generation=instance.generation,
            family=build.spec.family,
            security_variant=security_variant,
            patched_properties=tuple(sorted(patch_set)),
            pair_id=build.pair_id,
            identities=tuple(
                RangeIdentity(
                    identity_id=UUID(account["id"]),
                    username=account["username"],
                    role=account["role"],
                    workspace_id=UUID(account["workspace_id"]) if account["workspace_id"] else None,
                )
                for account in self._public_accounts(build.build_id)
            ),
        )

    def _public_accounts(self, build_id: UUID) -> list[dict[str, str | None]]:
        build = self.state.verify_build_integrity(build_id)
        if build.spec.family not in {"saas", ENTERPRISE_FAMILY}:
            return []
        fixture = json.loads(
            (self.state.build_dir(build_id) / "fixture.json").read_text(encoding="utf-8")
        )
        return fixture["accounts"] if build.spec.family == "saas" else fixture["users"]

    def identity_credentials(self, instance_id: UUID, identity_id: UUID) -> dict[str, str]:
        instance = self.state.load_instance(instance_id)
        for account in self._public_accounts(instance.build_id):
            if account["id"] == str(identity_id):
                username = account["username"]
                return {
                    "username": username,
                    "password": hmac.new(
                        instance.nonce.hex.encode(), username.encode(), hashlib.sha256
                    ).hexdigest()[:32],
                }
        raise ValueError("unknown range identity")

    async def reset_instance(self, instance_id: UUID) -> RangeInstanceStatus:
        async with self.get_instance_guard(instance_id):
            instance = self.state.load_instance(instance_id)
            self.state.verify_build_integrity(instance.build_id)
            if instance.state == "destroyed":
                raise ValueError("destroyed range instances cannot be reset")
            await self._down(instance)
            self._save_state(instance, "stopped", nonce=uuid4(), generation=instance.generation + 1)
            return await self._start_instance_unlocked(instance_id)

    async def stop_instance(self, instance_id: UUID) -> RangeInstanceStatus:
        async with self.get_instance_guard(instance_id):
            instance = self.state.load_instance(instance_id)
            if instance.state == "destroyed":
                return self._status(instance, "destroyed")
            self.state.verify_build_integrity(instance.build_id)
            await self._assert_owned(instance)
            await self._compose(instance, "stop", timeout_seconds=40)
            self._save_state(instance, "stopped")
            return await self.instance_status(instance_id)

    async def destroy_instance(self, instance_id: UUID) -> RangeInstanceStatus:
        async with self.get_instance_guard(instance_id):
            instance = self.state.load_instance(instance_id)
            if instance.state != "destroyed":
                self.state.verify_build_integrity(instance.build_id)
                await self._down(instance)
                instance = self._save_state(instance, "destroyed")
            return self._status(instance, "destroyed")

    async def execute_hello_http(self, range_id: UUID, path: str) -> dict[str, object]:
        instance = self.state.load_instance(range_id)
        if (await self.instance_status(range_id)).state != "healthy":
            raise DockerCommandError("range is not healthy")
        result = await self._compose(
            instance,
            "exec",
            "-T",
            "gateway",
            "python",
            "/app/http_worker.py",
            input_bytes=json.dumps({"method": "GET", "path": path}).encode("utf-8"),
            timeout_seconds=15,
        )
        return json.loads(result)

    async def execute_saas_http(
        self,
        range_id: UUID,
        method: str,
        path: str,
        json_body: dict[str, JsonValue] | None,
        identity_id: UUID | None,
    ) -> dict[str, object]:
        instance = self.state.load_instance(range_id)
        build = self.state.verify_build_integrity(instance.build_id)
        if build.spec.family != "saas":
            raise ValueError("range is not a SaaS build")
        if (await self.instance_status(range_id)).state != "healthy":
            raise DockerCommandError("range is not healthy")
        identity = self.identity_credentials(range_id, identity_id) if identity_id else None
        result = await self._compose(
            instance,
            "exec",
            "-T",
            "gateway",
            "python",
            "/app/http_worker.py",
            input_bytes=json.dumps(
                {"method": method, "path": path, "json_body": json_body, "identity": identity}
            ).encode(),
            timeout_seconds=15,
        )
        return json.loads(result)

    async def execute_enterprise_http(
        self,
        range_id: UUID,
        method: str,
        path: str,
        json_body: dict[str, JsonValue] | None,
        identity_id: UUID | None,
    ) -> dict[str, object]:
        instance = self.state.load_instance(range_id)
        build = self.state.verify_build_integrity(instance.build_id)
        if build.spec.family != ENTERPRISE_FAMILY:
            raise ValueError("range is not an enterprise build")
        if (await self.instance_status(range_id)).state != "healthy":
            raise DockerCommandError("range is not healthy")
        identity = self.identity_credentials(range_id, identity_id) if identity_id else None
        result = await self._compose(
            instance,
            "exec",
            "-T",
            "gateway",
            "python",
            "/app/http_worker.py",
            input_bytes=json.dumps(
                {"method": method, "path": path, "json_body": json_body, "identity": identity}
            ).encode(),
            timeout_seconds=15,
        )
        return json.loads(result)


def _parse_compose_ps(output: str) -> list[dict[str, object]]:
    content = output.strip()
    if not content:
        return []
    if content.startswith("["):
        return json.loads(content)
    return [json.loads(line) for line in content.splitlines()]


def _check_owner(labels: dict[str, str], instance: InstanceManifest) -> None:
    if labels.get("org.offsecgym.managed") != "true" or labels.get(
        "org.offsecgym.instance_id"
    ) != str(instance.instance_id):
        raise DockerCommandError("Compose project contains a resource not owned by this instance")
