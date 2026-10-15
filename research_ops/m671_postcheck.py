"""Run the future M6.7.1 read-only PostgreSQL replay; never invoke a model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m671_postcheck import postcheck
from offsecgym.storage.event_store import PostgresEventStore


async def main_async(args: argparse.Namespace) -> None:
    url = os.environ.get("OFFSECGYM_DATABASE_URL")
    if not url:
        raise ValueError("dedicated database URL required")
    engine = create_async_engine(url)
    try:
        manifest_bytes = args.manifest.read_bytes()
        manifest = json.loads(manifest_bytes)
        result = await postcheck(
            manifest,
            args.journal.read_bytes(),
            hashlib.sha256(manifest_bytes).hexdigest(),
            args.pair_directory,
            args.state_root,
            PostgresEventStore(engine),
            engine,
            protocol_root=Path(__file__).resolve().parents[1],
        )
        # The analysis path is intentionally create-only and is not written
        # during this prospective protocol PR.
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "analysis_bytes"},
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--pair-directory", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
