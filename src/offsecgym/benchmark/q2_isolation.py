"""Trusted, local-only network and storage isolation probes for synthetic ranges."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID, uuid4

from offsecgym.runtime.compose import run_command

OWNER_LABEL = "org.offsecgym.t11b_owner"
CANARY_PORT = 45678
BUSINESS_TABLES = ("workspaces", "accounts", "documents", "invoices", "tickets", "replies")

CANARY_SERVER = """
import socket
from pathlib import Path
counter = Path('/tmp/q2-canary-count')
with socket.socket() as server:
    server.bind(('0.0.0.0', 45678))
    server.listen(4)
    counter.write_text('0')
    Path('/tmp/q2-canary-ready').write_text('ready')
    while True:
        connection, _ = server.accept()
        with connection:
            connection.settimeout(3)
            if connection.recv(8) == b'ping':
                counter.write_text(str(int(counter.read_text()) + 1))
                connection.sendall(b'ack')
"""

CANARY_CONTROL = """
import socket
with socket.create_connection(('canary', 45678), timeout=2) as connection:
    connection.settimeout(2)
    connection.sendall(b'ping')
    assert connection.recv(8) == b'ack'
print('ack')
"""

RANGE_PROBE = """
import socket
import sys
try:
    with socket.create_connection((sys.argv[1], 45678), timeout=2):
        print('connected')
except OSError:
    print('blocked')
"""

STORAGE_SNAPSHOT = """
import hashlib
import json
import sqlite3
db = sqlite3.connect('/tmp/acmecloud.sqlite3')
tables = ('workspaces', 'accounts', 'documents', 'invoices', 'tickets', 'replies')
digests = {}
for table in tables:
    rows = db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
    raw = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    digests[table] = hashlib.sha256(raw).hexdigest()
exists = db.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='q2_isolation_marker'"
).fetchone()
marker = bool(exists and db.execute('SELECT 1 FROM q2_isolation_marker LIMIT 1').fetchone())
print(json.dumps({'business_sha256': digests, 'marker_present': marker}, sort_keys=True))
"""

INSERT_MARKER = """
import sqlite3
import sys
db = sqlite3.connect('/tmp/acmecloud.sqlite3')
db.execute('CREATE TABLE q2_isolation_marker (id TEXT PRIMARY KEY)')
db.execute('INSERT INTO q2_isolation_marker VALUES (?)', (sys.argv[1],))
db.commit()
"""


async def service_container(project_name: str, service: str, instance_id: UUID) -> str:
    ids = (
        await run_command(
            [
                "docker",
                "ps",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=20,
        )
    ).splitlines()
    if len(ids) != 1:
        raise ValueError("expected exactly one owned service container")
    details = json.loads(
        await run_command(
            ["docker", "inspect", ids[0], "--format", "{{json .}}"], timeout_seconds=20
        )
    )
    labels = details["Config"]["Labels"]
    if (
        labels.get("org.offsecgym.managed") != "true"
        or labels.get("org.offsecgym.instance_id") != str(instance_id)
        or labels.get("com.docker.compose.project") != project_name
        or labels.get("com.docker.compose.service") != service
        or set(details["NetworkSettings"]["Networks"]) != {f"{project_name}_range"}
    ):
        raise ValueError("service container ownership or attachment mismatch")
    return ids[0]


async def inspect_range_attachments(project_name: str, instance_id: UUID) -> str:
    """Return a digest only after exact owned-container and network checks pass."""
    network = f"{project_name}_range"
    network_info = json.loads(
        await run_command(
            ["docker", "network", "inspect", network, "--format", "{{json .}}"],
            timeout_seconds=20,
        )
    )
    labels = network_info.get("Labels") or {}
    if (
        network_info.get("Internal") is not True
        or labels.get("org.offsecgym.managed") != "true"
        or labels.get("org.offsecgym.instance_id") != str(instance_id)
    ):
        raise ValueError("range network ownership or internal configuration mismatch")
    ids = (
        await run_command(
            [
                "docker",
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.ID}}",
            ],
            timeout_seconds=20,
        )
    ).splitlines()
    if len(ids) != 2:
        raise ValueError("unexpected range container count")
    if set(network_info.get("Containers") or {}) != set(ids):
        raise ValueError("unexpected range network member")
    services = set()
    inventory = []
    for container_id in ids:
        details = json.loads(
            await run_command(
                ["docker", "inspect", container_id, "--format", "{{json .}}"],
                timeout_seconds=20,
            )
        )
        owner = details["Config"]["Labels"]
        service = owner.get("com.docker.compose.service")
        if (
            owner.get("com.docker.compose.project") != project_name
            or owner.get("org.offsecgym.managed") != "true"
            or owner.get("org.offsecgym.instance_id") != str(instance_id)
            or service not in {"saas", "gateway"}
        ):
            raise ValueError("unexpected container ownership or service")
        attachments = details["NetworkSettings"]["Networks"]
        ports = details["NetworkSettings"]["Ports"]
        if (
            not isinstance(ports, dict)
            or set(attachments) != {network}
            or any(value is not None for value in ports.values())
        ):
            raise ValueError("unexpected network attachment or published port")
        services.add(service)
        inventory.append({"service": service, "networks": sorted(attachments), "ports": ports})
    if services != {"saas", "gateway"}:
        raise ValueError("range services are incomplete")
    raw = json.dumps(sorted(inventory, key=lambda row: row["service"]), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


async def storage_snapshot(container_id: str) -> dict[str, object]:
    output = await run_command(
        ["docker", "exec", container_id, "python", "-c", STORAGE_SNAPSHOT],
        timeout_seconds=20,
    )
    payload = json.loads(output)
    if set(payload.get("business_sha256", {})) != set(BUSINESS_TABLES):
        raise ValueError("storage snapshot table inventory mismatch")
    if type(payload.get("marker_present")) is not bool:
        raise ValueError("storage marker status missing")
    return payload


async def insert_storage_marker(container_id: str) -> None:
    await run_command(
        ["docker", "exec", container_id, "python", "-c", INSERT_MARKER, uuid4().hex],
        timeout_seconds=20,
    )


async def _canary_container_labels(name: str) -> dict[str, str] | None:
    try:
        labels = json.loads(
            await run_command(
                ["docker", "inspect", name, "--format", "{{json .Config.Labels}}"],
                timeout_seconds=20,
            )
        )
    except Exception:
        return None
    return labels


async def _canary_network_labels(name: str) -> dict[str, str] | None:
    try:
        labels = json.loads(
            await run_command(
                ["docker", "network", "inspect", name, "--format", "{{json .Labels}}"],
                timeout_seconds=20,
            )
        )
    except Exception:
        return None
    return labels


async def local_canary_isolation(target_container: str, image_name: str) -> dict[str, object]:
    """Positive local control and negative probe from the actual target namespace."""
    owner = uuid4().hex
    network = f"offsecgym_t11b_canary_{owner}"
    server = f"offsecgym_t11b_server_{owner}"
    result: dict[str, object] = {
        "control_connected": False,
        "probe_executed": False,
        "range_to_canary_blocked": False,
        "connection_count_unchanged": False,
        "attachment_isolation": False,
        "canary_network_internal": False,
        "cleanup_ok": False,
    }
    try:
        await run_command(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                f"{OWNER_LABEL}={owner}",
                network,
            ],
            timeout_seconds=20,
        )
        await run_command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                server,
                "--network",
                network,
                "--network-alias",
                "canary",
                "--label",
                f"{OWNER_LABEL}={owner}",
                "--user",
                "10001:10001",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=8m",
                "--pids-limit",
                "32",
                "--memory",
                "64m",
                "--cpus",
                "0.2",
                image_name,
                "python",
                "-u",
                "-c",
                CANARY_SERVER,
            ],
            timeout_seconds=30,
        )
        for _ in range(30):
            ready = (
                await run_command(
                    [
                        "docker",
                        "exec",
                        server,
                        "python",
                        "-c",
                        "from pathlib import Path; print(Path('/tmp/q2-canary-ready').exists())",
                    ],
                    timeout_seconds=10,
                )
            ).strip()
            if ready == "True":
                break
            await asyncio.sleep(0.2)
        else:
            raise ValueError("canary listener did not become ready")
        server_info = json.loads(
            await run_command(
                ["docker", "inspect", server, "--format", "{{json .NetworkSettings.Networks}}"],
                timeout_seconds=20,
            )
        )
        if set(server_info) != {network}:
            raise ValueError("canary network attachment mismatch")
        target_info = json.loads(
            await run_command(
                [
                    "docker",
                    "inspect",
                    target_container,
                    "--format",
                    "{{json .NetworkSettings.Networks}}",
                ],
                timeout_seconds=20,
            )
        )
        result["attachment_isolation"] = network not in target_info and not (
            set(server_info) & set(target_info)
        )
        if not result["attachment_isolation"]:
            raise ValueError("canary and target network attachment overlap")
        canary_ip = server_info[network]["IPAddress"]
        control = await run_command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--label",
                f"{OWNER_LABEL}={owner}",
                "--user",
                "10001:10001",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                image_name,
                "python",
                "-c",
                CANARY_CONTROL,
            ],
            timeout_seconds=20,
        )
        result["control_connected"] = control.strip() == "ack"
        if not result["control_connected"]:
            raise ValueError("positive canary control failed")
        count_before = int(
            (
                await run_command(
                    ["docker", "exec", server, "cat", "/tmp/q2-canary-count"], timeout_seconds=10
                )
            ).strip()
        )
        if count_before != 1:
            raise ValueError("canary connection count after control is unexpected")
        probe = await run_command(
            ["docker", "exec", target_container, "python", "-c", RANGE_PROBE, canary_ip],
            timeout_seconds=8,
        )
        result["probe_executed"] = probe.strip() in {"blocked", "connected"}
        result["range_to_canary_blocked"] = probe.strip() == "blocked"
        count_after = int(
            (
                await run_command(
                    ["docker", "exec", server, "cat", "/tmp/q2-canary-count"], timeout_seconds=10
                )
            ).strip()
        )
        result["connection_count_unchanged"] = count_after == count_before
        result["canary_network_internal"] = (
            await run_command(
                ["docker", "network", "inspect", network, "--format", "{{.Internal}}"],
                timeout_seconds=20,
            )
        ).strip() == "true"
    finally:
        cleanup_ok = True
        server_labels = await _canary_container_labels(server)
        if server_labels is not None:
            if server_labels.get(OWNER_LABEL) == owner:
                try:
                    await run_command(["docker", "rm", "-f", server], timeout_seconds=20)
                except Exception:
                    cleanup_ok = False
            else:
                cleanup_ok = False
        network_labels = await _canary_network_labels(network)
        if network_labels is not None:
            if network_labels.get(OWNER_LABEL) == owner:
                try:
                    await run_command(["docker", "network", "rm", network], timeout_seconds=20)
                except Exception:
                    cleanup_ok = False
            else:
                cleanup_ok = False
        if await _canary_container_labels(server) is not None:
            cleanup_ok = False
        if await _canary_network_labels(network) is not None:
            cleanup_ok = False
        result["cleanup_ok"] = cleanup_ok
    return result
