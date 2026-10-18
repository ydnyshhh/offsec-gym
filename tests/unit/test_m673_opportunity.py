"""Public candidate path and worker-clock negative controls."""

from __future__ import annotations

from offsecgym.research.m673_experiment_design import (
    public_queue_qualification,
    simulate_http_opportunities,
)
from offsecgym.research.m673_opportunity import qualify_public_path


def _path(candidates: list[dict[str, object]] | None, opportunities: int = 2):
    return qualify_public_path(
        queued_candidates=candidates,
        identity_visible=True,
        jobs_route_visible=True,
        cancel_route_visible=True,
        opportunities=opportunities,
    )


def test_oracle_only_target_does_not_create_public_path() -> None:
    result = _path(None)
    assert result["indeterminate"]
    assert result["candidate_count"] is None
    assert result["feasible_before_deadline"] is None


def test_unique_public_candidate_requires_one_discovery_call() -> None:
    result = _path([{"id": "public-job", "status": "QUEUED"}])
    assert result["minimum_discovery_http_calls"] == 1
    assert result["total_minimum_http_calls"] == 2
    assert not result["uniquely_identifiable_before_first_http"]
    assert result["uniquely_identifiable_after_one_http"]
    assert result["feasible_before_deadline"]


def test_two_indistinguishable_jobs_remain_ambiguous() -> None:
    result = _path([{"id": "one"}, {"id": "two"}])
    assert result["candidate_count"] == 2
    assert result["verdict"] == "multiple indistinguishable targets remain"
    assert not result["feasible_before_deadline"]


def test_public_listing_pagination_is_unknown_not_solvable() -> None:
    result = public_queue_qualification(
        listing={"items": [{"id": "one", "status": "QUEUED"}], "total": 2},
        identity_visible=True,
        jobs_route_visible=True,
        cancel_route_visible=True,
        opportunities=2,
    )
    assert result["indeterminate"]


def test_read_consumes_tick_but_model_turn_does_not() -> None:
    direct = simulate_http_opportunities(first_tick=2, due_tick=4, discovery_calls=0)
    after_read = simulate_http_opportunities(first_tick=2, due_tick=4, discovery_calls=1)
    late = simulate_http_opportunities(first_tick=2, due_tick=4, discovery_calls=2)
    assert direct["cancel_request_tick"] == 2
    assert after_read["cancel_request_tick"] == 3
    assert after_read["cancel_before_due"]
    assert not late["cancel_before_due"]


def test_physical_actionability_does_not_imply_identifiability() -> None:
    assert simulate_http_opportunities(first_tick=2, due_tick=4, discovery_calls=0)[
        "cancel_before_due"
    ]
    assert _path(None)["feasible_before_deadline"] is None
