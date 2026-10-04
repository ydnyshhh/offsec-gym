"""Private, replay-verifiable context carry at a post-tool probe boundary."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from offsecgym.interfaces import EventStore
from offsecgym.providers.artifacts import payload_sha256
from offsecgym.runtime.manifests import write_json_atomic
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import ModelCallStarted, ProbeCheckpointSaved
from offsecgym.storage.reporting_branch import prefix_sha256


class ProbeCheckpoint(StrictModel):
    schema_version: str = "1"
    checkpoint_id: UUID
    run_id: UUID
    source_sequence: int = Field(gt=0)
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    latest_model_call_id: UUID
    latest_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_items: tuple[dict[str, object], ...]
    carry_items: tuple[dict[str, object], ...]
    working_state_text: str = Field(max_length=7500)


class ProbeCheckpointStore:
    def __init__(self, events: EventStore, state_root: Path) -> None:
        self.events = events
        self.root = state_root / "probe_checkpoints"

    def _path(self, run_id: UUID, checkpoint_id: UUID) -> Path:
        return self.root / run_id.hex / f"{checkpoint_id.hex}.json"

    async def save(
        self,
        run_id: UUID,
        *,
        latest_model_call_id: UUID,
        latest_request_sha256: str,
        base_items: list[dict[str, object]],
        carry_items: list[dict[str, object]],
        working_state_text: str,
    ) -> ProbeCheckpointSaved:
        trace = list(await self.events.read_run(run_id))
        if not trace or [item.sequence_number for item in trace] != list(range(1, len(trace) + 1)):
            raise ValueError("checkpoint requires a contiguous source trace")
        starts = [
            event
            for event in trace
            if isinstance(event, ModelCallStarted) and event.call_id == latest_model_call_id
        ]
        if len(starts) != 1 or starts[0].input_sha256 != latest_request_sha256:
            raise ValueError("checkpoint model request is not in its source trace")
        checkpoint = ProbeCheckpoint(
            checkpoint_id=uuid4(),
            run_id=run_id,
            source_sequence=len(trace),
            source_trace_sha256=prefix_sha256(trace),
            latest_model_call_id=latest_model_call_id,
            latest_request_sha256=latest_request_sha256,
            base_items=tuple(base_items),
            carry_items=tuple(carry_items),
            working_state_text=working_state_text,
        )
        path = self._path(run_id, checkpoint.checkpoint_id)
        for directory in (self.root, path.parent):
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
                raise OSError("checkpoint directory is not private")
        if path.exists() or path.is_symlink():
            raise FileExistsError("checkpoint ID already exists")
        payload = checkpoint.model_dump(mode="json")
        write_json_atomic(path, payload)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise OSError("checkpoint artifact is not private")
        event = ProbeCheckpointSaved(
            run_id=run_id,
            actor="controller",
            checkpoint_id=checkpoint.checkpoint_id,
            source_sequence=checkpoint.source_sequence,
            source_trace_sha256=checkpoint.source_trace_sha256,
            checkpoint_sha256=payload_sha256(payload),
            latest_model_call_id=latest_model_call_id,
            latest_request_sha256=latest_request_sha256,
        )
        return await self.events.append(event)

    async def load_latest(self, run_id: UUID) -> ProbeCheckpoint:
        trace = list(await self.events.read_run(run_id))
        saved = [event for event in trace if isinstance(event, ProbeCheckpointSaved)]
        if not saved:
            raise ValueError("probe run has no post-tool checkpoint")
        event = saved[-1]
        if (
            event.run_id != run_id
            or event.sequence_number != event.source_sequence + 1
            or prefix_sha256(trace[: event.source_sequence]) != event.source_trace_sha256
        ):
            raise ValueError("checkpoint source prefix does not match authoritative events")
        path = self._path(run_id, event.checkpoint_id)
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise OSError("checkpoint artifact is missing or unsafe")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload_sha256(payload) != event.checkpoint_sha256:
            raise ValueError("checkpoint artifact digest differs from event")
        checkpoint = ProbeCheckpoint.model_validate(payload)
        if any(
            (
                checkpoint.run_id != run_id,
                checkpoint.checkpoint_id != event.checkpoint_id,
                checkpoint.source_sequence != event.source_sequence,
                checkpoint.source_trace_sha256 != event.source_trace_sha256,
                checkpoint.latest_model_call_id != event.latest_model_call_id,
                checkpoint.latest_request_sha256 != event.latest_request_sha256,
            )
        ):
            raise ValueError("checkpoint artifact provenance differs from event")
        return checkpoint
