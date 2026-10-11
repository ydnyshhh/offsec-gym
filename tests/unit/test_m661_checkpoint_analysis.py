"""Missingness and pairing gates for future checkpoint-response analysis."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from offsecgym.research.m661_checkpoint_analysis import (
    checkpoint_response_curves,
    extract_checkpoint_cell,
    paired_checkpoint_differences,
)
from offsecgym.research.m661_checkpoints import _digest
from offsecgym.research.m661_seed_exclusions import (
    load_exclusion_registry,
    require_fresh_seed,
)
from offsecgym.schemas.events import RangeStarted, RunCompleted, RunStarted


def cell(seed: int, checkpoint: str, *, success: bool, auditable: bool = True) -> dict:
    return {
        "root_id": "B2-REVOKED-ROLE",
        "seed": seed,
        "checkpoint_id": checkpoint,
        "variant": "vulnerable",
        "auditable": auditable,
        "terminal_status": "completed" if auditable else "provider_failed",
        "model_calls": 2 if auditable else 1,
        "input_tokens": 100,
        "output_tokens": 10,
        "http_actions": 3,
        "trusted_setup_http_actions": 5 if checkpoint == "B2-S3" else 2,
        "experiment_completed": success,
        "full_transition": success,
        "offline_complete_chain": success,
        "complete_trusted_witness": False,
        "root_class_submitted": success,
        "validated_root": False,
        "witness_comparable": checkpoint == "B2-S0",
        "validation_comparable": checkpoint == "B2-S0",
    }


def test_curves_keep_invalid_cells_and_do_not_compare_validator_rates() -> None:
    rows = [
        cell(1, "B2-S0", success=False),
        cell(2, "B2-S0", success=False),
        cell(1, "B2-S3", success=True),
        cell(2, "B2-S3", success=False, auditable=False),
    ]
    curves = checkpoint_response_curves(rows, bootstrap_replicates=200)["curves"]
    early, late = curves
    assert early["full_transition"]["all_assigned_rate"] == 0
    assert late["assigned_cells"] == 2 and late["auditable_cells"] == 1
    assert late["full_transition"]["all_assigned_rate"] is None
    assert late["full_transition"]["missingness_bounds"] == [0.5, 1.0]
    assert late["complete_trusted_witness"]["all_assigned_rate"] is None
    assert late["validated_root"]["missingness_bounds"] is None
    pairs = paired_checkpoint_differences(rows, bootstrap_replicates=200)
    assert pairs[0]["assigned_seed_pairs"] == 2
    assert pairs[0]["auditable_seed_pairs"] == 1
    assert pairs[0]["difference"] is None
    assert pairs[0]["missingness_bounds"] == [0.0, 1.0]
    assert pairs[0]["causal_interpretation_authorized"] is False


def test_seed_registry_reconstructs_pinned_historical_union() -> None:
    root = Path(__file__).parents[2]
    excluded, digest = load_exclusion_registry(root)
    assert len(excluded) == 426 and len(digest) == 64
    assert set(range(900_000, 900_024)) <= excluded
    with pytest.raises(ValueError, match="prior or qualification"):
        require_fresh_seed(root, 900_000)
    assert require_fresh_seed(root, 1_200_001) == digest


def test_post_terminal_model_action_cannot_enter_stage_extraction(monkeypatch) -> None:
    run_id, build_id, instance_id = uuid4(), uuid4(), uuid4()
    trace = [
        RunStarted(run_id=run_id, actor="controller", sequence_number=1, experiment_hash="test"),
        RangeStarted(
            run_id=run_id,
            actor="controller",
            sequence_number=2,
            build_id=build_id,
            range_instance_id=instance_id,
            range_generation=1,
        ),
        RunCompleted(run_id=run_id, actor="controller", sequence_number=3, status="completed"),
    ]
    fixture = {"test": True}
    receipt = {"fixture_sha256": _digest(fixture)}
    monkeypatch.setattr(
        "offsecgym.research.m661_checkpoint_analysis._verify_binding",
        lambda *_args: receipt,
    )
    action = SimpleNamespace(request=SimpleNamespace(source_phase=None, run_id=run_id), sequence=4)
    with pytest.raises(ValueError, match="post-terminal"):
        extract_checkpoint_cell(
            b"{}", trace, [], [action], [], SimpleNamespace(build_id=build_id), fixture
        )
