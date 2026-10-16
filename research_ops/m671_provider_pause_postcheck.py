"""Read-only final replay of the frozen M6.7.1 prefix plus approved continuation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from m671_collect import MANIFEST_SHA256, sha
from m671_provider_pause_stop import stopped_records
from m671_resume_after_provider_pause import combined_journal
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m671_postcheck import postcheck
from offsecgym.storage.event_store import PostgresEventStore


async def replay(args: argparse.Namespace) -> dict[str, object]:
    private = args.stopped_journal.parent
    if (
        args.stopped_journal != private / "journal.jsonl"
        or args.stopped_log != private / "collector.log"
        or args.continuation_root != private / "provider-pause-continuation"
        or args.state_root != private / "range-state"
        or not os.getenv("OFFSECGYM_DATABASE_URL")
    ):
        raise ValueError("final read-only postcheck paths or database differ")
    raw = args.manifest.read_bytes()
    if sha(raw) != MANIFEST_SHA256:
        raise ValueError("frozen manifest bytes differ")
    manifest = json.loads(raw)
    stopped_records(manifest, private)
    later = args.continuation_root / "journal.jsonl"
    artifact = args.continuation_root / "frozen-analysis.json"
    if any(path.is_symlink() or not path.is_file() for path in (later, artifact)):
        raise ValueError("continuation journal or analysis artifact is unsafe")
    combined = combined_journal(args.stopped_journal.read_bytes(), later.read_bytes())
    engine = create_async_engine(os.environ["OFFSECGYM_DATABASE_URL"])
    try:
        events = PostgresEventStore(engine)
        first = await postcheck(
            manifest,
            combined,
            MANIFEST_SHA256,
            args.continuation_root / "pairs",
            args.state_root,
            events,
            engine,
            protocol_root=args.protocol_root,
        )
        second = await postcheck(
            manifest,
            combined,
            MANIFEST_SHA256,
            args.continuation_root / "pairs",
            args.state_root,
            events,
            engine,
            protocol_root=args.protocol_root,
        )
        if first["analysis_bytes"] != second["analysis_bytes"] or (
            artifact.read_bytes() != first["analysis_bytes"]
        ):
            raise ValueError("frozen analysis is not byte-identical to PostgreSQL replay")
        return {key: value for key, value in first.items() if key != "analysis_bytes"}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "manifest",
        "protocol-root",
        "stopped-journal",
        "stopped-log",
        "state-root",
        "continuation-root",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    print(json.dumps(asyncio.run(replay(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
