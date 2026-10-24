"""Run bounded, read-only A1 public candidate discovery on a local synthetic range."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from offsecgym.benchmark.a1_public import (
    audit_public_observations,
    discover_public_candidate,
    freeze_visibility,
    public_reachability,
)
from offsecgym.benchmark.a1_runtime import (
    MemoryEvents,
    destroy_owned,
    private_write,
    qualification_source_pins,
    require_private_root,
)
from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.runtime.compose import ComposeRangeRuntime
from offsecgym.schemas.domain import (
    AgentContext,
    AgentTask,
    ExperimentContext,
    agent_visible_context,
)
from offsecgym.schemas.specs import Budget

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "docs/benchmark/t11c-a1-public-reachability.json"


async def execute(seed: int, variant: str, private_root: Path) -> dict[str, object]:
    manifest = make_task_manifest("A1", task_mode="scoped_audit", variant=variant, seed=seed)
    if manifest.private.source_sha256 != source_pins():
        raise ValueError("source_pin_mismatch")
    runtime = ComposeRangeRuntime(private_root / "runtime")
    build_id = await runtime.build(manifest.private.range_spec)
    instance_id = await runtime.create_instance(build_id)
    project = runtime.state.load_instance(instance_id).project_name
    cleanup = False
    private: dict[str, object] = {}
    result: dict[str, object] = {}
    try:
        status = await runtime.start_instance(instance_id)
        if status.state != "healthy":
            raise RuntimeError("range_not_healthy")
        metadata = await runtime.snapshot_metadata(instance_id)
        # This is the current monolithic runner's account projection. The model
        # prompt renders IDs and instructs /api/me role discovery; roles are not
        # included in the reference policy's effective public inputs.
        visible = agent_visible_context(metadata, visibility_policy="white_box_accounts")
        budget = Budget(max_actions=24, max_http_requests=24, max_total_tokens=4096)
        run_id = uuid4()
        task = AgentTask(task_id=uuid4(), goal=manifest.public.public_objective, budget=budget)
        context = AgentContext(run_id=run_id, objective=task.goal, range=visible)
        contract, public, rendered = freeze_visibility(
            manifest.public, task, context, manifest.private.source_sha256
        )
        events = MemoryEvents()
        gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)
        experiment = ExperimentContext(
            run_id=run_id,
            range_instance_id=instance_id,
            range_generation=status.generation,
            budget=budget,
            allowed_identity_ids=visible.identity_ids,
        )
        raw, actor, workspace, candidate = await discover_public_candidate(
            public, gateway, experiment
        )
        graph = await audit_public_observations(
            state=runtime.state,
            events=events,
            task_id=manifest.task.task_id,
            contract=contract,
            experiment=experiment,
            raw=raw,
            actor=actor,
            workspace=workspace,
            candidate=candidate,
        )
        q3 = public_reachability(graph, contract)
        private = {
            "visibility_contract": contract.model_dump(mode="json"),
            "model_rendered_context": rendered,
            "graph": graph.model_dump(mode="json"),
            "events": [item.model_dump(mode="json") for item in events.items],
        }
        result = {
            "milestone": "T1.1c",
            "policy_version": "t11c-a1-public-discovery-v1",
            "task_id": manifest.task.task_id,
            "seed": seed,
            "variant": variant,
            "visibility_contract_id": contract.version,
            "visibility_parity": contract.visibility_parity,
            "q3": q3,
            "public_observation_count": len(graph.observations),
            "public_graph_node_count": len(graph.nodes),
            "public_graph_edge_count": len(graph.edges),
            "candidate_count": int(graph.candidate is not None),
            "observation_graph_sha256": hashlib.sha256(
                json.dumps(graph.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest(),
            "source_sha256": manifest.private.source_sha256,
            "qualification_source_sha256": qualification_source_pins(ROOT),
            "provider_calls": 0,
        }
    finally:
        cleanup = await destroy_owned(runtime, instance_id, project)
    result["docker_cleanup_verified"] = cleanup
    if not cleanup:
        result["q3"]["status"] = "unknown"
        result["q3"]["reachability"] = "unknown"
    private_write(private_root / "q3-private.json", private)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", choices=("A1",), required=True)
    parser.add_argument("--seed", type=int, choices=(42, 43, 44), required=True)
    parser.add_argument("--variant", choices=("vulnerable", "patched"), default="vulnerable")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    manifest = make_task_manifest(
        "A1", task_mode="scoped_audit", variant=args.variant, seed=args.seed
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "task_id": manifest.task.task_id,
                    "seed": args.seed,
                    "variant": args.variant,
                    "provider_calls": 0,
                },
                sort_keys=True,
            )
        )
        return
    if args.output_private is None:
        parser.error("--execute requires --output-private")
    private_root = require_private_root(args.output_private, ROOT)
    report = asyncio.run(execute(args.seed, args.variant, private_root))
    if args.seed == 42 and args.variant == "vulnerable":
        PUBLIC.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("task_id", "seed", "variant", "q3", "docker_cleanup_verified")
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
