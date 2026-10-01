"""M6.2.2 bootstrap uses only audited discovery before matched workers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment import WorkerExperimentRunner
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers.base import ModelTurn
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.domain import EntityRef
from offsecgym.schemas.events import (
    ActionRequested,
    ControllerBudgetDeclared,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    PrerequisiteBootstrapStarted,
    SchedulerDecision,
    TaskBudgetGranted,
    TaskBudgetReleased,
    WorkerFinished,
    WorkerPacketPrepared,
    WorkerScheduled,
)
from offsecgym.schemas.evidence import RequestArtifact
from offsecgym.schemas.specs import BootstrapBudget, Budget, ExperimentSpec, ModelSpec, RangeSpec
from offsecgym.storage.controller import PostgresControllerState
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.projection import project_controller_events
from offsecgym.worldview import EventWorldState


class BlockProvider:
    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        return {
            "model": model.name,
            "input": list(input_items),
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }

    async def complete(self, request_payload):
        output = (
            {
                "type": "function_call",
                "call_id": "blocked",
                "name": "task_blocked",
                "arguments": json.dumps(
                    {
                        "reason": "Fake provider stops after handoff verification",
                        "missing_prerequisite": "A live diagnostic model",
                    }
                ),
            },
        )
        raw = {
            "id": "synthetic",
            "status": "completed",
            "output": list(output),
            "usage": {"input_tokens": 200, "output_tokens": 10},
        }
        return ModelTurn(
            response_id="synthetic",
            status="completed",
            output=output,
            usage=raw["usage"],
            raw_response=raw,
        )


@pytest.mark.postgres
@pytest.mark.docker
async def test_bootstrap_snapshot_and_packet_gates_match_across_schedulers(tmp_path: Path) -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {docker.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    base = RangeSpec.model_validate(
        yaml.safe_load((Path(__file__).parents[2] / "examples" / "saas-range.yaml").read_text())
    )
    engine = create_async_engine(url)
    runtime = ComposeRangeRuntime(tmp_path)
    try:
        events = PostgresEventStore(engine)
        snapshots = []
        packets_by_arm = []
        for parallel in (False, True):
            spec = ExperimentSpec(
                name="bootstrap_fake_parallel" if parallel else "bootstrap_fake_sequential",
                seed=1,
                range=base,
                bootstrap_budget=BootstrapBudget(
                    max_actions=32,
                    max_http_requests=32,
                    max_wall_seconds=120,
                ),
                budget=Budget(
                    max_actions=60,
                    max_http_requests=60,
                    max_model_calls=6,
                    max_total_tokens=120000,
                    max_output_tokens_per_call=128,
                    max_workers=6,
                    max_concurrency=6 if parallel else 1,
                    max_wall_seconds=600,
                ),
                orchestrator=(
                    "bootstrapped_parallel_workers"
                    if parallel
                    else "bootstrapped_sequential_workers"
                ),
                memory="structured",
                surface_visibility="known_routes",
                model=ModelSpec(provider="openai", name="mock-model"),
                validation="deterministic",
            )
            outcome = await WorkerExperimentRunner(runtime, events, BlockProvider()).run(spec)
            assert outcome.evaluation.score_valid
            trace = await events.read_run(outcome.run_id)
            starts = [e for e in trace if isinstance(e, PrerequisiteBootstrapStarted)]
            ends = [e for e in trace if isinstance(e, PrerequisiteBootstrapCompleted)]
            assert len(starts) == len(ends) == 1
            completed = ends[0]
            snapshots.append(completed.snapshot_hash)
            assert completed.action_count == completed.http_request_count
            assert 0 < completed.action_count <= 32
            assert completed.identity_count >= 8
            assert (
                min(completed.document_count, completed.invoice_count, completed.ticket_count) >= 1
            )
            assert not any(
                isinstance(e, ModelCallStarted)
                and starts[0].sequence_number < e.sequence_number < completed.sequence_number
                for e in trace
            )
            scheduled = [e for e in trace if isinstance(e, WorkerScheduled)]
            assert len(scheduled) == 6
            assert completed.sequence_number < min(e.sequence_number for e in scheduled)
            bootstrap_requests = [
                e for e in trace if isinstance(e, ActionRequested) and e.source_phase == "bootstrap"
            ]
            assert len(bootstrap_requests) == completed.action_count
            assert all(
                e.worker_id is None and e.originating_call_id is None and e.method == "GET"
                for e in bootstrap_requests
            )
            for event in bootstrap_requests:
                assert event.range_instance_id is not None
                assert event.request_artifact_id is not None
                artifact_path = (
                    runtime.state.instance_dir(event.range_instance_id)
                    / "requests"
                    / f"{event.request_artifact_id.hex}.json"
                )
                artifact = RequestArtifact.model_validate_json(artifact_path.read_bytes())
                assert artifact.source_phase == "bootstrap"
                assert artifact.action_id == event.action_id
                assert artifact.method == "GET"
                assert artifact.path == "/api/me" or re.fullmatch(
                    r"/api/workspaces/[0-9a-f-]{36}/(documents|invoices|tickets)",
                    artifact.path,
                )
            declared = next(e for e in trace if isinstance(e, ControllerBudgetDeclared))
            assert declared.budget.max_actions == declared.budget.max_http_requests == 92
            metrics = orchestration_metrics(trace)
            assert metrics.bootstrap_http_dispatches == completed.action_count
            assert metrics.worker_http_dispatches == 0
            assert metrics.bootstrap_snapshot_hash == completed.snapshot_hash
            packets = [e.packet for e in trace if isinstance(e, WorkerPacketPrepared)]
            assert len(packets) == 6
            by_kind = {
                e.packet.contract.route_family: e.packet
                for e in trace
                if isinstance(e, WorkerPacketPrepared)
            }
            for route, entity_type in (
                ("GET /api/documents/{id}", "document"),
                ("GET /api/invoices/{id}", "invoice"),
                ("GET /api/support/tickets/{id}", "ticket"),
                ("GET /api/public/invoices/{id}/preview", "invoice"),
                ("POST /api/invoices/{id}/refund", "invoice"),
            ):
                packet = by_kind[route]
                assert any(entity.entity_type == entity_type for entity in packet.relevant_entities)
                assert any(
                    item.source_phase == "bootstrap" and item.path == "/api/me"
                    for item in packet.prior_checked_actions
                )
                collection = {
                    "document": "documents",
                    "invoice": "invoices",
                    "ticket": "tickets",
                }[entity_type]
                assert any(
                    item.source_phase == "bootstrap" and item.path.endswith(f"/{collection}")
                    for item in packet.prior_checked_actions
                )
            assert len([e for e in trace if isinstance(e, WorkerFinished)]) == 6
            projection = project_controller_events(trace)
            assert projection.active_workers == 0 and not projection.active_actions
            assert not projection.active_coverage and not projection.model_reservations
            facts = await EventWorldState(events).query(outcome.run_id)
            memberships = {
                fact.subject.entity_id: fact.object_value.entity_id
                for fact in facts
                if fact.subject.entity_type == "identity"
                and fact.predicate == "member_of"
                and isinstance(fact.object_value, EntityRef)
            }
            for route in ("GET /api/documents/{id}", "GET /api/invoices/{id}"):
                identity_ids = [
                    e.entity_id
                    for e in by_kind[route].relevant_entities
                    if e.entity_type == "identity"
                ]
                assert len({memberships[i] for i in identity_ids if i in memberships}) >= 2
            packets_by_arm.append(
                sorted(
                    (
                        p.objective,
                        p.contract,
                        p.budget_slice,
                        tuple((e.entity_type, e.entity_id) for e in p.relevant_entities),
                        tuple(
                            (a.method, a.path, a.identity_id, a.source_phase)
                            for a in p.prior_checked_actions
                        ),
                    )
                    for p in packets
                )
            )
        assert snapshots[0] == snapshots[1]
        assert packets_by_arm[0] == packets_by_arm[1]
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.docker
async def test_escrowed_bootstrap_accounts_match_across_schedulers(tmp_path: Path) -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {docker.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    engine = create_async_engine(url)
    runtime = ComposeRangeRuntime(tmp_path)
    events = PostgresEventStore(engine)
    controller = PostgresControllerState(events)
    signatures = []
    try:
        for mode in ("sequential", "parallel"):
            config = (
                Path(__file__).parents[2]
                / "experiments"
                / "configs"
                / f"kimi-k3-m623-escrowed-{mode}.yaml"
            )
            spec = ExperimentSpec.model_validate(yaml.safe_load(config.read_text()))
            outcome = await WorkerExperimentRunner(runtime, events, BlockProvider()).run(spec)
            trace = await events.read_run(outcome.run_id)
            completed = next(
                event for event in trace if isinstance(event, PrerequisiteBootstrapCompleted)
            )
            accounts = await controller.worker_escrow_snapshot(outcome.run_id)
            assert len(accounts) == 6
            assert sorted(row["model_call_limit"] for row in accounts) == [3, 3, 3, 3, 4, 4]
            assert {row["token_limit"] for row in accounts} == {20000}
            assert {row["action_limit"] for row in accounts} == {10}
            assert {row["http_limit"] for row in accounts} == {10}
            assert all(row["used_model_calls"] >= 1 for row in accounts)
            assert all(row["used_tokens"] <= row["token_limit"] for row in accounts)
            assert all(row["reserved_tokens"] == 0 for row in accounts)
            assert all(row["used_actions"] <= row["action_limit"] for row in accounts)
            assert len([event for event in trace if isinstance(event, WorkerFinished)]) == 6
            projection = project_controller_events(trace)
            assert not projection.active_actions and not projection.model_reservations
            assert len(projection.worker_escrows) == 6
            for account in accounts:
                replay = projection.worker_escrows[account["worker_id"]]
                assert replay.used_tokens == account["used_tokens"]
                assert replay.used_model_calls == account["used_model_calls"]
                assert replay.used_actions == account["used_actions"]
                assert replay.used_http_requests == account["used_http_requests"]
            packets = [event.packet for event in trace if isinstance(event, WorkerPacketPrepared)]
            signatures.append(
                (
                    completed.snapshot_hash,
                    sorted(
                        (
                            packet.objective,
                            packet.contract,
                            packet.budget_slice,
                            tuple((e.entity_type, e.entity_id) for e in packet.relevant_entities),
                            tuple(
                                (a.fingerprint, a.method, a.path, a.identity_id)
                                for a in packet.prior_checked_actions
                            ),
                        )
                        for packet in packets
                    ),
                )
            )
        assert signatures[0] == signatures[1]
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.docker
async def test_elastic_scheduler_fake_workers_replay_all_leases(tmp_path: Path) -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    docker = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {docker.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    engine = create_async_engine(url)
    runtime = ComposeRangeRuntime(tmp_path)
    events = PostgresEventStore(engine)
    try:
        config = (
            Path(__file__).parents[2]
            / "experiments"
            / "configs"
            / "kimi-k3-m63-elastic-sequential.yaml"
        )
        spec = ExperimentSpec.model_validate(yaml.safe_load(config.read_text()))
        outcome = await WorkerExperimentRunner(runtime, events, BlockProvider()).run(spec)
        trace = await events.read_run(outcome.run_id)
        grants = [event for event in trace if isinstance(event, TaskBudgetGranted)]
        releases = [event for event in trace if isinstance(event, TaskBudgetReleased)]
        decisions = [event for event in trace if isinstance(event, SchedulerDecision)]
        assert len(grants) == len(releases) == len(decisions) == 6
        assert [event.kind for event in grants] == [
            "identity",
            "invoice",
            "refund",
            "public",
            "document",
            "ticket",
        ]
        assert all(event.reason_codes[event.selected_task] == "selected" for event in decisions)
        assert decisions[0].reason_codes["refund"] == "dependency_not_ready"
        accounts = await PostgresControllerState(events).worker_escrow_snapshot(outcome.run_id)
        assert len(accounts) == 6
        projection = project_controller_events(trace)
        assert not projection.active_actions and not projection.model_reservations
        for account in accounts:
            replay = projection.worker_escrows[account["worker_id"]]
            assert replay.token_limit == account["token_limit"] == account["used_tokens"]
            assert replay.model_call_limit == account["model_call_limit"]
            assert replay.used_model_calls == account["used_model_calls"]
    finally:
        await engine.dispose()
