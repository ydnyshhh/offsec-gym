"""Verify A1 report integrity, source pins, gate completeness, and cleanup receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path

from offsecgym.benchmark.a1_admission import SEEDS, VARIANTS
from offsecgym.benchmark.a1_reference_record import A1ReferenceRecord
from offsecgym.benchmark.a1_runtime import (
    private_write,
    qualification_source_pins,
    require_private_root,
)
from offsecgym.benchmark.manifests import make_task_manifest, source_pins
from offsecgym.benchmark.q2_runtime_qualification import Q2QualificationRecord

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/benchmark"


def execute(private_root: Path) -> dict[str, object]:
    admission_path = DOCS / "t11f-a1-admission.json"
    admission = json.loads(admission_path.read_text())
    digest = admission.pop("normalized_sha256")
    normalized = json.dumps(admission, sort_keys=True, separators=(",", ":")).encode()
    rebuilt = hashlib.sha256(normalized).hexdigest()
    admission["normalized_sha256"] = digest
    pins = qualification_source_pins(ROOT)
    checks: dict[str, bool] = {
        "admission_digest": digest == rebuilt,
        "source_pins": admission.get("source_sha256") == source_pins()
        and admission.get("qualification_source_sha256") == pins,
        "provider_calls_zero": admission.get("provider_calls") == 0,
        "historical_results_unmodified": admission.get("historical_results_modified") is False,
        "six_cells_present": len(admission.get("cells", [])) == 6,
    }
    cleanup = True
    for seed in SEEDS:
        for variant in VARIANTS:
            suffix = f"{seed}-{variant}"
            manifest = make_task_manifest(
                "A1", task_mode="scoped_audit", variant=variant, seed=seed
            )
            q2 = Q2QualificationRecord.model_validate_json(
                (DOCS / f"t11f-a1-q2-{suffix}.json").read_bytes()
            )
            reference = A1ReferenceRecord.model_validate_json(
                (DOCS / f"t11f-a1-reference-{suffix}.json").read_bytes()
            )
            checks[f"{suffix}_task_binding"] = (
                q2.task_id == reference.task_id == manifest.task.task_id
                and q2.seed == reference.seed == seed
                and q2.variant == reference.variant == variant
            )
            checks[f"{suffix}_source_binding"] = (
                q2.source_sha256 == reference.source_sha256 == source_pins()
                and reference.qualification_source_sha256 == pins
            )
            checks[f"{suffix}_gate_evidence"] = (
                q2.q2_status == "pass"
                and reference.q3_status == "pass"
                and (
                    reference.q4_status == "pass"
                    if variant == "vulnerable"
                    else reference.patched_control_status == "pass"
                )
            )
            cleanup &= (
                q2.checks["teardown_integrity"].status == "pass"
                and reference.docker_cleanup_verified
            )
    checks["owned_docker_cleanup_receipts"] = cleanup
    private_base = ROOT / ".offsecgym/benchmark"
    private_files = tuple(private_base.rglob("*-private.json")) + tuple(
        path
        for suffix in (f"{seed}-{variant}" for seed in SEEDS for variant in VARIANTS)
        for name in ("evidence.json", "record.json")
        for path in private_base.glob(f"t11f-q2-{suffix}/*/{name}")
    )
    checks["private_artifacts_restricted"] = bool(private_files) and all(
        path.is_file()
        and stat.S_IMODE(path.stat().st_mode) == 0o600
        and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        for path in private_files
    )
    checks["canonical_report_rebuild"] = (
        json.dumps(admission, sort_keys=True, indent=2) + "\n" == admission_path.read_text()
    )
    result = {
        "milestone": "T1.1c-f",
        "postcheck_version": "t11-postcheck-v1",
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
        "admission_digest": digest,
        "postcheck_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "provider_calls": 0,
    }
    private_write(
        private_root / "postcheck-private.json",
        {
            "checks": checks,
            "private_paths_sha256": hashlib.sha256(
                "\n".join(sorted(str(path.relative_to(ROOT)) for path in private_files)).encode()
            ).hexdigest(),
        },
    )
    (DOCS / "t11-postcheck.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output-private", type=Path)
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"seeds": SEEDS, "variants": VARIANTS, "provider_calls": 0}))
        return
    if args.output_private is None:
        parser.error("--execute requires --output-private")
    result = execute(require_private_root(args.output_private, ROOT))
    print(json.dumps({"status": result["status"], "checks": result["checks"]}, sort_keys=True))


if __name__ == "__main__":
    main()
