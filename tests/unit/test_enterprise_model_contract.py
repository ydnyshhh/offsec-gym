"""The model's enterprise finding tool must exactly match scored contracts."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from offsecgym.runtime.enterprise import fixture_for_seed, oracle_for_fixture
from offsecgym.schemas.domain import EvidenceRef
from offsecgym.schemas.specs import ModelSpec
from offsecgym.solver.monolithic import EnterpriseFindingArgs, MonolithicSecurityAgent


class RecordingFindings:
    def __init__(self) -> None:
        self.proposals = []

    async def submit(self, proposal, **_kwargs):
        self.proposals.append(proposal)
        return SimpleNamespace(finding_id=uuid4())


@pytest.mark.asyncio
async def test_enterprise_finding_tool_matches_each_hidden_property(tmp_path) -> None:
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


def test_enterprise_finding_rejects_cross_root_transition() -> None:
    with pytest.raises(ValidationError, match="supported transition"):
        EnterpriseFindingArgs(
            claim="invalid",
            asset_id=uuid4(),
            evidence=(
                EvidenceRef(
                    action_id=uuid4(),
                    evidence_id=uuid4(),
                    description="unsupported",
                ),
            ),
            root_cause_hypothesis=None,
            subject_role="operator",
            action="POST /api/changes/{id}/deploy",
            resource_type="change_request",
            from_state="PENDING",
            to_state="SUCCEEDED",
        )
