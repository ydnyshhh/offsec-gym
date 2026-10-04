"""The M6.5.2 eligibility amendment accepts only the documented preflight edge."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

budget_preflight_pattern = runpy.run_path(
    str(Path(__file__).parents[2] / "research_ops/m652_resume_after_preflight.py")
)["budget_preflight_pattern"]


def _event(sequence: int, type_name: str, **fields: object) -> SimpleNamespace:
    return SimpleNamespace(sequence_number=sequence, type=type_name, **fields)


def _trace() -> list[SimpleNamespace]:
    return [
        _event(1, "model_call_completed"),
        _event(2, "probe_checkpoint_saved"),
        _event(3, "context_retrieved"),
        _event(4, "model_reservation_rejected", reason_code="model_token_budget_exhausted"),
        _event(5, "reporting_prefix_rejected", reason_code="invalid_source_prefix_ValueError"),
        _event(6, "finding_validated"),
        _event(7, "run_completed", status="budget_exhausted"),
    ]


def test_accepts_only_checkpoint_followed_by_token_preflight_stop() -> None:
    assert budget_preflight_pattern(_trace(), 1)
    wrong_reason = _trace()
    wrong_reason[3].reason_code = "provider_failed"
    assert not budget_preflight_pattern(wrong_reason, 1)
    late_action = _trace()
    late_action.insert(5, _event(6, "action_requested"))
    for number, item in enumerate(late_action, start=1):
        item.sequence_number = number
    assert not budget_preflight_pattern(late_action, 1)
    wrong_terminal = _trace()
    wrong_terminal[-1].status = "completed"
    assert not budget_preflight_pattern(wrong_terminal, 1)
