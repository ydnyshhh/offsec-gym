"""Repeat fabricated, no-model persistence checks against disposable PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from m683_contract_replay import canonical, create_once, sha
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.research.m681_detached_persistence import (
    ScriptedOperation,
    SyntheticRunContext,
    run_detached_script,
)
from offsecgym.research.m683_reference_contracts import compare_contracts
from offsecgym.schemas.events import ExperimentStateChanged, FindingValidated, RunCompleted
from offsecgym.storage.event_store import PostgresEventStore

OBJECT = "11111111-1111-4111-8111-111111111111"


def _test_url() -> str:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        raise ValueError("OFFSECGYM_TEST_DATABASE_URL is required")
    parsed = make_url(url)
    if parsed.database != "offsecgym_test" or parsed.host not in {"localhost", "127.0.0.1"}:
        raise ValueError("qualification requires disposable local offsecgym_test")
    return url


def _arguments(target: str) -> dict[str, object]:
    return {
        "hypothesis": "Fabricated state is observable",
        "subject_identity": None,
        "target_objects": [target],
        "preconditions": [],
        "planned_steps": ["Observe fabricated state", "Record fabricated state"],
        "expected_positive_observation": "Recorded",
        "expected_negative_observation": "Absent",
        "required_evidence": [],
    }


async def _round(url: str) -> dict[str, object]:
    writer_engine = create_async_engine(url)
    reader_engine = create_async_engine(url)
    cases = (("C0", OBJECT), ("C1", f"change_request:{OBJECT}"), ("C2", f"change:{OBJECT}"))
    run_ids = [uuid4() for _ in cases]
    invalid_run = uuid4()
    try:
        writer = PostgresEventStore(writer_engine)
        for run_id, (contract, target) in zip(run_ids, cases, strict=True):
            result = compare_contracts(_arguments(target), contracts=(contract,))[contract]
            if not result.post_normalization_schema_accepted or result.validated_arguments is None:
                raise AssertionError("fabricated contract input unexpectedly rejected")
            recorded = await run_detached_script(
                writer,
                run_id=run_id,
                synthetic_run_context=SyntheticRunContext("m683-fabricated"),
                scripted_argument_dict=result.validated_arguments.model_dump(mode="json"),
                normalization_enabled=False,
                scripted_operations=(
                    ScriptedOperation("get"),
                    ScriptedOperation("update", current_step=1),
                    ScriptedOperation("complete"),
                ),
            )
            if recorded.validated_arguments != result.validated_arguments:
                raise AssertionError("detached persistence changed validated input")
        invalid = compare_contracts(_arguments(f"unknown:{OBJECT}"))["C2"]
        if invalid.validated_arguments is not None or await writer.read_run(invalid_run):
            raise AssertionError("rejected input emitted a state event")
        await writer_engine.dispose()
        reader = PostgresEventStore(reader_engine)
        projections: dict[str, object] = {}
        for run_id, (contract, _) in zip(run_ids, cases, strict=True):
            trace = await reader.read_run(run_id)
            states = [event for event in trace if isinstance(event, ExperimentStateChanged)]
            if len(states) != 3 or [event.sequence_number for event in states] != [2, 3, 4]:
                raise AssertionError("authoritative state event sequence differs")
            if any(event.run_id != run_id for event in states):
                raise AssertionError("cross-run attribution differs")
            if any(event.target_objects != (UUID(OBJECT),) for event in states):
                raise AssertionError("normalized target UUID changed")
            if any(
                event.planned_steps != ("Observe fabricated state", "Record fabricated state")
                or event.hypothesis != "Fabricated state is observable"
                for event in states
            ):
                raise AssertionError("model-independent experiment content changed")
            verdicts = sum(isinstance(event, (FindingValidated, RunCompleted)) for event in trace)
            if verdicts or states[-1].status != "completed":
                raise AssertionError("state qualification created a verdict or failed to complete")
            projections[contract] = {
                "authoritative_state_events": len(states),
                "fresh_connection_readback": True,
                "target_uuid_identity_preserved": True,
                "planned_steps_and_hypothesis_preserved": True,
                "completion_persisted": True,
                "security_or_task_verdict_events": verdicts,
            }
        if await reader.read_run(invalid_run) or await reader.read_run(uuid4()):
            raise AssertionError("invalid or unrelated run acquired state")
        return {
            "contracts": projections,
            "invalid_input_side_effect_free": True,
            "cross_run_isolation": True,
            "database": "disposable_local_offsecgym_test",
        }
    finally:
        await writer_engine.dispose()
        await reader_engine.dispose()


async def qualify(code_root: Path) -> dict[str, object]:
    url = _test_url()
    first, second = await _round(url), await _round(url)
    if canonical(first) != canonical(second):
        raise ValueError("fabricated qualification projections are not byte-identical")
    sources = (
        "src/offsecgym/research/m683_reference_contracts.py",
        "src/offsecgym/research/m681_detached_persistence.py",
        "src/offsecgym/solver/experiment_state.py",
        "research_ops/m683_qualify.py",
        "tests/integration/test_m683_detached_postgres.py",
    )
    return {
        "milestone": "M6.8.3",
        "scope": "fabricated_detached_no_model_persistence",
        "result": first,
        "deterministic_reconstructions": 2,
        "canonical_projection_sha256": hashlib.sha256(canonical(first)).hexdigest(),
        "source_hashes": {name: sha(code_root / name) for name in sources},
        "provider_calls": 0,
        "live_autonomous_agent_treatment_qualified": False,
        "historical_outcomes_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args()
    code_root = Path(__file__).resolve().parents[1]
    public = canonical(asyncio.run(qualify(code_root)))
    create_once(args.public_output, public, private=False)
    print(json.dumps({"public_sha256": hashlib.sha256(public).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
