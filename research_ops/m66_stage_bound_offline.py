"""Reconstruct one frozen M6.6 stage after the reporter-packet bound failure.

This process imports only the exact frozen protocol checkout. It relaxes the
packet-size guard in memory to the observed 70,045 characters solely while
reading the stopped trace. It never runs a model or writes study state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROTOCOL_COMMIT = "9fe5b24e8fa1550ed681e9345673cbc1457e7e23"
CELL_ID = "6a1de650f2404e27"
TRACE_SHA256 = "c8f48f176e628a5fab038f2a3e7c18f009e64d51c8b1e6f2b82bbdc45a38ba0f"
FROZEN_PACKET_LIMIT = 70_000
EXACT_PACKET_CHARS = 70_045


def reconstruct(
    *,
    protocol_root: Path,
    manifest_path: Path,
    trace_path: Path,
    state_dir: Path,
) -> dict:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=protocol_root, text=True
    ).strip()
    if head != PROTOCOL_COMMIT:
        raise ValueError("offline stage replay requires the exact frozen protocol commit")
    sys.path.insert(0, str(protocol_root / "research_ops"))
    sys.path.insert(0, str(protocol_root / "src"))

    from m66_confirmatory_stages import extract  # noqa: PLC0415
    from m66_extract_stages import _witness_source_trace  # noqa: PLC0415
    from pydantic import ValidationError  # noqa: PLC0415

    from offsecgym.research import m65_witness_packet as packet  # noqa: PLC0415
    from offsecgym.research.m66_confirmatory_extract import (  # noqa: PLC0415
        terminal_bounded_inputs,
    )
    from offsecgym.schemas.events import parse_event  # noqa: PLC0415

    if packet.MAX_PACKET_CHARS != FROZEN_PACKET_LIMIT:
        raise ValueError("frozen reporter-packet limit differs")
    trace = [parse_event(item) for item in json.loads(trace_path.read_bytes())]
    bounded, _, _ = terminal_bounded_inputs(trace, [], [])
    source = _witness_source_trace(bounded)
    try:
        extract(manifest_path, CELL_ID, trace_path, TRACE_SHA256, state_dir)
    except ValidationError as exc:
        if "reporter packet exceeds its declared context bound" not in str(exc):
            raise ValueError("frozen extractor failed for a different reason") from exc
    else:
        raise ValueError("frozen stage extraction no longer reproduces the failure")

    original = packet.MAX_PACKET_CHARS
    try:
        packet.MAX_PACKET_CHARS = EXACT_PACKET_CHARS
        bundle = packet.build_reporter_bundle(
            source,
            state_dir,
            expected_run_id=trace[0].run_id,
            project_visible_identities=False,
        )
        if len(bundle.packet.model_dump_json()) != EXACT_PACKET_CHARS:
            raise ValueError("reconstructed packet does not match the measured bound")
        return extract(manifest_path, CELL_ID, trace_path, TRACE_SHA256, state_dir)
    finally:
        packet.MAX_PACKET_CHARS = original


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = reconstruct(
        protocol_root=args.protocol_root.resolve(),
        manifest_path=args.manifest.resolve(),
        trace_path=args.trace.resolve(),
        state_dir=args.state_dir.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"cell_id": CELL_ID, "packet_chars": EXACT_PACKET_CHARS}))


if __name__ == "__main__":
    main()
