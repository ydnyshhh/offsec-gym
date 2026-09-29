"""Audited HTTP gateway backed by a fixed worker inside a range network."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from collections.abc import Sequence
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from offsecgym.gateway.policy import scope_reason
from offsecgym.interfaces import EventStore
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import write_json_atomic
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.events import (
    ActionBlocked,
    ActionCompleted,
    ActionFailed,
    ActionRequested,
    AnyTraceEvent,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact


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
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._last_dispatch: dict[UUID, float] = {}

    async def execute(self, action: ActionRequest, context: ExperimentContext) -> ActionResult:
        started = monotonic()
        lock = self._locks.setdefault(context.run_id, asyncio.Lock())
        async with lock:
            async with self.runtime.get_instance_guard(context.range_instance_id):
                previous = await self.events.read_run(context.run_id)
                request_artifact = RequestArtifact(
                    request_artifact_id=uuid4(),
                    run_id=context.run_id,
                    action_id=action.action_id,
                    range_instance_id=context.range_instance_id,
                    range_generation=context.range_generation,
                    worker_id=action.worker_id,
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
                await self.events.append(requested)
                reason = await self._policy_reason(action, context, previous)
                if reason:
                    await self.events.append(
                        ActionBlocked(
                            run_id=context.run_id,
                            actor="gateway",
                            action_id=action.action_id,
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
                self._last_dispatch[context.run_id] = monotonic()
                try:
                    instance = self.runtime.state.load_instance(context.range_instance_id)
                    build = self.runtime.state.verify_build_integrity(instance.build_id)
                    if build.spec.family == "hello":
                        response = await self.runtime.execute_hello_http(
                            context.range_instance_id, action.path
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
                            evidence_id=evidence.evidence_id,
                            duration_ms=result.duration_ms,
                            http_status=result.http_status,
                            response_sha256=response_sha256,
                            causation_id=requested.event_id,
                        )
                    )
                    return result
                except (DockerCommandError, ValueError, KeyError, TypeError, OSError, TimeoutError):
                    await self.events.append(
                        ActionFailed(
                            run_id=context.run_id,
                            actor="gateway",
                            action_id=action.action_id,
                            reason_code="gateway_execution_failed",
                            causation_id=requested.event_id,
                        )
                    )
                    return ActionResult(
                        action_id=action.action_id,
                        status="failed",
                        reason_code="gateway_execution_failed",
                        duration_ms=_elapsed_ms(started),
                    )

    async def _policy_reason(
        self,
        action: ActionRequest,
        context: ExperimentContext,
        previous: Sequence[AnyTraceEvent],
    ) -> str | None:
        try:
            instance = self.runtime.state.load_instance(context.range_instance_id)
            if instance.generation != context.range_generation:
                return "range_generation_mismatch"
            build = self.runtime.state.verify_build_integrity(instance.build_id)
            reason = scope_reason(action, context, build.spec.family)
            if reason:
                return reason
            if build.spec.family not in {"hello", "saas"}:
                return "range_out_of_scope"
            if (
                build.spec.family == "saas"
                and action.identity_id is not None
                and all(
                    account["id"] != str(action.identity_id)
                    for account in self.runtime._public_accounts(instance.build_id)
                )
            ):
                return "identity_unknown"
            if (await self.runtime.instance_status(context.range_instance_id)).state != "healthy":
                return "range_unhealthy"
        except (ValueError, DockerCommandError, OSError):
            return "range_unavailable"
        if any(
            isinstance(event, ActionRequested) and event.action_id == action.action_id
            for event in previous
        ):
            return "duplicate_action"
        requests = sum(isinstance(event, ActionRequested) for event in previous)
        dispatched = sum(isinstance(event, (ActionCompleted, ActionFailed)) for event in previous)
        if context.budget.max_actions is not None and requests >= context.budget.max_actions:
            return "action_budget_exhausted"
        if (
            context.budget.max_http_requests is not None
            and dispatched >= context.budget.max_http_requests
        ):
            return "http_budget_exhausted"
        last_dispatch = self._last_dispatch.get(context.run_id)
        if last_dispatch is not None and monotonic() - last_dispatch < self.min_interval_seconds:
            return "rate_limited"
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
