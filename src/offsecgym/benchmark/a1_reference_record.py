"""Consistency constraints for published A1 reference qualification records."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from offsecgym.benchmark.stages import StageResult
from offsecgym.schemas.common import StrictModel


class A1ReferenceRecord(StrictModel):
    milestone: Literal["T1.1e"]
    policy_version: Literal["t11e-a1-reference-qualification-v1"]
    task_id: str
    seed: int = Field(ge=0)
    variant: Literal["vulnerable", "patched"]
    visibility_parity: bool
    q3_status: Literal["pass", "fail", "unknown", "not_evaluated"]
    q4_status: Literal["pass", "fail", "unknown", "not_evaluated"]
    patched_control_status: Literal["pass", "fail", "not_applicable"]
    candidate_publicly_discoverable: bool
    pre_decisive_relationship_known: bool
    post_decisive_relationship_confirmed: bool | None
    finding_submitted: bool
    canonical_validation_status: Literal["validated", "rejected", "inconclusive"] | None
    matched_expected_root: bool
    scoring_agreement: bool
    candidate_count: int = Field(ge=0)
    true_positives: int | None = Field(default=None, ge=0)
    false_positives: int | None = Field(default=None, ge=0)
    false_negatives: int | None = Field(default=None, ge=0)
    patched_recall_undefined: bool
    blocking_reasons: tuple[str, ...]
    stage_results: tuple[StageResult, ...]
    public_observation_count: int = Field(ge=0)
    public_graph_node_count: int = Field(ge=0)
    public_graph_edge_count: int = Field(ge=0)
    source_sha256: dict[str, str]
    qualification_source_sha256: dict[str, str]
    provider_calls: Literal[0] = 0
    docker_cleanup_verified: bool

    @model_validator(mode="after")
    def no_unearned_pass(self) -> A1ReferenceRecord:
        if not self.qualification_source_sha256:
            raise ValueError("qualification implementation source pins required")
        if tuple(row.stage_id for row in self.stage_results) != tuple(
            f"S{index}" for index in range(7)
        ):
            raise ValueError("complete ordered S0-S6 evidence is required")
        if self.q4_status == "pass" and not (
            self.variant == "vulnerable"
            and self.visibility_parity
            and self.q3_status == "pass"
            and self.candidate_publicly_discoverable
            and self.post_decisive_relationship_confirmed is True
            and self.finding_submitted
            and self.canonical_validation_status == "validated"
            and self.matched_expected_root
            and self.scoring_agreement
            and self.true_positives == 1
            and not self.blocking_reasons
            and self.docker_cleanup_verified
            and all(row.status == "true" for row in self.stage_results)
        ):
            raise ValueError("Q4 pass lacks complete public, validator, or stage evidence")
        if self.patched_control_status == "pass" and not (
            self.variant == "patched"
            and self.q4_status != "pass"
            and self.candidate_publicly_discoverable
            and not self.finding_submitted
            and self.canonical_validation_status is None
            and self.true_positives == 0
            and self.patched_recall_undefined
            and self.scoring_agreement
            and self.docker_cleanup_verified
            and "patched_access_denied" in self.blocking_reasons
        ):
            raise ValueError("patched control pass lacks denial and undefined recall")
        return self
