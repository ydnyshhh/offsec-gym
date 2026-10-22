"""No-model, no-identifier taxonomy checks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest

from offsecgym.research.m681_rejection_taxonomy import (
    PROPOSAL_DISPOSITIONS,
    REFERENCE_CATEGORIES,
    classify_proposals,
    reference_category,
)

OBJECT = UUID("11111111-1111-4111-8111-111111111111")
ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))
from research_ops.m6811_taxonomy import generate  # noqa: E402


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (str(OBJECT), "canonical_uuid"),
        (f"project:{OBJECT}", "supported_typed_uuid"),
        (f"change:{OBJECT}", "unsupported_typed_uuid"),
        (f"project {OBJECT}", "other_uuid_wrapper"),
        (f"project:{OBJECT} {OBJECT}", "multiple_identifiers"),
        ("change workflow endpoints", "natural_language_reference"),
        ("!", "malformed_reference"),
        (None, "other"),
    ],
)
def test_reference_categories(value, expected):
    assert reference_category(value) == expected


def _row(targets: list[str], proposal_id: str) -> dict[str, object]:
    return {
        "tool_call_id": proposal_id,
        "arguments": {
            "hypothesis": "Synthetic check",
            "subject_identity": None,
            "target_objects": targets,
            "preconditions": [],
            "planned_steps": ["Observe"],
            "expected_positive_observation": "Observed",
            "expected_negative_observation": "Absent",
            "required_evidence": [],
        },
        "references": [
            {"context_observed": True, "public_source_grounded": None, "object_resolved": None}
            for _ in targets
        ],
        "would_support_correct_experiment": None,
    }


def test_exclusive_dispositions_and_public_privacy():
    rows = [
        _row([f"project:{OBJECT}"], "private-a"),
        _row([f"project:{OBJECT}", f"change:{OBJECT}"], "private-b"),
        _row([f"project {OBJECT}"], "private-c"),
    ]
    public, private = classify_proposals(rows, require_historical_counts=False)
    assert public["proposal_count"] == 3
    assert sum(public["proposal_dispositions"].values()) == 3
    assert public["proposal_dispositions"]["fully_eligible_and_schema_valid"] == 1
    assert public["proposal_dispositions"]["mixed_eligible_and_ineligible_references"] == 1
    assert public["proposal_dispositions"]["ambiguous_reference_syntax"] == 1
    assert public["proposal_issue_flags_nonexclusive"]["unsupported_kind"] == 1
    assert len(private) == 3
    assert all(row["disposition"] in PROPOSAL_DISPOSITIONS for row in private)
    assert "private-" not in json.dumps(public)
    assert str(OBJECT) not in json.dumps(public)


def test_checked_in_historical_aggregate_is_exhaustive_and_pinned():
    public = json.loads(
        (ROOT / "docs/diagnostics/m681-reference-failure-taxonomy.json").read_text()
    )
    assert public["proposal_count"] == 18
    assert public["reference_count"] == 43
    assert public["strict_schema_accepted_proposals"] == 0
    assert public["normalization_eligible_proposals"] == 4
    assert public["normalized_schema_accepted_proposals"] == 4
    assert public["frozen_grammar_accepted_references"] == 27
    assert sum(public["proposal_dispositions"].values()) == 18
    assert sum(public["reference_categories"].values()) == 43
    assert set(public["reference_categories"]) == set(REFERENCE_CATEGORIES)
    assert public["retained_private_input_sha256"] == (
        "34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647"
    )


def test_private_historical_reconstruction_when_pinned_input_is_available():
    path = os.getenv("OFFSECGYM_M6811_PRIVATE_INPUT")
    if not path:
        pytest.skip("pinned private input not mounted")
    public, private = generate(Path(path))
    checked_in = json.loads(
        (ROOT / "docs/diagnostics/m681-reference-failure-taxonomy.json").read_text()
    )
    assert public == checked_in
    assert len(private) == 18
    assert all(row["disposition"] in PROPOSAL_DISPOSITIONS for row in private)
