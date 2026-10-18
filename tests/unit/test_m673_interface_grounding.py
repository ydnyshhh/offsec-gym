"""Parsing, grounding, and no-model interface controls."""

from __future__ import annotations

import json

from offsecgym.research.m673_experiment_design import replay_argument_schemas
from offsecgym.research.m673_interface_grounding import ground_reference, reference_category


def _args(reference: str) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic hypothesis",
        "subject_identity": None,
        "target_objects": [reference],
        "preconditions": [],
        "planned_steps": ["observe"],
        "expected_positive_observation": "changed",
        "expected_negative_observation": "unchanged",
        "required_evidence": [],
    }


def test_embedded_uuid_is_representation_mismatch_and_opaque_parse_only() -> None:
    reference = "change:123e4567-e89b-12d3-a456-426614174000"
    replay = replay_argument_schemas(_args(reference))
    assert reference_category(reference) == "valid_uuid_in_other_structure"
    assert replay == {
        "strict_uuid": False,
        "optional_uuid": False,
        "opaque_string": True,
        "structured_public_reference": False,
    }
    assert not ground_reference(reference, [{"instructions": "synthetic", "input": []}])[
        "supported_by_context"
    ]


def test_rejection_echo_is_not_public_grounding() -> None:
    reference = "change:123e4567-e89b-12d3-a456-426614174000"
    request = {
        "input": [
            {
                "type": "function_call_output",
                "output": json.dumps(
                    {
                        "error": "invalid_tool_call",
                        "reason_code": "invalid_tool_arguments",
                        "echo": reference,
                    }
                ),
            }
        ],
    }
    assert not ground_reference(reference, [request])["identifier_observed"]


def test_public_uuid_observation_can_ground_wrapped_reference_without_oracle() -> None:
    reference = "change:123e4567-e89b-12d3-a456-426614174000"
    request = {
        "input": [
            {
                "type": "function_call_output",
                "output": json.dumps(
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "status": "PENDING",
                    }
                ),
            }
        ]
    }
    result = ground_reference(reference, [request])
    assert result["identifier_observed"]
    assert result["uniquely_resolvable"]
    assert result["public_candidate_count"] == 1


def test_invented_reference_and_hidden_only_id_are_unsupported() -> None:
    result = ground_reference("invented-change", [{"instructions": "synthetic", "input": []}])
    assert not result["supported_by_context"]
    assert result["public_candidate_ids"] == []
