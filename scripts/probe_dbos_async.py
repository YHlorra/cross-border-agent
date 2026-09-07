"""Minimal async-workflow repro: does DBOS execute an async workflow started
from an async context, on this box? Writes /async_ok marker when it does."""
from __future__ import annotations

import asyncio
import os
import sys

from dbos import DBOS

_URL = os.environ.get("PG_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or \
    "postgresql://postgres@127.0.0.1:5433/crossborder"
DBOS(config={"name": "dbos-async-probe", "system_database_url": _URL, "app_database_url": _URL})


@DBOS.step()
async def step_touch() -> None:
    import psycopg

    with psycopg.connect(_URL) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dbos_async_probe (
               id text primary key, phase text not null)"""
        )
        conn.execute(
            "INSERT INTO dbos_async_probe (id, phase) VALUES ('t1','step') "
            "ON CONFLICT (id) DO UPDATE SET phase = EXCLUDED.phase"
        )
        conn.commit()


@DBOS.workflow()
async def wf() -> str:
    await step_touch()
    await asyncio.sleep(2)
    import psycopg

    with psycopg.connect(_URL) as conn:
        conn.execute(
            "INSERT INTO dbos_async_probe (id, phase) VALUES ('t1','done') "
            "ON CONFLICT (id) DO UPDATE SET phase = EXCLUDED.phase"
        )
        conn.commit()
    return "done"


async def main() -> None:
    DBOS.launch()
    handle = DBOS.start_workflow(wf)
    try:
        result = await asyncio.wait_for(handle, timeout=25)
        print("RESULT=" + str(result))
    except asyncio.TimeoutError:
        print("TIMEOUT waiting workflow")


if __name__ == "__main__":
    asyncio.run(main())
