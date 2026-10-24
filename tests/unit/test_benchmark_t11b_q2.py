"""Q2 admission must fail closed when evidence or source binding is incomplete."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from offsecgym.benchmark.q2_runtime_qualification import (
    MANDATORY_CHECKS,
    Q2CheckResult,
    Q2QualificationRecord,
    aggregate_q2,
    evidence_digest,
)


def _passing() -> dict[str, Q2CheckResult]:
    return {
        name: Q2CheckResult(
            status="pass", reason_code="verified", evidence_sha256=evidence_digest(name)
        )
        for name in MANDATORY_CHECKS
    }


def _record(checks: dict[str, Q2CheckResult]) -> Q2QualificationRecord:
    return aggregate_q2(
        task_id="t0-test",
        source_commit="abc123",
        source_sha256={"source": evidence_digest("source")},
        checks=checks,
        real_docker_used=True,
        real_gateway_used=True,
        local_canary_used=True,
    )


def test_all_evidence_produces_scoped_q2_pass_only() -> None:
    record = _record(_passing())
    assert record.q2_status == "pass"
    assert record.q2_blocking_reasons == ()
    assert all(
        status == "not_evaluated"
        for status in (record.q3_status, record.q4_status, record.q5_status, record.q6_status)
    )
    assert record.benchmark_ready is False and record.provider_calls == 0


def test_missing_check_cannot_pass() -> None:
    checks = _passing()
    checks.pop("active_local_canary_isolation")
    record = _record(checks)
    assert record.q2_status == "not_evaluated"
    assert record.checks["active_local_canary_isolation"].reason_code == "evidence_missing"


def test_explicit_failure_is_distinct_from_nonexecution() -> None:
    checks = _passing()
    checks["trusted_application_storage_isolation"] = Q2CheckResult(
        status="fail", reason_code="application_state_mismatch"
    )
    checks.pop("reset_state_integrity")
    record = _record(checks)
    assert record.q2_status == "fail"
    assert record.checks["reset_state_integrity"].status == "not_evaluated"


def test_pass_requires_sha256_evidence_and_live_flags() -> None:
    with pytest.raises(ValidationError):
        Q2CheckResult(status="pass", reason_code="verified")
    with pytest.raises(ValidationError):
        aggregate_q2(
            task_id="t0-test",
            source_commit="abc123",
            source_sha256={"source": evidence_digest("source")},
            checks=_passing(),
            real_docker_used=False,
            real_gateway_used=True,
            local_canary_used=True,
        )


def test_record_rejects_tampering_and_mismatched_source() -> None:
    payload = _record(_passing()).model_dump()
    payload["q2_status"] = "not_evaluated"
    with pytest.raises(ValidationError):
        Q2QualificationRecord.model_validate(payload)
    payload = _record(_passing()).model_dump()
    payload["source_sha256"] = {}
    with pytest.raises(ValidationError):
        Q2QualificationRecord.model_validate(payload)


def test_unknown_check_rejected() -> None:
    checks = _passing()
    checks["other_task"] = Q2CheckResult(
        status="pass", reason_code="verified", evidence_sha256="0" * 64
    )
    with pytest.raises(ValueError, match="unrecognized"):
        _record(checks)
