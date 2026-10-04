"""Fake-provider paired reporting uses one source prefix and independent scores."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.experiment.reporting_context import PairedReportingContextRunner
from offsecgym.providers.artifacts import ModelCallArtifacts
from offsecgym.providers.base import ModelTurn, ModelUsage
from offsecgym.research.m652_audit import audit_paired_branches, replay_branch_score
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.manifests import utc_now
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import (
    AgentResult,
    RangeControllerMetadata,
    RangeInstanceStatus,
    ValidationContext,
)
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    ModelCallCompleted,
    ModelCallStarted,
    PrerequisiteBootstrapCompleted,
    RangeStarted,
    ReporterStarted,
    ReportingBranchStarted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.specs import (
    BootstrapBudget,
    Budget,
    ExperimentSpec,
    ModelSpec,
    ReporterBudget,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.reporting_branch import ReportingBranchStore


def _spec():
    base = ExperimentSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "experiments/configs/scripted-saas.yaml").read_text()
        )
    )
    return base.model_copy(
        update={
            "orchestrator": "bootstrapped_monolithic",
            "memory": "structured",
            "surface_visibility": "known_routes",
            "model": ModelSpec(provider="fake", name="fake"),
            "budget": Budget(
                max_actions=10,
                max_http_requests=10,
                max_model_calls=1,
                max_total_tokens=5_000,
                max_output_tokens_per_call=128,
                max_wall_seconds=30,
            ),
            "bootstrap_budget": BootstrapBudget(
                max_actions=1, max_http_requests=1, max_wall_seconds=10
            ),
        }
    )


def _call(index: int) -> dict[str, object]:
    return {
        "type": "function_call",
        "name": "finish_report",
        "call_id": f"call-{index}",
        "arguments": json.dumps({"summary": "done"}),
    }


class QueueProvider:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        payload = {
            "model": model.name,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }
        self.requests.append(payload)
        return payload

    async def complete(self, request_payload):
        output = (_call(len(self.requests)),)
        return ModelTurn(
            response_id=f"response-{len(self.requests)}",
            status="completed",
            output=output,
            usage=ModelUsage(input_tokens=200, output_tokens=100),
            raw_response={"output": output},
        )


class CompleteProbeProvider(QueueProvider):
    async def complete(self, request_payload):
        output = () if len(self.requests) == 1 else (_call(len(self.requests)),)
        return ModelTurn(
            response_id=f"response-{len(self.requests)}",
            status="completed",
            output=output,
            usage=ModelUsage(input_tokens=200, output_tokens=100),
            raw_response={"output": list(output)},
        )


def no_docker_runtime(tmp_path: Path, monkeypatch) -> ComposeRangeRuntime:
    runtime = ComposeRangeRuntime(tmp_path)

    async def status(instance_id):
        instance = runtime.state.load_instance(instance_id)
        return RangeInstanceStatus(
            instance_id=instance_id,
            build_id=instance.build_id,
            generation=instance.generation,
            state="healthy",
            checked_at=utc_now(),
        )

    async def metadata(instance_id):
        instance = runtime.state.load_instance(instance_id)
        build = runtime.state.verify_build_integrity(instance.build_id)
        return RangeControllerMetadata(
            instance_id=instance_id,
            build_id=instance.build_id,
            generation=instance.generation,
            project_name=instance.project_name,
            spec_sha256=build.spec_sha256,
            seed=build.spec.seed,
            state="healthy",
            family="saas",
            security_variant="vulnerable",
        )

    monkeypatch.setattr(runtime, "start_instance", status)
    monkeypatch.setattr(runtime, "snapshot_metadata", metadata)
    monkeypatch.setattr(runtime, "destroy_instance", status)
    return runtime


@pytest.mark.postgres
async def test_real_probe_agent_saves_reconstructable_post_tool_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        events = PostgresEventStore(engine)
        runtime = no_docker_runtime(tmp_path, monkeypatch)
        provider = CompleteProbeProvider()
        budget = ReporterBudget(
            max_total_tokens=20_000,
            max_model_calls=1,
            max_output_tokens_per_call=128,
            max_retrieval_calls=2,
            max_finding_submissions=2,
            max_wall_seconds=20,
        )

        class NoBootstrapRunner(PairedReportingContextRunner):
            async def _before_agent(self, spec, context, tools):
                await events.append(
                    PrerequisiteBootstrapCompleted(
                        run_id=context.run_id,
                        actor="controller",
                        snapshot_hash="0" * 64,
                        identity_count=0,
                        workspace_count=0,
                        document_count=0,
                        invoice_count=0,
                        ticket_count=0,
                        action_count=0,
                        http_request_count=0,
                    )
                )

        runner = NoBootstrapRunner(runtime, events, provider, budget, checkpoint_after_calls=1)
        spec = _spec().model_copy(
            update={"budget": _spec().budget.model_copy(update={"max_total_tokens": 120_000})}
        )
        outcome = await runner.run(spec)
        assert outcome.evaluation.score_valid, outcome.failure_reason
        trace = await events.read_run(outcome.run_id)
        assert len(provider.requests) == 3, [
            (event.type, getattr(event, "reason_code", None)) for event in trace[-8:]
        ]
        branches = {item.arm: item.branch_id for item in runner.arm_outcomes[outcome.run_id]}
        audited = await audit_paired_branches(
            events,
            tmp_path,
            outcome.run_id,
            branches,
            provider=provider,
            model=spec.model,
        )
        assert all(item["first_request_bytes_reconstructed"] for item in audited["arms"].values())
    finally:
        await engine.dispose()


@pytest.mark.postgres
async def test_paired_runner_forks_one_fake_probe_without_live_model_or_http(
    tmp_path: Path, monkeypatch
) -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        events = PostgresEventStore(engine)
        runtime = no_docker_runtime(tmp_path, monkeypatch)
        provider = QueueProvider()
        budget = ReporterBudget(
            max_total_tokens=5_000,
            max_model_calls=1,
            max_output_tokens_per_call=128,
            max_retrieval_calls=2,
            max_finding_submissions=2,
            max_wall_seconds=20,
        )

        class FakeProbe:
            def __init__(self, runner):
                self.runner = runner

            async def run(self, task, context, tools):
                trace = await events.read_run(context.run_id)
                range_event = next(x for x in trace if isinstance(x, RangeStarted))
                instance_id = range_event.range_instance_id
                assert instance_id is not None
                action_id, request_id, evidence_id, call_id = (uuid4() for _ in range(4))
                request = RequestArtifact(
                    request_artifact_id=request_id,
                    run_id=context.run_id,
                    action_id=action_id,
                    range_instance_id=instance_id,
                    range_generation=0,
                    destination="saas",
                    method="GET",
                    path="/api/me",
                )
                raw = b"{}"
                evidence = Evidence(
                    evidence_id=evidence_id,
                    run_id=context.run_id,
                    action_id=action_id,
                    range_instance_id=instance_id,
                    range_generation=0,
                    request_artifact_id=request_id,
                    http_status=200,
                    body_b64=base64.b64encode(raw).decode(),
                    response_sha256=hashlib.sha256(raw).hexdigest(),
                )
                instance = tmp_path / "instances" / instance_id.hex
                (instance / "requests").mkdir(parents=True, exist_ok=True)
                (instance / "evidence").mkdir(parents=True, exist_ok=True)
                (instance / "requests" / f"{request_id.hex}.json").write_text(
                    request.model_dump_json()
                )
                (instance / "evidence" / f"{evidence_id.hex}.json").write_text(
                    evidence.model_dump_json()
                )
                await events.append(
                    ActionRequested(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action_id,
                        action_type="http_request",
                        destination="saas",
                        method="GET",
                        path_sha256=hashlib.sha256(b"/api/me").hexdigest(),
                        range_instance_id=instance_id,
                        range_generation=0,
                        request_artifact_id=request_id,
                    )
                )
                await events.append(
                    ActionCompleted(
                        run_id=context.run_id,
                        actor="gateway",
                        action_id=action_id,
                        evidence_id=evidence_id,
                        duration_ms=1,
                        http_status=200,
                        response_sha256=evidence.response_sha256,
                    )
                )
                model_artifacts = ModelCallArtifacts(tmp_path)
                request_id, request_sha = model_artifacts.write(
                    context.run_id, call_id, "request", {"input": "probe"}
                )
                response_id, response_sha = model_artifacts.write(
                    context.run_id, call_id, "response", {"output": []}
                )
                await events.append(
                    ModelCallStarted(
                        run_id=context.run_id,
                        actor="controller",
                        call_id=call_id,
                        provider="fake",
                        model="fake",
                        input_sha256=request_sha,
                        request_artifact_id=request_id,
                        request_sha256=request_sha,
                    )
                )
                await events.append(
                    ModelCallCompleted(
                        run_id=context.run_id,
                        actor="controller",
                        call_id=call_id,
                        provider_status="completed",
                        tool_call_count=0,
                        input_tokens=10,
                        output_tokens=5,
                        response_artifact_id=response_id,
                        response_sha256=response_sha,
                    )
                )
                await self.runner.checkpoints.save(
                    context.run_id,
                    latest_model_call_id=call_id,
                    latest_request_sha256=request_sha,
                    base_items=[{"role": "user", "content": "probe"}],
                    carry_items=[],
                    working_state_text="checked /api/me",
                )
                return AgentResult(task_id=task.task_id, status="completed")

        class FakeProbeRunner(PairedReportingContextRunner):
            async def _before_agent(self, spec, context, tools):
                return None

            def _agent(self, findings_store, spec):
                return FakeProbe(self)

        runner = FakeProbeRunner(runtime, events, provider, budget, checkpoint_after_calls=1)
        assigned_run_id = uuid4()
        result = await runner.run(_spec(), run_id=assigned_run_id)
        assert result.run_id == assigned_run_id
        assert result.evaluation.score_valid
        outcomes = runner.arm_outcomes[result.run_id]
        assert {item.arm for item in outcomes} == {"fresh", "continuation"}
        assert all(item.evaluation.score_valid for item in outcomes)
        assert len(provider.requests) == 2
        assert provider.requests[0]["tools"] == provider.requests[1]["tools"]
        audit = await audit_paired_branches(
            events,
            tmp_path,
            result.run_id,
            {item.arm: item.branch_id for item in outcomes},
            provider=provider,
            model=_spec().model,
        )
        assert audit["arms"]["fresh"]["model_calls"] == 1
        assert audit["arms"]["continuation"]["model_calls"] == 1
        assert all(arm["first_request_bytes_reconstructed"] for arm in audit["arms"].values())

        class DriftProvider(QueueProvider):
            def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
                result = super().prepare_request(
                    model, instructions, input_items, tools, max_output_tokens
                )
                result["instructions"] = "changed after collection"
                return result

        with pytest.raises(ValueError, match="byte for byte"):
            await audit_paired_branches(
                events,
                tmp_path,
                result.run_id,
                {item.arm: item.branch_id for item in outcomes},
                provider=DriftProvider(),
                model=_spec().model,
            )
        for item in outcomes:
            branch = ReportingBranchStore(events, item.branch_id, result.run_id)
            trace = await branch.read_run(result.run_id)
            assert len([x for x in trace if isinstance(x, ReportingBranchStarted)]) == 1
            assert len([x for x in trace if isinstance(x, ReporterStarted)]) == 1
            assert len([x for x in trace if isinstance(x, ActionRequested)]) == 1
            source_range = next(x for x in trace if isinstance(x, RangeStarted))
            oracle = StateOracleStore(runtime.state).load_for_context(
                ValidationContext(
                    run_id=result.run_id,
                    range_instance_id=source_range.range_instance_id,
                    range_generation=source_range.range_generation,
                    build_id=result.build_id,
                )
            )
            assert await replay_branch_score(branch, oracle) == item.evaluation
        source = await events.read_run(result.run_id)
        assert not any(isinstance(x, ReporterStarted) for x in source)
    finally:
        await engine.dispose()
