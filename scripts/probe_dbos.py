"""DBOS Windows feasibility probe — durable-agent-tasks tasks 0.1.

Verifies the load-bearing assumptions of durable execution on this box:
  enqueue  configure DBOS, launch, start the probe workflow (writes
           "started", then durably waits on message topic "go"), print
           WORKFLOW_ID + PID, and STAY ALIVE so it can be hard-killed.
  resume   launch DBOS again (recovery sweep), send "approve" to the
           workflow, poll for the "approve" final marker.
  verify   print current markers + workflow status.

Requires PG_TEST_DATABASE_URL (or DATABASE_URL) pointing at Postgres.
"""
from __future__ import annotations

import os
import sys
import time

from dbos import DBOS


def _url() -> str:
    url = os.environ.get("PG_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("need PG_TEST_DATABASE_URL or DATABASE_URL")
        sys.exit(2)
    return url


_DB_URL = _url()
DBOS(config={"name": "crossborder-probe", "system_database_url": _DB_URL, "app_database_url": _DB_URL})


@DBOS.step()
def _step_write(marker: str, phase: str) -> None:
    import psycopg

    with psycopg.connect(_DB_URL) as conn:
        conn.execute(
            """INSERT INTO dbos_probe_markers (marker, phase)
               VALUES (%s, %s)
               ON CONFLICT (marker) DO UPDATE SET phase = EXCLUDED.phase""",
            (marker, phase),
        )
        conn.commit()


@DBOS.workflow()
def probe_workflow(marker: str) -> str:
    _step_write(marker, "started")
    msg = DBOS.recv("go", timeout_seconds=90)
    decision = msg if isinstance(msg, str) else "timeout"
    _step_write(marker, decision)
    return decision


def _ensure_table() -> None:
    import psycopg

    with psycopg.connect(_DB_URL) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dbos_probe_markers (
               marker TEXT PRIMARY KEY, phase TEXT NOT NULL)"""
        )
        conn.commit()


def _markers() -> dict[str, str]:
    import psycopg

    with psycopg.connect(_DB_URL) as conn:
        rows = conn.execute(
            "SELECT marker, phase FROM dbos_probe_markers"
        ).fetchall()
    return {marker: phase for marker, phase in rows}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    _ensure_table()

    if mode == "enqueue":
        DBOS.launch()
        handle = DBOS.start_workflow(probe_workflow, "probe-1")
        print("WORKFLOW_ID=" + str(handle.workflow_id), flush=True)
        print("PID=" + str(os.getpid()), flush=True)
        time.sleep(3)
        print("markers(now)=" + str(_markers()), flush=True)
        # stay alive so the caller can hard-kill (taskkill /F /PID <pid>)
        time.sleep(600)
    elif mode == "resume":
        DBOS.launch()
        time.sleep(5)  # recovery sweep
        wf_id = sys.argv[2]
        DBOS.send(wf_id, "approve", "go")
        for _ in range(30):
            time.sleep(1)
            if _markers().get("probe-1") == "approve":
                print("RESUME_OK final=approve")
                return
        print("RESUME_TIMEOUT markers=" + str(_markers()))
        sys.exit(1)
    elif mode == "verify":
        wf_id = sys.argv[2] if len(sys.argv) > 2 else ""
        handle = DBOS.retrieveWorkflow(wf_id) if wf_id else None
        status = handle.get_workflow_status() if handle else "n/a"
        print("markers=" + str(_markers()) + " status=" + str(status))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
