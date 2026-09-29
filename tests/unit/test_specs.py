from uuid import UUID

import pytest
from pydantic import ValidationError

from offsecgym.schemas.common import derived_id, new_id
from offsecgym.schemas.specs import Budget, ExperimentSpec, RangeSpec


def test_range_spec_rejects_unknown_fields_and_negative_counts() -> None:
    valid = {"schema_version": "1", "family": "hello", "scenario": "health", "seed": 42}
    assert RangeSpec.model_validate(valid).seed == 42
    with pytest.raises(ValidationError):
        RangeSpec.model_validate({**valid, "unexpected": True})
    with pytest.raises(ValidationError):
        RangeSpec.model_validate({**valid, "identities": {"member": -1}})


def test_experiment_requires_model_for_non_scripted_agent() -> None:
    raw = {
        "name": "test",
        "seed": 1,
        "range": {"family": "hello", "scenario": "health", "seed": 42},
        "budget": {"max_actions": 10},
        "orchestrator": "monolithic",
    }
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(raw)
    raw["model"] = {"provider": "test", "name": "mock"}
    assert ExperimentSpec.model_validate(raw).model is not None


def test_budget_requires_a_limit_and_bounded_concurrency() -> None:
    with pytest.raises(ValidationError):
        Budget()
    with pytest.raises(ValidationError):
        Budget(max_workers=2, max_concurrency=3)
    assert Budget(max_workers=3, max_concurrency=2).max_concurrency == 2


def test_derived_ids_are_stable() -> None:
    namespace = UUID("12345678-1234-5678-1234-567812345678")
    assert derived_id(namespace, "range", "document") == derived_id(namespace, "range", "document")
    assert derived_id(namespace, "range", "document") != derived_id(namespace, "range", "invoice")
    assert new_id() != new_id()
