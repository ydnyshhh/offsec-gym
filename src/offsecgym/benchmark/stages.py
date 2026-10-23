"""Offline stage vocabulary; no model text or HTTP status implies stage success."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel

StageId = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
StageStatus = Literal["true", "false", "unknown", "not_applicable"]
EvidenceSource = Literal[
    "range_instance_event",
    "verified_gateway_event",
    "verified_public_response",
    "generation_bound_artifact",
    "canonical_validator",
]
STAGE_NAMES = {
    "S0": "environment initialized",
    "S1": "relevant public objects discovered",
    "S2": "required relationships established",
    "S3": "decisive observation or action completed",
    "S4": "required postcondition observed",
    "S5": "complete proof established",
    "S6": "finding independently validated",
}


class StageResult(StrictModel):
    stage_id: StageId
    status: StageStatus
    evidence_source: EvidenceSource | None = None
    evidence_reference: str | None = Field(default=None, min_length=1)
    rule_version: Literal["t0-stage-v1"] = "t0-stage-v1"
    reason_code: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_authority_for_success(self) -> StageResult:
        if self.status == "true" and (
            self.evidence_source is None or self.evidence_reference is None
        ):
            raise ValueError("stage success requires an authoritative evidence reference")
        if self.status in {"unknown", "not_applicable"} and (
            self.evidence_source is not None or self.evidence_reference is not None
        ):
            raise ValueError("unknown or inapplicable stage cannot carry proof")
        return self


def unqualified_stage_vector() -> tuple[StageResult, ...]:
    """A task definition alone cannot establish any run-level stage."""
    return tuple(
        StageResult(stage_id=stage_id, status="unknown", reason_code="no_run_evidence")
        for stage_id in STAGE_NAMES
    )
