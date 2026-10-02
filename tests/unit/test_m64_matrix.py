"""The prospective matrix is paired, deterministic, and marks infeasible cells."""

from collections import Counter
from pathlib import Path

from offsecgym.research.m64_matrix import plan_m64_matrix


def test_m64_matrix_is_deterministic_and_accounts_for_fixed_worker_floor() -> None:
    root = Path(__file__).parents[2]
    first = plan_m64_matrix(root, source_commit="frozen-test-code")
    second = plan_m64_matrix(root, source_commit="frozen-test-code")
    assert first == second
    assert first["range_compiler_version"] == "tenant-boundary-v2/compiler-1"
    assert len(first["range_pairs"]) == 10
    assert len({item["pair_id"] for item in first["range_pairs"].values()}) == 10
    assert all(
        item["vulnerable_build_id"] != item["patched_build_id"]
        for item in first["range_pairs"].values()
    )
    assert len(first["cells"]) == 300
    assert first["planned_live_cells"] == 180
    assert first["structurally_infeasible_cells"] == 120
    assert first["fixed_worker_minimum_tokens"] == 117000
    assert first["maximum_live_model_tokens"] == 20400000
    assert first["maximum_live_model_calls"] == 3600
    assert first["usd_ceiling"] is None and first["requires_price_and_pilot_review"]
    assert Counter(item["arm"] for item in first["cells"] if item["policy_feasible"]) == {
        "fixed_sequential": 40,
        "matched_parallel": 40,
        "opportunity_aware": 100,
    }
    infeasible = [item for item in first["cells"] if not item["policy_feasible"]]
    assert {item["worker_token_budget"] for item in infeasible} == {40000, 60000, 80000}
    assert all(item["infeasible_reason"] == "fixed_worker_one_turn_floor" for item in infeasible)
    live_orders = sorted(item["order"] for item in first["cells"] if item["policy_feasible"])
    assert live_orders == list(range(1, 181))
