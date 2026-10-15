"""Synthetic, no-provider postcheck and frozen journal rejection cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as Obj
from uuid import uuid4

import pytest
from test_m671_protocol import ROOT, receipt

from offsecgym.research import m671_postcheck
from offsecgym.research.m661_checkpoint_analysis import CheckpointDataIntegrityError
from offsecgym.research.m671_postcheck import (
    _cell,
    journal_prefix,
    postcheck,
    trace_digest,
    verify_manifest,
)
from offsecgym.research.m671_protocol import plan_manifest
from offsecgym.schemas.events import ModelCallStarted, RunStarted


class _Values:
    def scalars(self):
        return self

    def all(self):
        return []


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, _):
        return _Values()


class _Engine:
    def connect(self):
        return _Connection()


def _manifest():
    manifest = plan_manifest(ROOT, protocol_commit="a" * 40, endpoint_receipt=receipt())
    raw = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    return manifest, hashlib.sha256(raw).hexdigest()


def test_manifest_assignment_replay() -> None:
    manifest, _ = _manifest()
    verify_manifest(manifest, root=ROOT)
    manifest["cells"][0]["arm"] = "A1B1"
    with pytest.raises(ValueError, match="assignment"):
        verify_manifest(manifest)


def test_manifest_rejects_drifted_model_and_analysis_plan() -> None:
    manifest, _ = _manifest()
    manifest["model_request"]["allow_fallbacks"] = True
    with pytest.raises(ValueError, match="model request"):
        verify_manifest(manifest)
    manifest, _ = _manifest()
    manifest["analysis_plan"]["bootstrap_replicates"] = 100
    with pytest.raises(ValueError, match="analysis or missingness"):
        verify_manifest(manifest)


def test_journal_no_retry_or_out_of_order() -> None:
    manifest, sha = _manifest()
    header = {"type": "batch_started", "protocol": manifest["protocol"], "manifest_sha256": sha}
    cell = manifest["cells"][0]
    run = "11111111-1111-1111-1111-111111111111"
    start = {"type": "cell_started", "order": 1, "cell_id": cell["cell_id"], "run_id": run}
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in [header, start])
    assert journal_prefix(manifest, raw, manifest_sha256=sha)[0]["type"] == "interrupted"
    with pytest.raises(ValueError, match="header"):
        journal_prefix(manifest, raw, manifest_sha256="0" * 64)
    bad = {**start, "cell_id": manifest["cells"][1]["cell_id"]}
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in [header, bad])
    with pytest.raises(ValueError, match="prefix"):
        journal_prefix(manifest, raw, manifest_sha256=sha)


def test_model_request_arm_schema_and_private_leak_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request = {
        "tools": [],
        "model": "moonshotai/kimi-k3",
        "provider": {"order": ["moonshotai"], "allow_fallbacks": False},
        "reasoning": {"effort": "high"},
        "instructions": "ordinary public instructions",
    }

    class Artifacts:
        def __init__(self, _):
            pass

        def read_verified(self, *_):
            return request

    monkeypatch.setattr(m671_postcheck, "ModelCallArtifacts", Artifacts)
    trace = [
        ModelCallStarted.model_construct(
            run_id=uuid4(),
            call_id=uuid4(),
            request_artifact_id=uuid4(),
            request_sha256="0" * 64,
        )
    ]
    receipt = {"setup_evidence_ids": [], "private_anchors": {"secret": "private"}}
    oracle = Obj(properties=[], root_causes=[])
    valid_sha = hashlib.sha256(b"[]").hexdigest()
    m671_postcheck._check_model_requests(
        tmp_path, trace, arm_schema_sha256=valid_sha, receipt=receipt, oracle=oracle
    )
    with pytest.raises(CheckpointDataIntegrityError, match="schema"):
        m671_postcheck._check_model_requests(
            tmp_path, trace, arm_schema_sha256="1" * 64, receipt=receipt, oracle=oracle
        )
    request["instructions"] = "private_anchors are disclosed"
    with pytest.raises(CheckpointDataIntegrityError, match="leaked"):
        m671_postcheck._check_model_requests(
            tmp_path, trace, arm_schema_sha256=valid_sha, receipt=receipt, oracle=oracle
        )


@pytest.mark.asyncio
async def test_corrupt_source_is_assigned_missing_but_programming_error_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest, _ = _manifest()
    cell = manifest["cells"][0]
    run_id = uuid4()
    trace = [RunStarted(run_id=run_id, actor="controller", experiment_hash="synthetic")]

    class Events:
        async def read_run(self, _):
            return trace

    record = {"run_id": str(run_id), "trace_sha256": "0" * 64}
    row, seen = await _cell(cell, record, events=Events(), state_root=tmp_path, manifest=manifest)
    assert seen == {run_id}
    assert row["assigned"] is True and row["trace_auditable"] is False
    record["trace_sha256"] = trace_digest(trace)

    async def bug(*_):
        raise TypeError("synthetic analyzer bug")

    monkeypatch.setattr(m671_postcheck, "audit_checkpoint_receipt", bug)
    with pytest.raises(TypeError, match="synthetic analyzer bug"):
        await _cell(cell, record, events=Events(), state_root=tmp_path, manifest=manifest)


@pytest.mark.asyncio
async def test_synthetic_unstarted_postcheck_retains_all_128(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, sha = _manifest()
    monkeypatch.setattr(
        m671_postcheck,
        "analyze_factorial",
        lambda rows, **_: {"assigned": len(rows), "synthetic": True},
    )
    header = {"type": "batch_started", "protocol": manifest["protocol"], "manifest_sha256": sha}
    for pair_index in range(64):
        cells = manifest["cells"][2 * pair_index : 2 * pair_index + 2]
        (tmp_path / f"pair-postcheck-{pair_index + 1}.json").write_text(
            json.dumps(
                {
                    "manifest_sha256": sha,
                    "pair_id": cells[0]["pair_id"],
                    "cell_ids": [item["cell_id"] for item in cells],
                    "run_ids": [None, None],
                }
            )
        )
    result = await postcheck(
        manifest,
        json.dumps(header).encode() + b"\n",
        sha,
        tmp_path,
        tmp_path,
        object(),
        _Engine(),
    )
    assert result["assigned"] == 128
    assert result["started"] == result["trace_auditable"] == result["score_valid"] == 0
    assert result["analysis_sha256"] == hashlib.sha256(result["analysis_bytes"]).hexdigest()
