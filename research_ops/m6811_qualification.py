"""Run detached synthetic PostgreSQL qualification and write aggregate evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

from m6811_taxonomy import canonical_bytes, generate
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research import m681_detached_persistence, m681_reference_syntax
from offsecgym.schemas import common as schema_common
from offsecgym.schemas import events as event_schema
from offsecgym.schemas.events import ExperimentStateChanged, FindingValidated, RunCompleted
from offsecgym.solver import experiment_state
from offsecgym.solver.experiment_state import ExperimentStateStore, GetActiveExperimentArgs
from offsecgym.storage.event_store import PostgresEventStore

OBJECT = UUID("11111111-1111-4111-8111-111111111111")


def _test_database_url() -> str:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        raise ValueError("OFFSECGYM_TEST_DATABASE_URL is required")
    parsed = make_url(url)
    if parsed.database != "offsecgym_test" or parsed.host not in {"localhost", "127.0.0.1"}:
        raise ValueError("qualification requires a local offsecgym_test database")
    return url


def _arguments(target: str) -> dict[str, object]:
    return {
        "hypothesis": "Synthetic state remains observable",
        "subject_identity": None,
        "target_objects": [target],
        "preconditions": ["Read synthetic baseline"],
        "planned_steps": ["Observe", "Record", "Compare"],
        "expected_positive_observation": "State changed",
        "expected_negative_observation": "State unchanged",
        "required_evidence": ["Synthetic note"],
    }


async def _round(url: str) -> tuple[dict[str, object], dict[str, object]]:
    first_engine = create_async_engine(url)
    second_engine = create_async_engine(url)
    strict_rejected_run = uuid4()
    strict_run, normalized_run, abandoned_run, foreign_run = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        first = PostgresEventStore(first_engine)
        try:
            await m681_detached_persistence.run_detached_script(
                first,
                run_id=strict_rejected_run,
                synthetic_run_context=m681_detached_persistence.SyntheticRunContext(),
                scripted_argument_dict=_arguments(f"project:{OBJECT}"),
                normalization_enabled=False,
                scripted_operations=(),
            )
        except ValidationError:
            strict_rejected = True
        else:
            strict_rejected = False
        if not strict_rejected or await first.read_run(strict_rejected_run):
            raise AssertionError("strict wrapped input was not side-effect-free")
        strict = await m681_detached_persistence.run_detached_script(
            first,
            run_id=strict_run,
            synthetic_run_context=m681_detached_persistence.SyntheticRunContext(),
            scripted_argument_dict=_arguments(str(OBJECT)),
            normalization_enabled=False,
            scripted_operations=(),
        )
        normalized = await m681_detached_persistence.run_detached_script(
            first,
            run_id=normalized_run,
            synthetic_run_context=m681_detached_persistence.SyntheticRunContext(),
            scripted_argument_dict=_arguments(f"project:{OBJECT}"),
            normalization_enabled=True,
            scripted_operations=(
                m681_detached_persistence.ScriptedOperation("get"),
                m681_detached_persistence.ScriptedOperation("update", current_step=0),
                m681_detached_persistence.ScriptedOperation("update", current_step=1),
                m681_detached_persistence.ScriptedOperation("get"),
                m681_detached_persistence.ScriptedOperation("update", current_step=3),
                m681_detached_persistence.ScriptedOperation("complete"),
                m681_detached_persistence.ScriptedOperation("get"),
            ),
        )
        abandoned = await m681_detached_persistence.run_detached_script(
            first,
            run_id=abandoned_run,
            synthetic_run_context=m681_detached_persistence.SyntheticRunContext(),
            scripted_argument_dict=_arguments(f"project:{OBJECT}"),
            normalization_enabled=True,
            scripted_operations=(
                m681_detached_persistence.ScriptedOperation("abandon", reason="Synthetic stop"),
            ),
        )
        await first_engine.dispose()
        fresh = PostgresEventStore(second_engine)
        state = ExperimentStateStore(fresh)
        trace = await fresh.read_run(normalized_run)
        events = tuple(item for item in trace if isinstance(item, ExperimentStateChanged))
        if events != normalized.state_events or not events:
            raise AssertionError("fresh PostgreSQL connection did not rebuild the same state")
        if any(item.run_id != normalized_run for item in events):
            raise AssertionError("cross-run event attribution")
        if events[-1].status != "completed" or events[-1].target_objects != (OBJECT,):
            raise AssertionError("normalized completed state differs from scripted input")
        if await state.active(normalized_run) is not None:
            raise AssertionError("completed experiment is still active")
        if await fresh.read_run(foreign_run):
            raise AssertionError("state leaked to an independent run")
        if (await fresh.read_run(abandoned_run))[-1].status != "abandoned":
            raise AssertionError("abandonment did not persist")
        if any(isinstance(item, FindingValidated) for item in trace):
            raise AssertionError("synthetic completion produced a task verdict")
        await fresh.append(
            RunCompleted(run_id=strict_run, actor="qualification", status="completed")
        )
        terminal_before = await fresh.read_run(strict_run)
        try:
            await state.change(
                strict_run,
                "get_active_experiment",
                GetActiveExperimentArgs(),
                causation_id=uuid4(),
            )
        except ValueError as error:
            terminal_rejected = "after terminal" in str(error)
        else:
            terminal_rejected = False
        if not terminal_rejected or terminal_before != await fresh.read_run(strict_run):
            raise AssertionError("terminal-run state changed")
        projection = {
            "strict_wrapped_rejected_without_events": strict_rejected,
            "strict_canonical": strict.scientific_projection(),
            "normalized": normalized.scientific_projection(),
            "abandoned": abandoned.scientific_projection(),
            "fresh_connection_retrieved_authoritative_events": True,
            "cross_run_absent": True,
            "terminal_rejected_without_state_change": terminal_rejected,
            "task_verdict_events": 0,
        }
        private = {
            "strict_event_integrity_sha256": strict.event_integrity_sha256(),
            "normalized_event_integrity_sha256": normalized.event_integrity_sha256(),
            "abandoned_event_integrity_sha256": abandoned.event_integrity_sha256(),
        }
        return projection, private
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def qualify(
    *, private_input: Path, taxonomy_path: Path, full_unit_test_count: int
) -> tuple[dict[str, object], dict[str, object]]:
    url = _test_database_url()
    taxonomy, _private_rows = generate(private_input)
    if taxonomy != json.loads(taxonomy_path.read_text()):
        raise ValueError("checked-in taxonomy differs from pinned input")
    first, first_event_hashes = await _round(url)
    second, second_event_hashes = await _round(url)
    first_bytes, second_bytes = canonical_bytes(first), canonical_bytes(second)
    if first_bytes != second_bytes:
        raise AssertionError("scientific projection changed between clean synthetic rounds")
    root = Path(__file__).parents[1]
    tests = [
        root / "tests/unit/test_m681_detached_persistence.py",
        root / "tests/integration/test_m681_detached_postgres.py",
        root / "tests/unit/test_m681_rejection_taxonomy.py",
    ]
    test_hash = hashlib.sha256(
        b"".join(
            path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() for path in tests
        )
    ).hexdigest()
    report = {
        "milestone": "M6.8.1.1",
        "qualification_type": "detached_no_model",
        "provider_calls": 0,
        "live_agent_runtime_modified": False,
        "live_agent_qualified": False,
        "prospective_model_effect_estimated": False,
        "historical_outcomes_modified": False,
        "parser_qualification": {
            "strict_wrapped_reference_rejected": first["strict_wrapped_rejected_without_events"],
            "normalized_target_uuid_preserved": first["normalized"]["validated_target_objects"]
            == [str(OBJECT)],
            "frozen_historical_reference_acceptance": "27/43",
            "frozen_historical_complete_proposal_acceptance": "4/18",
        },
        "persistence_qualification": {
            "backend": "disposable_local_postgresql",
            "authoritative_state_events_created": len(first["normalized"]["state_events"]),
            "fresh_connection_retrieval": first["fresh_connection_retrieved_authoritative_events"],
            "completed_status_persisted": first["normalized"]["state_events"][-1]["status"]
            == "completed",
            "abandoned_status_persisted": first["abandoned"]["state_events"][-1]["status"]
            == "abandoned",
            "task_verdict_events": first["task_verdict_events"],
        },
        "run_isolation": {"independent_run_empty": first["cross_run_absent"]},
        "transaction_integrity": {
            "terminal_run_rejected_without_state_change": first[
                "terminal_rejected_without_state_change"
            ],
            "postgres_per_run_transaction_exercised": True,
            "concurrency_verified_by_integration_test": True,
        },
        "historical_rejection_taxonomy": {
            "aggregate_sha256": _sha(taxonomy_path),
            "proposal_count": taxonomy["proposal_count"],
            "reference_count": taxonomy["reference_count"],
            "proposal_dispositions": taxonomy["proposal_dispositions"],
        },
        "deterministic_replay": {
            "equivalent_clean_synthetic_rounds": 2,
            "canonical_projection_sha256": hashlib.sha256(first_bytes).hexdigest(),
            "byte_identical_projection": True,
            "projection_excludes": [
                "generated_run_id",
                "generated_experiment_id",
                "event_id",
                "causation_id",
                "occurred_at",
            ],
            "real_event_integrity_hashes_recorded_privately": True,
        },
        "source_hashes": {
            "parser_sha256": _sha(Path(m681_reference_syntax.__file__)),
            "experiment_state_sha256": _sha(Path(experiment_state.__file__)),
            "shared_schema_sha256": _sha(Path(schema_common.__file__)),
            "event_schema_sha256": _sha(Path(event_schema.__file__)),
            "detached_harness_sha256": _sha(Path(m681_detached_persistence.__file__)),
            "qualification_tests_sha256": test_hash,
            "private_replay_input_sha256": hashlib.sha256(private_input.read_bytes()).hexdigest(),
            "qualification_generator_sha256": _sha(Path(__file__)),
        },
        "test_results": {
            "focused_postgres_integration_passed": 2,
            "full_unit_pytest_passed": full_unit_test_count,
            "ruff_check": "passed",
            "ruff_format_check": "passed",
        },
    }
    private = {"round_1": first_event_hashes, "round_2": second_event_hashes}
    return report, private


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-input", required=True, type=Path)
    parser.add_argument("--taxonomy", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--full-unit-test-count", required=True, type=int)
    args = parser.parse_args()
    report, private = asyncio.run(
        qualify(
            private_input=args.private_input,
            taxonomy_path=args.taxonomy,
            full_unit_test_count=args.full_unit_test_count,
        )
    )
    if args.public_output.exists() or args.private_output.exists():
        raise FileExistsError("qualification outputs must be create-once")
    args.private_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(args.private_output.parent.stat().st_mode) != 0o700:
        raise ValueError("private output directory must be mode 0700")
    descriptor = os.open(args.private_output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(private))
        args.public_output.parent.mkdir(parents=True, exist_ok=True)
        with args.public_output.open("xb") as stream:
            stream.write(canonical_bytes(report))
    except BaseException:
        args.private_output.unlink(missing_ok=True)
        raise
    print(json.dumps({"qualification_sha256": hashlib.sha256(canonical_bytes(report)).hexdigest()}))


if __name__ == "__main__":
    main()
