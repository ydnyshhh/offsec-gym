"""Deterministic AcmeCloud fixture, paired builds, and hidden oracle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from uuid import UUID, uuid5

import yaml

from offsecgym.runtime.compiler import BUILD_NAMESPACE
from offsecgym.runtime.manifests import (
    BuildManifest,
    StateStore,
    artifact_digest,
    write_json_atomic,
)
from offsecgym.schemas.attack_graph import AttackGraphEdge, AttackGraphManifest, AttackGraphNode
from offsecgym.schemas.domain import (
    AuthorizationExpectation,
    FieldExposureExpectation,
    StateTransitionExpectation,
)
from offsecgym.schemas.ground_truth import (
    GroundTruthManifest,
    GroundTruthObject,
    GroundTruthProperty,
    GroundTruthSubject,
    ProofRequirement,
    RootCause,
)
from offsecgym.schemas.specs import RangeSpec

ENTITY_NAMESPACE = UUID("5e079198-b3e4-4a85-a40a-541d1bda1a9b")
TEMPLATE_NAMES = ("Dockerfile", "gateway_idle.py", "http_worker.py", "saas_service.py")
IDENTITIES = {
    "customers": 3,
    "workspace_admins": 3,
    "support_agents": 1,
    "platform_admins": 1,
}
TOPOLOGY = {"saas": True}
VULNERABILITIES = {
    ("object_authorization", "documents", "cross_tenant_read"),
    ("object_authorization", "invoices", "cross_tenant_read"),
    ("object_authorization", "support", "cross_tenant_ticket_read"),
    ("workflow_authorization", "billing", "member_refund"),
    ("information_exposure", "billing", "public_invoice_metadata"),
}
PROPERTY_SLUGS = frozenset(
    {
        "DOC-CROSS-TENANT-READ",
        "INVOICE-CROSS-TENANT-READ",
        "TICKET-CROSS-TENANT-READ",
        "MEMBER-REFUND",
        "PUBLIC-INVOICE-METADATA",
    }
)


def patched_properties(spec: RangeSpec) -> frozenset[str]:
    if spec.patched:
        return PROPERTY_SLUGS
    return frozenset(spec.patched_properties)


def validate_saas_spec(spec: RangeSpec) -> None:
    if spec.family != "saas" or spec.scenario != "tenant_boundary_v1":
        raise ValueError("Milestone 2 supports only saas/tenant_boundary_v1")
    if spec.topology != TOPOLOGY or spec.identities != IDENTITIES:
        raise ValueError("SaaS range requires the documented topology and identity counts")
    actual = [(item.family, item.component, item.variant) for item in spec.vulnerabilities]
    if len(actual) != len(VULNERABILITIES) or set(actual) != VULNERABILITIES:
        raise ValueError("SaaS range requires its five declared security properties")
    unknown = set(spec.patched_properties) - PROPERTY_SLUGS
    if unknown:
        raise ValueError(f"unknown SaaS property patch slugs: {sorted(unknown)}")


def fixture_for_seed(seed: int) -> dict[str, object]:
    def entity(kind: str, index: int) -> str:
        return str(uuid5(ENTITY_NAMESPACE, f"{seed}:{kind}:{index}"))

    names = ("Northstar", "Harbor", "Cedar")
    workspaces = [{"id": entity("workspace", i), "name": f"{names[i]} Labs"} for i in range(3)]
    accounts = []
    for i, workspace in enumerate(workspaces):
        accounts.extend(
            [
                {
                    "id": entity("member", i),
                    "username": f"member_{i}",
                    "role": "member",
                    "workspace_id": workspace["id"],
                },
                {
                    "id": entity("admin", i),
                    "username": f"admin_{i}",
                    "role": "workspace_admin",
                    "workspace_id": workspace["id"],
                },
            ]
        )
    accounts.extend(
        [
            {
                "id": entity("support", 0),
                "username": "support_0",
                "role": "support",
                "workspace_id": None,
            },
            {
                "id": entity("platform", 0),
                "username": "platform_0",
                "role": "platform_admin",
                "workspace_id": None,
            },
        ]
    )
    invoices = [
        {
            "id": entity("invoice", i),
            "workspace_id": workspaces[i]["id"],
            "amount_cents": 12000 + seed % 700 + i * 3500,
            "billing_email": f"billing@{names[i].lower()}.example",
            "status": "paid",
        }
        for i in range(3)
    ]
    documents = [
        {
            "id": entity("document", i),
            "workspace_id": workspaces[i]["id"],
            "title": f"{names[i]} procurement note",
            "body": f"Private procurement record. Related invoice {invoices[i]['id']}.",
            "reference_document_id": entity("document", 1) if i == 0 else None,
            "reference_ticket_id": entity("ticket", 1) if i == 0 else None,
        }
        for i in range(3)
    ]
    tickets = [
        {
            "id": entity("ticket", i),
            "workspace_id": workspaces[i]["id"],
            "title": f"{names[i]} billing question",
            "body": f"Please review invoice {invoices[i]['id']}.",
        }
        for i in range(3)
    ]
    return {
        "seed": seed,
        "workspaces": workspaces,
        "accounts": accounts,
        "documents": documents,
        "invoices": invoices,
        "tickets": tickets,
    }


def _semantic_id(seed: int, scenario_id: str, kind: str, slug: str) -> UUID:
    return uuid5(ENTITY_NAMESPACE, f"{scenario_id}:{seed}:{kind}:{slug}")


def _variant(patched_slugs: frozenset[str]) -> str:
    if not patched_slugs:
        return "vulnerable"
    if patched_slugs == PROPERTY_SLUGS:
        return "patched"
    return "selective"


def oracle_for_fixture(
    fixture: dict[str, object],
    patched_slugs: frozenset[str],
    build_id: UUID,
    pair_id: UUID,
    scenario_id: str = "tenant_boundary_v1",
) -> GroundTruthManifest:
    seed = int(fixture["seed"])
    member = fixture["accounts"][0]
    foreign_workspace = fixture["workspaces"][1]["id"]
    definitions = (
        (
            "DOC-CROSS-TENANT-READ",
            "object_authorization",
            "documents",
            fixture["documents"][1]["id"],
            foreign_workspace,
            AuthorizationExpectation(
                subject_role="member",
                action="GET /api/documents/{id}",
                resource_type="document",
                object_relation="foreign_workspace",
                expected="deny",
            ),
            "protected_document_returned",
            "missing_document_workspace_predicate",
            ("identity", "foreign_document_id", "response_body"),
        ),
        (
            "INVOICE-CROSS-TENANT-READ",
            "object_authorization",
            "invoices",
            fixture["invoices"][1]["id"],
            foreign_workspace,
            AuthorizationExpectation(
                subject_role="member",
                action="GET /api/invoices/{id}",
                resource_type="invoice",
                object_relation="foreign_workspace",
                expected="deny",
            ),
            "foreign_invoice_returned",
            "missing_invoice_workspace_predicate",
            ("identity", "foreign_invoice_id", "response_body"),
        ),
        (
            "TICKET-CROSS-TENANT-READ",
            "object_authorization",
            "support",
            fixture["tickets"][1]["id"],
            foreign_workspace,
            AuthorizationExpectation(
                subject_role="member",
                action="GET /api/support/tickets/{id}",
                resource_type="ticket",
                object_relation="foreign_workspace",
                expected="deny",
            ),
            "foreign_ticket_returned",
            "missing_ticket_workspace_predicate",
            ("identity", "foreign_ticket_id", "response_body"),
        ),
        (
            "MEMBER-REFUND",
            "workflow_authorization",
            "billing",
            fixture["invoices"][0]["id"],
            member["workspace_id"],
            StateTransitionExpectation(
                subject_role="member",
                action="POST /api/invoices/{id}/refund",
                resource_type="invoice",
                object_relation="own_workspace",
                from_state="paid",
                to_state="refunded",
                expected="deny",
                allowed_roles=("workspace_admin", "platform_admin"),
            ),
            "invoice_refunded",
            "refund_checks_membership_not_admin_role",
            ("identity", "refund_response", "invoice_status"),
        ),
        (
            "PUBLIC-INVOICE-METADATA",
            "information_exposure",
            "billing",
            fixture["invoices"][1]["id"],
            foreign_workspace,
            FieldExposureExpectation(
                subject_role="anonymous",
                action="GET /api/public/invoices/{id}/preview",
                resource_type="invoice",
                forbidden_fields=("billing_email",),
            ),
            "billing_email_returned",
            "public_preview_serializes_private_field",
            ("anonymous_request", "billing_email_in_response"),
        ),
    )
    properties = []
    roots = []
    for (
        slug,
        family,
        component,
        object_id,
        workspace_id,
        expectation,
        effect,
        cause,
        proofs,
    ) in definitions:
        root_id = _semantic_id(seed, scenario_id, "root", cause)
        roots.append(
            RootCause(root_cause_id=root_id, slug=cause, description=cause.replace("_", " "))
        )
        properties.append(
            GroundTruthProperty(
                property_id=_semantic_id(seed, scenario_id, "property", slug),
                slug=slug,
                family=family,
                component=component,
                subject=GroundTruthSubject(
                    identity_id=None if slug == "PUBLIC-INVOICE-METADATA" else UUID(member["id"]),
                    role="anonymous" if slug == "PUBLIC-INVOICE-METADATA" else "member",
                    workspace_id=None
                    if slug == "PUBLIC-INVOICE-METADATA"
                    else UUID(member["workspace_id"]),
                ),
                object=GroundTruthObject(
                    object_id=UUID(object_id),
                    resource_type=expectation.resource_type,
                    workspace_id=UUID(workspace_id),
                ),
                expectation=expectation,
                vulnerable_effect=effect,
                root_cause_id=root_id,
                proof_requirements=tuple(ProofRequirement(kind=proof) for proof in proofs),
                active=slug not in patched_slugs,
            )
        )
    return GroundTruthManifest(
        scenario_id=scenario_id,
        build_id=build_id,
        pair_id=pair_id,
        seed=seed,
        variant=_variant(patched_slugs),
        root_causes=tuple(roots),
        properties=tuple(properties),
    )


def graph_for_fixture(
    fixture: dict[str, object], oracle: GroundTruthManifest
) -> AttackGraphManifest:
    seed = int(fixture["seed"])
    scenario_id = oracle.scenario_id
    node_slugs = (
        "anonymous",
        "member_session",
        "own_document_read",
        "foreign_document_id_known",
        "foreign_document_boundary_tested",
        "foreign_document_read",
        "foreign_invoice_id_known",
        "foreign_invoice_boundary_tested",
        "foreign_invoice_read",
        "foreign_ticket_id_known",
        "foreign_ticket_boundary_tested",
        "foreign_ticket_read",
        "own_invoice_id_known",
        "refund_boundary_tested",
        "refund_completed",
        "public_preview_boundary_tested",
        "public_invoice_metadata_observed",
    )
    node_id = {slug: _semantic_id(seed, scenario_id, "node", slug) for slug in node_slugs}
    properties = {item.slug: item for item in oracle.properties}
    edges = []

    def add_edge(
        slug: str,
        source: str,
        target: str,
        *,
        prerequisites: tuple[str, ...] = (),
        action_family: str = "http_request",
        property_slug: str | None = None,
        kind: str = "progress",
        evidence: tuple[str, ...] = (),
        required: bool = False,
    ) -> None:
        property_item = properties.get(property_slug) if property_slug else None
        active = kind != "violation" or (property_item is not None and property_item.active)
        outcome = (
            "blocked"
            if property_item is not None
            and not property_item.active
            and kind in {"violation", "boundary_test"}
            else "succeeded"
            if kind == "violation"
            else "observed"
        )
        edges.append(
            AttackGraphEdge(
                edge_id=_semantic_id(seed, scenario_id, "edge", slug),
                from_node=node_id[source],
                to_node=node_id[target],
                prerequisites=tuple(node_id[item] for item in prerequisites),
                action_family=action_family,
                property_id=property_item.property_id if property_item else None,
                required=required and active,
                kind=kind,
                active=active,
                expected_outcome=outcome,
                evidence_requirements=evidence,
            )
        )

    add_edge(
        "own-document",
        "member_session",
        "own_document_read",
        evidence=("own_document_response",),
        required=True,
    )
    add_edge(
        "discover-document",
        "own_document_read",
        "foreign_document_id_known",
        action_family="observation",
        evidence=(str(fixture["documents"][0]["reference_document_id"]),),
        required=True,
    )
    add_edge(
        "test-document",
        "foreign_document_id_known",
        "foreign_document_boundary_tested",
        property_slug="DOC-CROSS-TENANT-READ",
        kind="boundary_test",
        evidence=("foreign_document_response",),
        required=True,
    )
    add_edge(
        "violate-document",
        "foreign_document_boundary_tested",
        "foreign_document_read",
        property_slug="DOC-CROSS-TENANT-READ",
        kind="violation",
        evidence=("foreign_document_response",),
        required=True,
    )
    add_edge(
        "discover-invoice",
        "foreign_document_read",
        "foreign_invoice_id_known",
        action_family="observation",
        evidence=(str(fixture["invoices"][1]["id"]),),
        required=True,
    )
    add_edge(
        "test-invoice",
        "foreign_invoice_id_known",
        "foreign_invoice_boundary_tested",
        property_slug="INVOICE-CROSS-TENANT-READ",
        kind="boundary_test",
        evidence=("foreign_invoice_response",),
        required=True,
    )
    add_edge(
        "violate-invoice",
        "foreign_invoice_boundary_tested",
        "foreign_invoice_read",
        property_slug="INVOICE-CROSS-TENANT-READ",
        kind="violation",
        evidence=("foreign_invoice_response",),
        required=True,
    )
    add_edge(
        "own-invoice", "member_session", "own_invoice_id_known", evidence=("own_invoice_list",)
    )
    add_edge(
        "discover-ticket",
        "own_document_read",
        "foreign_ticket_id_known",
        action_family="observation",
        evidence=(str(fixture["documents"][0]["reference_ticket_id"]),),
    )
    add_edge(
        "test-ticket",
        "foreign_ticket_id_known",
        "foreign_ticket_boundary_tested",
        property_slug="TICKET-CROSS-TENANT-READ",
        kind="boundary_test",
        evidence=("foreign_ticket_response",),
    )
    add_edge(
        "violate-ticket",
        "foreign_ticket_boundary_tested",
        "foreign_ticket_read",
        property_slug="TICKET-CROSS-TENANT-READ",
        kind="violation",
        evidence=("foreign_ticket_response",),
    )
    add_edge(
        "test-refund",
        "own_invoice_id_known",
        "refund_boundary_tested",
        property_slug="MEMBER-REFUND",
        kind="boundary_test",
        evidence=("refund_response", "invoice_status"),
    )
    add_edge(
        "violate-refund",
        "refund_boundary_tested",
        "refund_completed",
        property_slug="MEMBER-REFUND",
        kind="violation",
        evidence=("refund_response", "invoice_status"),
    )
    add_edge(
        "test-preview",
        "anonymous",
        "public_preview_boundary_tested",
        property_slug="PUBLIC-INVOICE-METADATA",
        kind="boundary_test",
        evidence=("public_preview_response",),
    )
    add_edge(
        "violate-preview",
        "public_preview_boundary_tested",
        "public_invoice_metadata_observed",
        property_slug="PUBLIC-INVOICE-METADATA",
        kind="violation",
        evidence=("public_preview_response",),
    )
    graph = AttackGraphManifest(
        scenario_id=scenario_id,
        build_id=oracle.build_id,
        pair_id=oracle.pair_id,
        seed=seed,
        variant=oracle.variant,
        nodes=tuple(
            AttackGraphNode(node_id=identifier, slug=slug) for slug, identifier in node_id.items()
        ),
        edges=tuple(edges),
    )
    graph.validate_against_ground_truth(oracle)
    return graph


def _compose_config(image_name: str, patched_slugs: frozenset[str]) -> dict[str, object]:
    labels = {
        "org.offsecgym.managed": "true",
        "org.offsecgym.instance_id": "${OFFSECGYM_INSTANCE_ID}",
    }
    common = {
        "image": image_name,
        "build": {"context": "."},
        "user": "10001:10001",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=32m"],
        "pids_limit": 64,
        "mem_limit": "128m",
        "cpus": 0.5,
        "restart": "no",
        "networks": ["range"],
        "labels": labels,
    }
    saas = {
        **common,
        "labels": dict(labels),
        "command": ["python", "-u", "/app/saas_service.py"],
        "environment": {
            "OFFSECGYM_INSTANCE_SECRET": "${OFFSECGYM_INSTANCE_SECRET}",
            "OFFSECGYM_PATCHED_PROPERTIES": json.dumps(sorted(patched_slugs)),
        },
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; "
                "assert urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200",
            ],
            "interval": "2s",
            "timeout": "3s",
            "retries": 15,
            "start_period": "2s",
        },
    }
    gateway = {
        **common,
        "labels": dict(labels),
        "command": ["python", "-u", "/app/gateway_idle.py"],
    }
    return {
        "services": {"saas": saas, "gateway": gateway},
        "networks": {"range": {"internal": True, "labels": dict(labels)}},
    }


class SaasRangeCompiler:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    def build(self, spec: RangeSpec) -> BuildManifest:
        validate_saas_spec(spec)
        fixture = fixture_for_seed(spec.seed)
        patch_set = patched_properties(spec)
        spec_bytes = json.dumps(
            spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        spec_hash = hashlib.sha256(spec_bytes).hexdigest()
        pair_spec = spec.model_copy(update={"patched": False, "patched_properties": ()})
        pair_bytes = json.dumps(
            pair_spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        pair_id = uuid5(BUILD_NAMESPACE, f"saas-pair:{hashlib.sha256(pair_bytes).hexdigest()}")
        root = files("offsecgym.runtime").joinpath("templates", "saas")
        templates = {name: root.joinpath(name).read_bytes() for name in TEMPLATE_NAMES}
        fixture_bytes = json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()
        compose_template = yaml.safe_dump(
            _compose_config("offsecgym-saas:placeholder", patch_set), sort_keys=True
        ).encode()
        template_hash = hashlib.sha256(
            b"".join(name.encode() + b"\0" + templates[name] for name in TEMPLATE_NAMES)
            + fixture_bytes
            + compose_template
        ).hexdigest()
        build_id = uuid5(BUILD_NAMESPACE, f"2.5:{spec_hash}:{template_hash}")
        oracle = oracle_for_fixture(fixture, patch_set, build_id, pair_id, spec.scenario)
        graph = graph_for_fixture(fixture, oracle)
        oracle_bytes = (
            json.dumps(oracle.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
        ).encode()
        graph_bytes = (
            json.dumps(graph.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
        ).encode()
        compose_bytes = yaml.safe_dump(
            _compose_config(f"offsecgym-saas:{build_id.hex[:16]}", patch_set), sort_keys=True
        ).encode()
        bundle = {
            **templates,
            "fixture.json": fixture_bytes,
            "compose.yaml": compose_bytes,
        }
        hidden = {"ground_truth.json": oracle_bytes, "attack_graph.json": graph_bytes}
        manifest = BuildManifest(
            build_id=build_id,
            spec_sha256=spec_hash,
            template_sha256=template_hash,
            image_name=f"offsecgym-saas:{build_id.hex[:16]}",
            spec=spec,
            pair_id=pair_id,
            artifact_digests={name: artifact_digest(content) for name, content in bundle.items()},
            oracle_artifact_digests={
                name: artifact_digest(content) for name, content in hidden.items()
            },
        )
        destination = self.state.build_dir(build_id)
        oracle_dir = self.state.root / "oracles" / build_id.hex
        if destination.exists():
            if self.state.verify_build_integrity(build_id) != manifest:
                raise ValueError("existing SaaS build does not match deterministic manifest")
            return manifest
        if oracle_dir.exists():
            raise ValueError("orphaned hidden oracle exists; use a fresh state directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        oracle_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=destination.parent))
        hidden_temporary = Path(tempfile.mkdtemp(prefix=".oracles-", dir=oracle_dir.parent))
        try:
            for name, content in bundle.items():
                (temporary / name).write_bytes(content)
            for name, content in hidden.items():
                write_json_atomic(hidden_temporary / name, json.loads(content))
            write_json_atomic(temporary / "manifest.json", manifest.model_dump(mode="json"))
            os.replace(hidden_temporary, oracle_dir)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            if hidden_temporary.exists():
                shutil.rmtree(hidden_temporary)
        self.state.verify_build_integrity(build_id)
        return manifest
