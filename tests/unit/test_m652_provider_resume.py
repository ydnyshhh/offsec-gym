"""The M6.5.2 provider-retention gate accepts only one terminal 429."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

from offsecgym.schemas.events import ActionRequested, ModelCallFailed, RunCompleted

scripts_dir = Path(__file__).parents[2] / "research_ops"
sys.path.insert(0, str(scripts_dir))
script_path = scripts_dir / "m652_resume_after_provider_failure.py"
provider_failure_pattern = runpy.run_path(str(script_path))["provider_failure_pattern"]


def _trace() -> list[object]:
    return [
        SimpleNamespace(sequence_number=1),
        ModelCallFailed.model_construct(
            sequence_number=2, http_status=429, reason_code="provider_rate_limited"
        ),
        SimpleNamespace(sequence_number=3),
        RunCompleted.model_construct(sequence_number=4, status="provider_failed"),
    ]


def test_only_one_terminal_rate_limit_without_late_work_is_retained() -> None:
    assert provider_failure_pattern(_trace())
    wrong_status = _trace()
    wrong_status[1] = wrong_status[1].model_copy(update={"http_status": 500})
    assert not provider_failure_pattern(wrong_status)
    wrong_reason = _trace()
    wrong_reason[1] = wrong_reason[1].model_copy(update={"reason_code": "provider_unavailable"})
    assert not provider_failure_pattern(wrong_reason)
    late_gateway = _trace()
    late_gateway[2] = ActionRequested.model_construct(sequence_number=3)
    assert not provider_failure_pattern(late_gateway)
    duplicate_failure = _trace()
    duplicate_failure[2] = ModelCallFailed.model_construct(
        sequence_number=3, http_status=429, reason_code="provider_rate_limited"
    )
    assert not provider_failure_pattern(duplicate_failure)
    wrong_terminal = _trace()
    wrong_terminal[-1] = wrong_terminal[-1].model_copy(update={"status": "completed"})
    assert not provider_failure_pattern(wrong_terminal)
