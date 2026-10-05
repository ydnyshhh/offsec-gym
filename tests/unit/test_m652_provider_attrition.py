"""The bounded attrition gate accepts transient failures without later work."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallFailed,
    ModelCallStarted,
    RunCompleted,
)

scripts_dir = Path(__file__).parents[2] / "research_ops"
sys.path.insert(0, str(scripts_dir))
script_path = scripts_dir / "m652_resume_with_provider_attrition.py"
transient_provider_failure_pattern = runpy.run_path(str(script_path))[
    "transient_provider_failure_pattern"
]


def _trace(reason: str, http_status: int | None) -> list[object]:
    return [
        SimpleNamespace(sequence_number=1),
        ModelCallStarted.model_construct(sequence_number=2, call_id="call-1"),
        ModelCallFailed.model_construct(
            sequence_number=3,
            call_id="call-1",
            reason_code=reason,
            http_status=http_status,
        ),
        SimpleNamespace(sequence_number=4),
        RunCompleted.model_construct(sequence_number=5, status="provider_failed"),
    ]


def test_transient_provider_failure_gate() -> None:
    for reason, status in (
        ("provider_unavailable", None),
        ("provider_rate_limited", 429),
        ("provider_timeout", 408),
        ("provider_server_error", 503),
    ):
        assert transient_provider_failure_pattern(_trace(reason, status))
    for reason, status in (
        ("provider_auth_failed", 401),
        ("provider_quota_exhausted", 402),
        ("provider_server_error", 400),
        ("provider_rate_limited", None),
    ):
        assert not transient_provider_failure_pattern(_trace(reason, status))
    late_action = _trace("provider_unavailable", None)
    late_action[3] = ActionRequested.model_construct(sequence_number=4)
    assert not transient_provider_failure_pattern(late_action)
    wrong_call = _trace("provider_unavailable", None)
    wrong_call[2] = wrong_call[2].model_copy(update={"call_id": "different"})
    assert not transient_provider_failure_pattern(wrong_call)
    wrong_terminal = _trace("provider_unavailable", None)
    wrong_terminal[-1] = wrong_terminal[-1].model_copy(update={"status": "completed"})
    assert not transient_provider_failure_pattern(wrong_terminal)
