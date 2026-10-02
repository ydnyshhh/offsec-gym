"""M6.3.1 readiness derives from evidence and admission protects trajectories."""

from uuid import uuid4

from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.schemas.domain import EntityRef, WorldFact
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    AdmissionDecision,
    ControllerBudgetDeclared,
    PrerequisiteBootstrapCompleted,
    TaskBudgetExtended,
    TaskBudgetGranted,
    WorkerFinished,
    WorkerObjectiveAction,
    WorldFactSubmitted,
)
from offsecgym.schemas.scheduler import TaskBudgetRequest
from offsecgym.schemas.specs import Budget
from offsecgym.solver.opportunity_workers import (
    _identity_state,
    _invoice_state,
    choose_admission,
)


def _observed(run_id, action_id, entity_type, entity_id, predicate, value):
    return WorldFactSubmitted(
        run_id=run_id,
        actor="controller",
        fact=WorldFact(
            fact_id=uuid4(),
            run_id=run_id,
            kind="observation",
            subject=EntityRef(entity_id=entity_id, entity_type=entity_type),
            predicate=predicate,
            object_value=value,
            source_action_ids=(action_id,),
            evidence_ids=(uuid4(),),
            confidence=1,
            status="observed",
        ),
    )


def _bootstrap_marker(run_id):
    return PrerequisiteBootstrapCompleted(
        run_id=run_id,
        actor="controller",
        snapshot_hash="a" * 64,
        identity_count=2,
        workspace_count=1,
        document_count=1,
        invoice_count=1,
        ticket_count=1,
        action_count=2,
        http_request_count=2,
    )


def test_bootstrap_identity_map_suppresses_redundant_worker() -> None:
    run_id, member, support, workspace = (uuid4() for _ in range(4))
    first_action, second_action = uuid4(), uuid4()
    trace = [
        _bootstrap_marker(run_id),
        ActionRequested(
            schema_version="1",
            run_id=run_id,
            actor="gateway",
            action_id=first_action,
            action_type="http_request",
            destination="saas",
            method="GET",
            source_phase="bootstrap",
        ),
        ActionCompleted(
            run_id=run_id, actor="gateway", action_id=first_action, duration_ms=1, http_status=200
        ),
        ActionRequested(
            schema_version="1",
            run_id=run_id,
            actor="gateway",
            action_id=second_action,
            action_type="http_request",
            destination="saas",
            method="GET",
            source_phase="bootstrap",
        ),
        ActionCompleted(
            run_id=run_id, actor="gateway", action_id=second_action, duration_ms=1, http_status=200
        ),
        _observed(run_id, first_action, "identity", member, "role", "member"),
        _observed(
            run_id,
            first_action,
            "identity",
            member,
            "member_of",
            EntityRef(entity_id=workspace, entity_type="workspace"),
        ),
        _observed(run_id, second_action, "identity", support, "role", "support"),
    ]
    assert _identity_state(trace, (member, support), bootstrap_only=True)[0]
    assert not _identity_state(trace[:-1], (member, support), bootstrap_only=True)[0]


def test_budget_exhausted_invoice_worker_can_satisfy_refund_dependency() -> None:
    run_id, invoice_id, worker_id, task_id = (uuid4() for _ in range(4))
    bootstrap_action, detail_action = uuid4(), uuid4()
    trace = [
        _bootstrap_marker(run_id),
        ActionRequested(
            schema_version="1",
            run_id=run_id,
            actor="gateway",
            action_id=bootstrap_action,
            action_type="http_request",
            destination="saas",
            method="GET",
            source_phase="bootstrap",
        ),
        _observed(
            run_id,
            bootstrap_action,
            "invoice",
            invoice_id,
            "workspace",
            EntityRef(entity_id=uuid4(), entity_type="workspace"),
        ),
        WorkerFinished(
            run_id=run_id,
            actor="controller",
            worker_id=worker_id,
            task_id=task_id,
            status="budget_exhausted",
        ),
    ]
    assert not _invoice_state(trace)[0]
    trace.extend(
        [
            ActionCompleted(
                run_id=run_id,
                actor="gateway",
                action_id=detail_action,
                worker_id=worker_id,
                task_id=task_id,
                duration_ms=1,
                http_status=200,
                evidence_id=uuid4(),
            ),
            WorkerObjectiveAction(
                run_id=run_id,
                actor="controller",
                worker_id=worker_id,
                task_id=task_id,
                action_id=detail_action,
                route_family="GET /api/invoices/{id}",
            ),
            _observed(run_id, detail_action, "invoice", invoice_id, "status", "open"),
        ]
    )
    assert _invoice_state(trace)[0]


def test_admission_reserves_dependency_and_replans() -> None:
    ready = ("invoice", "public", "document", "ticket")
    admitted, forecast = choose_admission(
        ready,
        {"tokens": 120000, "calls": 20, "actions": 60, "http": 60},
        forecast_refund=True,
    )
    assert set(admitted) == {"invoice", "public"}
    assert forecast == ("refund",)
    admitted, forecast = choose_admission(
        ("refund", "public", "document", "ticket"),
        {"tokens": 83000, "calls": 16, "actions": 50, "http": 50},
        forecast_refund=False,
    )
    assert set(admitted) == {"refund", "public"}
    assert forecast == ()


def test_opportunity_displacement_counts_extension_crossing_ready_minimum() -> None:
    run_id, worker_id, task_id = (uuid4() for _ in range(3))
    request = TaskBudgetRequest(
        minimum_viable_tokens=40,
        preferred_tokens=40,
        max_tokens=80,
        minimum_model_calls=1,
        max_model_calls=2,
        expected_actions=1,
        max_actions=1,
        expected_http_requests=1,
        max_http_requests=1,
    )
    trace = [
        ControllerBudgetDeclared(
            run_id=run_id,
            actor="controller",
            budget=Budget(
                max_total_tokens=100, max_model_calls=10, max_actions=10, max_http_requests=10
            ),
        ),
        AdmissionDecision(
            run_id=run_id,
            actor="scheduler",
            ready_minimum_tokens={"invoice": 40, "public": 30},
            admitted_kinds=("invoice",),
            forecast_kinds=(),
            task_states={"invoice": "READY", "public": "READY"},
            available_tokens=100,
            available_model_calls=10,
            available_actions=10,
            available_http_requests=10,
            utility_scores={"invoice": 2, "public": 1},
            reason_codes={"invoice": "admitted", "public": "higher_utility_set"},
            state_snapshot_hash="a" * 64,
        ),
        TaskBudgetGranted(
            run_id=run_id,
            actor="scheduler",
            worker_id=worker_id,
            task_id=task_id,
            kind="invoice",
            request=request,
            token_limit=40,
            model_call_limit=1,
            action_limit=1,
            http_limit=1,
        ),
        TaskBudgetExtended(
            run_id=run_id,
            actor="scheduler",
            worker_id=worker_id,
            task_id=task_id,
            prior_token_limit=40,
            token_limit=71,
            prior_model_call_limit=1,
            model_call_limit=1,
            reason_code="model_preflight_required",
        ),
    ]
    metrics = orchestration_metrics(trace)
    assert metrics.opportunity_displacement_tokens == 31
    assert metrics.admissible_unused_tokens == 0
    assert (metrics.ready_objectives, metrics.admitted_objectives, metrics.executed_objectives) == (
        2,
        1,
        0,
    )
    assert metrics.admission_coverage == 0.5
    assert metrics.execution_coverage == 0.0
