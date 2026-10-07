"""Convert audited confirmatory stages into the frozen assignment ledger.

The caller authenticates traces and stage artifacts against PostgreSQL before
passing them here. Missing stage evidence stays missing, never a zero outcome.
"""

from __future__ import annotations

from collections.abc import Mapping

from offsecgym.research.m66_confirmatory_analysis import ROOTS, ConfirmatoryRecord


def _key(cell: Mapping[str, object]) -> tuple[object, ...]:
    return (cell["range_family"], cell["seed"], cell["variant"], cell["arm"])


def record_from_audit(
    cell: Mapping[str, object],
    *,
    terminal: Mapping[str, object] | None,
    stage_output: Mapping[str, object] | None,
    manifest_sha256: str,
) -> ConfirmatoryRecord:
    """One assigned cell, including unstarted and unmeasurable terminals."""
    family, seed, variant, arm = _key(cell)
    if (
        family not in ROOTS
        or variant not in {"vulnerable", "patched"}
        or arm
        not in {
            "control",
            "witness",
        }
    ):
        raise ValueError("assignment key differs from confirmatory ontology")
    base = {
        "range_family": family,
        "seed": seed,
        "variant": variant,
        "arm": arm,
        "assigned_roots": ROOTS[family] if variant == "vulnerable" else (),
    }
    if terminal is None:
        if stage_output is not None:
            raise ValueError("unstarted cell cannot have a stage output")
        return ConfirmatoryRecord.model_validate(
            {**base, "status": "unstarted", "score_valid": False, "trace_auditable": False}
        )
    if terminal.get("cell_id") != cell.get("cell_id") or terminal.get("order") != cell.get("order"):
        raise ValueError("terminal journal row differs from assigned cell")
    status = terminal.get("status")
    score_valid = terminal.get("score_valid")
    if not isinstance(score_valid, bool):
        raise ValueError("terminal row lacks Boolean score validity")
    if stage_output is None:
        if score_valid:
            raise ValueError("score-valid terminal cannot silently lose its stage audit")
        return ConfirmatoryRecord.model_validate(
            {
                **base,
                "status": status,
                "score_valid": False,
                "trace_auditable": False,
            }
        )
    if (
        stage_output.get("protocol") != "m66-confirmatory-v1"
        or stage_output.get("manifest_sha256") != manifest_sha256
        or stage_output.get("cell_id") != cell.get("cell_id")
        or stage_output.get("trace_sha256") != terminal.get("trace_sha256")
    ):
        raise ValueError("stage artifact differs from frozen cell or source trace")
    stages = stage_output.get("stages")
    if not isinstance(stages, dict) or any(
        stages.get(name) != expected
        for name, expected in (
            ("run_id", terminal.get("run_id")),
            ("range_family", family),
            ("seed", seed),
            ("variant", variant),
            ("arm", arm),
            ("arm_order", cell.get("arm_order")),
            ("status", status),
            ("score_valid", score_valid),
            ("score_replay", terminal.get("evaluation")),
            ("input_tokens", terminal.get("input_tokens")),
            ("output_tokens", terminal.get("output_tokens")),
            ("gateway_actions", terminal.get("gateway_actions")),
        )
    ):
        raise ValueError("stage extraction differs from terminal and oracle score replay")
    roots = stages.get("roots")
    if not isinstance(roots, list) or {row.get("root") for row in roots} != set(ROOTS[family]):
        raise ValueError("stage root rows differ from frozen ontology")
    if len(roots) != len(ROOTS[family]):
        raise ValueError("stage root rows contain duplicates")
    if any(type(row.get("complete_witness")) is not bool for row in roots):
        raise ValueError("stage root rows lack Boolean witness outcomes")
    if variant == "vulnerable" and any(row.get("applicable") is not True for row in roots):
        raise ValueError("assigned vulnerable root is absent from the compiled fixture")
    complete = (
        tuple(row["root"] for row in roots if row["complete_witness"])
        if variant == "vulnerable"
        else ()
    )
    return ConfirmatoryRecord.model_validate(
        {
            **base,
            "status": status,
            "score_valid": score_valid,
            "trace_auditable": True,
            "complete_witness_roots": complete,
            "patched_submitted_findings": stages["patched_submitted_findings"],
            "patched_rejected_findings": stages["patched_rejected_findings"],
            "patched_inconclusive_findings": stages["patched_inconclusive_findings"],
            "http_actions": stages["gateway_actions"],
            "input_tokens": stages["input_tokens"],
            "output_tokens": stages["output_tokens"],
        }
    )
