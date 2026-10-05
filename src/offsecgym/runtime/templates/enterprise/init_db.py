"""One-shot deterministic schema and fixture initializer."""

import json
import time
from pathlib import Path

import pg_client as psycopg
from service_common import connect


def main():
    for attempt in range(40):
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    for statement in Path("/app/schema.sql").read_text().split(";"):
                        if statement.strip():
                            cur.execute(statement)
                    fixture = json.loads(Path("/app/fixture.json").read_text())
                    cur.execute(
                        "INSERT INTO logical_clock(singleton,tick,origin) VALUES (true,0,%s)",
                        (fixture["clock_origin"],),
                    )
                    tables = (
                        ("organizations", ("id", "name")),
                        ("projects", ("id", "organization_id", "name", "created_at")),
                        ("users", ("id", "username", "display_name", "organization_id", "team")),
                        ("environments", ("id", "project_id", "name", "revision", "updated_at")),
                        (
                            "role_assignments",
                            ("id", "user_id", "project_id", "role", "active", "updated_at"),
                        ),
                        (
                            "access_requests",
                            (
                                "id",
                                "project_id",
                                "requester_id",
                                "target_user_id",
                                "requested_role",
                                "status",
                                "approver_id",
                                "revision",
                                "created_at",
                                "updated_at",
                            ),
                        ),
                        (
                            "changes",
                            (
                                "id",
                                "project_id",
                                "environment_id",
                                "requester_id",
                                "status",
                                "approver_id",
                                "revision",
                                "summary",
                                "created_at",
                                "updated_at",
                            ),
                        ),
                        (
                            "jobs",
                            (
                                "id",
                                "change_id",
                                "environment_id",
                                "initiator_id",
                                "status",
                                "queued_tick",
                                "due_tick",
                                "queued_change_revision",
                                "queued_authorized",
                                "started_tick",
                                "finished_tick",
                                "created_at",
                                "updated_at",
                            ),
                        ),
                    )
                    for table, columns in tables:
                        placeholders = ",".join("%s" for _ in columns)
                        query = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
                        for row in fixture[table]:
                            cur.execute(query, tuple(row.get(column) for column in columns))
                    for item in fixture["access_requests"] + fixture["changes"]:
                        if item["status"] not in ("PENDING", "PENDING_APPROVAL", "APPROVED"):
                            cur.execute(
                                """INSERT INTO audit_events(
                                           tick,actor_id,executor,action,object_id,project_id,status,created_at)
                                           VALUES (0,%s,'fixture',%s,%s,%s,%s,%s)""",
                                (
                                    item["requester_id"],
                                    "historical.import",
                                    item["id"],
                                    item["project_id"],
                                    item["status"],
                                    item["updated_at"],
                                ),
                            )
            return
        except psycopg.OperationalError:
            if attempt == 39:
                raise
            time.sleep(0.25)


if __name__ == "__main__":
    main()
