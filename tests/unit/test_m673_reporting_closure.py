"""Exclusive reporting units and model-originated closure qualification."""

from __future__ import annotations

import pytest

from offsecgym.research.m673_experiment_design import qualify_model_authored_closure
from offsecgym.research.m673_reporting_reconciliation import reconcile_dispositions


def test_reporting_flags_can_overlap_without_double_counting() -> None:
    rows = [
        {
            "run_id": "r",
            "model_call_id": "m1",
            "tool_call_id": "t1",
            "final_disposition": "proposal_never_processed",
            "validation_errors": ["schema", "unprocessed"],
        },
        {
            "run_id": "r",
            "model_call_id": "m2",
            "tool_call_id": "t2",
            "final_disposition": "recorded_finding",
            "validation_errors": [],
        },
    ]
    assert reconcile_dispositions(rows) == {"proposal_never_processed": 1, "recorded_finding": 1}
    with pytest.raises(ValueError, match="duplicate"):
        reconcile_dispositions(rows + [rows[0]])


def test_successful_action_alone_is_not_evidence_closure() -> None:
    result = qualify_model_authored_closure(
        action_completed=True,
        observation_requested=False,
        observation_completed=False,
        same_object=True,
        same_identity=True,
        before_terminal=True,
    )
    assert result["successful_action"]
    assert not result["complete_observation"]


@pytest.mark.parametrize("field", ["same_object", "same_identity", "before_terminal"])
def test_wrong_object_identity_or_timing_invalidates_closure(field: str) -> None:
    inputs = dict(
        action_completed=True,
        observation_requested=True,
        observation_completed=True,
        same_object=True,
        same_identity=True,
        before_terminal=True,
    )
    inputs[field] = False
    assert not qualify_model_authored_closure(**inputs)["complete_observation"]
    inputs[field] = True
    assert qualify_model_authored_closure(**inputs)["complete_observation"]
