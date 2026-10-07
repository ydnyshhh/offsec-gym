"""Analyze the complete M6.6 assignment from independently audited cell artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from offsecgym.research.m66_confirmatory_analysis import analyze_confirmatory
from offsecgym.research.m66_confirmatory_records import record_from_audit


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def analyze(manifest_path: Path, journal_path: Path, audit_path: Path) -> dict[str, object]:
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    root = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != manifest["protocol_commit"] or manifest["protocol"] != "m66-confirmatory-v2":
        raise ValueError("analysis requires the exact confirmatory protocol checkout")
    manifest_sha = _sha(manifest_raw)
    cells = manifest["cells"]
    if len(cells) != 280 or [cell["order"] for cell in cells] != list(range(1, 281)):
        raise ValueError("analysis requires all 280 ordered assignments")
    lines = [json.loads(line) for line in journal_path.read_bytes().splitlines()]
    if not lines or lines[0].get("manifest_sha256") != manifest_sha:
        raise ValueError("journal header differs from frozen assignment")
    completed = {}
    next_order = 1
    if (len(lines) - 1) % 2:
        raise ValueError("interrupted cell requires authoritative reconciliation")
    for started, terminal in zip(lines[1::2], lines[2::2], strict=True):
        cell = cells[next_order - 1]
        if (
            started.get("type") != "cell_started"
            or terminal.get("type") != "cell_completed"
            or started.get("cell_id") != cell["cell_id"]
            or terminal.get("cell_id") != cell["cell_id"]
            or started.get("order") != next_order
            or terminal.get("order") != next_order
        ):
            raise ValueError("journal cell order or identity differs from manifest")
        completed[cell["cell_id"]] = terminal
        next_order += 1
    audit_bundle = json.loads(audit_path.read_bytes())
    if audit_bundle.get("manifest_sha256") != manifest_sha or audit_bundle.get(
        "journal_sha256"
    ) != _sha(journal_path.read_bytes()):
        raise ValueError("authoritative audit does not bind manifest and journal bytes")
    audits = audit_bundle.get("cells")
    if not isinstance(audits, dict) or set(audits) != set(completed):
        raise ValueError("audited cell set differs from completed journal cells")
    rows = []
    for cell in cells:
        terminal = completed.get(cell["cell_id"])
        stage_output = None
        if terminal is not None:
            audit = audits[cell["cell_id"]]
            if audit.get("cell_id") != cell["cell_id"] or audit.get("run_id") != terminal.get(
                "run_id"
            ):
                raise ValueError("audit cell or run identity differs")
            stage_path = audit.get("stage_path")
            stage_sha = audit.get("stage_sha256")
            if stage_path is not None or stage_sha is not None:
                if not isinstance(stage_path, str) or not isinstance(stage_sha, str):
                    raise ValueError("stage path and hash must both be present")
                stage_raw = Path(stage_path).read_bytes()
                if _sha(stage_raw) != stage_sha:
                    raise ValueError("stage artifact differs from authoritative audit")
                stage_output = json.loads(stage_raw)
            elif not audit.get("unmeasurable_reason"):
                raise ValueError("completed cell lacks audited stage or missingness reason")
        rows.append(
            record_from_audit(
                cell,
                terminal=terminal,
                stage_output=stage_output,
                manifest_sha256=manifest_sha,
                protocol=manifest["protocol"],
            )
        )
    keys = frozenset(
        (cell["range_family"], cell["seed"], cell["variant"], cell["arm"]) for cell in cells
    )
    return {
        "protocol": manifest["protocol"],
        "manifest_sha256": manifest_sha,
        "journal_sha256": _sha(journal_path.read_bytes()),
        "audit_sha256": _sha(audit_path.read_bytes()),
        "observed_cells": len(completed),
        "unstarted_cells": len(cells) - len(completed),
        "results": analyze_confirmatory(
            tuple(rows), protocol=manifest["protocol"], assigned_keys=keys
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.manifest, args.journal, args.audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
