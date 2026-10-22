"""Run the detached, pinned M6.8.2 audit with no model or gateway dispatch."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path

from offsecgym.research.m673_audit import load_snapshot, sha
from offsecgym.research.m682_grounding_audit import (
    PARSER_SHA,
    admit_private_rows,
    audit,
    canonical,
)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("private output directory must be mode 0700")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def generate(
    project_root: Path, code_root: Path, private_input: Path
) -> tuple[dict[str, object], list[dict[str, object]]]:
    parser_path = code_root / "src/offsecgym/research/m681_reference_syntax.py"
    if sha(parser_path) != PARSER_SHA:
        raise ValueError("frozen reference parser source differs")
    snapshot = await load_snapshot(project_root)
    rows = admit_private_rows(private_input, snapshot)
    public, private = audit(snapshot, rows)
    code_paths = [
        code_root / "src/offsecgym/research/m682_grounding_audit.py",
        code_root / "src/offsecgym/research/m682_public_evidence_index.py",
        code_root / "src/offsecgym/research/m682_object_resolution.py",
        code_root / "src/offsecgym/research/m682_hypothesis_alignment.py",
        code_root / "src/offsecgym/research/m681_rejection_taxonomy.py",
        parser_path,
        code_root / "research_ops/m682_grounding_audit.py",
    ]
    public["source_hashes"] = {
        path.relative_to(code_root).as_posix(): sha(path) for path in code_paths
    }
    public["authoritative_trace_inventory_sha256"] = hashlib.sha256(
        canonical(
            sorted(
                str(cell.record["trace_sha256"])
                for cell in snapshot.cells
                if cell.record and cell.row["trace_auditable"] is True
            )
        )
    ).hexdigest()
    return public, private


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--private-input", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args()
    code_root = Path(__file__).resolve().parents[1]
    first_public, first_private = asyncio.run(
        generate(args.project_root, code_root, args.private_input)
    )
    second_public, second_private = asyncio.run(
        generate(args.project_root, code_root, args.private_input)
    )
    public_data, private_data = canonical(first_public), canonical(first_private)
    if public_data != canonical(second_public) or private_data != canonical(second_private):
        raise ValueError("historical audit output is not byte-identical across two reconstructions")
    if args.public_output.exists() or args.private_output.exists():
        raise FileExistsError("output must be create-once")
    _write_private(args.private_output, private_data)
    try:
        args.public_output.parent.mkdir(parents=True, exist_ok=True)
        args.public_output.write_bytes(public_data)
    except BaseException:
        args.private_output.unlink(missing_ok=True)
        raise
    print(
        json.dumps(
            {
                "public_sha256": hashlib.sha256(public_data).hexdigest(),
                "private_sha256": hashlib.sha256(private_data).hexdigest(),
                "deterministic_reconstructions": 2,
                "proposals": first_public["cohort"]["proposals"],
                "references": first_public["cohort"]["references"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
