"""No-provider gates for the exact cell-113 environment-failure recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m671_cell113_recovery_postcheck as final_audit  # noqa: E402
import m671_resume_after_cell113_environment as recovery  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads(
    (ROOT / "experiments/manifests/m671-factorial-feasibility-v1.json").read_bytes()
)


def test_paid_range_excludes_every_started_cell() -> None:
    pairs = recovery.pending_pairs(MANIFEST)
    assert [index for index, _ in pairs] == list(range(57, 65))
    assert [cell["order"] for _, pair in pairs for cell in pair if cell["order"] >= 114] == list(
        range(114, 129)
    )
    assert recovery.NEW_APPROVAL_SHA256 == "0" * 64
    assert recovery.OPERATIONAL_MERGE_COMMIT == "0" * 40
    changed = json.loads(json.dumps(MANIFEST))
    changed["cells"][113]["order"] = 113
    with pytest.raises(ValueError, match="exact frozen paid range"):
        recovery.pending_pairs(changed)


def test_approval_binds_missing_cells_retained_reservation_and_consumed_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = "f" * 40
    monkeypatch.setattr(recovery, "OPERATIONAL_MERGE_COMMIT", merge)
    approval = {
        "approved_at": "2026-10-16T18:10:00Z",
        "approved_by": "human_user",
        "approved_for_paid_calls": True,
        "approval_scope": recovery.CONTINUATION_SCOPE,
        "protocol": recovery.PROTOCOL,
        "manifest_sha256": recovery.MANIFEST_SHA256,
        "assignment_sha256": MANIFEST["assignment_sha256"],
        "source_commit": recovery.SOURCE_COMMIT,
        "protocol_commit": recovery.PROTOCOL_COMMIT,
        "operational_merge_commit": merge,
        "prior_paid_approval_sha256": recovery.ORIGINAL_APPROVAL_SHA256,
        "consumed_continuation_approval_sha256": recovery.PREVIOUS_APPROVAL_SHA256,
        "consumed_cell109_approval_sha256": recovery.CELL109_APPROVAL_SHA256,
        "prior_execution_merge_commit": recovery.PREVIOUS_PIN_MERGE_COMMIT,
        "cell113_failed_run_id": str(recovery.FAILED_RUN_ID),
        "cell113_stopped_journal_sha256": recovery.STOPPED_JOURNAL_SHA256,
        "cell113_stopped_log_sha256": recovery.STOPPED_LOG_SHA256,
        "cell113_stopped_file_set_sha256": recovery.STOPPED_FILE_SET_SHA256,
        "interrupted_run_id": str(recovery.INTERRUPTED_RUN_ID),
        "unsettled_call_id": str(recovery.UNSETTLED_CALL_ID),
        "unsettled_reserved_tokens": recovery.UNSETTLED_RESERVED_TOKENS,
        "unsettled_reserved_cost_microusd": recovery.UNSETTLED_RESERVED_COST_MICROUSD,
        "retained_missing_orders": [109, 113],
        "retained_provider_failed_orders": [85, 86],
        "first_paid_cell_order": 114,
        "last_paid_cell_order": 128,
        "paid_cell_ids": [cell["cell_id"] for cell in MANIFEST["cells"][113:]],
        "paid_cells_authorized": 15,
        "fresh_launch_count": 1,
        "no_retry_or_replacement": True,
        "retain_unsettled_reservation_without_mutation": True,
        "selected_endpoint": f"{recovery.UPSTREAM} | {recovery.REVISION}",
        "input_usd_per_million_tokens": str(recovery.INPUT_PRICE),
        "output_usd_per_million_tokens": str(recovery.OUTPUT_PRICE),
        "cumulative_estimated_cost_stop_usd": MANIFEST["cumulative_estimated_cost_stop_usd"],
    }
    path = tmp_path / "cell113-paid-approval.json"
    raw = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", recovery.sha(raw))
    assert recovery.scoped_approval(path, MANIFEST) == approval
    approval["retained_missing_orders"] = [109]
    changed = json.dumps(approval, sort_keys=True).encode()
    path.write_bytes(changed)
    monkeypatch.setattr(recovery, "NEW_APPROVAL_SHA256", recovery.sha(changed))
    with pytest.raises(ValueError, match="exact scope and reservation"):
        recovery.scoped_approval(path, MANIFEST)


def test_final_journal_rejects_cell113_retry_or_gap() -> None:
    header = {"type": "batch_started", "protocol": recovery.PROTOCOL}
    lines = [header]
    for cell in MANIFEST["cells"][113:]:
        lines.extend(
            (
                {
                    "type": "cell_started",
                    "order": cell["order"],
                    "cell_id": cell["cell_id"],
                    "run_id": str(cell["order"]),
                },
                {
                    "type": "cell_completed",
                    "order": cell["order"],
                    "cell_id": cell["cell_id"],
                    "run_id": str(cell["order"]),
                    "experiment_sha256": cell["experiment_sha256"],
                },
            )
        )
    raw = b"\n".join(json.dumps(line).encode() for line in lines) + b"\n"
    assert len(final_audit.new_records(raw, MANIFEST, header=header)) == 15
    lines[1]["order"] = 113
    raw = b"\n".join(json.dumps(line).encode() for line in lines) + b"\n"
    with pytest.raises(ValueError, match="unique frozen-order"):
        final_audit.new_records(raw, MANIFEST, header=header)
