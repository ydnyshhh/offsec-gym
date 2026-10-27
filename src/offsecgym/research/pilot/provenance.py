"""Offline checks for retained pilot identities and request artifacts.

This module validates exported evidence. It does not authenticate an export's
origin, reconstruct a production request, or contact an execution service.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.providers.artifacts import payload_sha256
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.events import (
    ModelCallCompleted,
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
    RunStarted,
    TraceEvent,
)
from offsecgym.solver.range_surface import range_surface

Status = Literal["verified", "mismatch", "not_evaluated"]


class ProvenanceAudit(StrictModel):
    version: Literal["r02b-offline-provenance-v1"] = "r02b-offline-provenance-v1"
    benchmark_manifest_consistency: Status
    runtime_task_identity_linkage: Status
    model_call_run_linkage: Status
    request_artifact_integrity: Status
    scoped_task_parity: Status
    production_request_parity: Status = "not_evaluated"
    runtime_dispatch_parity: Status = "not_evaluated"
    run_id: UUID | None = None
    runtime_task_id: UUID | None = None
    request_artifacts_checked: int = Field(ge=0)
    issues: tuple[str, ...] = ()

    @property
    def has_mismatch(self) -> bool:
        return any(
            getattr(self, field) == "mismatch"
            for field in (
                "benchmark_manifest_consistency",
                "runtime_task_identity_linkage",
                "model_call_run_linkage",
                "request_artifact_integrity",
                "scoped_task_parity",
                "production_request_parity",
                "runtime_dispatch_parity",
            )
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _request_payload(
    path: Path, start: ModelCallStarted
) -> tuple[dict[str, object] | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "missing_request_artifact"
    try:
        record = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError):
        return None, "invalid_request_artifact"
    if not isinstance(record, dict) or set(record) != {
        "artifact_id",
        "run_id",
        "call_id",
        "kind",
        "payload",
    }:
        return None, "invalid_request_artifact"
    if (
        record["artifact_id"] != str(start.request_artifact_id)
        or record["run_id"] != str(start.run_id)
        or record["call_id"] != str(start.call_id)
        or record["kind"] != "request"
        or not isinstance(record["payload"], dict)
    ):
        return None, "request_artifact_identity_mismatch"
    try:
        actual = payload_sha256(record["payload"])
    except (TypeError, ValueError):
        return None, "invalid_request_artifact"
    if actual != start.input_sha256 or actual != start.request_sha256:
        return None, "request_artifact_digest_mismatch"
    return record["payload"], None


def _scoped_content(payload: dict[str, object], expected_objective: str) -> bool:
    instructions = payload.get("instructions")
    items = payload.get("input")
    if not isinstance(instructions, str) or not isinstance(items, list) or not items:
        return False
    first = items[0]
    if not isinstance(first, dict) or first.get("role") != "user":
        return False
    content = first.get("content")
    return (
        isinstance(content, str)
        and content.startswith(f"Goal: {expected_objective}. Allowed identity IDs: ")
        and range_surface("saas").objective not in instructions
    )


def _binding_status(
    path: Path,
    expected_sha256: str | None,
    benchmark_task_id: str,
    run_id: UUID | None,
    runtime_task_id: UUID | None,
) -> tuple[Status, str | None]:
    if expected_sha256 is None:
        return "not_evaluated", None
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        return "mismatch", "invalid_independent_binding_pin"
    if path.is_symlink() or not path.is_file():
        return "mismatch", "missing_pinned_task_binding"
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            return "mismatch", "task_binding_digest_mismatch"
        binding = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, ValueError):
        return "mismatch", "invalid_pinned_task_binding"
    if not isinstance(binding, dict) or set(binding) != {
        "schema_version",
        "benchmark_task_id",
        "runtime_task_id",
        "run_id",
    }:
        return "mismatch", "invalid_pinned_task_binding"
    if (
        binding["schema_version"] != "1"
        or binding["benchmark_task_id"] != benchmark_task_id
        or binding["runtime_task_id"] != str(runtime_task_id)
        or binding["run_id"] != str(run_id)
        or runtime_task_id is None
    ):
        return "mismatch", "task_binding_identity_mismatch"
    return "verified", None


def audit_retained_provenance(
    *,
    benchmark_task_id: str,
    seed: int,
    variant: Literal["vulnerable", "patched"],
    provider: str,
    model_id: str,
    events: tuple[TraceEvent, ...],
    request_dir: Path,
    binding_path: Path,
    independent_binding_sha256: str | None = None,
) -> ProvenanceAudit:
    """Check evidence relationships; only an external digest pin can bind task IDs."""
    issues: list[str] = []
    manifest = make_task_manifest("A1", task_mode="scoped_audit", seed=seed, variant=variant)
    manifest_status: Status = (
        "verified" if benchmark_task_id == manifest.task.task_id else "mismatch"
    )
    if manifest_status == "mismatch":
        issues.append("benchmark_task_id_mismatch")

    run_ids = {event.run_id for event in events}
    run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
    starts = [event for event in events if isinstance(event, RunStarted)]
    endings = [event for event in events if isinstance(event, RunCompleted)]
    calls = [event for event in events if isinstance(event, ModelCallStarted)]
    terminals = [
        event for event in events if isinstance(event, (ModelCallCompleted, ModelCallFailed))
    ]
    sequences = [event.sequence_number for event in events]
    call_ids = [event.call_id for event in calls]
    terminal_ids = [event.call_id for event in terminals]
    runtime_ids = {event.task_id for event in (*calls, *terminals) if event.task_id is not None}
    runtime_task_id = next(iter(runtime_ids)) if len(runtime_ids) == 1 else None
    call_by_id = {event.call_id: event for event in calls}
    call_link_valid = (
        run_id is not None
        and len(starts) == len(endings) == 1
        and bool(calls)
        and len(sequences) == len(set(sequences))
        and sequences == sorted(sequences)
        and all(number > 0 for number in sequences)
        and len({event.event_id for event in events}) == len(events)
        and starts[0].sequence_number < endings[0].sequence_number
        and len(call_ids) == len(set(call_ids))
        and len(terminal_ids) == len(set(terminal_ids))
        and set(terminal_ids) == set(call_ids)
        and len(runtime_ids) <= 1
        and all(
            starts[0].sequence_number < call.sequence_number < endings[0].sequence_number
            and call.provider == provider
            and call.model == model_id
            for call in calls
        )
        and all(
            call_by_id[terminal.call_id].sequence_number < terminal.sequence_number
            and terminal.sequence_number < endings[0].sequence_number
            and terminal.task_id == call_by_id[terminal.call_id].task_id
            and terminal.worker_id == call_by_id[terminal.call_id].worker_id
            for terminal in terminals
        )
    )
    model_link_status: Status = "verified" if call_link_valid else "mismatch"
    if not call_link_valid:
        issues.append("model_call_run_linkage_mismatch")

    binding_status, binding_issue = _binding_status(
        binding_path, independent_binding_sha256, benchmark_task_id, run_id, runtime_task_id
    )
    if len(runtime_ids) > 1:
        binding_status, binding_issue = "mismatch", "contradictory_runtime_task_ids"
    if binding_issue:
        issues.append(binding_issue)

    payloads: list[dict[str, object]] = []
    request_status: Status = "not_evaluated"
    for call in calls:
        if call.request_artifact_id is None or call.request_sha256 is None:
            if (call.request_artifact_id is None) != (
                call.request_sha256 is None
            ) or call.schema_version == "2":
                request_status = "mismatch"
                issues.append("missing_request_reference")
            continue
        payload, error = _request_payload(request_dir / f"{call.call_id}.json", call)
        if error:
            request_status = "mismatch"
            issues.append(error)
        elif payload is not None:
            payloads.append(payload)
    if request_status != "mismatch" and calls and len(payloads) == len(calls):
        request_status = "verified"

    scoped_status: Status = "not_evaluated"
    if request_status == "verified":
        if not all(
            _scoped_content(payload, manifest.public.public_objective) for payload in payloads
        ):
            scoped_status = "mismatch"
            issues.append("scoped_request_content_mismatch")
        # Matching the objective and absence of one known generic phrase do not
        # prove the complete scoped, model-visible request contract.

    return ProvenanceAudit(
        benchmark_manifest_consistency=manifest_status,
        runtime_task_identity_linkage=binding_status,
        model_call_run_linkage=model_link_status,
        request_artifact_integrity=request_status,
        scoped_task_parity=scoped_status,
        run_id=run_id,
        runtime_task_id=runtime_task_id,
        request_artifacts_checked=len(payloads),
        issues=tuple(dict.fromkeys(issues)),
    )
