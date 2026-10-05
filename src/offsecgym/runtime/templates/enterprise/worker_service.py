"""Separate deterministic execution worker; ordinary API calls advance its logical queue."""

import hmac
import json
import os
from pathlib import Path

from service_common import Handler, audit, connect, role_active, row, rows, serve, stamp

PATCHES = frozenset(json.loads(Path("/app/implementation.json").read_text()))


class WorkerHandler(Handler):
    def handle_api(self):
        if self.command != "POST" or self.path != "/internal/settle":
            return self.error(404, "not found")
        if not hmac.compare_digest(
            self.headers.get("X-Internal-Key", ""), os.environ["OFFSECGYM_INSTANCE_SECRET"]
        ):
            return self.error(403, "operation not permitted")
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE logical_clock SET tick=tick+1 WHERE singleton=true RETURNING tick"
                )
                current = cur.fetchone()["tick"]
                due = rows(
                    cur,
                    """SELECT j.id FROM jobs j WHERE j.status='QUEUED'
                                   AND j.due_tick<=%s ORDER BY j.due_tick,j.id FOR UPDATE""",
                    (current,),
                )
                for candidate in due:
                    job = row(cur, "SELECT * FROM jobs WHERE id=%s FOR UPDATE", (candidate["id"],))
                    change = row(
                        cur, "SELECT * FROM changes WHERE id=%s FOR UPDATE", (job["change_id"],)
                    )
                    valid = change["status"] == "QUEUED" and role_active(
                        cur, job["initiator_id"], change["project_id"], "operator"
                    )
                    if "B3-CANCELLED-JOB" in PATCHES and not valid:
                        cur.execute(
                            """UPDATE jobs SET status='CANCELLED',finished_tick=%s,
                                       updated_at=%s WHERE id=%s""",
                            (current, stamp(cur), job["id"]),
                        )
                        audit(
                            cur,
                            job["initiator_id"],
                            "job.cancelled",
                            job["id"],
                            change["project_id"],
                            "CANCELLED",
                            executor="worker",
                        )
                        continue
                    cur.execute(
                        """UPDATE jobs SET status='RUNNING',started_tick=%s,
                                   updated_at=%s WHERE id=%s""",
                        (current, stamp(cur), job["id"]),
                    )
                    audit(
                        cur,
                        job["initiator_id"],
                        "job.started",
                        job["id"],
                        change["project_id"],
                        "RUNNING",
                        executor="worker",
                    )
                    cur.execute(
                        """UPDATE environments SET revision=revision+1,updated_at=%s
                                   WHERE id=%s""",
                        (stamp(cur), job["environment_id"]),
                    )
                    cur.execute(
                        """UPDATE changes SET status='SUCCEEDED',revision=revision+1,
                                   updated_at=%s WHERE id=%s""",
                        (stamp(cur), change["id"]),
                    )
                    cur.execute(
                        """UPDATE jobs SET status='SUCCEEDED',finished_tick=%s,
                                   updated_at=%s WHERE id=%s""",
                        (current, stamp(cur), job["id"]),
                    )
                    audit(
                        cur,
                        job["initiator_id"],
                        "environment.revision_changed",
                        job["environment_id"],
                        change["project_id"],
                        "SUCCEEDED",
                        executor="worker",
                    )
                    audit(
                        cur,
                        job["initiator_id"],
                        "job.completed",
                        job["id"],
                        change["project_id"],
                        "SUCCEEDED",
                        executor="worker",
                    )
            conn.commit()
        return self.send_json(200, {"settled": True})


if __name__ == "__main__":
    serve(WorkerHandler)
