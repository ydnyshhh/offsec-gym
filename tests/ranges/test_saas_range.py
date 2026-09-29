"""Live oracle proofs against paired SaaS ranges."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError, run_command
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.specs import Budget, RangeSpec


class MemoryEventStore:
    def __init__(self) -> None:
        self.items = []

    async def append(self, event):
        sequence = sum(item.run_id == event.run_id for item in self.items) + 1
        stored = event.model_copy(update={"sequence_number": sequence})
        self.items.append(stored)
        return stored

    async def read_run(self, run_id: UUID):
        return [item for item in self.items if item.run_id == run_id]


def require_docker() -> None:
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        if os.getenv("OFFSECGYM_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required by CI: {result.stderr[-400:]}")
        pytest.skip("Docker daemon is unavailable")


def spec(patched: bool) -> RangeSpec:
    name = "saas-range-patched.yaml" if patched else "saas-range.yaml"
    import yaml

    return RangeSpec.model_validate(
        yaml.safe_load((Path(__file__).parents[2] / "examples" / name).read_text())
    )


def response_json(result) -> dict[str, object]:
    return json.loads(result.body_text or "{}")


@pytest.mark.docker
async def test_saas_oracle_pair_and_containment(tmp_path: Path) -> None:
    require_docker()
    runtime = ComposeRangeRuntime(tmp_path)
    vulnerable_build = await runtime.build(spec(False))
    patched_build = await runtime.build(spec(True))
    selective_build = await runtime.build(
        spec(False).model_copy(update={"patched_properties": ("DOC-CROSS-TENANT-READ",)})
    )
    assert len({vulnerable_build, patched_build, selective_build}) == 3
    assert (
        len(
            {
                runtime.state.load_build(build_id).pair_id
                for build_id in (vulnerable_build, patched_build, selective_build)
            }
        )
        == 1
    )
    fixture = json.loads((runtime.state.build_dir(vulnerable_build) / "fixture.json").read_text())
    oracle = json.loads(
        (runtime.state.root / "oracles" / vulnerable_build.hex / "ground_truth.json").read_text()
    )
    assert {item["object"]["object_id"] for item in oracle["properties"]} == {
        fixture["documents"][1]["id"],
        fixture["invoices"][1]["id"],
        fixture["tickets"][1]["id"],
        fixture["invoices"][0]["id"],
    }
    instances: list[UUID] = []
    try:
        vulnerable = await runtime.start_instance(await runtime.create_instance(vulnerable_build))
        instances.append(vulnerable.instance_id)
        patched = await runtime.start_instance(await runtime.create_instance(patched_build))
        instances.append(patched.instance_id)
        selective = await runtime.start_instance(await runtime.create_instance(selective_build))
        instances.append(selective.instance_id)
        assert vulnerable.state == patched.state == selective.state == "healthy"
        vulnerable_meta = await runtime.snapshot_metadata(vulnerable.instance_id)
        patched_meta = await runtime.snapshot_metadata(patched.instance_id)
        selective_meta = await runtime.snapshot_metadata(selective.instance_id)
        assert vulnerable_meta.family == patched_meta.family == "saas"
        assert vulnerable_meta.variant == "vulnerable"
        assert vulnerable_meta.patched_properties == ()
        assert patched_meta.variant == "patched"
        assert len(patched_meta.patched_properties) == 5
        assert selective_meta.variant == "selective"
        assert selective_meta.patched_properties == ("DOC-CROSS-TENANT-READ",)
        assert vulnerable_meta.pair_id == patched_meta.pair_id
        assert vulnerable_meta.identities == patched_meta.identities
        member = vulnerable_meta.identities[0].identity_id
        admin = vulnerable_meta.identities[3].identity_id
        support = vulnerable_meta.identities[-2].identity_id
        assert runtime.identity_credentials(vulnerable.instance_id, member) != (
            runtime.identity_credentials(patched.instance_id, member)
        )

        vulnerable_instance = runtime.state.load_instance(vulnerable.instance_id)
        patched_instance = runtime.state.load_instance(patched.instance_id)
        assert vulnerable_instance.project_name != patched_instance.project_name
        target_ids = (
            await run_command(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={vulnerable_instance.project_name}",
                    "--filter",
                    "label=com.docker.compose.service=saas",
                    "--format",
                    "{{.ID}}",
                ]
            )
        ).strip()
        assert target_ids
        ports = json.loads(
            await run_command(
                ["docker", "inspect", target_ids, "--format", "{{json .NetworkSettings.Ports}}"]
            )
        )
        assert all(binding is None for binding in ports.values())
        assert (
            await run_command(
                [
                    "docker",
                    "network",
                    "inspect",
                    f"{vulnerable_instance.project_name}_range",
                    "--format",
                    "{{.Internal}}",
                ]
            )
        ).strip() == "true"
        patched_target = (
            await run_command(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={patched_instance.project_name}",
                    "--filter",
                    "label=com.docker.compose.service=saas",
                    "--format",
                    "{{.ID}}",
                ]
            )
        ).strip()
        patched_ip = (
            await run_command(
                [
                    "docker",
                    "inspect",
                    patched_target,
                    "--format",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                ]
            )
        ).strip()
        with pytest.raises(DockerCommandError):
            await runtime._compose(
                vulnerable_instance,
                "exec",
                "-T",
                "gateway",
                "python",
                "-c",
                f"import socket; socket.create_connection(('{patched_ip}',8080),2)",
                timeout_seconds=8,
            )
        with pytest.raises(DockerCommandError):
            await runtime._compose(
                vulnerable_instance,
                "exec",
                "-T",
                "gateway",
                "python",
                "-c",
                "import socket; socket.create_connection(('1.1.1.1',443),2)",
                timeout_seconds=8,
            )

        # The first handler acknowledges the headers, then waits for its request body.
        # A second handler must answer while that first request remains unfinished.
        await runtime._compose(
            vulnerable_instance,
            "exec",
            "-T",
            "gateway",
            "python",
            "-c",
            """
import socket
body = b'{"username":"unknown","password":"x"}'
headers = (f'POST /api/login HTTP/1.1\\r\\nHost: saas\\r\\nContent-Length: {len(body)}\\r\\n'
           'Expect: 100-continue\\r\\nConnection: close\\r\\n\\r\\n').encode()
with socket.create_connection(('saas', 8080), timeout=3) as blocked:
    blocked.settimeout(3)
    blocked.sendall(headers)
    assert blocked.recv(1024).startswith(b'HTTP/1.1 100 Continue')
    blocked.settimeout(0.2)
    try:
        blocked.recv(1024)
    except socket.timeout:
        pass
    else:
        raise AssertionError('first request finished before its body arrived')
    with socket.create_connection(('saas', 8080), timeout=3) as probe:
        probe.settimeout(2)
        probe.sendall(b'GET /health HTTP/1.1\\r\\nHost: saas\\r\\nConnection: close\\r\\n\\r\\n')
        assert probe.recv(1024).startswith(b'HTTP/1.1 200 OK')
    blocked.sendall(body)
    assert blocked.recv(1024).startswith(b'HTTP/1.1 401 Unauthorized')
""",
            timeout_seconds=8,
        )

        events = MemoryEventStore()
        gateway = ComposeActionGateway(runtime, events, min_interval_seconds=0)
        contexts = {
            range_id: ExperimentContext(
                run_id=uuid4(),
                range_instance_id=range_id,
                range_generation=0,
                budget=Budget(max_actions=50, max_http_requests=45),
                allowed_identity_ids=(member, admin, support),
            )
            for range_id in instances
        }

        async def call(range_id, path, identity=member, method="GET", body=None):
            context = contexts[range_id]
            return await gateway.execute(
                ActionRequest(
                    run_id=context.run_id,
                    kind="http_request",
                    destination="saas",
                    method=method,
                    path=path,
                    identity_id=identity,
                    json_body=body,
                ),
                context,
            )

        own_document = fixture["documents"][0]
        foreign_document = fixture["documents"][1]
        own_invoice = fixture["invoices"][0]
        foreign_invoice = fixture["invoices"][1]
        foreign_ticket = fixture["tickets"][1]
        own_workspace = fixture["workspaces"][0]["id"]
        foreign_workspace = fixture["workspaces"][1]["id"]
        own_evidence = None
        for range_id, expected_foreign_status in (
            (vulnerable.instance_id, 200),
            (patched.instance_id, 403),
        ):
            own = await call(range_id, f"/api/documents/{own_document['id']}")
            if range_id == vulnerable.instance_id:
                own_evidence = own.evidence_id
            assert own.http_status == 200
            assert response_json(own)["reference_document_id"] == foreign_document["id"]
            assert response_json(own)["reference_ticket_id"] == foreign_ticket["id"]
            foreign = await call(range_id, f"/api/documents/{foreign_document['id']}")
            assert foreign.http_status == expected_foreign_status
            if expected_foreign_status == 200:
                assert foreign_invoice["id"] in response_json(foreign)["body"]
            invoice = await call(range_id, f"/api/invoices/{foreign_invoice['id']}")
            ticket = await call(range_id, f"/api/support/tickets/{foreign_ticket['id']}")
            assert invoice.http_status == ticket.http_status == expected_foreign_status
            refund = await call(
                range_id,
                f"/api/invoices/{own_invoice['id']}/refund",
                method="POST",
                body={"reason": "duplicate charge"},
            )
            assert refund.http_status == expected_foreign_status
            own_invoice_after = await call(range_id, f"/api/invoices/{own_invoice['id']}")
            assert response_json(own_invoice_after)["status"] == (
                "refunded" if expected_foreign_status == 200 else "paid"
            )
            preview = await call(
                range_id,
                f"/api/public/invoices/{foreign_invoice['id']}/preview",
                identity=None,
            )
            assert preview.http_status == 200
            assert ("billing_email" in response_json(preview)) == (expected_foreign_status == 200)

            # Decoys and ordinary authorized paths behave identically in both variants.
            assert (
                await call(range_id, f"/api/workspaces/{foreign_workspace}/documents")
            ).http_status == 403
            assert (
                await call(range_id, f"/api/workspaces/{own_workspace}/members")
            ).http_status == 403
            assert (
                await call(range_id, f"/api/workspaces/{foreign_workspace}/members", admin)
            ).http_status == 200
            assert (
                await call(
                    range_id,
                    f"/api/support/tickets/{foreign_ticket['id']}/reply",
                    method="POST",
                    body={"body": "received"},
                )
            ).http_status == 403
            assert (
                await call(
                    range_id,
                    f"/api/support/tickets/{foreign_ticket['id']}/reply",
                    support,
                    "POST",
                    {"body": "received"},
                )
            ).http_status == 201
            assert (
                await call(range_id, f"/api/workspaces/{foreign_workspace}/documents", support)
            ).http_status == 403
            assert (
                await call(range_id, f"/api/workspaces/{foreign_workspace}/tickets", support)
            ).http_status == 200

        # Fix only document authorization. Direct probes use known fixture IDs so a
        # blocked document chain cannot hide another still-vulnerable endpoint.
        selective_id = selective.instance_id
        assert (await call(selective_id, f"/api/documents/{own_document['id']}")).http_status == 200
        assert (
            await call(selective_id, f"/api/documents/{foreign_document['id']}")
        ).http_status == 403
        assert (
            await call(selective_id, f"/api/invoices/{foreign_invoice['id']}")
        ).http_status == 200
        assert (
            await call(selective_id, f"/api/support/tickets/{foreign_ticket['id']}")
        ).http_status == 200
        selective_preview = await call(
            selective_id,
            f"/api/public/invoices/{foreign_invoice['id']}/preview",
            identity=None,
        )
        assert selective_preview.http_status == 200
        assert "billing_email" in response_json(selective_preview)

        async def race_refund():
            run_id = uuid4()
            context = ExperimentContext(
                run_id=run_id,
                range_instance_id=selective_id,
                range_generation=0,
                budget=Budget(max_actions=1),
                allowed_identity_ids=(member,),
            )
            return await gateway.execute(
                ActionRequest(
                    run_id=run_id,
                    kind="http_request",
                    destination="saas",
                    method="POST",
                    path=f"/api/invoices/{own_invoice['id']}/refund",
                    identity_id=member,
                    json_body={"reason": "duplicate charge"},
                ),
                context,
            )

        refunds = await asyncio.gather(race_refund(), race_refund())
        assert sorted(result.http_status for result in refunds) == [200, 409]
        assert (
            response_json(await call(selective_id, f"/api/invoices/{own_invoice['id']}"))["status"]
            == "refunded"
        )

        outside_identity = vulnerable_meta.identities[-1].identity_id
        denied = await call(vulnerable.instance_id, "/api/me", outside_identity)
        assert denied.status == "blocked" and denied.reason_code == "identity_out_of_scope"
        password = runtime.identity_credentials(vulnerable.instance_id, member)["password"]
        denied_login = await call(
            vulnerable.instance_id,
            "/api/login",
            None,
            "POST",
            {"password": password, "nested": {"token": password}},
        )
        assert denied_login.status == "blocked"
        login_request = next(
            event
            for event in events.items
            if event.run_id == contexts[vulnerable.instance_id].run_id
            and event.action_id == denied_login.action_id
            and event.type == "action_requested"
        )
        request_path = (
            runtime.state.instance_dir(vulnerable.instance_id)
            / "requests"
            / f"{login_request.request_artifact_id.hex}.json"
        )
        request_text = request_path.read_text(encoding="utf-8")
        assert request_path.stat().st_mode & 0o777 == 0o600
        assert password not in request_text
        assert json.loads(request_text)["json_body"] == {
            "password": "*",
            "nested": {"token": "*"},
        }
        evidence_path = (
            runtime.state.instance_dir(vulnerable.instance_id)
            / "evidence"
            / f"{own_evidence.hex}.json"
        )
        evidence = evidence_path.read_text()
        assert "password" not in evidence
        assert (
            runtime.identity_credentials(vulnerable.instance_id, member)["password"] not in evidence
        )

        old_credentials = runtime.identity_credentials(vulnerable.instance_id, member)
        assert (await runtime.reset_instance(vulnerable.instance_id)).state == "healthy"
        contexts[vulnerable.instance_id] = contexts[vulnerable.instance_id].model_copy(
            update={"range_generation": 1}
        )
        assert runtime.identity_credentials(vulnerable.instance_id, member) != old_credentials
        assert (
            await call(vulnerable.instance_id, f"/api/invoices/{own_invoice['id']}")
        ).http_status == 200
        assert (
            response_json(await call(vulnerable.instance_id, f"/api/invoices/{own_invoice['id']}"))[
                "status"
            ]
            == "paid"
        )
        assert (await runtime.instance_status(patched.instance_id)).state == "healthy"
    finally:
        for instance_id in instances:
            await runtime.destroy_instance(instance_id)
