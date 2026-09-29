from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from offsecgym.schemas.events import ActionRequested, RunStarted, parse_event


def test_event_serialization_round_trip() -> None:
    event = ActionRequested(
        run_id=uuid4(),
        actor="gateway",
        action_id=uuid4(),
        action_type="http_request",
        destination="hello",
        range_instance_id=uuid4(),
        range_generation=0,
        request_artifact_id=uuid4(),
    )
    parsed = parse_event(event.model_dump(mode="json"))
    assert isinstance(parsed, ActionRequested)
    assert parsed == event
    assert event.schema_version == "2"
    assert parsed.sequence_number == 0


def test_event_rejects_unknown_type_and_naive_timestamp() -> None:
    event = RunStarted(run_id=uuid4(), actor="controller", experiment_hash="sha256:abc")
    with pytest.raises(ValidationError):
        parse_event({**event.model_dump(mode="json"), "type": "unknown"})
    with pytest.raises(ValidationError):
        RunStarted(
            run_id=uuid4(),
            actor="controller",
            experiment_hash="sha256:abc",
            occurred_at=datetime(2026, 1, 1),
        )
