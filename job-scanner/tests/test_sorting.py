"""Sorting the job list.

Every order ends in a tie-breaker. Without one, rows with equal scores swap
places between page loads and the list looks broken rather than sorted.
"""

from __future__ import annotations

import pytest

from db import queries


async def _job(db, listing, *, title: str, company: str = "Acme",
               score: int | None = None) -> int:
    job_id = await db.insert_job(listing(title=title, company=company))
    if score is not None:
        await db.save_quick_score(job_id, score, "s", [])
    return job_id


async def test_highest_score_puts_unscored_jobs_last(db, listing):
    """SQLite sorts NULL below everything in DESC, which without a guard puts
    every unscored job at the top of a "highest score" list."""
    await _job(db, listing, title="No score")
    await _job(db, listing, title="Low", score=20)
    await _job(db, listing, title="High", score=90)

    jobs = await db.list_jobs(applied=0, order="score")

    assert [j["title"] for j in jobs] == ["High", "Low", "No score"]


async def test_title_sorts_alphabetically_ignoring_case(db, listing):
    """Case has to be ignored explicitly. Plain SQLite ordering is by byte, so
    every capitalised title sorts ahead of every lowercase one and a list of
    real job titles comes out in two separate alphabets."""
    for title in ("apple role", "Zebra role", "Middle role"):
        await _job(db, listing, title=title)

    jobs = await db.list_jobs(applied=0, order="title")

    assert [j["title"] for j in jobs] == ["apple role", "Middle role", "Zebra role"]


async def test_company_sorts_alphabetically(db, listing):
    await _job(db, listing, title="A", company="Zalando")
    await _job(db, listing, title="B", company="Adyen")

    jobs = await db.list_jobs(applied=0, order="company")

    assert [j["company"] for j in jobs] == ["Adyen", "Zalando"]


async def test_an_unknown_order_falls_back_instead_of_failing(db, listing):
    """A hand-edited URL must not produce a 500, and must never reach SQL."""
    await _job(db, listing, title="A")

    jobs = await db.list_jobs(applied=0, order="'; DROP TABLE jobs; --")

    assert len(jobs) == 1


async def test_every_order_has_a_tie_breaker():
    for name, clause in queries.SORTS.items():
        assert "id DESC" in clause or "id ASC" in clause, (
            f"{name} can reshuffle equal rows between page loads")


async def test_sorting_applies_to_every_list(db, listing):
    """Each tab reads from a different query; an order honoured by one and
    ignored by another is worse than no sorting at all.

    Separate jobs per list on purpose: the To review query excludes anything
    applied to, so reusing the same rows would silently test nothing."""
    # Scored below the To review threshold, or they would leak into that list
    # as well and the strong assertion would be testing the wrong rows.
    # Inserted in alphabetical order on purpose: newest-first would then give
    # the reverse, so a query that ignores the requested order cannot pass by
    # accidentally agreeing with it.
    saved_b = await _job(db, listing, title="Alpha saved", score=10)
    saved_a = await _job(db, listing, title="Beta saved", score=30)
    await db.toggle_favorite(saved_a)
    await db.toggle_favorite(saved_b)

    applied_b = await _job(db, listing, title="Alpha applied")
    applied_a = await _job(db, listing, title="Beta applied")
    await db.set_stage(applied_a, "applied")
    await db.set_stage(applied_b, "applied")

    await _job(db, listing, title="Alpha strong", score=80)
    await _job(db, listing, title="Beta strong", score=90)

    assert [j["title"] for j in await db.list_favorites(order="title")] == [
        "Alpha saved", "Beta saved"]
    assert [j["title"] for j in await db.list_applied(order="title")] == [
        "Alpha applied", "Beta applied"]
    strong = [j["title"] for j in await db.list_strong_jobs(80, order="title")]
    assert strong == ["Alpha strong", "Beta strong"]


async def test_the_applied_tab_can_still_sort_by_when_you_applied(db, listing):
    older = await _job(db, listing, title="Older")
    newer = await _job(db, listing, title="Newer")
    import db.queries as q

    q_now = q._utc_now_iso
    q._utc_now_iso = lambda: "2026-07-01T09:00:00+00:00"
    await db.set_stage(older, "applied")
    q._utc_now_iso = lambda: "2026-07-20T09:00:00+00:00"
    await db.set_stage(newer, "applied")
    q._utc_now_iso = q_now

    jobs = await db.list_applied(order="applied")

    assert [j["title"] for j in jobs] == ["Newer", "Older"]


def test_every_menu_label_is_backed_by_a_real_order():
    import main

    assert set(main.SORT_OPTIONS) <= set(queries.SORTS)


def test_recently_applied_is_offered_only_where_it_means_something():
    """On a list of jobs nobody applied to, that order sorts by a date most
    rows do not have."""
    import main

    assert "applied" in main.SORT_OPTIONS


# --------------------------------------------------------------------------- #
# The page has no sort control                                                 #
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded_client():
    """A TestClient over a database with one applied job and one not.

    Sync on purpose: TestClient drives its own event loop and deadlocks inside
    an async test."""
    import asyncio
    import sqlite3
    from contextlib import closing

    from fastapi.testclient import TestClient

    import main
    from config import settings
    from db.models import init_db

    asyncio.run(init_db())
    # `with connect()` alone commits but never closes; on Windows the open handle
    # then stops the next test's fixture from deleting the database file.
    with closing(sqlite3.connect(settings.db_path)) as raw, raw:
        raw.execute("DELETE FROM jobs")
        raw.execute(
            "INSERT INTO jobs (source, external_id, title, company, scraped_at, "
            "score, status, applied, applied_at, stage, stage_best) "
            "VALUES ('linkedin', 'sort-1', 'A role', 'Acme', '2026-07-01', 90, "
            "'scored', 1, '2026-07-01', 'hr_interview', 'hr_interview')")
        raw.execute(
            "INSERT INTO jobs (source, external_id, title, company, scraped_at, "
            "score, status, applied) "
            "VALUES ('linkedin', 'sort-2', 'B role', 'Beta', '2026-07-02', 70, "
            "'scored', 0)")
    with TestClient(main.app) as client:
        yield client


def test_the_page_offers_no_sort_menu(seeded_client):
    """It was removed: one dropdown beside a destructive button, for a list
    whose natural order is obvious per tab."""
    page = seeded_client.get("/", params={"view": "all"}).text

    assert "sort-select" not in page


def test_a_sort_in_the_url_changes_nothing(seeded_client):
    """The knob is gone, so a hand-edited URL must not reach the query layer."""
    plain = seeded_client.get("/", params={"view": "all"}).text
    tampered = seeded_client.get("/", params={"view": "all", "sort": "title"}).text

    import re

    titles = lambda page: re.findall(r'class="title" href="/jobs/\d+">([^<]+)</a>', page)
    assert titles(plain) == titles(tampered)


def test_each_tab_still_uses_the_order_that_suits_it():
    """Sorting did not go away, only the control. "Recently applied" would sort
    a list of jobs nobody applied to by a date most rows do not have."""
    import inspect

    import main

    source = inspect.getsource(main.index)
    assert '"applied" if view == "applied" else "recent"' in source


def test_the_stage_filter_survived_the_removal(seeded_client):
    """Only the sort menu was taken out."""
    page = seeded_client.get("/", params={"view": "applied"}).text

    assert "stage-chip" in page


def test_the_other_orders_are_still_available_to_the_query_layer():
    """Bringing the menu back should be a template change, not a rewrite."""
    assert {"score", "title", "company"} <= set(queries.SORTS)
