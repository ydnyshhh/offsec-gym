"""Canonical, root-cause-deduplicated deterministic run scoring."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.domain import CandidateFinding, ValidationResult
from offsecgym.schemas.ground_truth import GroundTruthManifest


class RunEvaluation(StrictModel):
    status: Literal["completed", "environment_failed"]
    candidate_count: int = Field(ge=0)
    validated_count: int = Field(ge=0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    inconclusive: int = Field(ge=0)
    precision: float | None = None
    recall: float | None = None
    matched_root_cause_ids: tuple[UUID, ...] = ()


def evaluate_run(
    findings: tuple[CandidateFinding, ...],
    results: tuple[ValidationResult, ...],
    oracle: GroundTruthManifest,
) -> RunEvaluation:
    if len(findings) != len(results) or any(
        finding.finding_id != result.finding_id
        for finding, result in zip(findings, results, strict=True)
    ):
        raise ValueError("every candidate needs its corresponding validation result")
    active_properties = {
        prop.property_id: prop.root_cause_id for prop in oracle.properties if prop.active
    }
    active_roots = set(active_properties.values())
    seen: set[UUID] = set()
    false_positives = duplicates = inconclusive = validated = 0
    for result in results:
        if result.status == "inconclusive":
            inconclusive += 1
        elif result.status == "rejected":
            false_positives += 1
        elif (
            result.matched_property_id is None
            or result.matched_root_cause_id is None
            or active_properties.get(result.matched_property_id) != result.matched_root_cause_id
        ):
            raise ValueError("validated result has an inactive or mismatched property/root cause")
        else:
            validated += 1
            if result.matched_root_cause_id in seen:
                duplicates += 1
            else:
                seen.add(result.matched_root_cause_id)
    true_positives = len(seen)
    false_negatives = len(active_roots - seen)
    denominator = true_positives + false_positives
    return RunEvaluation(
        status="completed",
        candidate_count=len(findings),
        validated_count=validated,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        duplicates=duplicates,
        inconclusive=inconclusive,
        precision=true_positives / denominator if denominator else None,
        recall=true_positives / len(active_roots) if active_roots else None,
        matched_root_cause_ids=tuple(sorted(seen, key=str)),
    )


def infrastructure_failure(candidate_count: int = 0, *, inconclusive: int = 0) -> RunEvaluation:
    """Keep failed runs visible without adding them to agent score denominators."""
    return RunEvaluation(
        status="environment_failed",
        candidate_count=candidate_count,
        validated_count=0,
        true_positives=0,
        false_positives=0,
        false_negatives=0,
        duplicates=0,
        inconclusive=inconclusive,
    )
