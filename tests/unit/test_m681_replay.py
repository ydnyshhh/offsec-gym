"""Aggregate historical replay keeps proposal details private."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

replay = runpy.run_path(str(Path(__file__).resolve().parents[2] / "research_ops/m681_replay.py"))[
    "replay"
]

UUID_TEXT = "123e4567-e89b-12d3-a456-426614174000"


def _row(index: int, reference: str) -> dict[str, object]:
    return {
        "tool_call_id": f"private-proposal-{index}",
        "arguments": {
            "hypothesis": "synthetic",
            "subject_identity": None,
            "target_objects": [reference],
            "preconditions": [],
            "planned_steps": ["observe"],
            "expected_positive_observation": "changed",
            "expected_negative_observation": "unchanged",
            "required_evidence": [],
        },
        "references": [
            {
                "context_observed": False,
                "public_source_grounded": None,
                "object_resolved": None,
            }
        ],
        "would_support_correct_experiment": None,
    }


def test_aggregate_uses_all_target_rule_and_excludes_private_values() -> None:
    rows = [_row(i, f"project:{UUID_TEXT}") for i in range(18)]
    rows[-1] = _row(17, f"project:{UUID_TEXT}:extra")
    public, private = replay(rows, "input-sha")
    assert public["proposals"] == {
        "total": 18,
        "strict_schema_accepted": 0,
        "normalization_eligible": 17,
        "post_normalization_schema_accepted": 17,
    }
    assert public["references"]["syntax_accepted"] == 17
    assert public["public_source_grounded"] is None
    assert len(private) == 18
    assert private[-1]["references"][0]["canonical_normalized_uuid"] is None
    public_text = json.dumps(public)
    assert UUID_TEXT not in public_text
    assert "private-proposal" not in public_text


def test_replay_rejects_incomplete_or_duplicate_private_inventory() -> None:
    with pytest.raises(ValueError, match="exactly 18"):
        replay([_row(0, UUID_TEXT)], "input-sha")
    with pytest.raises(ValueError, match="identity"):
        replay([_row(0, UUID_TEXT) for _ in range(18)], "input-sha")
