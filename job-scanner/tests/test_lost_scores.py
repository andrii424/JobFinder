"""A zero the model never decided on.

854 jobs in the live database carry score 0 with no summary and no flags. They
were not judged badly - the scorer returned an empty answer and the code wrote
the default. Below every threshold, so in no list; scored, so never retried.
"""

from __future__ import annotations



def _response(payload: str):
    class _Msg:
        content = payload
        finish_reason = "stop"

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    return type("R", (), {"choices": [_Choice()]})()


class _Client:
    def __init__(self, payload):
        self._payload = payload
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kwargs):
        return _response(self._payload)


async def _score(monkeypatch, payload):
    from matcher import matcher as mm

    monkeypatch.setattr(mm, "AsyncOpenAI", lambda *a, **k: _Client(payload))
    m = mm.AIMatcher(model="x", api_key="sk-test")
    return await m.quick_score({"id": 1, "title": "PM", "description": "text"}, "instructions")


# --------------------------------------------------------------------------- #
# What counts as a verdict                                                     #
# --------------------------------------------------------------------------- #

async def test_an_empty_answer_is_not_a_score_of_zero(monkeypatch):
    """The exact shape the 854 arrived in: valid JSON, nothing in it."""
    result = await _score(monkeypatch, "{}")

    assert result.failed is True
    assert "no score" in result.summary.lower()


async def test_an_answer_missing_the_score_field_is_not_a_verdict(monkeypatch):
    result = await _score(monkeypatch, '{"summary": "Looks plausible", "red_flags": []}')

    assert result.failed is True


async def test_unreadable_json_is_not_a_verdict(monkeypatch):
    result = await _score(monkeypatch, "sorry, I cannot do that")

    assert result.failed is True


async def test_a_real_zero_is_still_a_real_zero(monkeypatch):
    """The model is allowed to say a job is worthless - with a reason."""
    result = await _score(monkeypatch,
                          '{"score": 0, "summary": "Retail role, unrelated.", "red_flags": []}')

    assert result.failed is False
    assert result.score == 0
    assert result.summary == "Retail role, unrelated."


async def test_a_good_answer_passes_through(monkeypatch):
    result = await _score(monkeypatch, '{"score": 78, "summary": "Strong overlap.", "red_flags": []}')

    assert result.failed is False and result.score == 78


# --------------------------------------------------------------------------- #
# And nothing writes one                                                       #
# --------------------------------------------------------------------------- #

def test_both_scan_paths_refuse_to_store_a_non_verdict():
    """Structural, because the two paths are 300 lines apart and the extension
    path is the one that used to drift. A `save_quick_score` that is not
    guarded by `result.failed` is the bug coming back."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "scheduler" / "jobs.py").read_text(encoding="utf-8")
    # Every place that stores a scorer result, with the 12 lines before it.
    for m in re.finditer(r"save_quick_score\(\s*\n?\s*j(id|ob\[)", src):
        before = src[max(0, m.start() - 600):m.start()]
        if "quick_score(" not in before:
            continue                      # a title-screen verdict, not a scorer one
        assert "result.failed" in before, (
            "a scorer result is stored without checking whether it is a verdict"
        )


async def test_the_repair_script_finds_exactly_the_lost_ones(db, add_job):
    """It must not sweep up jobs the model genuinely scored zero."""

    from db import conn

    lost = await add_job(score=0, external_id="lost")
    judged = await add_job(score=0, external_id="judged")
    fine = await add_job(score=71, external_id="fine")
    async with conn.connect() as c:
        await c.execute("UPDATE jobs SET match_summary='No summary.', red_flags='[]' "
                        "WHERE id=?", (lost,))
        await c.execute("UPDATE jobs SET match_summary='Retail role.', red_flags='[]' "
                        "WHERE id=?", (judged,))
        await c.commit()

    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rescore_lost.py"
    spec = importlib.util.spec_from_file_location("rescore_lost", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async with conn.connect() as c:
        ids = [r[0] for r in await (await c.execute(mod.LOST)).fetchall()]

    assert ids == [lost], f"expected only the lost one, got {ids}"
    assert judged not in ids and fine not in ids
