"""The filter panel on the Jobs page. A filter that matches too little hides
real jobs without any error, so each one is checked on its own."""

from datetime import datetime, timedelta, timezone

from db import conn
from db.queries import Narrow


async def _set(job_id, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    async with conn.connect() as db:
        await db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [*cols.values(), job_id])
        await db.commit()


def _ids(rows):
    return {r["external_id"] for r in rows}


async def test_no_filter_narrows_nothing(db, add_job):
    await add_job(score=80, external_id="a")
    await add_job(score=None, external_id="b")

    assert not Narrow().active()
    assert _ids(await db.list_jobs(applied=0, narrow=Narrow())) == {"a", "b"}


async def test_status_filters(db, add_job):
    await add_job(score=80, favorite=1, external_id="saved")
    await add_job(score=80, pinned=1, external_id="pinned")
    await add_job(external_id="unscored")
    checked = await add_job(score=80, external_id="checked")
    await _set(checked, advanced=1)

    for status in ("saved", "pinned", "unscored", "checked"):
        rows = await db.list_jobs(applied=0, narrow=Narrow(status=status))
        assert _ids(rows) == {status}, status


async def test_company_is_exact_and_ignores_case(db, add_job):
    """"Acme" must not pull in "Acme Labs" - the panel lists them separately."""
    await add_job(score=80, company="Acme", external_id="acme")
    await add_job(score=80, company="ACME", title="Second role", external_id="acme2")
    await add_job(score=80, company="Acme Labs", external_id="labs")

    assert _ids(await db.list_jobs(applied=0, narrow=Narrow(company="acme"))) == {"acme", "acme2"}


async def test_source_filter(db, add_job):
    await add_job(score=80, source="linkedin", external_id="li")
    await add_job(score=80, source="remotive", external_id="rem")

    assert _ids(await db.list_jobs(applied=0, narrow=Narrow(source="remotive"))) == {"rem"}


async def test_min_score_keeps_the_boundary(db, add_job):
    await add_job(score=79, external_id="79")
    await add_job(score=80, external_id="80")
    await add_job(external_id="none")

    assert _ids(await db.list_jobs(applied=0, narrow=Narrow(min_score=80))) == {"80"}


async def test_date_filter_uses_found_date_but_applied_date_on_applied(db, add_job):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=20)).isoformat(timespec="seconds")
    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")

    found_long_ago = await add_job(score=80, external_id="found-old")
    await _set(found_long_ago, scraped_at=old)
    await add_job(score=80, external_id="found-new")
    # Found long ago but applied today: on Applied it counts as recent.
    applied_today = await add_job(score=80, applied=1, external_id="applied-now")
    await _set(applied_today, scraped_at=old)
    applied_old = await add_job(score=80, applied=1, external_id="applied-old")
    await _set(applied_old, applied_at=old)

    assert _ids(await db.list_jobs(applied=0, narrow=Narrow(since=cutoff))) == {"found-new"}
    assert _ids(await db.list_applied(narrow=Narrow(since=cutoff, date_col="applied_at"))) == {"applied-now"}


async def test_filters_apply_to_every_tab(db, add_job):
    await add_job(score=90, company="Target", favorite=1, external_id="t")
    await add_job(score=90, company="Other", favorite=1, external_id="o")
    n = Narrow(company="Target")

    assert _ids(await db.list_strong_jobs(60, narrow=n)) == {"t"}
    assert _ids(await db.list_favorites(narrow=n)) == {"t"}


async def test_bad_values_from_the_url_are_dropped_not_applied():
    """A stale bookmark must open the page, not filter on garbage."""
    import main

    n = main._narrow_from("all", status="nonsense", company="", source="",
                          since="99y", min_score="abc")
    assert not n.active()
    assert main._narrow_from("applied", "", "", "", "7d", "").date_col == "applied_at"


async def test_export_carries_the_filters(db, add_job):
    from fastapi.testclient import TestClient

    import main

    await add_job(score=90, company="Target", external_id="t")
    await add_job(score=90, company="Other", external_id="o")

    with TestClient(main.app) as client:
        csv_text = client.get("/export.csv?view=strong&company=Target").text

    assert "Target" in csv_text and "Other" not in csv_text


async def test_page_shows_a_chip_for_each_filter(db, add_job):
    from fastapi.testclient import TestClient

    import main

    await add_job(score=90, company="Target", external_id="t")
    with TestClient(main.app) as client:
        html = client.get("/?view=all&company=Target&min=80").text

    assert 'data-clear="company"' in html and 'data-clear="min"' in html
    assert "Score 80+" in html
