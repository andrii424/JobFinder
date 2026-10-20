"""Whether a scan can write while a page is being read.

A search can be lost to `database is locked`: fetched, scored, paid for, and
never stored. The scan is writing while the page polls /api/scan-status. With
SQLite's default journal those two block each other, and the loser waits five
seconds and then raises.
"""

from __future__ import annotations

import asyncio

from db import conn


async def test_the_database_is_in_wal_mode(db):
    """WAL removes the collision instead of lengthening the wait: readers and
    one writer proceed at the same time."""
    async with conn.connect() as c:
        mode = (await (await c.execute("PRAGMA journal_mode")).fetchone())[0]

    assert str(mode).lower() == "wal"


async def test_every_connection_waits_instead_of_giving_up_at_once(db):
    """The five-second default is what the lost search ran into."""
    async with conn.connect() as c:
        ms = (await (await c.execute("PRAGMA busy_timeout")).fetchone())[0]

    assert int(ms) >= 10_000


async def test_reading_works_while_a_write_is_open(db, add_job):
    """The exact shape of the failure: a long write in progress, a read
    arriving in the middle of it. Under the old journal this raised."""
    await add_job(score=90)

    async with conn.connect() as writer:
        await writer.execute("BEGIN IMMEDIATE")
        await writer.execute("UPDATE jobs SET score = 91")

        # …and now the page asks what is going on, as it does every few seconds.
        rows = await db.list_jobs()
        assert len(rows) == 1

        await writer.commit()

    assert (await db.list_jobs())[0]["score"] == 91


async def test_two_writers_queue_rather_than_one_dying(db, add_job):
    """Writer against writer is the case WAL does not solve, and the timeout
    does: the second one waits its turn instead of raising."""
    await add_job(score=50)

    async def slow_writer():
        async with conn.connect() as c:
            await c.execute("BEGIN IMMEDIATE")
            await c.execute("UPDATE jobs SET score = 60")
            await asyncio.sleep(0.4)
            await c.commit()

    async def other_writer():
        await asyncio.sleep(0.1)
        async with conn.connect() as c:
            await c.execute("UPDATE jobs SET status = 'new'")
            await c.commit()

    await asyncio.gather(slow_writer(), other_writer())   # must not raise


def test_nothing_opens_the_database_the_old_way():
    """One helper, or the fix only covers the call sites someone remembered."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "db").glob("*.py"):
        if path.name == "conn.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "aiosqlite.connect(" in line:
                offenders.append(f"{path.name}:{i}")

    assert not offenders, f"opened without the timeout at {offenders}"
