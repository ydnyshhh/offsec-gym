"""Sequential workers share exact state through the real range and validator."""

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
from offsecgym.schemas.events import FindingSubmitted, WorkerPacketPrepared
from offsecgym.schemas.specs import Budget, ExperimentSpec, ModelSpec, RangeSpec
from offsecgym.storage.event_store import PostgresEventStore


class HandoffPreviewProvider:
    def __init__(self) -> None:
        self.steps: dict[str, int] = {}
        self.identity_id: str | None = None
        self.invoice_id: str | None = None
        self.prior_invoice_evidence: dict[str, str] | None = None

    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        return {
            "model": model.name,
            "instructions": instructions,
            "input": list(input_items),
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }

    async def complete(self, request_payload):
        items = request_payload["input"]
        prompt = items[0]["content"]
        objective = re.search(r"Goal: ([^.]+)\.", prompt).group(1)
        step = self.steps.get(objective, 0)
        self.steps[objective] = step + 1
        latest = next(
            (item for item in reversed(items) if item.get("type") == "function_call_output"),
            None,
        )
        name = None
        args = None
        if objective.startswith("Map identities"):
            if step == 0:
                self.identity_id = re.search(
                    r"Allowed identity IDs: ([0-9a-f-]{36})", prompt
                ).group(1)
                name = "http_request"
                args = {
                    "method": "GET",
                    "path": "/api/me",
                    "identity_id": self.identity_id,
                    "body_json": None,
                }
            elif step == 1:
                account = json.loads(json.loads(latest["output"])["body_text"])
                name = "http_request"
                args = {
                    "method": "GET",
                    "path": f"/api/workspaces/{account['workspace_id']}/invoices",
                    "identity_id": self.identity_id,
                    "body_json": None,
                }
            elif step == 2:
                invoices = json.loads(json.loads(latest["output"])["body_text"])
                self.invoice_id = invoices["items"][0]["id"]
                name = "http_request"
                args = {
                    "method": "GET",
                    "path": f"/api/invoices/{self.invoice_id}",
                    "identity_id": self.identity_id,
                    "body_json": None,
                }
        elif objective.startswith("Test anonymous public"):
            if step == 0:
                packet = json.loads(prompt.split("Worker packet: ", 1)[1])
                invoices = [
                    item
                    for item in packet["relevant_entities"]
                    if item["entity_type"] == "invoice" and item["entity_id"] == self.invoice_id
                ]
                assert invoices, "invoice discovered by first worker must reach public worker"
                prior_actions = {
                    item["action_id"]: item for item in packet["prior_checked_actions"]
                }
                self.prior_invoice_evidence = next(
                    {
                        "action_id": item["action_id"],
                        "evidence_id": item["evidence_id"],
                        "description": "same-invoice detail from identity worker",
                    }
                    for item in packet["relevant_evidence"]
                    if item["action_id"] in prior_actions
                    and prior_actions[item["action_id"]]["path"]
                    == f"/api/invoices/{self.invoice_id}"
                )
                name = "http_request"
                args = {
                    "method": "GET",
                    "path": f"/api/public/invoices/{self.invoice_id}/preview",
                    "identity_id": None,
                    "body_json": None,
                }
            elif step == 1:
                observed = json.loads(latest["output"])
                assert "billing_email" in observed["body_text"]
                name = "submit_exposure_finding"
                args = {
                    "claim": "Anonymous invoice preview exposed billing email",
                    "asset_id": self.invoice_id,
                    "evidence": [
                        {
                            "action_id": observed["action_id"],
                            "evidence_id": observed["evidence_id"],
                            "description": "public preview",
                        },
                        self.prior_invoice_evidence,
                    ],
                    "root_cause_hypothesis": None,
                    "subject_role": "anonymous",
                    "action": "GET /api/public/invoices/{id}/preview",
                    "resource_type": "invoice",
                    "forbidden_fields": ["billing_email"],
                }
        output = (
            (
                {
                    "type": "function_call",
                    "call_id": f"{objective}:{step}",
                    "name": name,
                    "arguments": json.dumps(args),
                },
            )
            if name is not None
            else ({"type": "message", "role": "assistant", "content": []},)
        )
        raw = {
            "id": f"{objective}:{step}",
            "status": "completed",
            "output": list(output),
            "usage": {"input_tokens": 200, "output_tokens": 30},
        }
        return ModelTurn(
            response_id=raw["id"],
            status="completed",
            output=output,
            usage=raw["usage"],
            raw_response=raw,
        )


@pytest.mark.postgres
@pytest.mark.docker
async def test_sequential_worker_handoff_validates_public_exposure(tmp_path: Path) -> None:
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
    spec = ExperimentSpec(
        name="sequential_mock_handoff",
        seed=1,
        range=base,
        budget=Budget(
            max_actions=18,
            max_http_requests=18,
            max_model_calls=24,
            max_total_tokens=120000,
            max_output_tokens_per_call=128,
            max_workers=6,
            max_concurrency=1,
        ),
        orchestrator="ephemeral_workers",
        memory="structured",
        surface_visibility="known_routes",
        model=ModelSpec(provider="openai", name="mock-model"),
        validation="deterministic",
    )
    engine = create_async_engine(url)
    try:
        events = PostgresEventStore(engine)
        provider = HandoffPreviewProvider()
        outcome = await WorkerExperimentRunner(ComposeRangeRuntime(tmp_path), events, provider).run(
            spec
        )
        assert outcome.evaluation.status == "completed"
        assert outcome.evaluation.true_positives == 1
        trace = await events.read_run(outcome.run_id)
        packets = [item for item in trace if isinstance(item, WorkerPacketPrepared)]
        assert len(packets) == 6
        public_packet = packets[4].packet
        assert provider.invoice_id is not None
        assert any(
            str(item.entity_id) == provider.invoice_id for item in public_packet.relevant_entities
        )
        assert any(
            item.source_worker_id == packets[0].worker_id
            for item in public_packet.relevant_evidence
        )
        findings = [item for item in trace if isinstance(item, FindingSubmitted)]
        assert len(findings) == 1
        assert findings[0].worker_id == packets[4].worker_id
        assert findings[0].task_id == packets[4].task_id
        metrics = orchestration_metrics(trace)
        assert metrics.http_dispatches == 4
        assert metrics.cross_worker_duplication_rate == 0
        assert metrics.coordinator_model_calls == 0
        assert metrics.findings_reusing_cross_worker_evidence == 1
        assert metrics.evidence_reuse_rate == 1
        assert metrics.time_to_first_valid_finding_seconds is not None
    finally:
        await engine.dispose()
