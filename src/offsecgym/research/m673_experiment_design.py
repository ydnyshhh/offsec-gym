"""Pure, no-model qualification helpers for five independent future designs."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from offsecgym.research.m673_interface_grounding import (
    OpaqueStartArgs,
    OptionalStartArgs,
    StructuredStartArgs,
    parses,
)
from offsecgym.research.m673_opportunity import qualify_public_path
from offsecgym.solver.experiment_state import StartExperimentArgs


def replay_argument_schemas(arguments: object) -> dict[str, bool]:
    """Parsing only: no state creation or object-resolution side effect."""
    return {
        "strict_uuid": parses(StartExperimentArgs, arguments),
        "optional_uuid": parses(OptionalStartArgs, arguments),
        "opaque_string": parses(OpaqueStartArgs, arguments),
        "structured_public_reference": parses(StructuredStartArgs, arguments),
    }


def simulate_http_opportunities(
    *, first_tick: int, due_tick: int, discovery_calls: int = 1
) -> dict[str, object]:
    """Each completed HTTP request advances the clock one tick; model turns do not."""
    if first_tick < 0 or due_tick < first_tick or discovery_calls < 0:
        raise ValueError("invalid clock inputs")
    request_tick = first_tick + discovery_calls
    return {
        "discovery_calls": discovery_calls,
        "cancel_request_tick": request_tick,
        "cancel_completion_tick": request_tick + 1,
        "cancel_before_due": request_tick < due_tick,
        "opportunities": due_tick - first_tick,
    }


def public_queue_qualification(
    *,
    listing: Mapping[str, object] | None,
    identity_visible: bool,
    jobs_route_visible: bool,
    cancel_route_visible: bool,
    opportunities: int,
) -> dict[str, object]:
    """Select a candidate without a target ID or private oracle argument."""
    if listing is None:
        queued = None
    else:
        items = listing.get("items")
        if not isinstance(items, list) or listing.get("total") != len(items):
            queued = None
        else:
            queued = [
                item for item in items if isinstance(item, dict) and item.get("status") == "QUEUED"
            ]
    return qualify_public_path(
        queued_candidates=queued,
        identity_visible=identity_visible,
        jobs_route_visible=jobs_route_visible,
        cancel_route_visible=cancel_route_visible,
        opportunities=opportunities,
    )


def qualify_model_authored_closure(
    *,
    action_completed: bool,
    observation_requested: bool,
    observation_completed: bool,
    same_object: bool,
    same_identity: bool,
    before_terminal: bool,
) -> dict[str, bool]:
    """A reminder itself never supplies or executes the required observation."""
    return {
        "successful_action": action_completed,
        "complete_observation": all(
            (
                action_completed,
                observation_requested,
                observation_completed,
                same_object,
                same_identity,
                before_terminal,
            )
        ),
    }


def static_request_perturbation(
    request: dict[str, object],
    *,
    field: str,
    value: object,
) -> dict[str, object]:
    """Return a detached synthetic request; never submit it to a provider."""
    if field not in {"transport_metadata", "synthetic_public_observation"}:
        raise ValueError("only controlled synthetic perturbation fields are supported")
    result = copy.deepcopy(request)
    result[field] = json.loads(json.dumps(value))
    return result
