"""Replay retained M6.7.3 proposals against detached M6.8.1 syntax rules.

This command reads a pinned private audit file and writes a private row table plus
public aggregate. It never imports the agent, gateway, provider, or database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

from offsecgym.research import m681_reference_syntax as syntax

_UUID_FRAGMENT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def replay(rows: object, input_sha256: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return a public aggregate and private rows without consulting a range."""
    if not isinstance(rows, list) or len(rows) != 18:
        raise ValueError("expected exactly 18 retained proposals")
    private_rows: list[dict[str, object]] = []
    proposal_counts: Counter[str] = Counter()
    reference_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("arguments"), dict):
            raise ValueError("malformed retained proposal")
        proposal_id = row.get("tool_call_id")
        arguments = row["arguments"]
        references = row.get("references")
        targets = arguments.get("target_objects")
        if (
            not isinstance(proposal_id, str)
            or proposal_id in seen_ids
            or not isinstance(targets, list)
            or not isinstance(references, list)
            or len(targets) != len(references)
        ):
            raise ValueError("proposal identity or target count mismatch")
        seen_ids.add(proposal_id)
        result = syntax.replay_start_arguments(arguments)
        proposal_counts["strict_schema_accepted"] += result.strict_schema_accepted
        proposal_counts["normalization_eligible"] += result.normalization_eligible
        proposal_counts["post_normalization_schema_accepted"] += (
            result.post_normalization_schema_accepted
        )
        private_refs: list[dict[str, object]] = []
        for original, retained in zip(targets, references, strict=True):
            if not isinstance(retained, dict):
                raise ValueError("malformed retained reference")
            fragment_count = (
                len(_UUID_FRAGMENT.findall(original)) if isinstance(original, str) else 0
            )
            reference_counts["proposed"] += 1
            try:
                parsed = syntax.parse_reference(original)
            except syntax.ReferenceSyntaxError:
                parsed = None
            if parsed is not None:
                reference_counts["syntax_accepted"] += 1
                if parsed.kind is not None:
                    kind_counts[parsed.kind] += 1
            private_refs.append(
                {
                    "original_reference": original,
                    "recognized_prefix_type": parsed.kind if parsed else None,
                    "uuid_count_detected": fragment_count,
                    "canonical_normalized_uuid": parsed.canonical_uuid if parsed else None,
                    "context_observed_identifier": retained.get("context_observed"),
                    "independently_verified_public_source_identifier": retained.get(
                        "public_source_grounded"
                    ),
                    "resolved_public_object_type": retained.get("object_resolved"),
                    "correct_target_correspondence": row.get("would_support_correct_experiment"),
                }
            )
        private_rows.append(
            {
                "original_proposal_id": proposal_id,
                "original_parsing_result": result.strict_schema_accepted,
                "normalization_eligible": result.normalization_eligible,
                "post_normalization_schema_result": result.post_normalization_schema_accepted,
                "references": private_refs,
            }
        )
    public = {
        "milestone": "M6.8.1",
        "scope": "offline_historical_syntax_replay",
        "retained_private_input_sha256": input_sha256,
        "parser_source_sha256": hashlib.sha256(Path(syntax.__file__).read_bytes()).hexdigest(),
        "proposals": {"total": len(rows), **dict(sorted(proposal_counts.items()))},
        "references": {
            **dict(sorted(reference_counts.items())),
            "accepted_kind_counts": dict(sorted(kind_counts.items())),
        },
        "public_source_grounded": None,
        "object_resolved": None,
        "correct_target_correspondence": None,
        "treatment_effect_estimate": None,
        "provider_calls_made": 0,
        "source_outcomes_changed": False,
    }
    return public, private_rows


def _create_once(path: Path, data: bytes, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if private and stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("private output directory must be mode 0700")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600 if private else 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    args = parser.parse_args()
    if stat.S_IMODE(args.private_input.stat().st_mode) != 0o600:
        raise ValueError("private input must be mode 0600")
    raw = args.private_input.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != args.expected_sha256:
        raise ValueError("private input checksum mismatch")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("malformed private audit envelope")
    public, private = replay(data.get("experiment_interface"), actual_sha256)
    if args.private_output.exists() or args.public_output.exists():
        raise FileExistsError("replay output already exists")
    _create_once(args.private_output, _canonical(private), private=True)
    _create_once(args.public_output, _canonical(public), private=False)
    print(json.dumps({"public_output_sha256": hashlib.sha256(_canonical(public)).hexdigest()}))


if __name__ == "__main__":
    main()
