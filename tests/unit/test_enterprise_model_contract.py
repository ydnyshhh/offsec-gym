"""The public finding tool stays generic while private validation remains exact."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from offsecgym.runtime.enterprise import FAMILY, fixture_for_seed, oracle_for_fixture
from offsecgym.schemas.domain import EvidenceRef
from offsecgym.schemas.specs import ModelSpec
from offsecgym.solver.monolithic import (
    EnterpriseFindingArgs,
    MonolithicSecurityAgent,
    model_tools,
)
from offsecgym.solver.range_surface import range_surface


class RecordingFindings:
    def __init__(self) -> None:
        self.proposals = []

    async def submit(self, proposal, **_kwargs):
        self.proposals.append(proposal)
        return SimpleNamespace(finding_id=uuid4())


@pytest.mark.asyncio
async def test_private_dispatch_still_matches_each_hidden_property(tmp_path) -> None:
    fixture, anchors = fixture_for_seed(42)
    oracle = oracle_for_fixture(fixture, anchors, frozenset(), uuid4(), uuid4())
    sink = RecordingFindings()
    agent = MonolithicSecurityAgent(
        provider=None,
        model=ModelSpec(provider="openai", name="fake"),
        findings=sink,
        events=None,
        state_root=tmp_path,
    )
    cases = (
        ("B1-SOD", "b1_request"),
        ("B2-REVOKED-ROLE", "b2_change"),
        ("B3-CANCELLED-JOB", "b3_change"),
    )
    for slug, anchor in cases:
        prop = next(item for item in oracle.properties if item.slug == slug)
        args = EnterpriseFindingArgs(
            claim="Synthetic temporal proof",
            asset_id=anchors[anchor],
            evidence=(
                EvidenceRef(
                    action_id=uuid4(),
                    evidence_id=uuid4(),
                    description="observed state",
                ),
            ),
            root_cause_hypothesis=None,
            subject_role=prop.expectation.subject_role,
            action=prop.expectation.action,
            resource_type=prop.expectation.resource_type,
            from_state=prop.expectation.from_state,
            to_state=prop.expectation.to_state,
            expected_behavior="The action should have been denied",
            observed_behavior="The cited response shows the state transition",
        )
        await agent._dispatch(
            "submit_finding",
            args,
            uuid4(),
            uuid4(),
            None,
            uuid4(),
            uuid4(),
            "fake-tool",
            None,
            [],
            [],
            None,
            2000,
            "enterprise_change_control_v1",
        )
        proposal = sink.proposals[-1]
        assert proposal.family == prop.family
        assert proposal.security_property == prop.expectation
        assert proposal.asset_id == prop.object.object_id


def _candidate(**overrides: str) -> dict:
    return {
        "claim": "Observed an unexpected workflow transition",
        "asset_id": uuid4(),
        "evidence": (
            EvidenceRef(action_id=uuid4(), evidence_id=uuid4(), description="transition"),
        ),
        "root_cause_hypothesis": None,
        "subject_role": "requester",
        "action": "POST /api/access-requests/{id}/reject",
        "resource_type": "access_request",
        "from_state": "SUBMITTED",
        "to_state": "REJECTED",
        "expected_behavior": "The request should remain pending",
        "observed_behavior": "The request became rejected",
        **overrides,
    }


def test_public_finding_schema_does_not_enumerate_hidden_transitions() -> None:
    tool = next(tool for tool in model_tools(family=FAMILY) if tool["name"] == "submit_finding")
    properties = tool["parameters"]["properties"]
    for field in ("subject_role", "action", "resource_type", "from_state", "to_state"):
        assert properties[field]["type"] == "string"
        assert "enum" not in properties[field]
    assert "separation of duties" not in range_surface(FAMILY).objective.lower()
    assert "cancelled queued" not in range_surface(FAMILY).objective.lower()
    assert EnterpriseFindingArgs(**_candidate()).action.endswith("/reject")
    assert (
        EnterpriseFindingArgs(
            **_candidate(action="POST /api/changes/{id}/deploy", from_state="PENDING")
        ).from_state
        == "PENDING"
    )


def test_enterprise_finding_accepts_only_real_public_mutation_routes() -> None:
    with pytest.raises(ValidationError, match="public mutating route"):
        EnterpriseFindingArgs(**_candidate(action="GET /api/changes/{id}"))
    with pytest.raises(ValidationError, match="public mutating route"):
        EnterpriseFindingArgs(**_candidate(action="POST /api/hidden-root/{id}/approve"))
