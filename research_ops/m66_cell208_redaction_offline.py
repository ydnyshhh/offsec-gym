"""Reconstruct cell 208's stage from a redacted request and pinned model response.

This is an offline exception for one completed source run. It does not change
the frozen protocol, model requests, gateway, validator, or study assignments.
The original request body is used only in memory after checking its hash
against the authoritative event and its redaction against the stored artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

PROTOCOL_COMMIT = "9fe5b24e8fa1550ed681e9345673cbc1457e7e23"
MANIFEST_SHA256 = "a8aa98654360945ad69f350c38153dccaf827434a4e157819f558ae8da05916c"
CELL_ID = "84757cc11cc22ebc"
RUN_ID = "5baad987-c9b6-4825-917c-d47dbac907a1"
TRACE_SHA256 = "93d7ccf7006e76b7599e129b1823802d8f121166ad0a1b6491ccfabbe2bf1470"
ACTION_ID = "1b6588f6-8570-4684-9d96-9adc3c4ea08a"
ACTION_SEQUENCE = 969
REDACTED_PATH = "budget_tokens"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _bound_original_body(action: dict, request: object, response: dict, redact) -> dict:
    matches = [
        item
        for item in response["output"]
        if item.get("type") == "function_call"
        and item.get("call_id") == action["originating_tool_call_id"]
        and item.get("name") == "http_request"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("arguments"), str):
        raise ValueError("cell-208 action lacks one bound model tool call")
    args = json.loads(matches[0]["arguments"])
    original = json.loads(args["body_json"])
    if (
        not isinstance(original, dict)
        or set(args) != {"body_json", "identity_id", "method", "path"}
        or args["method"] != action["method"]
        or args["identity_id"] != action["identity_id"]
        or _sha256(args["path"].encode()) != action["path_sha256"]
        or _sha256(_canonical(original)) != action["body_sha256"]
        or redact(original) != request.json_body
        or request.json_body.get(REDACTED_PATH) != "*"
        or {key for key, value in request.json_body.items() if value == "*"} != {REDACTED_PATH}
        or str(request.action_id) != ACTION_ID
        or str(request.run_id) != RUN_ID
    ):
        raise ValueError("redacted artifact, model response, and event do not bind")
    return original


def reconstruct(
    *, protocol_root: Path, manifest_path: Path, trace_path: Path, state_dir: Path
) -> dict:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=protocol_root, text=True
    ).strip()
    if head != PROTOCOL_COMMIT or _sha256(manifest_path.read_bytes()) != MANIFEST_SHA256:
        raise ValueError("offline replay requires the exact frozen protocol and manifest")
    if _sha256(trace_path.read_bytes()) != TRACE_SHA256:
        raise ValueError("cell-208 trace differs from its journal binding")
    sys.path.insert(0, str(protocol_root / "research_ops"))
    sys.path.insert(0, str(protocol_root / "src"))

    from m66_confirmatory_stages import extract  # noqa: PLC0415

    from offsecgym.gateway.compose import _redact_json  # noqa: PLC0415
    from offsecgym.providers.artifacts import ModelCallArtifacts  # noqa: PLC0415
    from offsecgym.schemas.evidence import RequestArtifact  # noqa: PLC0415

    trace = json.loads(trace_path.read_bytes())
    if len({item["run_id"] for item in trace}) != 1 or trace[0]["run_id"] != RUN_ID:
        raise ValueError("cell-208 source run differs")
    requested = [
        item
        for item in trace
        if item["type"] == "action_requested" and item["action_id"] == ACTION_ID
    ]
    completed = [
        item
        for item in trace
        if item["type"] == "action_completed" and item["action_id"] == ACTION_ID
    ]
    calls = [
        item
        for item in trace
        if item["type"] == "model_call_completed"
        and requested
        and item["call_id"] == requested[0]["originating_call_id"]
    ]
    if (
        len(requested) != 1
        or len(completed) != 1
        or len(calls) != 1
        or requested[0]["sequence_number"] != ACTION_SEQUENCE
        or completed[0]["evidence_id"] is None
    ):
        raise ValueError("cell-208 redacted action identity differs")
    action, call = requested[0], calls[0]
    request_path = (
        state_dir
        / "instances"
        / action["range_instance_id"].replace("-", "")
        / "requests"
        / (action["request_artifact_id"].replace("-", "") + ".json")
    )
    request_raw = request_path.read_bytes()
    request = RequestArtifact.model_validate_json(request_raw)
    response = ModelCallArtifacts(state_dir).read_verified(
        UUID(RUN_ID),
        UUID(call["call_id"]),
        "response",
        UUID(call["response_artifact_id"]),
        call["response_sha256"],
    )
    original = _bound_original_body(action, request, response, _redact_json)

    try:
        extract(manifest_path, CELL_ID, trace_path, TRACE_SHA256, state_dir)
    except ValueError as exc:
        if str(exc) != "action artifact and authoritative event disagree":
            raise ValueError("frozen stage failed for a different reason") from exc
    else:
        raise ValueError("frozen stage no longer reproduces the redaction failure")

    original_parser = RequestArtifact.model_validate_json
    substitutions = 0

    def parse_request(raw: bytes):
        nonlocal substitutions
        parsed = original_parser(raw)
        if raw == request_raw:
            substitutions += 1
            return parsed.model_copy(update={"json_body": original})
        return parsed

    with patch.object(RequestArtifact, "model_validate_json", staticmethod(parse_request)):
        result = extract(manifest_path, CELL_ID, trace_path, TRACE_SHA256, state_dir)
    # The frozen stage code reads the same request once for proof actions and
    # once for the observed-request ledger. No other artifact may be replaced.
    if substitutions != 2:
        raise ValueError("offline source-body substitution count differs")
    return result


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
    print(json.dumps({"cell_id": CELL_ID, "reconciled_field": REDACTED_PATH}))


if __name__ == "__main__":
    main()
