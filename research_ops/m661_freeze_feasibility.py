"""Create the excluded M6.6.1 feasibility manifest once, after protocol merge.

This performs a public metadata GET only. It never requests a model completion,
reads an API key, or creates paid-call authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

from offsecgym.research.m661_feasibility_protocol import (
    ENDPOINT_URL,
    INPUT_PRICE_PER_MILLION,
    OUTPUT_PRICE_PER_MILLION,
    REQUESTED_MODEL,
    SELECTED_REVISION,
    UPSTREAM,
    plan_feasibility,
    require_protocol_checkout,
)


def live_endpoint_receipt() -> dict[str, object]:
    with urlopen(ENDPOINT_URL, timeout=20) as response:
        raw = response.read(2_000_000)
    payload = json.loads(raw)
    selected = [
        item
        for item in payload["data"]["endpoints"]
        if item.get("name") == f"{UPSTREAM} | {SELECTED_REVISION}"
        and item.get("provider_name") == UPSTREAM
    ]
    if len(selected) != 1:
        raise ValueError("selected Moonshot AI endpoint is absent or ambiguous")
    endpoint = selected[0]
    if (
        endpoint.get("model_id") != REQUESTED_MODEL
        or endpoint.get("status") != 0
        or Decimal(endpoint["pricing"]["prompt"]) * 1_000_000
        != Decimal(str(INPUT_PRICE_PER_MILLION))
        or Decimal(endpoint["pricing"]["completion"]) * 1_000_000
        != Decimal(str(OUTPUT_PRICE_PER_MILLION))
        or not {"tools", "tool_choice", "reasoning_effort"}
        <= set(endpoint.get("supported_parameters", []))
    ):
        raise ValueError("selected endpoint price, status, or tool support drifted")
    return {
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": ENDPOINT_URL,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "model_id": REQUESTED_MODEL,
        "endpoint": endpoint["name"],
        "upstream_provider": UPSTREAM,
        "revision": SELECTED_REVISION,
        "status": endpoint["status"],
        "input_usd_per_million_tokens": INPUT_PRICE_PER_MILLION,
        "output_usd_per_million_tokens": OUTPUT_PRICE_PER_MILLION,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--protocol-commit", required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = args.output.resolve()
    expected = root / "experiments/manifests/m661-feasibility-v1.json"
    if output != expected:
        raise ValueError("M6.6.1 feasibility manifest path differs")
    require_protocol_checkout(root, args.protocol_commit)
    receipt = live_endpoint_receipt()
    plan = plan_feasibility(root, protocol_commit=args.protocol_commit, endpoint_receipt=receipt)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "protocol_commit": args.protocol_commit,
                "planned_cells": plan["planned_cells"],
                "cumulative_estimated_cost_stop_usd": plan["cumulative_estimated_cost_stop_usd"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
