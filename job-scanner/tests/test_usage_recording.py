"""What the models actually charged for.

Every answer carries a usage block and the app never stored one, so every cost
figure it produced was arithmetic over measured prompts rather than a fact. That
arithmetic was wrong twice in a week: once about the published price list, once
about whether prefix caching was working at all.
"""

from __future__ import annotations

from db import usage


class _Usage:
    def __init__(self, prompt, completion, cached=None, details=None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        if cached is not None:
            self.prompt_cache_hit_tokens = cached
        if details is not None:
            self.prompt_tokens_details = details


# --------------------------------------------------------------------------- #
# Reading the number every provider names differently                          #
# --------------------------------------------------------------------------- #

def test_deepseek_reports_the_cache_hit_its_own_way():
    assert usage._cached_from(_Usage(3000, 40, cached=2560)) == 2560


def test_openai_reports_it_somewhere_else_entirely():
    class _Details:
        cached_tokens = 2048

    assert usage._cached_from(_Usage(3000, 40, details=_Details())) == 2048


def test_a_dict_shaped_details_block_is_read_too():
    assert usage._cached_from(_Usage(3000, 40, details={"cached_tokens": 128})) == 128


def test_no_cache_information_reads_as_zero_not_as_a_crash():
    """Getting this wrong looks like 'caching stopped working', which is a
    conclusion someone would act on."""
    assert usage._cached_from(_Usage(3000, 40)) == 0


# --------------------------------------------------------------------------- #
# Storing it                                                                   #
# --------------------------------------------------------------------------- #

async def test_a_call_is_recorded_with_its_four_numbers(db):
    await usage.record("quick", "deepseek-v4-flash", _Usage(3362, 32, cached=2594))

    rows = await usage.totals(30)
    assert len(rows) == 1
    assert rows[0]["stage"] == "quick" and rows[0]["model"] == "deepseek-v4-flash"
    assert rows[0]["prompt"] == 3362 and rows[0]["cached"] == 2594
    assert rows[0]["completion"] == 32


async def test_calls_are_grouped_by_stage_and_model(db):
    await usage.record("quick", "flash", _Usage(1000, 10, cached=800))
    await usage.record("quick", "flash", _Usage(1000, 10, cached=900))
    await usage.record("advanced", "gpt-4.1", _Usage(7000, 700, cached=6272))

    rows = {r["stage"]: r for r in await usage.totals(30)}
    assert rows["quick"]["calls"] == 2 and rows["quick"]["prompt"] == 2000
    assert rows["advanced"]["calls"] == 1


async def test_bookkeeping_never_costs_a_scored_job(db, monkeypatch):
    """It runs inside the scoring path. A failure to write a statistic must not
    take the answer down with it."""
    async def _explode(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(usage.conn, "connect", _explode)

    await usage.record("quick", "flash", _Usage(10, 1))     # must not raise


async def test_a_missing_usage_block_is_not_an_error(db):
    await usage.record("quick", "flash", None)

    assert await usage.totals(30) == []


# --------------------------------------------------------------------------- #
# Wired into the calls that cost money                                         #
# --------------------------------------------------------------------------- #

def test_every_paid_call_site_records_what_it_spent():
    """Structural: the four scoring calls and the writer. A new call site that
    forgets this leaves a hole in the only real cost figure the app has."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    m = (root / "matcher" / "matcher.py").read_text(encoding="utf-8")
    g = (root / "matcher" / "generate.py").read_text(encoding="utf-8")

    assert m.count("await usage.record(") == 4, "title ×2, quick, advanced"
    assert 'usage.record("advanced", self.advanced_model' in m, (
        "the deep check must record the model it actually used"
    )
    assert 'usage.record("write"' in g


async def test_the_statistics_page_shows_it(db):
    from fastapi.testclient import TestClient

    import main

    await usage.record("quick", "deepseek-v4-flash", _Usage(3362, 32, cached=2594))
    with TestClient(main.app) as client:
        page = client.get("/statistic").text

    assert "Model usage" in page
    assert "deepseek-v4-flash" in page
    assert "77%" in page, "the cached share is the number that matters"
