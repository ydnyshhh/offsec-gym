"""Both Range B model arms use the same live surface and audited gateway."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from test_enterprise_experiment import MemoryEvents

from offsecgym.providers.base import ModelTurn
from offsecgym.research.m66_pair import WitnessPlanningPairRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.enterprise import FAMILY
from offsecgym.schemas.events import ActionRequested
from offsecgym.schemas.specs import BootstrapBudget, Budget, ExperimentSpec, ModelSpec, RangeSpec
from offsecgym.solver.monolithic import model_tools


class OneReadProvider:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def prepare_request(self, model, instructions, input_items, tools, max_output_tokens):
        return {
            "model": model.name,
            "instructions": instructions,
            "input": list(input_items),
            "tools": tools,
            "max_output_tokens": max_output_tokens,
        }

    async def complete(self, request_payload):
        self.requests.append(request_payload)
        if len(self.requests) == 1:
            match = re.search(
                r"Allowed identity IDs: ([0-9a-f-]{36})",
                request_payload["input"][0]["content"],
            )
            assert match is not None
            self.identity_id = match.group(1)
            output = (
                {
                    "type": "function_call",
                    "call_id": "read_me",
                    "name": "http_request",
                    "arguments": json.dumps(
                        {
                            "method": "GET",
                            "path": "/api/me",
                            "identity_id": match.group(1),
                            "body_json": None,
                        }
                    ),
                },
            )
        elif len(self.requests) == 2:
            latest = next(
                item
                for item in reversed(request_payload["input"])
                if item.get("type") == "function_call_output"
            )
            observed = json.loads(latest["output"])
            output = (
                {
                    "type": "function_call",
                    "call_id": "unsupported_finding",
                    "name": "submit_finding",
                    "arguments": json.dumps(
                        {
                            "claim": "A cited read alone cannot prove an enterprise transition",
                            "asset_id": self.identity_id,
                            "evidence": [
                                {
                                    "action_id": observed["action_id"],
                                    "evidence_id": observed["evidence_id"],
                                    "description": "identity read",
                                }
                            ],
                            "root_cause_hypothesis": None,
                            "subject_role": "change_approver",
                            "action": "POST /api/access-requests/{id}/approve",
                            "resource_type": "access_request",
                            "from_state": "PENDING",
                            "to_state": "APPLIED",
                        }
                    ),
                },
            )
        else:
            output = ({"type": "message", "role": "assistant", "content": []},)
        raw = {
            "id": f"fake-{len(self.requests)}",
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


def test_enterprise_tool_surface_has_only_witness_intervention() -> None:
    control = {tool["name"] for tool in model_tools(structured=True, family=FAMILY)}
    treatment = {
        tool["name"] for tool in model_tools(structured=True, family=FAMILY, witness_planning=True)
    }
    assert control == treatment - {"start_witness", "get_witness"}
    assert "submit_finding" in control
    assert not any(name.startswith("submit_authorization") for name in control)
    enterprise_entity = next(
        tool for tool in model_tools(structured=True, family=FAMILY) if tool["name"] == "get_entity"
    )
    saas_entity = next(
        tool for tool in model_tools(structured=True, family="saas") if tool["name"] == "get_entity"
    )
    assert "job" in enterprise_entity["parameters"]["properties"]["entity_type"]["enum"]
    assert "invoice" not in enterprise_entity["parameters"]["properties"]["entity_type"]["enum"]
    assert "job" not in saas_entity["parameters"]["properties"]["entity_type"]["enum"]


@pytest.mark.docker
@pytest.mark.parametrize("patched", (False, True))
async def test_enterprise_control_and_witness_use_same_gateway(
    tmp_path: Path, patched: bool
) -> None:
    available = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if available.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {available.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")
    range_spec = RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[2] / "examples/enterprise-change-control.yaml").read_text()
        )
    )
    range_spec = range_spec.model_copy(update={"patched": patched})
    spec = ExperimentSpec(
        name="enterprise_fake_model_arms",
        seed=42,
        range=range_spec,
        budget=Budget(
            max_actions=8,
            max_http_requests=8,
            max_model_calls=4,
            max_total_tokens=8000,
            max_output_tokens_per_call=500,
            max_wall_seconds=180,
        ),
        bootstrap_budget=BootstrapBudget(
            max_actions=40,
            max_http_requests=40,
            max_wall_seconds=90,
        ),
        orchestrator="bootstrapped_monolithic",
        memory="structured",
        surface_visibility="known_routes",
        validation="deterministic",
        model=ModelSpec(provider="openai", name="fake-provider"),
    )
    providers = []

    def provider_factory():
        provider = OneReadProvider()
        providers.append(provider)
        return provider

    events = MemoryEvents()
    pair = await WitnessPlanningPairRunner(
        ComposeRangeRuntime(tmp_path), events, provider_factory
    ).run_pair(spec)
    assert pair.control.build_id == pair.witness.build_id == pair.build_id
    first_requests = {}
    for name, outcome, provider in (
        ("control", pair.control, providers[0]),
        ("witness", pair.witness, providers[1]),
    ):
        assert outcome.evaluation.score_valid
        assert outcome.evaluation.status == "completed"
        assert len(provider.requests) == 3
        assert outcome.evaluation.false_positives == 1
        first_requests[name] = provider.requests[0]
        instructions = provider.requests[0]["instructions"]
        assert "POST /api/changes/{id}/deploy" in instructions
        assert "POST /api/invoices/{id}/refund" not in instructions
        requests = [
            event
            for event in events.items
            if isinstance(event, ActionRequested) and event.run_id == outcome.run_id
        ]
        agent_reads = [event for event in requests if event.source_phase is None]
        assert len(agent_reads) == 1
        assert agent_reads[0].destination == FAMILY
    assert first_requests["control"]["input"][0] == first_requests["witness"]["input"][0]
    assert first_requests["witness"]["instructions"].startswith(
        first_requests["control"]["instructions"]
    )
