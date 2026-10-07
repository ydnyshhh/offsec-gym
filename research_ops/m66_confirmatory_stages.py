"""Read-only, terminal-bounded M6.6 confirmatory root-stage extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from m66_extract_stages import _model_artifacts, _requests, _witness_source_trace

from offsecgym.research.m64_stage_ledger import _load_actions
from offsecgym.research.m65_witness_packet import build_reporter_bundle
from offsecgym.research.m66_confirmatory_extract import (
    extract_confirmatory_stages,
    terminal_bounded_inputs,
)
from offsecgym.schemas.events import (
    ActionRequested,
    ModelCallCompleted,
    ModelCallStarted,
    RangeStarted,
    WitnessHypothesisStarted,
    parse_event,
)
from offsecgym.schemas.ground_truth import GroundTruthManifest
from offsecgym.worldview.witness import project_witness


def extract(
    manifest_path: Path, cell_id: str, trace_path: Path, trace_sha256: str, state_dir: Path
) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(manifest_path.read_bytes())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != manifest["protocol_commit"]:
        raise ValueError("confirmatory stage extraction requires exact protocol_commit")
    cells = [cell for cell in manifest["cells"] if cell["cell_id"] == cell_id]
    if len(cells) != 1:
        raise ValueError("confirmatory cell identity is missing or duplicated")
    cell = cells[0]
    raw = trace_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != trace_sha256:
        raise ValueError("confirmatory trace hash differs from journal")
    decoded = json.loads(raw)
    trace = [parse_event(item) for item in decoded]
    if not trace or len({event.run_id for event in trace}) != 1:
        raise ValueError("confirmatory trace run identity differs")
    bounded, _, _ = terminal_bounded_inputs(trace, [], [])
    call_ids = {event.call_id for event in bounded if isinstance(event, ModelCallStarted)}
    for event in bounded:
        if (
            isinstance(event, ActionRequested)
            and event.source_phase is None
            and (event.originating_call_id is None or event.originating_call_id not in call_ids)
        ):
            raise ValueError("confirmatory gateway request lacks an attributable model call")
        if isinstance(event, ModelCallStarted) and (
            event.provider != "openrouter" or event.model != manifest["model_request"]["name"]
        ):
            raise ValueError("confirmatory model request differs from frozen policy")
        if isinstance(event, ModelCallCompleted) and (
            event.resolved_model_revision,
            event.resolved_upstream_provider,
        ) != (
            manifest["expected_selected_endpoint"]["revision"],
            manifest["expected_selected_endpoint"]["upstream_provider"],
        ):
            raise ValueError("confirmatory selected model endpoint drifted")
    ranges = [event for event in bounded if isinstance(event, RangeStarted)]
    if len(ranges) != 1 or ranges[0].build_id is None:
        raise ValueError("confirmatory trace lacks one versioned range start")
    build_id = ranges[0].build_id.hex
    oracle = GroundTruthManifest.model_validate_json(
        (state_dir / "oracles" / build_id / "ground_truth.json").read_bytes()
    )
    fixture = json.loads((state_dir / "builds" / build_id / "fixture.json").read_bytes())
    names, reminder_bytes, tool_origins = _model_artifacts(bounded, state_dir)
    if any(
        (event.originating_call_id, event.originating_tool_call_id) not in tool_origins
        for event in bounded
        if isinstance(event, ActionRequested) and event.source_phase is None
    ):
        raise ValueError("confirmatory gateway action lacks an attributable model tool call")
    hypotheses = [event for event in bounded if isinstance(event, WitnessHypothesisStarted)]
    witness_statuses = []
    if hypotheses:
        source = _witness_source_trace(bounded)
        bundle = build_reporter_bundle(source, state_dir, expected_run_id=trace[0].run_id)
        witness_statuses = [project_witness(item, bundle).status for item in hypotheses]
    result = extract_confirmatory_stages(
        trace,
        _load_actions(decoded, state_dir),
        _requests(trace, state_dir),
        oracle,
        fixture,
        family=cell["range_family"],
        variant=cell["variant"],
        arm=cell["arm"],
        seed=cell["seed"],
        arm_order=tuple(cell["arm_order"]),
        tool_names=names,
        witness_statuses=witness_statuses,
        reminder_bytes=reminder_bytes,
    )
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "cell_id": cell_id,
        "trace_sha256": trace_sha256,
        "stages": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--trace-sha256", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.manifest, args.cell_id, args.trace, args.trace_sha256, args.state_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
