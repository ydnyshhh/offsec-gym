"""No-network checks for the separate M6.6 v2 execution boundary."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research_ops"))
import m66_collect_pilot_v2 as collector  # noqa: E402
from m66_pilot_v2_audit import audit_pair_inventory  # noqa: E402

MANIFEST = ROOT / "experiments/manifests/m66-pilot-v2.json"


def _approval() -> dict:
    return {
        "protocol": "m66-pilot-v2",
        "manifest_sha256": collector.EXPECTED_MANIFEST_SHA256,
        "source_commit": collector.EXPECTED_SOURCE_COMMIT,
        "protocol_commit": collector.EXPECTED_PROTOCOL_COMMIT,
        "approved_at": "2026-10-06T17:00:00Z",
        "cost_ceiling_usd": 15.0,
        "approval_scope": "excluded_feasibility_pilot_only",
        "v1_approval_reused": False,
        "approved_for_paid_calls": True,
    }


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def test_frozen_manifest_and_cell_specs_remain_pinned() -> None:
    raw = MANIFEST.read_bytes()
    assert collector._sha256(raw) == collector.EXPECTED_MANIFEST_SHA256
    manifest = json.loads(raw)
    assert manifest["paid_model_calls_authorized"] is False
    assert manifest["source_commit"] == collector.EXPECTED_SOURCE_COMMIT
    assert manifest["protocol_commit"] == collector.EXPECTED_PROTOCOL_COMMIT
    assert len(manifest["cells"]) == 8
    for cell in manifest["cells"]:
        collector._spec(ROOT, manifest, cell)


def test_approval_guard_rejects_missing_hash_and_v1_reuse(monkeypatch) -> None:
    manifest = json.loads(MANIFEST.read_bytes())
    approval = _approval()
    monkeypatch.setattr(collector, "EXPECTED_APPROVAL_SHA256", "__POST_APPROVAL_SHA256__")
    with pytest.raises(ValueError, match="remains closed"):
        collector._execution_approval(manifest, _encoded(approval))
    monkeypatch.setattr(
        collector, "EXPECTED_APPROVAL_SHA256", collector._sha256(_encoded(approval))
    )
    assert collector._execution_approval(manifest, _encoded(approval)) == approval
    reused = {**approval, "v1_approval_reused": True}
    monkeypatch.setattr(collector, "EXPECTED_APPROVAL_SHA256", collector._sha256(_encoded(reused)))
    with pytest.raises(ValueError, match="does not authorize"):
        collector._execution_approval(manifest, _encoded(reused))
    wrong_manifest = {**approval, "manifest_sha256": "0" * 64}
    monkeypatch.setattr(
        collector, "EXPECTED_APPROVAL_SHA256", collector._sha256(_encoded(wrong_manifest))
    )
    with pytest.raises(ValueError, match="does not authorize"):
        collector._execution_approval(manifest, _encoded(wrong_manifest))


@pytest.mark.asyncio
async def test_request_guard_rejects_fallback_before_provider_call(monkeypatch) -> None:
    async def unexpected_network_call(_self, _request):
        raise AssertionError("provider network call must not happen")

    monkeypatch.setattr(collector.OpenRouterResponsesProvider, "complete", unexpected_network_call)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    provider = guard.provider("unused-test-key")
    with pytest.raises(collector.ProviderRequestError, match="pilot_request_policy_drift"):
        await provider.complete(
            {
                "model": "moonshotai/kimi-k3",
                "provider": {"order": ["moonshotai"], "allow_fallbacks": True},
                "reasoning": {"effort": "high"},
                "store": False,
            }
        )
    assert guard.stopped is True


@pytest.mark.asyncio
async def test_response_guard_rejects_wrong_selected_upstream(monkeypatch) -> None:
    async def wrong_endpoint(_self, _request):
        return SimpleNamespace(
            raw_response={
                "openrouter_metadata": {
                    "endpoints": {
                        "available": [
                            {
                                "selected": True,
                                "model": "moonshotai/kimi-k3-20260715",
                                "provider": "Another Provider",
                            }
                        ]
                    }
                }
            }
        )

    monkeypatch.setattr(collector.OpenRouterResponsesProvider, "complete", wrong_endpoint)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    provider = guard.provider("unused-test-key")
    with pytest.raises(collector.ProviderFailure, match="pilot_endpoint_drift"):
        await provider.complete(
            {
                "model": "moonshotai/kimi-k3",
                "provider": {"order": ["moonshotai"], "allow_fallbacks": False},
                "reasoning": {"effort": "high"},
                "store": False,
            }
        )
    assert guard.stopped is True


@pytest.mark.asyncio
async def test_response_guard_stops_on_unexpected_provider_error(monkeypatch) -> None:
    async def unexpected_error(_self, _request):
        raise RuntimeError("malformed provider response")

    monkeypatch.setattr(collector.OpenRouterResponsesProvider, "complete", unexpected_error)
    guard = collector.EndpointGuard(("moonshotai/kimi-k3-20260715", "Moonshot AI"))
    provider = guard.provider("unused-test-key")
    with pytest.raises(RuntimeError, match="malformed provider response"):
        await provider.complete(
            {
                "model": "moonshotai/kimi-k3",
                "provider": {"order": ["moonshotai"], "allow_fallbacks": False},
                "reasoning": {"effort": "high"},
                "store": False,
            }
        )
    assert guard.stopped is True


def test_live_endpoint_price_drift_is_rejected(monkeypatch) -> None:
    manifest = json.loads(MANIFEST.read_bytes())
    endpoint = {
        "name": "Moonshot AI | moonshotai/kimi-k3-20260715",
        "model_id": "moonshotai/kimi-k3",
        "pricing": {"prompt": "0.000003", "completion": "0.000016"},
    }
    payload = json.dumps({"data": {"endpoints": [endpoint]}}).encode()
    monkeypatch.setattr(collector, "urlopen", lambda *_args, **_kwargs: BytesIO(payload))
    with pytest.raises(ValueError, match="price has drifted"):
        collector._fresh_endpoint_check(manifest)


def test_pair_receipt_is_create_only_and_private(tmp_path: Path) -> None:
    path = tmp_path / "pair-postcheck-1.json"
    digest = collector._write_json_create_only(path, {"pair_number": 1})
    assert digest == collector._sha256(path.read_bytes())
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        collector._write_json_create_only(path, {"pair_number": 2})


@pytest.mark.asyncio
async def test_pair_inventory_rejects_unjournaled_event_store_run() -> None:
    extra_run_id = uuid4()

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [extra_run_id]

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return Result()

    class Engine:
        def connect(self):
            return Connection()

    with pytest.raises(ValueError, match="unjournaled"):
        await audit_pair_inventory(Engine(), None, {}, [])
