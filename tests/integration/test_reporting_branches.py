"""PostgreSQL isolation for two reporting arms over one source prefix."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.events import (
    ActionCompleted,
    ModelCallStarted,
    ModelToolRejected,
    ProbeCheckpointSaved,
    ReportingBranchStarted,
    RunCompleted,
    RunStarted,
)
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.storage.reporting_branch import ReportingBranchStore, prefix_sha256


@pytest.mark.postgres
async def test_reporting_branches_are_durable_and_do_not_cross_contaminate() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        source = PostgresEventStore(engine)
        run_id, call_id, checkpoint_id = (uuid4() for _ in range(3))
        await source.append(RunStarted(run_id=run_id, actor="test", experiment_hash="a" * 64))
        await source.append(
            ModelCallStarted(
                run_id=run_id,
                actor="test",
                call_id=call_id,
                provider="fake",
                model="fake",
                input_sha256="b" * 64,
                request_artifact_id=uuid4(),
                request_sha256="b" * 64,
            )
        )
        prefix = await source.read_run(run_id)
        await source.append(
            ProbeCheckpointSaved(
                run_id=run_id,
                actor="controller",
                checkpoint_id=checkpoint_id,
                source_sequence=2,
                source_trace_sha256=prefix_sha256(prefix),
                checkpoint_sha256="c" * 64,
                latest_model_call_id=call_id,
                latest_request_sha256="b" * 64,
            )
        )
        frozen_sha = prefix_sha256(await source.read_run(run_id))
        fresh = await ReportingBranchStore.create(
            source, run_id, arm="fresh", checkpoint_id=checkpoint_id
        )
        continued = await ReportingBranchStore.create(
            source, run_id, arm="continuation", checkpoint_id=checkpoint_id
        )
        await fresh.append(
            ReportingBranchStarted(
                run_id=run_id,
                actor="controller",
                branch_id=fresh.branch_id,
                arm="fresh",
                checkpoint_id=checkpoint_id,
                source_trace_sha256=frozen_sha,
                initial_context_sha256="d" * 64,
            )
        )
        await continued.append(
            ReportingBranchStarted(
                run_id=run_id,
                actor="controller",
                branch_id=continued.branch_id,
                arm="continuation",
                checkpoint_id=checkpoint_id,
                source_trace_sha256=frozen_sha,
                initial_context_sha256="e" * 64,
            )
        )
        await fresh.append(
            ModelToolRejected(
                run_id=run_id,
                actor="reporter",
                model_call_id=uuid4(),
                tool_call_id="bad",
                tool_name="submit_finding",
                reason_code="invalid_tool_arguments",
            )
        )
        assert len(await fresh.read_run(run_id)) == 5
        assert len(await continued.read_run(run_id)) == 4
        assert len(await source.read_run(run_id)) == 3
        with pytest.raises(ValueError, match="cannot append gateway"):
            await fresh.append(
                ActionCompleted(
                    run_id=run_id,
                    actor="gateway",
                    action_id=uuid4(),
                    duration_ms=0,
                )
            )
        await source.append(RunCompleted(run_id=run_id, actor="controller", status="completed"))
        assert len(await fresh.read_run(run_id)) == 5
        assert len(await continued.read_run(run_id)) == 4
        reloaded = ReportingBranchStore(source, fresh.branch_id, run_id)
        assert await reloaded.read_run(run_id) == await fresh.read_run(run_id)
    finally:
        await engine.dispose()
