"""Read-only final PostgreSQL replay with cells 109, 113, and 116 missing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from m671_cell116_recovery_postcheck import postcheck
from m671_collect import pinned_inputs
from m671_resume_after_cell116_environment import (
    journal_header,
    scoped_approval,
)
from sqlalchemy.ext.asyncio import create_async_engine


async def replay(args: argparse.Namespace) -> dict[str, object]:
    private = args.stopped_journal.parent.resolve()
    if (
        args.stopped_journal != private / "journal.jsonl"
        or args.state_root != private / "range-state"
        or args.continuation_root != private / "cell116-continuation"
        or args.continuation_approval != private / "cell116-paid-approval.json"
        or not os.getenv("OFFSECGYM_DATABASE_URL")
    ):
        raise ValueError("final read-only postcheck paths or database differ")
    raw = pinned_inputs(
        args.execution_root.resolve(),
        args.source_root.resolve(),
        args.protocol_root.resolve(),
        args.manifest.resolve(),
    )
    manifest = json.loads(raw)
    scoped_approval(args.continuation_approval, manifest)
    journal_path = args.continuation_root / "journal.jsonl"
    artifact = args.continuation_root / "frozen-analysis.json"
    if any(path.is_symlink() or not path.is_file() for path in (journal_path, artifact)):
        raise ValueError("new journal or frozen analysis artifact is unsafe")
    header = json.loads(journal_path.read_bytes().splitlines()[0])
    if any(header.get(key) != value for key, value in journal_header().items()):
        raise ValueError("new continuation journal header differs")
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    try:
        first = await postcheck(
            manifest,
            private,
            args.continuation_root,
            args.state_root,
            engine,
            header=header,
            protocol_root=args.protocol_root.resolve(),
        )
        second = await postcheck(
            manifest,
            private,
            args.continuation_root,
            args.state_root,
            engine,
            header=header,
            protocol_root=args.protocol_root.resolve(),
        )
        if (
            first["analysis_bytes"] != second["analysis_bytes"]
            or artifact.read_bytes() != first["analysis_bytes"]
        ):
            raise ValueError("frozen factorial analysis is not byte-identical to PostgreSQL replay")
        return {key: value for key, value in first.items() if key != "analysis_bytes"}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution-root",
        "source-root",
        "protocol-root",
        "manifest",
        "continuation-approval",
        "stopped-journal",
        "state-root",
        "continuation-root",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    print(json.dumps(asyncio.run(replay(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
