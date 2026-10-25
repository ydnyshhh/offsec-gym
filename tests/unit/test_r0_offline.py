"""R0 checks use benign, synthetic retained records and no provider or gateway."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from offsecgym.benchmark.passive_evidence_audit import (
    CandidateReference,
    RetainedPublicObservation,
    audit_retained_observation,
)
from offsecgym.benchmark.task_diversity_audit import diversity_audit
from offsecgym.research.pilot.offline import analyze_run, empty_report
from offsecgym.research.pilot.protocol import dry_run, load_protocol
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    RangeStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact


def _retained():
    run, instance, action, artifact, evidence_id, identity = (uuid4() for _ in range(6))
    path = "/api/items/123"
    body = json.dumps({"reference_id": "benign-object"}).encode()
    digest = hashlib.sha256(body).hexdigest()
    request = RequestArtifact(
        request_artifact_id=artifact,
        run_id=run,
        action_id=action,
        range_instance_id=instance,
        range_generation=1,
        identity_id=identity,
        destination="synthetic",
        method="GET",
        path=path,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run,
        action_id=action,
        range_instance_id=instance,
        range_generation=1,
        request_artifact_id=artifact,
        identity_id=identity,
        http_status=200,
        body_b64=base64.b64encode(body).decode(),
        response_sha256=digest,
    )
    start = ActionRequested(
        run_id=run,
        actor="gateway",
        sequence_number=2,
        action_id=action,
        action_type="http_request",
        destination="synthetic",
        method="GET",
        path_sha256=hashlib.sha256(path.encode()).hexdigest(),
        identity_id=identity,
        range_instance_id=instance,
        range_generation=1,
        request_artifact_id=artifact,
    )
    end = ActionCompleted(
        run_id=run,
        actor="gateway",
        sequence_number=3,
        causation_id=start.event_id,
        action_id=action,
        evidence_id=evidence_id,
        duration_ms=5,
        http_status=200,
        response_sha256=digest,
    )
    record = RetainedPublicObservation(
        task_id="synthetic-task",
        visibility_contract_id="synthetic-contract",
        request=request,
        evidence=evidence,
        events=(start, end),
    )
    claim = CandidateReference(
        object_type="document",
        source_field_path="$.reference_id",
        observed_value="benign-object",
        action_id=action,
        evidence_id=evidence_id,
    )
    return record, claim


def _audit(record, claim):
    return audit_retained_observation(
        record,
        claim,
        expected_task_id="synthetic-task",
        expected_visibility_contract_id="synthetic-contract",
        public_routes=("GET /api/items/{id}",),
        visible_identity_ids=(record.request.identity_id,),
    )


def test_passive_field_requires_authoritative_pairing():
    record, claim = _retained()
    result = _audit(record, claim)
    assert result.field_provenance_valid
    assert result.q3_status == "not_evaluated"
    assert not _audit(
        record, claim.model_copy(update={"action_id": uuid4()})
    ).field_provenance_valid
    assert not _audit(
        record, claim.model_copy(update={"evidence_id": uuid4()})
    ).field_provenance_valid
    assert not _audit(
        record, claim.model_copy(update={"observed_value": "wrong"})
    ).field_provenance_valid
    assert not _audit(
        record, claim.model_copy(update={"source_field_path": "$.wrong"})
    ).field_provenance_valid
    assert not _audit(record.model_copy(update={"events": ()}), claim).field_provenance_valid
    assert not _audit(
        record.model_copy(update={"events": record.events * 2}), claim
    ).field_provenance_valid
    assert not _audit(record.model_copy(update={"task_id": "wrong"}), claim).field_provenance_valid
    wrong_run = record.evidence.model_copy(update={"run_id": uuid4()})
    assert not _audit(
        record.model_copy(update={"evidence": wrong_run}), claim
    ).field_provenance_valid
    wrong_cause = record.events[1].model_copy(update={"causation_id": uuid4()})
    assert not _audit(
        record.model_copy(update={"events": (record.events[0], wrong_cause)}), claim
    ).field_provenance_valid
    wrong_identity = record.request.model_copy(update={"identity_id": uuid4()})
    assert not _audit(
        record.model_copy(update={"request": wrong_identity}), claim
    ).field_provenance_valid


def test_passive_rejects_wrong_generation_and_response_digest():
    record, claim = _retained()
    changed = record.evidence.model_copy(update={"range_generation": 2})
    assert not _audit(record.model_copy(update={"evidence": changed}), claim).field_provenance_valid
    with pytest.raises(ValueError, match="hash mismatch"):
        Evidence.model_validate({**record.evidence.model_dump(), "response_sha256": "0" * 64})


def test_protocol_is_frozen_and_execution_is_blocked():
    protocol = load_protocol(Path("configs/research/r0-a1-pilot.yaml"))
    plan = dry_run(protocol, Path("docs/benchmark/t11f-a1-admission.json"))
    assert len(plan["cells"]) == 6
    assert plan["model_count"] == 0
    assert plan["scoped_task_parity"] is False
    assert plan["provider_calls"] == 0
    with pytest.raises(ValueError, match="source pins"):
        type(protocol).model_validate({**protocol.model_dump(), "source_pin_sha256": "0" * 64})


def test_cli_model_execution_fails_closed():
    result = subprocess.run(
        [
            sys.executable,
            "research_ops/benchmark_r0_a1_pilot.py",
            "--protocol",
            "configs/research/r0-a1-pilot.yaml",
            "--execute-models",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "scoped A1 controller parity is not qualified" in result.stderr


def test_empty_offline_report_has_no_mock_scores(tmp_path: Path):
    output = tmp_path / ".offsecgym" / "r0"
    result = subprocess.run(
        [
            sys.executable,
            "research_ops/benchmark_r0_analyze.py",
            "--input",
            str(tmp_path / "no-traces"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    summary = json.loads((output / "pilot_summary.json").read_text())
    assert summary["status"] == "not_executed"
    assert summary["actual_model_runs"] == 0
    assert summary["model_performance_claims"] is False


def test_trace_metrics_remain_descriptive_and_unscored():
    record, _ = _retained()
    run = record.request.run_id
    instance = record.request.range_instance_id
    events = (
        RunStarted(run_id=run, actor="controller", sequence_number=1, experiment_hash="synthetic"),
        RangeStarted(
            run_id=run,
            actor="controller",
            sequence_number=2,
            build_id=uuid4(),
            range_instance_id=instance,
            range_generation=1,
        ),
        record.events[0].model_copy(update={"sequence_number": 3}),
        record.events[1].model_copy(update={"sequence_number": 4}),
        RunCompleted(run_id=run, actor="controller", sequence_number=5, status="provider_failed"),
    )
    result = analyze_run(events)
    assert result.actions == result.http_requests == 1
    assert result.model_calls == 0
    assert result.input_tokens is None or result.input_tokens == 0
    assert result.score_valid is None
    assert result.stages["S0"] == "true"
    assert result.stages["S1"] == "unknown"
    assert empty_report()["actual_model_runs"] == 0
    with pytest.raises(ValueError, match="sequence"):
        analyze_run((events[0], events[0], events[-1]))


def test_static_diversity_never_qualifies_new_archetypes():
    report = diversity_audit()
    assert len(report["tasks"]) == 4
    assert report["new_archetypes_qualified"] == 0
    assert {row["public_reachability"] for row in report["tasks"][1:]} == {"not_evaluated"}
