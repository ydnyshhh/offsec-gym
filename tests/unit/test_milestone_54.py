"""M5.4 persistent checked state and bounded retrieval carryover."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from test_milestone_3_runner import MemoryEvents, no_docker_runtime
from test_milestone_4 import QueueProvider, model_spec, turn
from test_milestone_52 import _result
from test_milestone_53 import _record

from offsecgym.experiment import MonolithicExperimentRunner
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import (
    MEMORY_CONTRIBUTION_MAX_CHARS,
    WORLD_TOOL_CARRY_MAX_CHARS,
    _bounded_world_result,
)
from offsecgym.worldview import EventWorldState, WorldContextBuilder
from offsecgym.worldview.working_set import ActiveWorkingSet


@pytest.mark.asyncio
async def test_checked_identity_survives_object_recency_eviction() -> None:
    events = MemoryEvents()
    state = EventWorldState(events)
    run_id = uuid4()
    working = ActiveWorkingSet(max_entities=2)
    checked = []
    for _ in range(8):
        identity, workspace = uuid4(), uuid4()
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path="/api/me",
            identity_id=identity,
        )
        result = _result(
            request,
            {"id": str(identity), "role": "member", "workspace_id": str(workspace)},
        )
        facts = await _record(state, events, request, result)
        working.observe(request, result, facts)
        checked.append((identity, workspace, request.action_id, result.evidence_id))
    object_actions = []
    for _ in range(26):
        document, identity = uuid4(), checked[0][0]
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/documents/{document}",
            identity_id=identity,
        )
        result = _result(request, {"error": "forbidden"}, status=403)
        working.observe(request, result, ())
        object_actions.append(request)
    assert len(working.snapshot()) == 2
    identity_lines, fact_ids = working.render_identities(max_chars=1900)
    identity_text = "\n".join(identity_lines)
    for identity, workspace, action, evidence in checked:
        assert f"identity:{identity} role=member workspace={workspace}" in identity_text
        assert f"action={action} evidence={evidence}" in identity_text
    assert len(fact_ids) == 16
    context = await WorldContextBuilder(state, events).build(
        run_id, "inspect documents", working_set=working
    )
    assert len(context.text) <= 7500
    assert "Checked identities" in context.text
    assert "Previously checked requests" in context.text
    for identity, workspace, _, _ in checked:
        assert f"identity:{identity} role=member workspace={workspace}" in context.text
    checked_section = context.text.split("Previously checked requests", 1)[1].split(
        "Active entity/evidence", 1
    )[0]
    assert len([line for line in checked_section.splitlines() if line.startswith("- GET")]) >= 24
    assert str(object_actions[-12].action_id) in checked_section
    compact = await WorldContextBuilder(state, events).build(
        run_id, str(checked[0][0]), max_facts=8, max_chars=850
    )
    assert 'role="member"' in compact.text


def test_checked_action_index_is_exact_bounded_and_separate_from_identities() -> None:
    working = ActiveWorkingSet(max_entities=1, max_checked_actions=3)
    run_id, identity = uuid4(), uuid4()
    records = []
    for _ in range(4):
        document = uuid4()
        request = ActionRequest(
            run_id=run_id,
            kind="http_request",
            destination="saas",
            method="GET",
            path=f"/api/documents/{document}",
            identity_id=identity,
        )
        result = _result(request, {"id": str(document)})
        working.observe(request, result, ())
        records.append((request, result))
    text = "\n".join(working.render_checked_actions(max_chars=3000))
    assert records[0][0].path not in text
    for request, result in records[1:]:
        assert request.path in text
        assert f"a={request.action_id} e={result.evidence_id}" in text
        assert result.response_sha256[:16] in text
    repeated = records[1][0].model_copy(update={"action_id": uuid4()})
    result = _result(repeated, {"id": str(uuid4())})
    working.observe(repeated, result, ())
    assert working.fingerprint(repeated) == working.fingerprint(records[1][0])
    assert f"a={repeated.action_id}" in "\n".join(working.render_checked_actions(max_chars=3000))


@pytest.mark.asyncio
async def test_two_worldview_queries_share_one_bounded_carry_budget(
    tmp_path: Path, monkeypatch
) -> None:
    queries = [
        {
            "type": "function_call",
            "call_id": f"query_{index}",
            "name": "query_worldview",
            "arguments": json.dumps({"query": "invoice access", "kind": None}),
        }
        for index in (1, 2)
    ]
    provider = QueueProvider(turn(*queries), turn())
    events = MemoryEvents()
    spec = model_spec(tokens=500, calls=3).model_copy(update={"memory": "structured"})
    outcome = await MonolithicExperimentRunner(
        no_docker_runtime(tmp_path, monkeypatch), events, provider
    ).run(spec)
    assert outcome.evaluation.status == "completed"
    second_input = provider.requests[1]["input"]
    outputs = [
        item["output"] for item in second_input if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 2
    assert sum(map(len, outputs)) <= WORLD_TOOL_CARRY_MAX_CHARS
    contexts = [
        item["content"]
        for item in second_input
        if item.get("role") == "user" and "Relevant world facts" in item.get("content", "")
    ]
    assert len(contexts) == 1
    assert len(contexts[0]) + sum(map(len, outputs)) <= MEMORY_CONTRIBUTION_MAX_CHARS


def test_world_result_bounding_preserves_complete_json() -> None:
    output = {
        "fact_ids": [str(uuid4()) for _ in range(8)],
        "summary": "\n".join(f"- fact {i}: " + "x" * 150 for i in range(20)),
    }
    bounded = _bounded_world_result(output, 1200)
    assert len(json.dumps(bounded, separators=(",", ":"))) <= 1200
    assert bounded["truncated"] is True
    assert bounded["summary"].endswith("x" * 150)
    entity = {
        "found": True,
        "entity": {
            "type": "document",
            "id": str(uuid4()),
            "attributes": {"body_excerpt": "x" * 512, "status": "open"},
            "evidence_groups": [
                {"action_id": str(uuid4()), "evidence_id": str(uuid4()), "fields": []}
                for _ in range(8)
            ],
        },
    }
    limited_entity = _bounded_world_result(entity, 1200)
    assert len(json.dumps(limited_entity, separators=(",", ":"))) <= 1200
    assert limited_entity["entity"]["id"] == entity["entity"]["id"]


def test_m54_config_preserves_m53_experiment_factors() -> None:
    root = Path(__file__).resolve().parents[2] / "experiments" / "configs"
    m53 = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-structured-m53.yaml").read_text())
    )
    m54 = ExperimentSpec.model_validate(
        yaml.safe_load((root / "kimi-k3-structured-m54.yaml").read_text())
    )
    assert m54.model_dump(mode="json", exclude={"name"}) == m53.model_dump(
        mode="json", exclude={"name"}
    )
