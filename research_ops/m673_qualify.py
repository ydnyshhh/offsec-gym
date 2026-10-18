"""Run synthetic, no-model M6.7.3 qualification cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from offsecgym.research.m673_audit import sha
from offsecgym.research.m673_experiment_design import (
    public_queue_qualification,
    qualify_model_authored_closure,
    replay_argument_schemas,
    simulate_http_opportunities,
    static_request_perturbation,
)
from offsecgym.research.m673_interface_grounding import ground_reference


def qualification() -> dict[str, object]:
    # The synthetic queue deliberately has no hidden target argument.
    unique = {"items": [{"id": "synthetic-job", "status": "QUEUED"}], "total": 1}
    ambiguous = {
        "items": [
            {"id": "synthetic-job-a", "status": "QUEUED"},
            {"id": "synthetic-job-b", "status": "QUEUED"},
        ],
        "total": 2,
    }
    route = {"identity_visible": True, "jobs_route_visible": True, "cancel_route_visible": True}
    schema_replay = replay_argument_schemas(
        {
            "hypothesis": "Synthetic hypothesis",
            "subject_identity": None,
            "target_objects": ["change:123e4567-e89b-12d3-a456-426614174000"],
            "preconditions": [],
            "planned_steps": ["observe"],
            "expected_positive_observation": "changed",
            "expected_negative_observation": "unchanged",
            "required_evidence": [],
        }
    )
    cases = {
        "tolerant_schema_accepts_wrapped_uuid_only_as_data": (
            schema_replay["opaque_string"] and not schema_replay["strict_uuid"]
        ),
        "oracle_only_reference_is_ungrounded": not ground_reference(
            "hidden-object", [{"instructions": "Synthetic public task", "input": []}]
        )["supported_by_context"],
        "one_public_candidate_two_calls": public_queue_qualification(
            listing=unique, opportunities=2, **route
        )["feasible_before_deadline"]
        is True,
        "two_queued_candidates_remain_ambiguous": public_queue_qualification(
            listing=ambiguous, opportunities=2, **route
        )["verdict"]
        == "multiple indistinguishable targets remain",
        "one_call_is_insufficient": public_queue_qualification(
            listing=unique, opportunities=1, **route
        )["feasible_before_deadline"]
        is False,
        "missing_public_listing_remains_unknown": public_queue_qualification(
            listing=None, opportunities=2, **route
        )["indeterminate"]
        is True,
        "read_advances_worker_tick": simulate_http_opportunities(
            first_tick=2, due_tick=4, discovery_calls=1
        )["cancel_request_tick"]
        == 3,
        "clock_four_calls_allows_more_discovery": simulate_http_opportunities(
            first_tick=2, due_tick=6, discovery_calls=3
        )["cancel_before_due"]
        is True,
        "reminder_does_not_execute_observation": not qualify_model_authored_closure(
            action_completed=True,
            observation_requested=False,
            observation_completed=False,
            same_object=True,
            same_identity=True,
            before_terminal=True,
        )["complete_observation"],
        "wrong_identity_rejected": not qualify_model_authored_closure(
            action_completed=True,
            observation_requested=True,
            observation_completed=True,
            same_object=True,
            same_identity=False,
            before_terminal=True,
        )["complete_observation"],
        "request_perturbation_is_detached": None,
    }
    source = {"input": [{"role": "user", "content": "synthetic"}]}
    changed = static_request_perturbation(
        source, field="synthetic_public_observation", value={"status": "QUEUED"}
    )
    cases["request_perturbation_is_detached"] = (
        source != changed and "synthetic_public_observation" not in source
    )
    if not all(cases.values()):
        raise ValueError("no-model qualification failed")
    return {
        "kind": "synthetic_no_model_qualification",
        "cases": cases,
        "passed": sum(cases.values()),
        "total": len(cases),
        "provider_calls": 0,
        "range_actions": 0,
        "effect_estimate": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualification()
    root = Path(__file__).resolve().parents[1]
    result["code_sha256"] = hashlib.sha256(
        json.dumps(
            {
                path.relative_to(root).as_posix(): sha(path)
                for path in (
                    root / "src/offsecgym/research/m673_experiment_design.py",
                    root / "src/offsecgym/research/m673_interface_grounding.py",
                    root / "src/offsecgym/research/m673_opportunity.py",
                    root / "research_ops/m673_qualify.py",
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {"passed": result["passed"], "total": result["total"], "sha256": sha(args.output)}
        )
    )


if __name__ == "__main__":
    main()
