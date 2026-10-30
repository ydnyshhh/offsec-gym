"""Synthetic offline fixtures only; no fixture represents a qualified model run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.providers.artifacts import payload_sha256
from offsecgym.research.pilot.provenance import audit_retained_provenance
from offsecgym.research.pilot.reporting import (
    RetainedRunMetadata,
    read_retained_run,
    write_offline_reports,
)
from offsecgym.schemas.events import (
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.solver.range_surface import range_surface

RUN = UUID(int=1)
TASK = UUID(int=2)
CALL = UUID(int=3)
ARTIFACT = UUID(int=4)
BENCHMARK = make_task_manifest("A1", task_mode="scoped_audit", seed=42, variant="vulnerable")


def _metadata(**updates: object) -> RetainedRunMetadata:
    values = dict(
        protocol_version="r0-a1-model-pilot-v2",
        task_id=BENCHMARK.task.task_id,
        seed=42,
        variant="vulnerable",
        provider="synthetic-provider",
        model_id="synthetic-model",
        source_commit="1" * 40,
        visibility_contract_hash="2" * 64,
        tool_schema_hash="3" * 64,
        budget_hash="4" * 64,
        evaluation_version="canonical-v1",
    )
    values.update(updates)
    return RetainedRunMetadata.model_validate(values)


def _payload(*, generic: bool = False) -> dict[str, object]:
    objective = (
        "Test the synthetic range's documented security boundaries"
        if generic
        else BENCHMARK.public.public_objective
    )
    return {
        "instructions": (
            range_surface("saas").objective if generic else "Scoped synthetic instructions"
        ),
        "input": [{"role": "user", "content": f"Goal: {objective}. Allowed identity IDs: {TASK}."}],
        "tools": [],
    }


def _events(payload: dict[str, object], *, legacy: bool = False):
    digest = payload_sha256(payload)
    start = ModelCallStarted(
        schema_version="1" if legacy else "2",
        run_id=RUN,
        actor="controller",
        sequence_number=2,
        call_id=CALL,
        task_id=TASK,
        provider="synthetic-provider",
        model="synthetic-model",
        input_sha256=digest,
        request_artifact_id=None if legacy else ARTIFACT,
        request_sha256=None if legacy else digest,
    )
    return (
        RunStarted(run_id=RUN, actor="controller", sequence_number=1, experiment_hash="synthetic"),
        start,
        ModelCallFailed(
            run_id=RUN,
            actor="controller",
            sequence_number=3,
            call_id=CALL,
            task_id=TASK,
            reason_code="synthetic_failure",
        ),
        RunCompleted(run_id=RUN, actor="controller", sequence_number=4, status="provider_failed"),
    )


def _write_request(
    prefix: Path,
    payload: dict[str, object],
    *,
    call_id: UUID = CALL,
    artifact_id: UUID = ARTIFACT,
) -> None:
    directory = prefix.with_suffix(".requests")
    directory.mkdir(exist_ok=True)
    record = {
        "artifact_id": str(artifact_id),
        "run_id": str(RUN),
        "call_id": str(call_id),
        "kind": "request",
        "payload": payload,
    }
    (directory / f"{call_id}.json").write_text(json.dumps(record))


def _write_binding(prefix: Path, *, runtime_task_id: UUID = TASK) -> str:
    record = {
        "schema_version": "1",
        "benchmark_task_id": BENCHMARK.task.task_id,
        "runtime_task_id": str(runtime_task_id),
        "run_id": str(RUN),
    }
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    prefix.with_suffix(".binding.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _audit(prefix: Path, metadata: RetainedRunMetadata, events, pin: str | None = None):
    return audit_retained_provenance(
        protocol_version=metadata.protocol_version,
        benchmark_task_id=metadata.task_id,
        seed=metadata.seed,
        variant=metadata.variant,
        provider=metadata.provider,
        model_id=metadata.model_id,
        events=events,
        request_dir=prefix.with_suffix(".requests"),
        binding_path=prefix.with_suffix(".binding.json"),
        independent_binding_sha256=pin,
    )


def _write_export(prefix: Path, metadata: RetainedRunMetadata, events) -> None:
    prefix.with_suffix(".metadata.json").write_text(metadata.model_dump_json())
    prefix.with_suffix(".events.jsonl").write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n"
    )
    prefix.with_suffix(".evaluation.json").write_text(
        json.dumps(
            {
                "status": "provider_failed",
                "score_valid": False,
                "candidate_count": 0,
                "validated_count": 0,
                "inconclusive": 0,
            }
        )
    )


def test_verified_internal_links_do_not_infer_task_binding(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = _events(payload)
    _write_request(prefix, payload)
    _write_binding(prefix)
    unpinned = _audit(prefix, _metadata(), events)
    assert unpinned.benchmark_manifest_consistency == "verified"
    assert unpinned.protocol_event_schema_consistency == "verified"
    assert unpinned.model_call_run_linkage == "verified"
    assert unpinned.request_artifact_integrity == "verified"
    assert unpinned.runtime_task_identity_linkage == "not_evaluated"
    assert unpinned.scoped_task_parity == "not_evaluated"
    assert unpinned.production_request_parity == "not_evaluated"
    assert unpinned.runtime_dispatch_parity == "not_evaluated"
    pin = hashlib.sha256(prefix.with_suffix(".binding.json").read_bytes()).hexdigest()
    anchored = _audit(prefix, _metadata(), events, pin)
    assert anchored.runtime_task_identity_linkage == "verified"
    assert anchored.scoped_task_parity == "not_evaluated"
    assert not anchored.has_mismatch
    _write_export(prefix, _metadata(), events)
    _metadata_row, record = read_retained_run(prefix)
    assert record.provenance_audit is not None
    assert record.provenance_audit.runtime_task_identity_linkage == "not_evaluated"
    assert record.model_dump(mode="json")["provenance_audit"]["request_artifact_integrity"] == (
        "verified"
    )


def test_wrong_benchmark_and_pinned_runtime_identity(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = _events(payload)
    _write_request(prefix, payload)
    pin = _write_binding(prefix, runtime_task_id=UUID(int=99))
    wrong = _metadata(
        task_id=make_task_manifest(
            "A1", task_mode="scoped_audit", seed=43, variant="vulnerable"
        ).task.task_id
    )
    assert _audit(prefix, wrong, events).benchmark_manifest_consistency == "mismatch"
    assert _audit(prefix, _metadata(), events, pin).runtime_task_identity_linkage == "mismatch"
    _write_export(prefix, _metadata(), events)
    with pytest.raises(ValueError, match="task_binding_identity_mismatch"):
        read_retained_run(prefix, independent_binding_sha256=pin)


@pytest.mark.parametrize(
    "mutation", ["mixed_run", "duplicate_call", "contradictory_task", "provider"]
)
def test_model_call_linkage_rejects_contradictions(tmp_path: Path, mutation: str):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = list(_events(payload))
    _write_request(prefix, payload)
    if mutation == "mixed_run":
        events[2] = events[2].model_copy(update={"run_id": UUID(int=99)})
    elif mutation == "duplicate_call":
        duplicate = events[1].model_copy(update={"sequence_number": 3})
        events.insert(2, duplicate)
        events[3] = events[3].model_copy(update={"sequence_number": 4})
        events[4] = events[4].model_copy(update={"sequence_number": 5})
    elif mutation == "contradictory_task":
        events[2] = events[2].model_copy(update={"task_id": UUID(int=99)})
    else:
        events[1] = events[1].model_copy(update={"model": "other-model"})
    result = _audit(prefix, _metadata(), tuple(events))
    assert result.model_call_run_linkage == "mismatch"
    assert result.has_mismatch


def test_request_digest_and_missing_artifact_block_import(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = _events(payload)
    _write_export(prefix, _metadata(), events)
    with pytest.raises(ValueError, match="missing_request_artifact"):
        read_retained_run(prefix)
    _write_request(prefix, payload)
    path = prefix.with_suffix(".requests") / f"{CALL}.json"
    record = json.loads(path.read_text())
    record["payload"]["instructions"] = "modified"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="request_artifact_digest_mismatch"):
        read_retained_run(prefix)


def test_v2_protocol_rejects_legacy_model_call_without_request(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    events = _events(_payload(), legacy=True)
    _write_export(prefix, _metadata(), events)
    audit = _audit(prefix, _metadata(), events)
    assert audit.model_call_run_linkage == "verified"
    assert audit.protocol_event_schema_consistency == "mismatch"
    assert audit.request_artifact_integrity == "mismatch"
    assert "protocol_v2_requires_model_call_schema_v2" in audit.issues
    assert audit.production_request_parity == "not_evaluated"
    assert audit.runtime_dispatch_parity == "not_evaluated"
    with pytest.raises(ValueError, match="protocol_v2_requires_model_call_schema_v2"):
        read_retained_run(prefix)


def test_v2_protocol_rejects_mixed_model_call_schemas_with_valid_artifacts(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    _write_request(prefix, payload)
    legacy_call_id = UUID(int=5)
    legacy_artifact_id = UUID(int=6)
    _write_request(prefix, payload, call_id=legacy_call_id, artifact_id=legacy_artifact_id)
    first = _events(payload)
    legacy_start = _events(payload, legacy=True)[1].model_copy(
        update={
            "sequence_number": 4,
            "call_id": legacy_call_id,
            "request_artifact_id": legacy_artifact_id,
            "request_sha256": payload_sha256(payload),
        }
    )
    legacy_terminal = first[2].model_copy(
        update={"event_id": UUID(int=7), "sequence_number": 5, "call_id": legacy_call_id}
    )
    events = (
        first[0],
        first[1],
        first[2],
        legacy_start,
        legacy_terminal,
        first[3].model_copy(update={"sequence_number": 6}),
    )
    metadata = _metadata()
    _write_export(prefix, metadata, events)
    audit = _audit(prefix, metadata, events)
    assert audit.model_call_run_linkage == "verified"
    assert audit.request_artifact_integrity == "verified"
    assert audit.protocol_event_schema_consistency == "mismatch"
    assert audit.request_artifacts_checked == 2
    assert audit.production_request_parity == "not_evaluated"
    assert audit.runtime_dispatch_parity == "not_evaluated"
    with pytest.raises(ValueError, match="protocol_v2_requires_model_call_schema_v2"):
        read_retained_run(prefix)


def test_request_artifact_identity_and_binding_pin_are_checked(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = _events(payload)
    _write_export(prefix, _metadata(), events)
    _write_request(prefix, payload)
    request_path = prefix.with_suffix(".requests") / f"{CALL}.json"
    record = json.loads(request_path.read_text())
    record["artifact_id"] = str(UUID(int=99))
    request_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="request_artifact_identity_mismatch"):
        read_retained_run(prefix)
    record["artifact_id"] = str(ARTIFACT)
    request_path.write_text(json.dumps(record))
    pin = _write_binding(prefix)
    prefix.with_suffix(".binding.json").write_text('{"tampered":true}')
    with pytest.raises(ValueError, match="task_binding_digest_mismatch"):
        read_retained_run(prefix, independent_binding_sha256=pin)


def test_private_report_serializes_separate_provenance_scopes(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    _write_request(prefix, payload)
    _write_export(prefix, _metadata(), _events(payload))
    output = tmp_path / ".offsecgym" / "offline"
    summary = write_offline_reports(tmp_path, output)
    assert summary["status"] == "analyzed_existing_unqualified"
    run = json.loads((output / "pilot_runs.jsonl").read_text().strip())
    audit = run["metrics"]["provenance_audit"]
    assert audit["request_artifact_integrity"] == "verified"
    assert audit["runtime_task_identity_linkage"] == "not_evaluated"
    assert audit["production_request_parity"] == "not_evaluated"
    assert audit["runtime_dispatch_parity"] == "not_evaluated"


def test_generic_trace_cannot_claim_scoped_a1(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload(generic=True)
    events = _events(payload)
    _write_request(prefix, payload)
    _write_export(prefix, _metadata(), events)
    result = _audit(prefix, _metadata(), events)
    assert result.scoped_task_parity == "mismatch"
    with pytest.raises(ValueError, match="scoped_request_content_mismatch"):
        read_retained_run(prefix)


def test_missing_version_and_unsupported_parity_claims_fail_closed():
    values = _metadata().model_dump()
    values.pop("protocol_version")
    with pytest.raises(ValidationError, match="protocol_version"):
        RetainedRunMetadata.model_validate(values)
    with pytest.raises(ValidationError, match="scoped_task_parity"):
        RetainedRunMetadata.model_validate(
            {**_metadata().model_dump(), "scoped_task_parity": "verified"}
        )


def test_v1_legacy_without_artifact_remains_unqualified(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = _events(payload, legacy=True)
    metadata = _metadata(protocol_version="r0-a1-model-pilot-v1")
    _write_export(prefix, metadata, events)
    _metadata_row, record = read_retained_run(prefix)
    assert record.provenance_audit is not None
    assert record.provenance_audit.model_call_run_linkage == "verified"
    assert record.provenance_audit.protocol_event_schema_consistency == "verified"
    assert record.provenance_audit.request_artifact_integrity == "not_evaluated"
    assert record.provenance_audit.runtime_task_identity_linkage == "not_evaluated"
    assert record.provenance_audit.scoped_task_parity == "not_evaluated"


def test_v1_partial_request_reference_is_a_mismatch(tmp_path: Path):
    prefix = tmp_path / "synthetic"
    payload = _payload()
    events = list(_events(payload, legacy=True))
    events[1] = events[1].model_copy(update={"request_artifact_id": ARTIFACT})
    result = _audit(prefix, _metadata(protocol_version="r0-a1-model-pilot-v1"), tuple(events))
    assert result.request_artifact_integrity == "mismatch"
    assert "missing_request_reference" in result.issues
