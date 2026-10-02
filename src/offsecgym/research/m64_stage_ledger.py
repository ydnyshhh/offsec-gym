"""Offline, descriptive stage ledger for completed M6.4 traces.

This code reads frozen trace and oracle artifacts. It never contacts a model or
range. It was implemented after collection and is not a primary score analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

from offsecgym.schemas.evidence import Evidence, RequestArtifact
from offsecgym.schemas.ground_truth import GroundTruthManifest, StateTransitionRequirement
from offsecgym.validation.deterministic import (
    DeterministicValidator,
    ProofAction,
    _action_route,
    _transition_evidenced,
)

ROOT_KINDS = {
    "DOC-CROSS-TENANT-READ": "document",
    "INVOICE-CROSS-TENANT-READ": "invoice",
    "TICKET-CROSS-TENANT-READ": "ticket",
    "MEMBER-REFUND": "refund",
    "PUBLIC-INVOICE-METADATA": "public",
}
COLLECTIONS = {"document": "documents", "invoice": "invoices", "ticket": "tickets"}
STAGES = (
    "prerequisite_not_ready",
    "ready_not_admitted",
    "admitted_not_executed",
    "executed_without_trace_proof",
    "trace_proof_without_submission",
    "submitted_not_validated",
    "validated",
)


def _load_actions(trace: list[dict[str, Any]], state_dir: Path) -> list[ProofAction]:
    requested = {
        event["action_id"]: event for event in trace if event["type"] == "action_requested"
    }
    actions: list[ProofAction] = []
    for done in trace:
        if done["type"] != "action_completed" or done["evidence_id"] is None:
            continue
        request_event = requested[done["action_id"]]
        instance = state_dir / "instances" / request_event["range_instance_id"].replace("-", "")
        request_path = (
            instance
            / "requests"
            / (request_event["request_artifact_id"].replace("-", "") + ".json")
        )
        evidence_path = instance / "evidence" / (done["evidence_id"].replace("-", "") + ".json")
        request = RequestArtifact.model_validate_json(request_path.read_bytes())
        evidence = Evidence.model_validate_json(evidence_path.read_bytes())
        body_hash = (
            hashlib.sha256(
                json.dumps(request.json_body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if request.json_body is not None
            else None
        )
        if (
            str(request.action_id) != done["action_id"]
            or str(evidence.action_id) != done["action_id"]
            or str(request.request_artifact_id) != request_event["request_artifact_id"]
            or evidence.request_artifact_id != request.request_artifact_id
            or str(evidence.evidence_id) != done["evidence_id"]
            or request.run_id != evidence.run_id
            or str(request.run_id) != done["run_id"]
            or request.range_instance_id != evidence.range_instance_id
            or str(request.range_instance_id) != request_event["range_instance_id"]
            or hashlib.sha256(request.path.encode()).hexdigest() != request_event["path_sha256"]
            or body_hash != request_event["body_sha256"]
            or evidence.http_status != done["http_status"]
            or evidence.response_sha256 != done["response_sha256"]
        ):
            raise ValueError("action artifact and authoritative event disagree")
        actions.append(ProofAction(request, evidence, done["sequence_number"]))
    return actions


def _trace_proof_assets(prop: Any, fixture: dict[str, Any], actions: list[ProofAction]) -> set[str]:
    assets: set[str] = set()
    for item in fixture[COLLECTIONS[prop.object.resource_type]]:
        asset_id = UUID(item["id"])
        method, path = _action_route(prop, asset_id)
        candidates = [
            action
            for action in actions
            if action.request.method == method
            and action.request.path == path
            and action.request.destination == "saas"
            and not action.evidence.truncated
            and action.body is not None
            and action.body.get("id") == str(asset_id)
            and DeterministicValidator._requirements_met(action, prop, fixture, asset_id)
        ]
        if any(isinstance(req, StateTransitionRequirement) for req in prop.proof_requirements):
            candidates = [
                action
                for action in candidates
                if _transition_evidenced(actions, action, prop, asset_id)
            ]
        if candidates:
            assets.add(str(asset_id))
    return assets


def ledger(manifest_path: Path, journal_path: Path, state_dir: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    feasible = {cell["cell_id"]: cell for cell in manifest["cells"] if cell["policy_feasible"]}
    records = [json.loads(line) for line in journal_path.read_text().splitlines() if line.strip()]
    completed = [record for record in records if record["type"] == "cell_completed"]
    if len(completed) != len(feasible) or {record["cell_id"] for record in completed} != set(
        feasible
    ):
        raise ValueError("stage ledger requires every feasible cell")
    counts: dict[tuple[str, int, str], Counter[str]] = defaultdict(Counter)
    invalid: list[dict[str, Any]] = []
    score_valid_vulnerable_runs = 0
    expected_validated_roots = 0
    for record in completed:
        cell = feasible[record["cell_id"]]
        if cell["variant"] != "vulnerable":
            continue
        if not record["observation"]["evaluation"]["score_valid"]:
            invalid.append(
                {"order": record["order"], "status": record["observation"]["evaluation"]["status"]}
            )
            continue
        score_valid_vulnerable_runs += 1
        expected_validated_roots += record["observation"]["evaluation"]["true_positives"]
        trace_bytes = Path(record["trace_path"]).read_bytes()
        if hashlib.sha256(trace_bytes).hexdigest() != record["trace_sha256"]:
            raise ValueError("trace hash differs from completed journal entry")
        trace = json.loads(trace_bytes)
        build_id = record["build_id"].replace("-", "")
        oracle = GroundTruthManifest.model_validate_json(
            (state_dir / "oracles" / build_id / "ground_truth.json").read_bytes()
        )
        fixture = json.loads((state_dir / "builds" / build_id / "fixture.json").read_text())
        actions = _load_actions(trace, state_dir)
        completed_ids = {
            event["action_id"] for event in trace if event["type"] == "action_completed"
        }
        matched = set(record["observation"]["evaluation"]["matched_root_cause_ids"])
        decisions = [event for event in trace if event["type"] == "admission_decision"]
        ready = {
            kind
            for decision in decisions
            for kind, status in decision["task_states"].items()
            if status == "READY"
        }
        granted = {event["kind"] for event in trace if event["type"] == "task_budget_granted"}
        packet_routes = {
            event["packet"]["contract"]["route_family"]
            for event in trace
            if event["type"] == "worker_packet_prepared"
            and event["packet"].get("contract") is not None
        }
        objective_routes = {
            event["route_family"]
            for event in trace
            if event["type"] == "worker_objective_action" and event["action_id"] in completed_ids
        }
        submissions = [event["finding"] for event in trace if event["type"] == "finding_submitted"]
        properties = [prop for prop in oracle.properties if prop.active]
        if len(properties) != 5 or {prop.slug for prop in properties} != set(ROOT_KINDS):
            raise ValueError("unexpected vulnerable oracle root set")
        for prop in properties:
            kind = ROOT_KINDS[prop.slug]
            route = prop.expectation.action
            if str(prop.root_cause_id) in matched:
                stage = "validated"
            elif decisions and kind not in ready:
                stage = "prerequisite_not_ready"
            elif kind not in granted if decisions else route not in packet_routes:
                stage = "ready_not_admitted" if decisions else "admitted_not_executed"
            elif route not in objective_routes:
                stage = "admitted_not_executed"
            else:
                proof_assets = _trace_proof_assets(prop, fixture, actions)
                if not proof_assets:
                    stage = "executed_without_trace_proof"
                elif not any(
                    finding["family"] == prop.family
                    and finding["security_property"] == prop.expectation.model_dump(mode="json")
                    and finding["asset_id"] in proof_assets
                    for finding in submissions
                ):
                    stage = "trace_proof_without_submission"
                else:
                    stage = "submitted_not_validated"
            counts[(cell["arm"], cell["worker_token_budget"], prop.slug)][stage] += 1
    if sum(sum(counter.values()) for counter in counts.values()) != 5 * score_valid_vulnerable_runs:
        raise ValueError("stage ledger does not cover every active vulnerable root")
    if sum(counter["validated"] for counter in counts.values()) != expected_validated_roots:
        raise ValueError("stage ledger validated roots differ from authoritative scoring")
    rows = [
        {
            "arm": arm,
            "budget": budget,
            "root": root,
            "valid_runs": sum(counter.values()),
            "stages": {stage: counter[stage] for stage in STAGES},
        }
        for (arm, budget, root), counter in sorted(counts.items())
    ]
    return {
        "method": (
            "post-collection trace-proof ledger; state-change replay only occurs "
            "for submitted findings"
        ),
        "protocol": manifest["protocol"],
        "invalid_vulnerable_cells": invalid,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = ledger(args.manifest, args.journal, args.state_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(result["rows"]), "invalid": result["invalid_vulnerable_cells"]}))


if __name__ == "__main__":
    main()
