"""Freeze M6.7.1 manifest once after protocol merge; public metadata GET only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

from offsecgym.research.m671_protocol import (
    ENDPOINT_URL,
    INPUT_PRICE,
    MANIFEST_PATH,
    OUTPUT_PRICE,
    REQUESTED_MODEL,
    REVISION,
    UPSTREAM,
    plan_manifest,
    require_clean_merge,
)


def endpoint_receipt() -> dict[str, object]:
    """Read exact public selected endpoint; never read a provider credential."""
    with urlopen(ENDPOINT_URL, timeout=20) as response:
        raw = response.read(2_000_000)
    payload = json.loads(raw)
    matches = [
        item
        for item in payload["data"]["endpoints"]
        if item.get("name") == f"{UPSTREAM} | {REVISION}" and item.get("provider_name") == UPSTREAM
    ]
    if len(matches) != 1:
        raise ValueError("selected Moonshot endpoint absent or ambiguous")
    item = matches[0]
    supported = set(item.get("supported_parameters", []))
    if (
        item.get("model_id") != REQUESTED_MODEL
        or item.get("status") != 0
        or not {"tools", "tool_choice", "reasoning_effort"} <= supported
        or Decimal(item["pricing"]["prompt"]) * 1_000_000 != INPUT_PRICE
        or Decimal(item["pricing"]["completion"]) * 1_000_000 != OUTPUT_PRICE
    ):
        raise ValueError("selected endpoint status, support, or prices drifted")
    return {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": ENDPOINT_URL,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "model_id": REQUESTED_MODEL,
        "endpoint": item["name"],
        "upstream_provider": UPSTREAM,
        "revision": REVISION,
        "status": item["status"],
        "tools_supported": True,
        "reasoning_supported": True,
        "input_usd_per_million_tokens": str(INPUT_PRICE),
        "output_usd_per_million_tokens": str(OUTPUT_PRICE),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--protocol-commit", required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    require_clean_merge(root, args.protocol_commit)
    output = root / MANIFEST_PATH
    if output.exists() or output.is_symlink():
        raise FileExistsError("live manifest already exists")
    receipt = endpoint_receipt()
    plan = plan_manifest(root, protocol_commit=args.protocol_commit, endpoint_receipt=receipt)
    raw = json.dumps(plan, sort_keys=True, indent=2).encode() + b"\n"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "planned_cells": plan["planned_cells"],
                "paid_model_calls_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
