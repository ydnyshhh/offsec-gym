"""Conservative, source-bound Q2 aggregation for the A1 synthetic range."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel

Q2_POLICY_VERSION = "t11b-a1-q2-v1"
MANDATORY_CHECKS = (
    "source_build_integrity",
    "healthy_runtime",
    "instance_identity_isolation",
    "internal_network_configuration",
    "no_published_ports",
    "network_attachment_integrity",
    "active_local_canary_isolation",
    "permitted_gateway_action",
    "forbidden_route_and_destination",
    "identity_run_generation_binding",
    "budget_and_duplicate_enforcement",
    "event_and_artifact_integrity",
    "trusted_application_storage_isolation",
    "reset_state_integrity",
    "teardown_integrity",
)
Status = Literal["pass", "fail", "unknown", "not_evaluated"]


def evidence_digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class Q2CheckResult(StrictModel):
    status: Status
    reason_code: str = Field(min_length=1)
    evidence_sha256: str | None = None

    @model_validator(mode="after")
    def requires_evidence_for_pass(self) -> Q2CheckResult:
        if self.status == "pass" and (
            self.evidence_sha256 is None
            or len(self.evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_sha256)
        ):
            raise ValueError("passing check requires SHA-256 evidence")
        return self


class Q2QualificationRecord(StrictModel):
    schema_version: Literal["1"] = "1"
    milestone: Literal["T1.1b"] = "T1.1b"
    task_id: str
    archetype_id: Literal["A1"] = "A1"
    range_family: Literal["saas"] = "saas"
    scenario: Literal["tenant_boundary_v2"] = "tenant_boundary_v2"
    seed: Literal[42] = 42
    variant: Literal["vulnerable"] = "vulnerable"
    source_commit: str
    source_sha256: dict[str, str]
    qualification_policy_version: Literal["t11b-a1-q2-v1"] = Q2_POLICY_VERSION
    checks: dict[str, Q2CheckResult]
    real_docker_used: bool
    real_gateway_used: bool
    local_canary_used: bool
    q2_status: Status
    q2_blocking_reasons: tuple[str, ...]
    q3_status: Literal["not_evaluated"] = "not_evaluated"
    q4_status: Literal["not_evaluated"] = "not_evaluated"
    q5_status: Literal["not_evaluated"] = "not_evaluated"
    q6_status: Literal["not_evaluated"] = "not_evaluated"
    benchmark_ready: Literal[False] = False
    provider_calls: Literal[0] = 0
    historical_results_modified: Literal[False] = False

    @model_validator(mode="after")
    def valid_projection(self) -> Q2QualificationRecord:
        if set(self.checks) != set(MANDATORY_CHECKS):
            raise ValueError("complete mandatory Q2 inventory is required")
        if not self.source_commit or not self.source_sha256:
            raise ValueError("source binding is required")
        statuses = [check.status for check in self.checks.values()]
        expected: Status = (
            "pass"
            if all(status == "pass" for status in statuses)
            else "fail"
            if "fail" in statuses
            else "not_evaluated"
        )
        if self.q2_status != expected:
            raise ValueError("Q2 status does not match mandatory checks")
        if self.q2_status == "pass" and not (
            self.real_docker_used and self.real_gateway_used and self.local_canary_used
        ):
            raise ValueError("Q2 pass requires real Docker, gateway, and canary")
        expected_blockers = tuple(
            name for name in MANDATORY_CHECKS if self.checks[name].status != "pass"
        )
        if self.q2_blocking_reasons != expected_blockers:
            raise ValueError("blocking reasons do not match checks")
        return self


def aggregate_q2(
    *,
    task_id: str,
    source_commit: str,
    source_sha256: dict[str, str],
    checks: dict[str, Q2CheckResult],
    real_docker_used: bool,
    real_gateway_used: bool,
    local_canary_used: bool,
) -> Q2QualificationRecord:
    complete = {
        name: checks.get(
            name,
            Q2CheckResult(status="not_evaluated", reason_code="evidence_missing"),
        )
        for name in MANDATORY_CHECKS
    }
    if set(checks) - set(MANDATORY_CHECKS):
        raise ValueError("unrecognized Q2 checks")
    statuses = [check.status for check in complete.values()]
    status: Status = (
        "pass"
        if all(item == "pass" for item in statuses)
        else "fail"
        if "fail" in statuses
        else "not_evaluated"
    )
    return Q2QualificationRecord(
        task_id=task_id,
        source_commit=source_commit,
        source_sha256=source_sha256,
        checks=complete,
        real_docker_used=real_docker_used,
        real_gateway_used=real_gateway_used,
        local_canary_used=local_canary_used,
        q2_status=status,
        q2_blocking_reasons=tuple(
            name for name in MANDATORY_CHECKS if complete[name].status != "pass"
        ),
    )
