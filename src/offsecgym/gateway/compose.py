"""Audited HTTP gateway backed by a fixed worker inside a range network."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from collections.abc import Sequence
from time import monotonic
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
            previous = await self.events.read_run(context.run_id)
            requested = ActionRequested(
                run_id=context.run_id,
                actor="gateway",
                action_id=action.action_id,
                action_type=action.kind,
                destination=action.destination,
                method=action.method,
                path_sha256=hashlib.sha256(action.path.encode("utf-8")).hexdigest(),
                identity_id=action.identity_id,
                body_sha256=hashlib.sha256(
                    json.dumps(action.json_body, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if action.json_body is not None
                else None,
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
                instance = self.runtime.state.load_instance(context.range_id)
                build = self.runtime.state.load_build(instance.build_id)
                if build.spec.family == "hello":
                    response = await self.runtime.execute_hello_http(context.range_id, action.path)
                else:
                    response = await self.runtime.execute_saas_http(
                        context.range_id,
                        action.method,
                        action.path,
                        action.json_body,
                        action.identity_id,
                    )
                raw = base64.b64decode(str(response["body_b64"]), validate=True)
                response_sha256 = hashlib.sha256(raw).hexdigest()
                evidence_id = uuid4()
                write_json_atomic(
                    self.runtime.state.instance_dir(context.range_id)
                    / "evidence"
                    / f"{evidence_id.hex}.json",
                    {
                        "action_id": str(action.action_id),
                        "request_path": action.path,
                        "request_method": action.method,
                        "identity_id": str(action.identity_id) if action.identity_id else None,
                        "json_body": action.json_body,
                        "http_status": response["http_status"],
                        "body_b64": response["body_b64"],
                        "response_sha256": response_sha256,
                        "truncated": response["truncated"],
                        "content_type": response["content_type"],
                        "redirect_location": response["redirect_location"],
                    },
                )
                result = ActionResult(
                    action_id=action.action_id,
                    status="completed",
                    duration_ms=_elapsed_ms(started),
                    evidence_id=evidence_id,
                    http_status=int(response["http_status"]),
                    body_text=raw.decode("utf-8", errors="replace"),
                    redirect_location=response["redirect_location"],
                    response_sha256=response_sha256,
                    truncated=bool(response["truncated"]),
                )
                await self.events.append(
                    ActionCompleted(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action.action_id,
                        evidence_id=evidence_id,
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
            instance = self.runtime.state.load_instance(context.range_id)
            build = self.runtime.state.load_build(instance.build_id)
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
            if (await self.runtime.status(context.range_id)).state != "healthy":
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
