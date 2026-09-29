import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym.schemas.events import RunStarted
from offsecgym.storage.event_store import PostgresEventStore


@pytest.mark.postgres
async def test_postgres_event_store_orders_and_deduplicates() -> None:
    url = os.getenv("OFFSECGYM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("OFFSECGYM_TEST_DATABASE_URL is not set")
    engine = create_async_engine(url)
    try:
        store = PostgresEventStore(engine)
        run_id = uuid4()
        first = RunStarted(run_id=run_id, actor="test", experiment_hash="sha256:one")
        second = RunStarted(run_id=run_id, actor="test", experiment_hash="sha256:two")
        written_first = await store.append(first)
        assert written_first.sequence_number == 1
        assert (await store.append(first)) == written_first
        with pytest.raises(ValueError, match="different content"):
            await store.append(first.model_copy(update={"experiment_hash": "sha256:changed"}))
        assert (await store.append(second)).sequence_number == 2
        assert await store.read_run(run_id) == [
            written_first,
            second.model_copy(update={"sequence_number": 2}),
        ]
    finally:
        await engine.dispose()
