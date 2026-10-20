"""Bulk actions on ticked rows, and the search box. A bulk action that touches
the wrong rows destroys data; a search that drops matches hides them."""


def _post_bulk(**data):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        return client.post("/jobs/bulk", data=data)


async def test_bulk_delete_removes_only_the_ticked_jobs(db, add_job):
    a = await add_job(score=80, external_id="a")
    b = await add_job(score=80, external_id="b")
    keep = await add_job(score=80, external_id="keep")

    r = _post_bulk(action="delete", ids=[a, b])

    assert r.json()["done"] == 2
    assert await db.get_job(a) is None and await db.get_job(b) is None
    assert await db.get_job(keep) is not None


async def test_bulk_applied_does_not_restamp_jobs_already_applied(db, add_job):
    """Restamping would move an old application into today's goal."""
    old = await add_job(score=80, applied=1, external_id="old")
    new = await add_job(score=80, external_id="new")
    stamped = (await db.get_job(old))["applied_at"]

    r = _post_bulk(action="applied", ids=[old, new])

    assert r.json()["done"] == 1
    assert (await db.get_job(old))["applied_at"] == stamped
    assert (await db.get_job(new))["applied"] == 1


async def test_bulk_save_never_unsaves(db, add_job):
    """The row star toggles; a mixed selection must not flip saved jobs off."""
    saved = await add_job(score=80, favorite=1, external_id="saved")
    plain = await add_job(score=80, external_id="plain")

    _post_bulk(action="save", ids=[saved, plain])

    assert (await db.get_job(saved))["favorite"] == 1
    assert (await db.get_job(plain))["favorite"] == 1


async def test_bulk_rejects_an_unknown_stage_without_touching_anything(db, add_job):
    job = await add_job(score=80, external_id="x")

    r = _post_bulk(action="stage", stage="hired-by-magic", ids=[job])

    assert r.status_code == 400
    assert (await db.get_job(job))["applied"] == 0


async def test_search_matches_title_company_and_location(db, add_job):
    await add_job(score=80, title="React Developer", company="Acme", external_id="1")
    await add_job(score=80, title="Backend Engineer", company="Reactor GmbH", external_id="2")
    await add_job(score=80, title="Designer", company="Other", location="Reactville", external_id="3")
    await add_job(score=80, title="Designer II", company="Nope", external_id="4")

    hits = {j["external_id"] for j in await db.list_jobs(applied=0, q="react")}

    assert hits == {"1", "2", "3"}


async def test_search_reaches_jobs_past_the_on_screen_limit(db, add_job):
    """The page shows the newest rows only; search is how an old one is found."""
    await add_job(score=80, title="Old Needle", external_id="old")
    for i in range(5):
        await add_job(score=80, external_id=f"new{i}")

    assert [j["external_id"] for j in await db.list_jobs(applied=0, limit=3, q="needle")] == ["old"]


async def test_search_treats_percent_literally(db, add_job):
    await add_job(score=80, title="100% Remote Engineer", external_id="pct")
    await add_job(score=80, title="Remote Engineer", external_id="plain")

    hits = {j["external_id"] for j in await db.list_strong_jobs(60, q="%")}

    assert hits == {"pct"}


async def test_search_applies_to_every_tab(db, add_job):
    await add_job(score=80, title="Match Me", favorite=1, applied=1, external_id="m")
    await add_job(score=80, title="Other", favorite=1, applied=1, external_id="o")

    assert [j["external_id"] for j in await db.list_favorites(q="match")] == ["m"]
    assert [j["external_id"] for j in await db.list_applied(q="match")] == ["m"]
    assert await db.list_strong_jobs(60, q="match") == []   # applied → not "To review"
