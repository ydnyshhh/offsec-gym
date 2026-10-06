"""Terminal-bounded offline stage extraction for the M6.6 confirmatory design."""

from __future__ import annotations

from offsecgym.research.m66_pilot_analysis import (
    Arm,
    ObservedRequest,
    Variant,
    extract_pilot_stages,
)
from offsecgym.schemas.events import RunCompleted, TraceEvent
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.validation.deterministic import ProofAction


def terminal_bounded_inputs(
    trace: list[TraceEvent],
    actions: list[ProofAction],
    requests: list[ObservedRequest],
) -> tuple[list[TraceEvent], list[ProofAction], list[ObservedRequest]]:
    """Exclude all source evidence later than the authoritative terminal event.

    The caller verifies event identities and artifact hashes before this step.
    A completed gateway action after ``RunCompleted`` is never eligible for a
    before/action/after witness, even if a stored response artifact exists.
    """
    endings = [event for event in trace if isinstance(event, RunCompleted)]
    if len(endings) != 1:
        raise ValueError("confirmatory source requires exactly one terminal event")
    if not trace or len({event.run_id for event in trace}) != 1:
        raise ValueError("confirmatory source has mixed or absent run identity")
    if [event.sequence_number for event in trace] != list(range(1, len(trace) + 1)):
        raise ValueError("confirmatory source event sequence is not contiguous")
    terminal = endings[0].sequence_number
    return (
        [event for event in trace if event.sequence_number <= terminal],
        [action for action in actions if action.sequence <= terminal],
        [request for request in requests if request.sequence <= terminal],
    )


def extract_confirmatory_stages(
    trace: list[TraceEvent],
    actions: list[ProofAction],
    requests: list[ObservedRequest],
    oracle: GroundTruthManifest,
    fixture: dict[str, object],
    *,
    family: str,
    variant: Variant,
    arm: Arm,
    seed: int,
    arm_order: tuple[Arm, Arm],
    tool_names: list[str],
    witness_statuses: list[str],
    reminder_bytes: int,
) -> dict[str, object]:
    """Reuse the frozen root predicates only on terminal-bounded evidence."""
    source, bounded_actions, bounded_requests = terminal_bounded_inputs(trace, actions, requests)
    return extract_pilot_stages(
        source,
        bounded_actions,
        bounded_requests,
        oracle,
        fixture,
        family=family,
        variant=variant,
        arm=arm,
        seed=seed,
        arm_order=arm_order,
        tool_names=tool_names,
        witness_statuses=witness_statuses,
        reminder_bytes=reminder_bytes,
    )
