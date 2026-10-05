"""Oracle-free temporal witness planning over trusted gateway event evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from offsecgym.interfaces import EventStore
from offsecgym.research.m65_witness_packet import (
    ReporterEvidenceBundle,
    WitnessAction,
    build_reporter_bundle,
)
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import RangeStarted, WitnessHypothesisStarted


class WitnessEvidenceSlot(StrictModel):
    action_id: UUID
    evidence_id: UUID
    request_sequence: int = Field(gt=0)
    completion_sequence: int = Field(gt=0)


class WitnessProjection(StrictModel):
    witness_id: UUID
    identity_id: UUID
    object_id: UUID
    range_generation: int
    status: Literal[
        "before_missing",
        "action_missing",
        "after_missing",
        "state_unchanged",
        "ambiguous_intervening_action",
        "complete",
    ]
    before: WitnessEvidenceSlot | None = None
    action: WitnessEvidenceSlot | None = None
    after: WitnessEvidenceSlot | None = None
    before_state: str | None = None
    after_state: str | None = None


def _slot(action: WitnessAction) -> WitnessEvidenceSlot:
    return WitnessEvidenceSlot(
        action_id=action.action_id,
        evidence_id=action.evidence_id,
        request_sequence=action.request_sequence,
        completion_sequence=action.completion_sequence,
    )


def _state(
    bundle: ReporterEvidenceBundle, action: WitnessAction, field: str, object_id: UUID
) -> str | None:
    if action.truncated or action.http_status != 200:
        return None
    try:
        body = json.loads(bundle.get_action(action.action_id)["response_body"])
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict) or body.get("id") != str(object_id):
        return None
    value = body.get(field)
    if type(value) not in (str, int, float, bool):
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def project_witness(
    hypothesis: WitnessHypothesisStarted, bundle: ReporterEvidenceBundle
) -> WitnessProjection:
    """Match same-identity/object observations in authoritative sequence order."""
    if (
        hypothesis.run_id != bundle.packet.run_id
        or hypothesis.range_generation != bundle.packet.range_generation
    ):
        raise ValueError("witness hypothesis belongs to another range generation")
    state_actions = [
        item
        for item in bundle.packet.actions
        if item.identity_id == hypothesis.identity_id
        and item.route_target_id == hypothesis.object_id
    ]
    action_object_id = hypothesis.action_object_id or hypothesis.object_id
    reads = [
        (item, value)
        for item in state_actions
        if item.method == "GET" and item.path == hypothesis.before_path
        if (value := _state(bundle, item, hypothesis.state_field, hypothesis.object_id)) is not None
    ]
    transitions = [
        item
        for item in bundle.packet.actions
        if item.identity_id == hypothesis.identity_id and item.route_target_id == action_object_id
        if item.method == hypothesis.action_method
        and item.path == hypothesis.action_path
        and 200 <= item.http_status < 300
        and not item.truncated
        and item.response_object_id in (None, action_object_id)
    ]
    best: WitnessProjection | None = None
    for transition in transitions:
        before = [
            entry for entry in reads if entry[0].completion_sequence < transition.request_sequence
        ]
        after = [
            entry for entry in reads if entry[0].request_sequence > transition.completion_sequence
        ]
        pre = before[-1] if before else None
        post = after[0] if after else None
        ambiguous = bool(
            pre is not None
            and post is not None
            and any(
                item.action_id != transition.action_id
                and item.route_target_id == hypothesis.object_id
                and item.method != "GET"
                and pre[0].completion_sequence < item.request_sequence
                and item.request_sequence < post[0].request_sequence
                for item in bundle.packet.actions
            )
        )
        status = (
            "before_missing"
            if pre is None
            else "after_missing"
            if post is None
            else "ambiguous_intervening_action"
            if ambiguous
            else "complete"
            if pre[1] != post[1]
            else "state_unchanged"
        )
        candidate = WitnessProjection(
            witness_id=hypothesis.witness_id,
            identity_id=hypothesis.identity_id,
            object_id=hypothesis.object_id,
            range_generation=hypothesis.range_generation,
            status=status,
            before=_slot(pre[0]) if pre else None,
            action=_slot(transition),
            after=_slot(post[0]) if post else None,
            before_state=pre[1] if pre else None,
            after_state=post[1] if post else None,
        )
        if status == "complete":
            return candidate
        if best is None or (
            sum(x is not None for x in (candidate.before, candidate.action, candidate.after)),
            transition.completion_sequence,
        ) > (
            sum(x is not None for x in (best.before, best.action, best.after)),
            best.action.completion_sequence if best.action else 0,
        ):
            best = candidate
    if best is not None:
        return best
    latest = reads[-1] if reads else None
    return WitnessProjection(
        witness_id=hypothesis.witness_id,
        identity_id=hypothesis.identity_id,
        object_id=hypothesis.object_id,
        range_generation=hypothesis.range_generation,
        status="action_missing" if latest else "before_missing",
        before=_slot(latest[0]) if latest else None,
        before_state=latest[1] if latest else None,
    )


class EventWitnessLedger:
    def __init__(self, events: EventStore, state_root: Path) -> None:
        self.events = events
        self.state_root = state_root

    async def start(
        self,
        run_id: UUID,
        *,
        identity_id: UUID,
        object_id: UUID,
        before_path: str,
        action_method: Literal["POST", "PUT", "PATCH", "DELETE"],
        action_path: str,
        state_field: str,
        causation_id: UUID,
        action_object_id: UUID | None = None,
    ) -> WitnessProjection:
        if str(object_id) not in before_path.split("/") or str(
            action_object_id or object_id
        ) not in action_path.split("/"):
            raise ValueError(
                "witness paths must address the same exact object UUID or declared action object"
            )
        trace = list(await self.events.read_run(run_id))
        ranges = [item for item in trace if isinstance(item, RangeStarted)]
        if len(ranges) != 1 or ranges[0].range_generation is None:
            raise ValueError("witness requires one versioned range generation")
        for prior in trace:
            if (
                isinstance(prior, WitnessHypothesisStarted)
                and prior.identity_id == identity_id
                and prior.object_id == object_id
                and prior.action_object_id == action_object_id
                and prior.action_method == action_method
                and prior.action_path == action_path
                and prior.before_path == before_path
                and prior.state_field == state_field
                and prior.range_generation == ranges[0].range_generation
            ):
                raise ValueError("witness hypothesis already exists")
        started = await self.events.append(
            WitnessHypothesisStarted(
                run_id=run_id,
                actor="solver",
                causation_id=causation_id,
                witness_id=uuid4(),
                identity_id=identity_id,
                object_id=object_id,
                action_object_id=action_object_id,
                range_generation=ranges[0].range_generation,
                before_path=before_path,
                action_method=action_method,
                action_path=action_path,
                state_field=state_field,
            )
        )
        return await self.get(run_id, started.witness_id)

    async def get(self, run_id: UUID, witness_id: UUID) -> WitnessProjection:
        trace = list(await self.events.read_run(run_id))
        hypotheses = [
            item
            for item in trace
            if isinstance(item, WitnessHypothesisStarted) and item.witness_id == witness_id
        ]
        if len(hypotheses) != 1:
            raise ValueError("witness hypothesis is absent or duplicated")
        bundle = build_reporter_bundle(trace, self.state_root, expected_run_id=run_id)
        return project_witness(hypotheses[0], bundle)

    async def render_active(self, run_id: UUID, *, max_chars: int = 1400) -> str:
        trace = list(await self.events.read_run(run_id))
        hypotheses = [item for item in trace if isinstance(item, WitnessHypothesisStarted)]
        if not hypotheses:
            return ""
        bundle = build_reporter_bundle(trace, self.state_root, expected_run_id=run_id)
        lines = ["Active temporal witnesses (observed state, not security verdict):"]
        for item in hypotheses[-3:]:
            view = project_witness(item, bundle)
            if view.status == "complete":
                continue
            line = (
                f"- witness={view.witness_id} actor={view.identity_id} object={view.object_id} "
                f"{item.action_method} {item.action_path} status={view.status} "
                f"before={view.before.action_id if view.before else 'missing'} "
                f"action={view.action.action_id if view.action else 'missing'} "
                f"after={view.after.action_id if view.after else 'missing'}"
            )
            if len("\n".join((*lines, line))) > max_chars:
                break
            lines.append(line)
        return "\n".join(lines) if len(lines) > 1 else ""
