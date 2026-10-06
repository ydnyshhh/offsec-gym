"""No-model Range B qualification across seeds and selective patches.

Run with ``python research_ops/qualify_range_b.py --output PATH``. The optional
``--compose`` phase uses the scripted public-API solver and real local Compose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from uuid import UUID

import yaml

from offsecgym.experiment import ScriptedExperimentRunner
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.runtime.enterprise import PROPERTY_SLUGS, fixture_for_seed
from offsecgym.schemas.specs import BootstrapBudget, Budget, ExperimentSpec, RangeSpec


class MemoryEvents:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        number = sum(item.run_id == event.run_id for item in self.items) + 1
        stored = event.model_copy(update={"sequence_number": number})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


def inspect_fixture(seed: int) -> dict[str, object]:
    fixture, anchors = fixture_for_seed(seed)
    again, repeated = fixture_for_seed(seed)
    assert fixture == again and anchors == repeated
    assert len(fixture["organizations"]) == 3
    assert len(fixture["projects"]) == 6
    assert len(fixture["users"]) == 18
    assert len(fixture["environments"]) == 18
    assert anchors["b1_actor"] != anchors["b3_actor"]
    assert anchors["b2_actor"] != anchors["b2_admin"]
    assert anchors["b1_project"] != anchors["b2_project"]
    requests = {item["id"]: item for item in fixture["access_requests"]}
    changes = {item["id"]: item for item in fixture["changes"]}
    assert requests[anchors["b1_request"]]["status"] == "PENDING"
    assert requests[anchors["b1_request"]]["requester_id"] == anchors["b1_actor"]
    assert changes[anchors["b2_change"]]["status"] == "PENDING_APPROVAL"
    assert changes[anchors["b3_change"]]["status"] == "APPROVED"
    for user, project, role in (
        (anchors["b1_actor"], anchors["b1_project"], "change_approver"),
        (anchors["b2_actor"], anchors["b2_project"], "change_approver"),
        (anchors["b3_actor"], anchors["b1_project"], "operator"),
        (anchors["b2_admin"], anchors["b2_project"], "org_admin"),
    ):
        assert any(
            item["user_id"] == user
            and item["project_id"] == project
            and item["role"] == role
            and item["active"]
            for item in fixture["role_assignments"]
        )
    return {"seed": seed}


async def qualify_compose(base: RangeSpec, seeds: tuple[int, ...]) -> list[dict[str, object]]:
    results = []
    with tempfile.TemporaryDirectory(prefix="offsecgym-range-b-qualification-") as root:
        runtime = ComposeRangeRuntime(Path(root))
        events = MemoryEvents()
        runner = ScriptedExperimentRunner(runtime, events)
        for seed in seeds:
            expected = (
                ("vulnerable", base.model_copy(update={"seed": seed}), 3, 0),
                ("patched", base.model_copy(update={"seed": seed, "patched": True}), 0, 0),
                *(
                    (
                        f"patch-{slug}",
                        base.model_copy(update={"seed": seed, "patched_properties": (slug,)}),
                        2,
                        0,
                    )
                    for slug in sorted(PROPERTY_SLUGS)
                ),
            )
            for label, variant, true_positives, false_positives in expected:
                outcome = await runner.run(
                    ExperimentSpec(
                        name=f"range_b_qualification_{seed}_{label}",
                        seed=seed,
                        range=variant,
                        budget=Budget(
                            max_actions=180,
                            max_http_requests=180,
                            max_wall_seconds=360,
                        ),
                        bootstrap_budget=BootstrapBudget(
                            max_actions=40,
                            max_http_requests=40,
                            max_wall_seconds=90,
                        ),
                        orchestrator="scripted",
                        validation="deterministic",
                    )
                )
                score = outcome.evaluation
                assert score.score_valid and score.status == "completed", (seed, label, score)
                assert (score.true_positives, score.false_positives) == (
                    true_positives,
                    false_positives,
                ), (seed, label, score)
                assert score.false_negatives == 0, (seed, label, score)
                results.append(
                    {
                        "seed": seed,
                        "variant": label,
                        "true_positives": score.true_positives,
                        "false_positives": score.false_positives,
                        "false_negatives": score.false_negatives,
                        "build_id": str(outcome.build_id),
                    }
                )
                print(f"qualified seed={seed} variant={label}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compose", action="store_true")
    args = parser.parse_args()
    base = RangeSpec.model_validate(
        yaml.safe_load(
            (Path(__file__).parents[1] / "examples/enterprise-change-control.yaml").read_text()
        )
    )
    fixtures = []
    with tempfile.TemporaryDirectory(prefix="offsecgym-range-b-compile-") as root:
        runtime = ComposeRangeRuntime(Path(root))
        for seed in range(100):
            inspection = inspect_fixture(seed)
            built = runtime.enterprise_compiler.build(base.model_copy(update={"seed": seed}))
            assert runtime.state.verify_build_integrity(built.build_id) == built
            oracle = json.loads(
                (
                    runtime.state.root / "oracles" / built.build_id.hex / "ground_truth.json"
                ).read_text()
            )
            assert {item["slug"] for item in oracle["properties"]} == PROPERTY_SLUGS
            assert all(item["active"] for item in oracle["properties"])
            inspection["build_id"] = str(built.build_id)
            inspection["pair_id"] = str(built.pair_id)
            fixtures.append(inspection)
        assert len({item["pair_id"] for item in fixtures}) == 100
    compose_seeds = (*range(9), 42)
    results = asyncio.run(qualify_compose(base, compose_seeds)) if args.compose else []
    payload = {
        "compiler_seed_count": len(fixtures),
        "compiler_builds": fixtures,
        "compose_seeds": list(compose_seeds) if args.compose else [],
        "compose_cells": results,
        "qualified": len(fixtures) == 100 and (not args.compose or len(results) == 50),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    print(f"wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
