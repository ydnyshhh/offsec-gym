"""Hypothetical paired-seed precision study; no pilot outcomes or model calls."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

REPLICATES = 20_000
SIMULATION_SEED = 660_030
SAMPLE_SIZES = (24, 30)
SCENARIOS = {
    "null_low_discordance": (0.10, 0.10),
    "null_moderate_discordance": (0.15, 0.15),
    "null_high_discordance": (0.20, 0.20),
    "modest_witness_gain": (0.20, 0.10),
    "larger_witness_gain": (0.25, 0.05),
}


def simulate() -> dict[str, object]:
    rng = random.Random(SIMULATION_SEED)
    output: dict[str, object] = {
        "purpose": "hypothetical design precision only; v1/v2 outcomes excluded",
        "simulation_seed": SIMULATION_SEED,
        "replicates": REPLICATES,
        "interval_method": "approximate paired-difference normal 95% width",
        "power_method": "two-sided exact McNemar test at 0.05",
        "target_total_95pct_width": 0.45,
        "scenarios": {},
    }
    results = output["scenarios"]
    assert isinstance(results, dict)
    for name, (witness_only, control_only) in SCENARIOS.items():
        scenario: dict[str, object] = {
            "witness_only_probability": witness_only,
            "control_only_probability": control_only,
            "true_difference": witness_only - control_only,
            "sample_sizes": {},
        }
        sizes = scenario["sample_sizes"]
        assert isinstance(sizes, dict)
        for n in SAMPLE_SIZES:
            widths = []
            exclusions = 0
            for _ in range(REPLICATES):
                draws = [rng.random() for _ in range(n)]
                differences = [
                    1 if u < witness_only else -1 if u < witness_only + control_only else 0
                    for u in draws
                ]
                mean = sum(differences) / n
                second = sum(value * value for value in differences) / n
                standard_error = math.sqrt(max(0.0, second - mean * mean) / (n - 1))
                half_width = 1.96 * standard_error
                widths.append(2 * half_width)
                positive = sum(value == 1 for value in differences)
                negative = sum(value == -1 for value in differences)
                discordant = positive + negative
                tail = min(positive, negative)
                p_value = min(
                    1.0,
                    2 * sum(math.comb(discordant, k) for k in range(tail + 1)) / (2**discordant),
                )
                exclusions += int(p_value < 0.05)
            widths.sort()
            sizes[str(n)] = {
                "median_total_95pct_width": round(widths[REPLICATES // 2], 4),
                "probability_width_at_most_target": round(
                    sum(width <= 0.45 for width in widths) / REPLICATES, 4
                ),
                "probability_exact_test_rejects_zero": round(exclusions / REPLICATES, 4),
            }
        results[name] = scenario
    return output


if __name__ == "__main__":
    destination = Path("docs/diagnostics/m66-confirmatory-precision-design.json")
    destination.write_text(json.dumps(simulate(), indent=2, sort_keys=True) + "\n")
