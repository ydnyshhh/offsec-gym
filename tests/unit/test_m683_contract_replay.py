"""Small synthetic replay cases; production admission verifies pinned hashes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from offsecgym.research.m683_contract_replay import replay

OBJECT = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


def _arguments(targets: list[str]) -> dict[str, object]:
    return {
        "hypothesis": "Fabricated observation",
        "subject_identity": None,
        "target_objects": targets,
        "preconditions": [],
        "planned_steps": ["Observe"],
        "expected_positive_observation": "Recorded",
        "expected_negative_observation": "Absent",
        "required_evidence": [],
    }


def _rows(targets: list[str], grounding: list[bool | None]):
    historical = [
        {
            "tool_call_id": "synthetic-call",
            "arguments": _arguments(targets),
            "references": [{"original_value": value} for value in targets],
        }
    ]
    refs = [
        {
            "raw_reference": value,
            "public_source_grounded": status,
            "object_resolution_status": "unique_typed" if status else "no_verified_public_match",
            "kind_match": True if status else None,
            "normalized_identifier": value.split(":")[-1],
            "offline_oracle_correspondence": {"target_root_match": False},
        }
        for value, status in zip(targets, grounding, strict=True)
    ]
    public = [
        {
            "ordinal": 0,
            "tool_call_id": "synthetic-call",
            "references": refs,
            "all_references_grounded": all(status is True for status in grounding),
            "all_references_correctly_typed": all(status is True for status in grounding),
            "public_relationship_edges": [],
        }
    ]
    return historical, public


def test_alias_adds_reference_and_whole_proposal_when_complete() -> None:
    historical, grounding = _rows([f"change:{OBJECT}"], [True])
    original = deepcopy((historical, grounding))
    public, private = replay(historical, grounding, require_historical_counts=False)
    assert (historical, grounding) == original
    assert public["contracts"]["C1"]["reference_level"]["rejected"] == 1
    assert public["contracts"]["C2"]["reference_level"]["accepted"] == 1
    assert public["C1_to_C2_marginal_references"]["total"] == 1
    assert public["C1_to_C2_marginal_references"]["grounded:true"] == 1
    assert public["C1_to_C2_marginal_proposals"]["total"] == 1
    assert public["contracts"]["C2"]["proposal_level"]["eligible_and_all_publicly_grounded"] == 1
    assert private[0]["contracts"]["C2"]["normalized_target_uuids"] == (OBJECT,)


def test_alias_does_not_salvage_mixed_array() -> None:
    historical, grounding = _rows([f"change:{OBJECT}", f"unknown:{SECOND}"], [True, False])
    public, private = replay(historical, grounding, require_historical_counts=False)
    assert public["C1_to_C2_marginal_references"]["total"] == 1
    assert public["C1_to_C2_marginal_proposals"].get("total", 0) == 0
    assert public["contracts"]["C2"]["proposal_level"]["mixed_accepted_and_rejected"] == 1
    assert private[0]["reference_count"] == 2
    assert private[0]["contracts"]["C2"]["failure_category"] == "reference:unknown_kind"
    assert private[0]["references"][0]["grounding"]["public_relationship_established"] is None


def test_grounding_is_independent_of_syntax() -> None:
    historical, grounding = _rows([f"change:{OBJECT}"], [None])
    public, _ = replay(historical, grounding, require_historical_counts=False)
    assert public["contracts"]["C2"]["grounding_stratification"]["accepted:unknown"] == 1
    assert public["C1_to_C2_marginal_references"]["grounded:unknown"] == 1


def test_misaligned_reference_aborts() -> None:
    historical, grounding = _rows([f"change:{OBJECT}"], [True])
    grounding[0]["references"][0]["raw_reference"] = "different"
    with pytest.raises(ValueError, match="alignment|bytes"):
        replay(historical, grounding, require_historical_counts=False)
