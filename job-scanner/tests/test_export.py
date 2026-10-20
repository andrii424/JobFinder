"""CSV export of a Jobs tab. An export that quietly drops rows hides real
matches the user believes they have saved elsewhere."""

import csv
import io


def _export(view="strong"):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        r = client.get(f"/export.csv?view={view}")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))


async def test_export_is_not_capped_by_the_on_screen_limit(db, add_job):
    """The page shows 150 rows; the export must carry every one."""
    for i in range(160):
        await add_job(score=90, external_id=f"j{i}")

    assert len(_export("strong")) == 160


async def test_strong_export_is_best_score_first_and_skips_weak_jobs(db, add_job):
    # Best one inserted FIRST: the page's newest-first order would put it last.
    await add_job(score=95, external_id="top")
    await add_job(score=70, external_id="mid")
    await add_job(score=10, external_id="weak")

    scores = [int(row["score"]) for row in _export("strong")]

    assert scores == [95, 70]


async def test_formula_like_titles_are_neutralised(db, add_job):
    await add_job(score=90, title="=HYPERLINK(\"http://evil\")", external_id="x")

    row = _export("strong")[0]

    assert row["title"].startswith("'=")
