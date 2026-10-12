"""Fail-closed checks around the approved, excluded M6.6.1 collector."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from offsecgym.providers.base import ModelTurn, ProviderFailure, ProviderRequestError
from offsecgym.providers.openrouter import OpenRouterResponsesProvider

sys.path.insert(0, str(Path(__file__).parents[2] / "research_ops"))
import m661_feasibility_collect as collector  # noqa: E402
import m661_feasibility_postcheck as postcheck  # noqa: E402

ROOT = Path(__file__).parents[2]
MANIFEST = json.loads((ROOT / "experiments/manifests/m661-feasibility-v1.json").read_bytes())


def test_frozen_spec_replays_all_96_assignments() -> None:
    for cell in MANIFEST["cells"]:
        spec = collector._spec(ROOT, MANIFEST, cell)
        assert spec.seed == cell["seed"]
        assert spec.range.patched == (cell["variant"] == "patched")


def test_one_pass_journal_requires_frozen_order_and_no_repeated_run() -> None:
    first = MANIFEST["cells"][0]
    second = MANIFEST["cells"][1]
    header = {
        "type": "batch_started",
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "expected_model_revision": collector.REVISION,
        "expected_upstream_provider": collector.UPSTREAM,
        "input_usd_per_million": 3.0,
        "output_usd_per_million": 15.0,
        "max_estimated_usd": 172.8,
    }
    started = {
        "type": "cell_started",
        "cell_id": first["cell_id"],
        "order": 1,
        "run_id": "00000000-0000-0000-0000-000000000001",
    }
    completed = {
        "type": "cell_completed",
        "cell_id": first["cell_id"],
        "order": 1,
        "run_id": started["run_id"],
        "experiment_sha256": first["experiment_sha256"],
    }

    def encoded(*items: dict) -> bytes:
        return b"".join((json.dumps(item) + "\n").encode() for item in items)

    assert postcheck._journal_records(MANIFEST, encoded(header, started, completed)) == [completed]
    with pytest.raises(ValueError, match="interrupted"):
        postcheck._journal_records(MANIFEST, encoded(header, started))
    changed = {**completed, "cell_id": second["cell_id"]}
    with pytest.raises(ValueError, match="frozen order"):
        postcheck._journal_records(MANIFEST, encoded(header, started, changed))


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (["provider_unavailable"], "continue"),
        (["provider_unavailable", "provider_timeout"], "pause"),
        (["provider_rate_limited", "provider_rate_limited"], "continue"),
        (["provider_rate_limited"] * 3, "pause"),
        (["provider_auth_failed"], "stop"),
    ],
)
def test_provider_health_preserves_isolated_failure(reasons: list[str], expected: str) -> None:
    records = [{"status": "provider_failed", "failure_reason": reason} for reason in reasons]
    assert collector._health_decision(records) == expected


def test_create_only_evidence_cannot_be_replaced(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    first = collector._write_json(path, {"first": 1})
    with pytest.raises(FileExistsError):
        collector._write_json(path, {"second": 2})
    assert collector._sha256(path.read_bytes()) == first


@pytest.mark.asyncio
async def test_endpoint_drift_stops_further_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def changed_endpoint(
        _provider: OpenRouterResponsesProvider, _payload: dict[str, object]
    ) -> ModelTurn:
        nonlocal calls
        calls += 1
        return ModelTurn.model_validate(
            {
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "raw_response": {
                    "openrouter_metadata": {
                        "endpoints": {
                            "available": [
                                {
                                    "selected": True,
                                    "model": "another-revision",
                                    "provider": "another-provider",
                                }
                            ]
                        }
                    }
                },
            }
        )

    monkeypatch.setattr(OpenRouterResponsesProvider, "complete", changed_endpoint)
    guard = collector.EndpointGuard()
    provider = guard.provider("test-key")
    payload = {
        "model": collector.MODEL,
        "provider": {"order": ["moonshotai"], "allow_fallbacks": False},
        "reasoning": {"effort": "high"},
        "store": False,
    }
    with pytest.raises(ProviderFailure, match="feasibility_endpoint_drift"):
        await provider.complete(payload)
    with pytest.raises(ProviderFailure, match="feasibility_provider_stopped"):
        await provider.complete(payload)
    assert calls == 1


@pytest.mark.asyncio
async def test_request_policy_drift_stops_before_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> ModelTurn:
        raise AssertionError("model request must not be sent")

    monkeypatch.setattr(OpenRouterResponsesProvider, "complete", forbidden)
    guard = collector.EndpointGuard()
    with pytest.raises(ProviderRequestError, match="feasibility_request_policy_drift"):
        await guard.provider("test-key").complete({"model": "different"})
    assert guard.stopped
