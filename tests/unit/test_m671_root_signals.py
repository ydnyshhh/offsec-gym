"""Offline root signals separate model action, completion, and state transition."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace as Obj
from uuid import uuid4

import pytest

from offsecgym.research import m671_root_signals as signals
from offsecgym.research.m66_pilot_analysis import ObservedRequest
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    FindingSubmitted,
    ModelCallStarted,
    RangeStarted,
)
from offsecgym.validation.deterministic import ProofAction


def _action(request: object, sequence: int, body: dict, status: int = 200) -> ProofAction:
    if not hasattr(request, "action_id"):
        request.action_id = uuid4()
    evidence = Obj(
        evidence_id=uuid4(),
        http_status=status,
        truncated=False,
        body_b64=base64.b64encode(json.dumps(body).encode()).decode(),
    )
    return ProofAction(request, evidence, sequence)


def _case(monkeypatch: pytest.MonkeyPatch, root: str):
    run, build, instance, call, actor, asset, project, env, job = [uuid4() for _ in range(9)]
    checkpoint = "B2-S3" if root.startswith("B2") else "B3-S2"
    receipt = {
        "root_id": root,
        "checkpoint_id": checkpoint,
        "private_anchors": {
            "b2_actor": str(actor),
            "b2_change": str(asset),
            "b2_project": str(project),
            "b3_actor": str(actor),
            "b3_change": str(asset),
            "b3_environment": str(env),
        },
        "state_assertions": {
            "cache_primed_at_start": True,
            "role_revoked_at_start": True,
            "role_active_at_start": False,
            "queued_at_start": True,
            "cancellation_window_open_at_start": True,
            "cancelled_at_start": False,
        },
        "verified_public_state": {"job_id": str(job), "due_tick": 7},
    }
    monkeypatch.setattr(signals, "_verify_binding", lambda *_: receipt)
    monkeypatch.setattr(signals, "_model_artifact_index", lambda *_: None)
    monkeypatch.setattr(signals, "_b2", lambda *_: "same_run_setup_absent")
    monkeypatch.setattr(signals, "_b3", lambda *_: "same_run_setup_absent")
    monkeypatch.setattr(
        signals,
        "_b3_deadline_diagnostics",
        lambda *_: {"cancel_commit_before_due_tick": True},
    )
    prop = Obj(
        slug=root,
        subject=Obj(identity_id=actor),
        object=Obj(object_id=asset, workspace_id=project),
        family="enterprise",
        expectation=Obj(action="target"),
    )
    oracle = Obj(build_id=build, properties=[prop])
    fixture = {
        "changes": [{"id": str(asset), "project_id": str(project), "environment_id": str(env)}],
        "environments": [{"id": str(env), "revision": 1}],
    }
    trace = [
        Obj(run_id=run),
        RangeStarted.model_construct(
            run_id=run,
            sequence_number=2,
            build_id=build,
            range_instance_id=instance,
            range_generation=1,
        ),
        ModelCallStarted.model_construct(
            run_id=run,
            sequence_number=3,
            call_id=call,
            task_id=None,
            worker_id=None,
        ),
    ]
    return Obj(
        trace=trace,
        receipt=receipt,
        oracle=oracle,
        fixture=fixture,
        actor=actor,
        asset=asset,
        project=project,
        env=env,
        job=job,
        call=call,
        instance=instance,
        run=run,
    )


def _decisive(case: object, *, root: str, trusted: bool = False):
    action_id = uuid4()
    path = f"/api/changes/{case.asset}/" + ("approve" if root.startswith("B2") else "cancel")
    request = Obj(
        action_id=action_id,
        method="POST",
        path=path,
        identity_id=case.actor,
    )
    observed = ObservedRequest(4, request)
    asked = ActionRequested.model_construct(
        action_id=action_id,
        sequence_number=4,
        run_id=case.run,
        source_phase="critical_checkpoint" if trusted else None,
        originating_call_id=None if trusted else case.call,
        originating_tool_call_id=None if trusted else "scripted",
        task_id=None,
        worker_id=None,
        range_instance_id=case.instance,
        range_generation=1,
    )
    done = ActionCompleted.model_construct(action_id=action_id, sequence_number=5)
    case.trace.extend([asked, done])
    return observed, request


def _extract(case: object, actions: list[ProofAction], requests: list[ObservedRequest]):
    return signals.extract_root_signals(
        b"synthetic-receipt",
        case.trace,
        [],
        actions,
        requests,
        case.oracle,
        case.fixture,
    ).signals


@pytest.mark.parametrize("status", [403, 200])
def test_b2_request_and_completion_are_not_transition_without_after(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    case = _case(monkeypatch, "B2-REVOKED-ROLE")
    observed, request = _decisive(case, root="B2", trusted=False)
    approved = _action(
        request,
        5,
        {
            "id": str(case.asset),
            "project_id": str(case.project),
            "status": "APPROVED",
            "approver_id": str(case.actor),
        },
        status,
    )
    result = _extract(case, [approved], [observed])
    assert result.decisive_action_request_ids == {request.action_id}
    assert result.decisive_action_completed_ids == {request.action_id}
    assert not result.transition_action_ids


def test_b2_full_after_state_and_trusted_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch, "B2-REVOKED-ROLE")
    observed, request = _decisive(case, root="B2")
    approved = _action(
        request,
        5,
        {
            "id": str(case.asset),
            "project_id": str(case.project),
            "status": "APPROVED",
            "approver_id": str(case.actor),
        },
    )
    later = _action(
        Obj(method="GET", path=f"/api/changes/{case.asset}", identity_id=case.actor),
        7,
        {"id": str(case.asset), "project_id": str(case.project), "status": "APPROVED"},
    )
    result = _extract(case, [approved, later], [observed])
    assert result.transition_action_ids == {request.action_id}
    assert not result.complete_witness_evidence_ids
    trusted = _case(monkeypatch, "B2-REVOKED-ROLE")
    trusted_observed, trusted_request = _decisive(trusted, root="B2", trusted=True)
    result = _extract(trusted, [_action(trusted_request, 5, {})], [trusted_observed])
    assert not result.decisive_action_request_ids


def test_b3_requires_cancellation_job_environment_and_worker_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch, "B3-CANCELLED-JOB")
    observed, request = _decisive(case, root="B3")
    cancel = _action(
        request,
        5,
        {"id": str(case.asset), "project_id": str(case.project), "status": "CANCELLED"},
    )
    job = _action(
        Obj(method="GET", path=f"/api/jobs/{case.job}", identity_id=case.actor),
        7,
        {
            "id": str(case.job),
            "status": "SUCCEEDED",
            "change_id": str(case.asset),
            "environment_id": str(case.env),
            "initiator_id": str(case.actor),
        },
    )
    assert not _extract(case, [cancel, job], [observed]).transition_action_ids
    environment = _action(
        Obj(method="GET", path=f"/api/environments/{case.env}", identity_id=case.actor),
        8,
        {"id": str(case.env), "project_id": str(case.project), "revision": 2},
    )
    assert not _extract(case, [cancel, job, environment], [observed]).transition_action_ids
    audit = _action(
        Obj(method="GET", path="/api/audit?limit=20", identity_id=case.actor),
        9,
        {
            "items": [
                {
                    "sequence": 1,
                    "action": "job.queued",
                    "object_id": str(case.job),
                    "project_id": str(case.project),
                },
                {
                    "sequence": 2,
                    "action": "change.cancel",
                    "object_id": str(case.asset),
                    "project_id": str(case.project),
                },
                {
                    "sequence": 3,
                    "action": "job.started",
                    "object_id": str(case.job),
                    "project_id": str(case.project),
                },
                {
                    "sequence": 4,
                    "action": "job.completed",
                    "object_id": str(case.job),
                    "project_id": str(case.project),
                },
                {
                    "sequence": 5,
                    "action": "environment.revision_changed",
                    "object_id": str(case.env),
                    "project_id": str(case.project),
                },
            ]
        },
    )
    result = _extract(case, [cancel, job, environment, audit], [observed])
    assert result.decisive_action_completed_ids == {request.action_id}
    assert result.transition_action_ids == {request.action_id}


def test_typed_root_finding_only(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch, "B2-REVOKED-ROLE")
    target = uuid4()
    unrelated = uuid4()
    case.trace.extend(
        [
            FindingSubmitted.model_construct(
                run_id=case.run,
                finding=Obj(
                    finding_id=target,
                    run_id=case.run,
                    asset_id=case.asset,
                    family="enterprise",
                    security_property=case.oracle.properties[0].expectation,
                ),
            ),
            FindingSubmitted.model_construct(
                run_id=case.run,
                finding=Obj(
                    finding_id=unrelated,
                    run_id=case.run,
                    asset_id=uuid4(),
                    family="enterprise",
                    security_property=case.oracle.properties[0].expectation,
                ),
            ),
        ]
    )
    assert _extract(case, [], []).root_class_finding_ids == {target}
