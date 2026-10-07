"""Isolated provider attrition keeps its randomized partner eligible."""

from offsecgym.research.m66_confirmatory_attrition import CellTerminal, provider_health


def test_isolated_transient_failure_continues_without_retry() -> None:
    assert provider_health((CellTerminal("provider_failed", "provider_unavailable"),)) == "continue"
    assert (
        provider_health(
            (CellTerminal("provider_failed", "provider_rate_limited"), CellTerminal("completed"))
        )
        == "continue"
    )


def test_two_transport_failures_or_three_of_ten_pause() -> None:
    assert (
        provider_health(
            (
                CellTerminal("provider_failed", "provider_unavailable"),
                CellTerminal("provider_failed", "provider_timeout"),
            )
        )
        == "pause"
    )
    assert (
        provider_health(
            (
                CellTerminal("provider_failed", "provider_rate_limited"),
                CellTerminal("completed"),
                CellTerminal("provider_failed", "provider_rate_limited"),
                CellTerminal("completed"),
                CellTerminal("provider_failed", "provider_rate_limited"),
            )
        )
        == "pause"
    )


def test_auth_quota_or_unknown_provider_failure_stops() -> None:
    for reason in (
        "provider_auth_failed",
        "provider_quota_exhausted",
        "provider_request_rejected",
        None,
    ):
        assert provider_health((CellTerminal("provider_failed", reason),)) == "stop"
