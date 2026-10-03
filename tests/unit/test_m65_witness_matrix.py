"""The prospective matrix is new, paired, bounded, and deterministic."""

from pathlib import Path

from offsecgym.research.m65_witness_matrix import SEEDS, plan_witness_matrix


def test_new_seed_matrix_has_paired_builds_and_explicit_worst_cost() -> None:
    root = Path(__file__).parents[2]
    manifest = plan_witness_matrix(root, source_commit="frozen-source")
    assert SEEDS == tuple(range(2001, 2011))
    assert set(SEEDS).isdisjoint(range(1001, 1011))
    assert 1101 not in SEEDS
    assert manifest["planned_live_cells"] == 20
    assert manifest["maximum_estimated_token_cost_usd"] == 60.0
    assert manifest["combined_model_budget"]["max_wall_seconds"] == 900
    assert manifest["requires_separate_paid_approval"]
    assert len({cell["cell_id"] for cell in manifest["cells"]}) == 20
    assert [cell["order"] for cell in manifest["cells"]] == list(range(1, 21))
    for seed in SEEDS:
        pair = manifest["range_pairs"][str(seed)]
        cells = [cell for cell in manifest["cells"] if cell["range_seed"] == seed]
        assert {cell["variant"] for cell in cells} == {"vulnerable", "patched"}
        assert {cell["pair_id"] for cell in cells} == {pair["pair_id"]}
    assert manifest == plan_witness_matrix(root, source_commit="frozen-source")
