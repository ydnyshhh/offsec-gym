"""Audited HTTP gateway backed by a fixed worker inside a range network."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from offsecgym.gateway.policy import scope_reason
from offsecgym.interfaces import EventStore
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import BuildIntegrityError, BuildManifest, write_json_atomic
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.storage.controller import LocalActionReservations, PostgresControllerState
from offsecgym.storage.event_store import PostgresEventStore


class ComposeActionGateway:
    def __init__(
        self,
        runtime: ComposeRangeRuntime,
        events: EventStore,
        *,
        min_interval_seconds: float = 0.1,
    ) -> None:
        if not math.isfinite(min_interval_seconds) or min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be finite and nonnegative")
        self.runtime = runtime
        self.events = events
        self.min_interval_seconds = min_interval_seconds
        if isinstance(events, PostgresEventStore):
            self.controller = PostgresControllerState(events)
        else:
            controller = getattr(events, "_local_action_reservations", None)
            if controller is None:
                controller = LocalActionReservations(events)
                events._local_action_reservations = controller
            self.controller = controller

    async def execute(self, action: ActionRequest, context: ExperimentContext) -> ActionResult:
        started = monotonic()
        async with self.runtime.get_instance_guard(context.range_instance_id):
            # Untrusted/stale context must not create an instance-scoped directory.
            instance = self.runtime.state.load_instance(context.range_instance_id)
            if instance.generation != context.range_generation:
                return ActionResult(
                    action_id=action.action_id,
                    status="blocked",
                    reason_code="range_generation_mismatch",
                    duration_ms=_elapsed_ms(started),
                )
            build = self.runtime.state.verify_build_integrity(instance.build_id)
            request_artifact = RequestArtifact(
                request_artifact_id=uuid4(),
                run_id=context.run_id,
                action_id=action.action_id,
                range_instance_id=context.range_instance_id,
                range_generation=context.range_generation,
                worker_id=action.worker_id,
                task_id=action.task_id,
                source_phase=action.source_phase,
                originating_call_id=action.originating_call_id,
                originating_tool_call_id=action.originating_tool_call_id,
                identity_id=action.identity_id,
                destination=action.destination,
                method=action.method,
                path=_redact_path(action.path),
                json_body=_redact_json(action.json_body),
            )
            write_json_atomic(
                self.runtime.state.instance_dir(context.range_instance_id)
                / "requests"
                / f"{request_artifact.request_artifact_id.hex}.json",
                request_artifact.model_dump(mode="json"),
            )
            requested = ActionRequested(
                schema_version="2",
                run_id=context.run_id,
                actor="gateway",
                action_id=action.action_id,
                action_type=action.kind,
                destination=action.destination,
                method=action.method,
                path_sha256=hashlib.sha256(action.path.encode("utf-8")).hexdigest(),
                worker_id=action.worker_id,
                task_id=action.task_id,
                source_phase=action.source_phase,
                originating_call_id=action.originating_call_id,
                originating_tool_call_id=action.originating_tool_call_id,
                identity_id=action.identity_id,
                body_sha256=hashlib.sha256(
                    json.dumps(action.json_body, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if action.json_body is not None
                else None,
                range_instance_id=context.range_instance_id,
                range_generation=context.range_generation,
                request_artifact_id=request_artifact.request_artifact_id,
            )
            if action.run_id != context.run_id:
                await self.events.append(requested)
                await self.events.append(
                    ActionBlocked(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        worker_id=action.worker_id,
                        task_id=action.task_id,
                        reason_code="run_mismatch",
                        causation_id=requested.event_id,
                    )
                )
                return ActionResult(
                    action_id=action.action_id,
                    status="blocked",
                    reason_code="run_mismatch",
                    duration_ms=_elapsed_ms(started),
                )
            try:
                request_reason = await self.controller.reserve_request(
                    action, context.budget, requested
                )
            except ValueError:
                return await self._record_failure(
                    action, context, None, started, "controller_budget_mismatch"
                )
            if request_reason:
                if request_reason != "duplicate_action":
                    await self.events.append(requested)
                await self.events.append(
                    ActionBlocked(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        worker_id=action.worker_id,
                        task_id=action.task_id,
                        reason_code=request_reason,
                        causation_id=(
                            requested.event_id if request_reason != "duplicate_action" else None
                        ),
                    )
                )
                return ActionResult(
                    action_id=action.action_id,
                    status="blocked",
                    reason_code=request_reason,
                    duration_ms=_elapsed_ms(started),
                )
            try:
                reason = await self._policy_reason(action, context, build)
            except BuildIntegrityError:
                await self._record_failure(
                    action, context, requested, started, "build_integrity_failed"
                )
                raise
            except (DockerCommandError, OSError, ValueError):
                return await self._record_failure(
                    action, context, requested, started, "range_unavailable"
                )
            if reason:
                await self.events.append(
                    ActionBlocked(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        worker_id=action.worker_id,
                        task_id=action.task_id,
                        reason_code=reason,
                        causation_id=requested.event_id,
                    )
                )
                return ActionResult(
                    action_id=action.action_id,
                    status="blocked",
                    reason_code=reason,
                    duration_ms=_elapsed_ms(started),
                )
            try:
                reason = await self.controller.reserve_action(
                    action,
                    context.budget,
                    min_interval_seconds=self.min_interval_seconds,
                    record_attempt=False,
                )
            except ValueError:
                return await self._record_failure(
                    action, context, requested, started, "controller_budget_mismatch"
                )
            if reason:
                await self.events.append(
                    ActionBlocked(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        worker_id=action.worker_id,
                        task_id=action.task_id,
                        reason_code=reason,
                        causation_id=requested.event_id,
                    )
                )
                return ActionResult(
                    action_id=action.action_id,
                    status="blocked",
                    reason_code=reason,
                    duration_ms=_elapsed_ms(started),
                )
            try:
                build = self.runtime.state.verify_build_integrity(instance.build_id)
                if build.spec.family == "hello":
                    response = await self.runtime.execute_hello_http(
                        context.range_instance_id, action.path
                    )
                elif build.spec.family == "enterprise_change_control_v1":
                    response = await self.runtime.execute_enterprise_http(
                        context.range_instance_id,
                        action.method,
                        action.path,
                        action.json_body,
                        action.identity_id,
                    )
                else:
                    response = await self.runtime.execute_saas_http(
                        context.range_instance_id,
                        action.method,
                        action.path,
                        action.json_body,
                        action.identity_id,
                    )
                raw = base64.b64decode(str(response["body_b64"]), validate=True)
                response_sha256 = hashlib.sha256(raw).hexdigest()
                evidence = Evidence(
                    evidence_id=uuid4(),
                    run_id=context.run_id,
                    action_id=action.action_id,
                    range_instance_id=context.range_instance_id,
                    range_generation=context.range_generation,
                    request_artifact_id=request_artifact.request_artifact_id,
                    worker_id=action.worker_id,
                    task_id=action.task_id,
                    identity_id=action.identity_id,
                    http_status=int(response["http_status"]),
                    body_b64=str(response["body_b64"]),
                    response_sha256=response_sha256,
                    truncated=bool(response["truncated"]),
                    content_type=response["content_type"],
                    redirect_location=response["redirect_location"],
                )
                write_json_atomic(
                    self.runtime.state.instance_dir(context.range_instance_id)
                    / "evidence"
                    / f"{evidence.evidence_id.hex}.json",
                    evidence.model_dump(mode="json"),
                )
                result = ActionResult(
                    action_id=action.action_id,
                    status="completed",
                    duration_ms=_elapsed_ms(started),
                    evidence_id=evidence.evidence_id,
                    http_status=evidence.http_status,
                    body_text=raw.decode("utf-8", errors="replace"),
                    redirect_location=evidence.redirect_location,
                    response_sha256=response_sha256,
                    truncated=evidence.truncated,
                )
                await self.events.append(
                    ActionCompleted(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        worker_id=action.worker_id,
                        task_id=action.task_id,
                        evidence_id=evidence.evidence_id,
                        duration_ms=result.duration_ms,
                        http_status=result.http_status,
                        response_sha256=response_sha256,
                        causation_id=requested.event_id,
                    )
                )
                return result
            except BuildIntegrityError:
                await self._record_failure(
                    action, context, requested, started, "build_integrity_failed"
                )
                raise
            except DockerCommandError:
                return await self._record_failure(
                    action, context, requested, started, "target_execution_failed"
                )
            except (ValueError, KeyError, TypeError, binascii.Error):
                return await self._record_failure(
                    action, context, requested, started, "worker_protocol_failed"
                )
            except (OSError, TimeoutError):
                return await self._record_failure(
                    action, context, requested, started, "range_unavailable"
                )
            except asyncio.CancelledError:
                await self._record_failure(action, context, requested, started, "action_cancelled")
                raise
            finally:
                await self.controller.release_action(action)

    async def _record_failure(
        self,
        action: ActionRequest,
        context: ExperimentContext,
        requested: ActionRequested | None,
        started: float,
        reason_code: str,
    ) -> ActionResult:
        await self.events.append(
            ActionFailed(
                run_id=context.run_id,
                actor="gateway",
                action_id=action.action_id,
                worker_id=action.worker_id,
                task_id=action.task_id,
                reason_code=reason_code,
                causation_id=requested.event_id if requested is not None else None,
            )
        )
        return ActionResult(
            action_id=action.action_id,
            status="failed",
            reason_code=reason_code,
            duration_ms=_elapsed_ms(started),
        )

    async def _policy_reason(
        self,
        action: ActionRequest,
        context: ExperimentContext,
        build: BuildManifest,
    ) -> str | None:
        reason = scope_reason(action, context, build.spec.family)
        if reason:
            return reason
        if build.spec.family not in {"hello", "saas", "enterprise_change_control_v1"}:
            return "range_out_of_scope"
        if (
            build.spec.family in {"saas", "enterprise_change_control_v1"}
            and action.identity_id is not None
            and all(
                account["id"] != str(action.identity_id)
                for account in self.runtime._public_accounts(build.build_id)
            )
        ):
            return "identity_unknown"
        if (await self.runtime.instance_status(context.range_instance_id)).state != "healthy":
            return "range_unhealthy"
        return None


def _elapsed_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(
        marker in lowered
        for marker in (
            "password",
            "passwd",
            "passphrase",
            "secret",
            "token",
            "credential",
            "authorization",
            "auth",
            "cookie",
            "session",
            "api_key",
            "access_key",
            "private_key",
        )
    )


def _redact_json(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None

    def redact(item: object) -> object:
        if isinstance(item, dict):
            return {
                key: "*" if _sensitive_key(key) else redact(child) for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return redact(value)


def _redact_path(path: str) -> str:
    try:
        parsed = urlsplit(path)
    except ValueError:
        return "*"
    query = urlencode(
        [
            (key, "*" if _sensitive_key(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = "*@" + netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
