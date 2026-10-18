"""Run the pinned M6.7.3 offline audit; never dispatch a model or range action."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path

from offsecgym.research.m673_audit import load_snapshot, sha
from offsecgym.research.m673_context_delta import analyze_context_delta
from offsecgym.research.m673_evidence_closure import analyze_closure
from offsecgym.research.m673_interface_grounding import analyze_interface
from offsecgym.research.m673_opportunity import analyze_opportunity
from offsecgym.research.m673_reporting_reconciliation import analyze_reporting


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def write_create_once(path: Path, value: object) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("private audit output directory must be mode 0700")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    data = canonical(value)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(data).hexdigest()


async def run(
    project_root: Path,
    code_root: Path,
    private_output: Path,
    public_json: Path,
    snapshot_at_utc: str,
) -> dict[str, object]:
    snapshot = await load_snapshot(project_root)
    analyses = {
        "experiment_interface": analyze_interface,
        "b3_opportunity": analyze_opportunity,
        "b2_evidence_closure": analyze_closure,
        "reporting_reconciliation": analyze_reporting,
        "response_stagnation": analyze_context_delta,
    }
    aggregate: dict[str, object] = {}
    detail: dict[str, object] = {}
    for name, function in analyses.items():
        aggregate[name], detail[name] = function(snapshot)
    code_paths = sorted((code_root / "src/offsecgym/research").glob("m673_*.py"))
    code_paths.append(code_root / "research_ops/m673_audit.py")
    code_hashes = {path.relative_to(code_root).as_posix(): sha(path) for path in code_paths}
    input_hashes = sorted(
        {
            digest
            for cell in snapshot.cells
            for call in cell.calls
            for digest in (call.start.request_sha256, call.completed.response_sha256)
        }
    )
    auditable_records = [
        cell.record for cell in snapshot.cells if cell.row["trace_auditable"] is True
    ]
    trace_hashes = sorted(str(record["trace_sha256"]) for record in auditable_records)
    checkpoint_hashes = sorted(str(record["receipt_sha256"]) for record in auditable_records)
    if len(trace_hashes) != 114 or len(checkpoint_hashes) != 114:
        raise ValueError("auditable trace/receipt hash inventory differs")
    private_sha = write_create_once(private_output, detail)
    result: dict[str, object] = {
        "milestone": "M6.7.3",
        "boundary": snapshot.boundary,
        "audit_timestamp_utc": snapshot_at_utc,
        "counts": snapshot.public_counts(),
        "analysis_code_sha256": hashlib.sha256(canonical(code_hashes)).hexdigest(),
        "verified_model_artifact_hash_set_sha256": hashlib.sha256(
            canonical(input_hashes)
        ).hexdigest(),
        "verified_model_artifact_count": len(input_hashes),
        "verified_source_trace_hash_set_sha256": hashlib.sha256(
            canonical(trace_hashes)
        ).hexdigest(),
        "verified_checkpoint_receipt_hash_set_sha256": hashlib.sha256(
            canonical(checkpoint_hashes)
        ).hexdigest(),
        "verified_source_trace_count": len(trace_hashes),
        "verified_checkpoint_receipt_count": len(checkpoint_hashes),
        "private_detail_sha256": private_sha,
        "analyses": aggregate,
        "provider_calls_made_by_this_audit": 0,
        "source_outcomes_changed": False,
    }
    public_json.parent.mkdir(parents=True, exist_ok=True)
    public_json.write_bytes(canonical(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--public-json", type=Path, required=True)
    parser.add_argument("--snapshot-at-utc", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.project_root,
            Path(__file__).resolve().parents[1],
            args.private_output,
            args.public_json,
            args.snapshot_at_utc,
        )
    )
    print(
        json.dumps(
            {"counts": result["counts"], "public_sha256": sha(args.public_json)}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
