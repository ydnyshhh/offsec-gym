"""Create one public aggregate and one private row table from pinned audit input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from offsecgym.research import m681_reference_syntax, m681_rejection_taxonomy

EXPECTED_INPUT_SHA256 = "34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def generate(private_input: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    if stat.S_IMODE(private_input.stat().st_mode) != 0o600:
        raise ValueError("private input must be mode 0600")
    raw = private_input.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != EXPECTED_INPUT_SHA256:
        raise ValueError("retained private input SHA-256 mismatch")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("malformed audit envelope")
    public, private = m681_rejection_taxonomy.classify_proposals(
        envelope.get("experiment_interface")
    )
    public["retained_private_input_sha256"] = actual
    public["parser_source_sha256"] = hashlib.sha256(
        Path(m681_reference_syntax.__file__).read_bytes()
    ).hexdigest()
    public["taxonomy_source_sha256"] = hashlib.sha256(
        Path(m681_rejection_taxonomy.__file__).read_bytes()
    ).hexdigest()
    public["taxonomy_generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return public, private


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-input", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args()
    public, private = generate(args.private_input)
    if args.private_output.exists() or args.public_output.exists():
        raise FileExistsError("output must be create-once")
    args.private_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(args.private_output.parent.stat().st_mode) != 0o700:
        raise ValueError("private output directory must be mode 0700")
    descriptor = os.open(args.private_output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(private))
        args.public_output.parent.mkdir(parents=True, exist_ok=True)
        with args.public_output.open("xb") as stream:
            stream.write(canonical_bytes(public))
    except BaseException:
        args.private_output.unlink(missing_ok=True)
        raise
    print(json.dumps({"public_sha256": hashlib.sha256(canonical_bytes(public)).hexdigest()}))


if __name__ == "__main__":
    main()
