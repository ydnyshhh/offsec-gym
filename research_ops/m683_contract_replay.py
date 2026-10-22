"""Generate a source-pinned, identifier-free M6.8.3 historical comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

import pydantic

from offsecgym.research.m683_contract_replay import (
    FROZEN_PARSER_SHA256,
    GROUNDING_AGGREGATE_SHA256,
    GROUNDING_DETAIL_SHA256,
    HISTORICAL_INPUT_SHA256,
    replay,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def source_hashes(code_root: Path) -> dict[str, str]:
    paths = (
        "src/offsecgym/research/m681_reference_syntax.py",
        "src/offsecgym/research/m681_detached_persistence.py",
        "src/offsecgym/solver/experiment_state.py",
        "src/offsecgym/research/m682_grounding_audit.py",
        "src/offsecgym/research/m682_public_evidence_index.py",
        "src/offsecgym/research/m682_object_resolution.py",
        "src/offsecgym/research/m683_reference_contracts.py",
        "src/offsecgym/research/m683_contract_replay.py",
        "research_ops/m683_contract_replay.py",
        "research_ops/m683_qualify.py",
        "tests/unit/test_m683_reference_contracts.py",
        "tests/unit/test_m683_contract_replay.py",
        "tests/integration/test_m683_detached_postgres.py",
        "docs/diagnostics/m683-no-model-qualification.json",
    )
    return {name: sha(code_root / name) for name in paths}


def generate(
    *, code_root: Path, historical_path: Path, grounding_path: Path
) -> tuple[bytes, bytes]:
    pinned = {
        historical_path: HISTORICAL_INPUT_SHA256,
        grounding_path: GROUNDING_DETAIL_SHA256,
        code_root
        / "docs/diagnostics/m682-reference-grounding-audit.json": GROUNDING_AGGREGATE_SHA256,
        code_root / "src/offsecgym/research/m681_reference_syntax.py": FROZEN_PARSER_SHA256,
    }
    for path, expected in pinned.items():
        if sha(path) != expected:
            raise ValueError(f"source SHA-256 mismatch: {path.name}")
    for path in (historical_path, grounding_path):
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("historical private input must be mode 0600")
        if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
            raise ValueError("historical private parent must be mode 0700")
    historical_envelope = json.loads(historical_path.read_bytes())
    grounding = json.loads(grounding_path.read_bytes())
    rows = historical_envelope.get("experiment_interface")
    public, private = replay(rows, grounding)
    qualification_path = code_root / "docs/diagnostics/m683-no-model-qualification.json"
    qualification = json.loads(qualification_path.read_bytes())
    contract_source = "src/offsecgym/research/m683_reference_contracts.py"
    if (
        qualification.get("source_hashes", {}).get(contract_source)
        != sha(code_root / contract_source)
        or qualification.get("provider_calls") != 0
        or qualification.get("result", {}).get("cross_run_isolation") is not True
        or set(qualification.get("result", {}).get("contracts", {})) != {"C0", "C1", "C2"}
    ):
        raise ValueError("detached PostgreSQL qualification pin differs")
    public["synthetic_persistence"] = {
        "detached_disposable_postgresql_qualified": True,
        "qualification_sha256": sha(qualification_path),
        "live_controller_qualified": False,
    }
    public["pydantic_version"] = pydantic.__version__
    public["source_hashes"] = {
        **source_hashes(code_root),
        "historical_proposal_input": HISTORICAL_INPUT_SHA256,
        "M6.8.2_grounding_detail": GROUNDING_DETAIL_SHA256,
        "M6.8.2_grounding_aggregate": GROUNDING_AGGREGATE_SHA256,
    }
    public_bytes, private_bytes = canonical(public), canonical(private)
    if re.search(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", public_bytes):
        raise ValueError("private UUID escaped into public aggregate")
    return public_bytes, private_bytes


def create_once(path: Path, data: bytes, *, private: bool) -> None:
    path.parent.mkdir(mode=0o700 if private else 0o755, parents=True, exist_ok=True)
    if private and stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("private output parent must be mode 0700")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600 if private else 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-input", required=True, type=Path)
    parser.add_argument("--grounding-input", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args()
    code_root = Path(__file__).resolve().parents[1]
    first = generate(
        code_root=code_root,
        historical_path=args.historical_input,
        grounding_path=args.grounding_input,
    )
    second = generate(
        code_root=code_root,
        historical_path=args.historical_input,
        grounding_path=args.grounding_input,
    )
    if first != second:
        raise ValueError("repeat reconstructions are not byte-identical")
    if args.private_output.exists() or args.public_output.exists():
        raise FileExistsError("outputs must be create-once")
    create_once(args.private_output, first[1], private=True)
    try:
        create_once(args.public_output, first[0], private=False)
    except BaseException:
        args.private_output.unlink(missing_ok=True)
        raise
    print(
        json.dumps(
            {
                "public_sha256": hashlib.sha256(first[0]).hexdigest(),
                "private_sha256": hashlib.sha256(first[1]).hexdigest(),
                "deterministic_reconstructions": 2,
                "proposals": 18,
                "references": 43,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
