"""No-model negative controls for M6.8.2's detached provenance rules."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from offsecgym.research.m673_audit import CallEvidence
from offsecgym.research.m682_grounding_audit import (
    _all_tristate,
    _identifier,
    admit_private_rows,
    aggregate_tables,
    canonical,
)
from offsecgym.research.m682_hypothesis_alignment import (
    hypothesis_support,
    relationship_edges,
)
from offsecgym.research.m682_object_resolution import (
    PublicObject,
    declared_kind_matches,
    objects_from_response,
    resolve,
)
from offsecgym.research.m682_public_evidence_index import (
    VerifiedSource,
    _read_source,
    delivery_for,
)
from offsecgym.runtime.enterprise import FAMILY
from offsecgym.schemas.events import (
    ActionCompleted,
    ActionRequested,
    ModelCallCompleted,
    ModelCallStarted,
)
from offsecgym.schemas.evidence import Evidence, RequestArtifact

ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER = "550e8400-e29b-41d4-a716-446655440001"


def test_structural_public_records_and_pagination() -> None:
    project = {"id": ID, "organization_id": OTHER, "name": "example"}
    records = objects_from_response(
        family=FAMILY,
        method="GET",
        path="/api/projects?limit=1",
        status=200,
        body={"items": [project], "total": 2},
        truncated=False,
    )
    assert resolve(ID, records) == ("project",)
    assert records[0].relationships == (("organization", OTHER),)
    assert resolve(OTHER, records) == ()  # a foreign key is not a fetched object
    assert (
        objects_from_response(
            family=FAMILY,
            method="POST",
            path="/api/projects",
            status=200,
            body=project,
            truncated=False,
        )
        == ()
    )
    assert (
        objects_from_response(
            family=FAMILY,
            method="GET",
            path="/api/projects",
            status=200,
            body={"items": [project], "total": 2},
            truncated=True,
        )
        == ()
    )


def test_type_correspondence_is_independent_of_parser_support() -> None:
    assert _identifier(f"project:{ID}") == (ID, "project", True)
    assert _identifier(f"change:{ID}") == (ID, "change", False)
    assert declared_kind_matches("change", ("change_request",)) is True
    assert declared_kind_matches("project", ("change_request",)) is False
    assert declared_kind_matches(None, ("project",)) is None
    assert declared_kind_matches("project", ("project", "change_request")) is None
    assert _identifier("a plain language reference") == (None, None, False)


def test_free_text_and_multiple_semantic_roles_do_not_force_type() -> None:
    body = {"items": [{"id": ID, "summary": f"refer to {OTHER}"}], "total": 1}
    records = objects_from_response(
        family=FAMILY,
        method="GET",
        path="/api/changes",
        status=200,
        body=body,
        truncated=False,
    )
    assert resolve(ID, records) == ("change_request",)
    assert resolve(OTHER, records) == ()
    duplicate = PublicObject(ID, "project", "/api/projects", "$.items[0]", ())
    assert resolve(ID, (*records, duplicate)) == ("change_request", "project")


def _call(run_id: UUID, sequence: int, request_input: list[dict[str, object]]) -> CallEvidence:
    call_id = uuid4()
    digest = "0" * 64
    start = ModelCallStarted(
        run_id=run_id,
        sequence_number=sequence,
        actor="controller",
        call_id=call_id,
        provider="test",
        model="offline",
        input_sha256=digest,
        request_artifact_id=uuid4(),
        request_sha256=digest,
    )
    done = ModelCallCompleted(
        run_id=run_id,
        sequence_number=sequence + 1,
        actor="controller",
        call_id=call_id,
        provider_status="completed",
        tool_call_count=1,
        input_tokens=0,
        output_tokens=0,
        response_artifact_id=uuid4(),
        response_sha256=digest,
    )
    return CallEvidence(start, done, {"input": request_input}, {"output": []})


def _source(run_id: UUID, phase: str = "model", sequence: int = 4) -> VerifiedSource:
    body = json.dumps({"id": ID, "name": "example"})
    return VerifiedSource(
        run_id=run_id,
        action_id=uuid4(),
        request_artifact_id=uuid4(),
        evidence_id=uuid4(),
        range_instance_id=uuid4(),
        range_generation=0,
        identity_id=None,
        source_phase=phase,
        originating_tool_call_id="tool-1" if phase == "model" else None,
        source_event_sequence=sequence,
        method="GET",
        path="/api/projects",
        http_status=200,
        response_sha256=hashlib.sha256(body.encode()).hexdigest(),
        body_text=body,
        truncated=False,
        objects=(PublicObject(ID, "project", "/api/projects", "$", ()),),
    )


def test_delivery_requires_matching_action_evidence_and_prior_model_request() -> None:
    run_id = uuid4()
    source = _source(run_id)
    item = {
        "type": "function_call_output",
        "call_id": "tool-1",
        "output": json.dumps(
            {
                "action_id": str(source.action_id),
                "evidence_id": str(source.evidence_id),
                "http_status": 200,
                "body_text": source.body_text,
            }
        ),
    }
    call = _call(run_id, 5, [item])
    assert delivery_for(source, ID, call, (call,)).identifier_in_delivered_source
    assert not delivery_for(_source(run_id, sequence=6), ID, call, (call,)).source_delivered
    assert not delivery_for(_source(uuid4()), ID, call, (call,)).source_delivered
    bad_item = {**item, "output": json.dumps({"error": "invalid_tool_call", "body_text": ID})}
    bad_call = _call(run_id, 5, [bad_item])
    assert not delivery_for(source, ID, bad_call, (bad_call,)).source_delivered
    assert not delivery_for(source, OTHER, call, (call,)).identifier_in_delivered_source


def test_bootstrap_requires_first_model_request_and_setup_is_not_implicitly_visible() -> None:
    run_id = uuid4()
    first = _call(run_id, 5, [{"role": "user", "content": f"project {ID}"}])
    second = _call(run_id, 8, [{"role": "user", "content": f"project {ID}"}])
    assert delivery_for(
        _source(run_id, "bootstrap", 3), ID, second, (first, second)
    ).source_delivered
    assert not delivery_for(
        _source(run_id, "critical_checkpoint", 3), ID, second, (first, second)
    ).source_delivered
    no_first = _call(run_id, 5, [{"role": "user", "content": "no identifier"}])
    assert not delivery_for(
        _source(run_id, "bootstrap", 3), ID, second, (no_first, second)
    ).source_delivered


def _artifact_triplet(root: Path) -> tuple[ActionRequested, ActionCompleted, Path]:
    run_id, action_id, instance_id, artifact_id, evidence_id = (uuid4() for _ in range(5))
    body = json.dumps({"id": ID, "name": "example"}).encode()
    request = RequestArtifact(
        request_artifact_id=artifact_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        destination=FAMILY,
        method="GET",
        path="/api/projects/" + ID,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        action_id=action_id,
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=artifact_id,
        http_status=200,
        body_b64=base64.b64encode(body).decode(),
        response_sha256=hashlib.sha256(body).hexdigest(),
    )
    asked = ActionRequested(
        run_id=run_id,
        sequence_number=3,
        actor="controller",
        action_id=action_id,
        action_type="http_request",
        destination=FAMILY,
        method="GET",
        path_sha256=hashlib.sha256(request.path.encode()).hexdigest(),
        range_instance_id=instance_id,
        range_generation=0,
        request_artifact_id=artifact_id,
    )
    done = ActionCompleted(
        run_id=run_id,
        sequence_number=4,
        actor="gateway",
        action_id=action_id,
        evidence_id=evidence_id,
        duration_ms=1,
        http_status=200,
        response_sha256=evidence.response_sha256,
        causation_id=asked.event_id,
    )
    base = root / "instances" / instance_id.hex
    (base / "requests").mkdir(parents=True)
    (base / "evidence").mkdir()
    (base / "requests" / f"{artifact_id.hex}.json").write_text(request.model_dump_json())
    evidence_path = base / "evidence" / f"{evidence_id.hex}.json"
    evidence_path.write_text(evidence.model_dump_json())
    return asked, done, evidence_path


def test_integrity_mismatch_aborts_instead_of_becoming_unknown(tmp_path: Path) -> None:
    asked, done, evidence_path = _artifact_triplet(tmp_path)
    assert resolve(ID, _read_source(asked, done, tmp_path, FAMILY).objects) == ("project",)
    with pytest.raises(ValueError, match="binding"):
        _read_source(asked, done.model_copy(update={"action_id": uuid4()}), tmp_path, FAMILY)
    with pytest.raises(ValueError, match="binding"):
        _read_source(asked, done.model_copy(update={"run_id": uuid4()}), tmp_path, FAMILY)
    with pytest.raises(ValueError, match="binding"):
        _read_source(asked, done.model_copy(update={"evidence_id": None}), tmp_path, FAMILY)
    with pytest.raises(ValueError, match="provenance"):
        _read_source(asked.model_copy(update={"range_generation": 1}), done, tmp_path, FAMILY)
    with pytest.raises((ValueError, OSError)):
        _read_source(
            asked.model_copy(update={"request_artifact_id": uuid4()}),
            done,
            tmp_path,
            FAMILY,
        )
    raw = json.loads(evidence_path.read_text())
    raw["response_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="hash mismatch"):
        _read_source(asked, done, tmp_path, FAMILY)


def test_missingness_and_determinism() -> None:
    assert _all_tristate([True, True]) is True
    assert _all_tristate([True, None]) is None
    assert _all_tristate([True, False, None]) is False
    assert canonical({"b": 2, "a": 1}) == canonical({"a": 1, "b": 2})


def test_hypothesis_is_not_inferred_from_keywords() -> None:
    change = PublicObject(ID, "change_request", "/api/changes", "$", (("project", OTHER),))
    project = PublicObject(OTHER, "project", "/api/projects", "$", ())
    edges = relationship_edges((ID, OTHER), (change, project))
    assert len(edges) == 1
    assert hypothesis_support("project change approval", edges) is None


def test_private_input_hash_failure_aborts(tmp_path: Path) -> None:
    private_input = tmp_path / "input.json"
    private_input.write_text("{}")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        admit_private_rows(private_input, None)  # type: ignore[arg-type]


def test_public_tables_keep_unknown_and_oracle_separate() -> None:
    first = {
        "raw_reference": f"project:{ID}",
        "category": "supported_typed_uuid",
        "syntax_accepted": True,
        "context_observed": False,
        "source_exists": False,
        "public_source_observed": False,
        "public_source_grounded": False,
        "verified_source_not_model_visible": False,
        "object_resolution_status": "no_verified_public_match",
        "kind_match": None,
        "offline_oracle_correspondence": {"target_root_match": True},
    }
    second = {
        **first,
        "raw_reference": f"change:{OTHER}",
        "category": "unsupported_typed_uuid",
        "syntax_accepted": False,
        "context_observed": True,
        "source_exists": True,
        "public_source_observed": True,
        "public_source_grounded": True,
        "object_resolution_status": "unique_typed",
        "kind_match": True,
        "offline_oracle_correspondence": {"target_root_match": False},
    }
    proposal = {
        "proposal_category": "mixed",
        "public_source_grounded_reference_count": 1,
        "all_references_grounded": False,
        "all_references_correctly_typed": False,
        "object_resolved_reference_count": 1,
        "reference_count": 2,
        "references": [first, second],
        "public_relationship_established": None,
        "hypothesis_relationship_supported": None,
    }
    tables = aggregate_tables([proposal], [first, second])
    totals = tables["stage_totals"]
    assert totals["target_match_without_grounding"] == 1
    assert totals["unsupported_but_grounded"] == 1
    assert totals["supported_without_grounding"] == 1
    assert tables["table_e_hypothesis_relationship"]["hypothesis_relationship_unknown"] == 1
    assert ID not in json.dumps(tables)
    assert OTHER not in json.dumps(tables)
