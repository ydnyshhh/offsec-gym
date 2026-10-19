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
        "context_observed"
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


def test_delivered_uuid_is_context_observed_without_source_provenance() -> None:
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
    assert result["context_observed"]
    assert result["public_source_grounded"] is None
    assert result["object_resolved"] is None


def test_model_authored_echo_does_not_establish_public_source_grounding() -> None:
    reference = "change:123e4567-e89b-12d3-a456-426614174000"
    request = {
        "input": [
            {
                "type": "function_call_output",
                "output": json.dumps({"hypothesis_echo": reference}),
            }
        ]
    }
    result = ground_reference(reference, [request])
    assert result["context_observed"]
    assert result["public_source_grounded"] is None
    assert result["object_resolved"] is None


def test_invented_reference_and_hidden_only_id_are_unsupported() -> None:
    result = ground_reference("invented-change", [{"instructions": "synthetic", "input": []}])
    assert not result["context_observed"]
    assert result["public_source_grounded"] is None
