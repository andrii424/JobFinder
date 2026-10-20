"""A vacancy that cannot be parsed must not kill the search it arrived in.

`LookupError: unknown encoding: windows-1252`, raised inside BeautifulSoup while
it resolved `&#150;`, travelled from one job description up through the source
generator and out of the scan loop. It ended 84 searches over seven days - the
requests had all returned 200, and the jobs behind them were simply never seen.
Twenty times the damage of the database locking that got noticed.

It could only ever happen in a build: a normal Python install carries every
codec, the packaged one carried the codecs nothing imports by name. So there are
two guards here, one for each half of that sentence.
"""

from __future__ import annotations

import pathlib

from scrapers import base
from scrapers.base import strip_html

SPEC = (pathlib.Path(__file__).resolve().parents[1] / "desktop" / "JobFinder.spec").read_text(encoding="utf-8")


def test_the_build_carries_the_codecs():
    """The fix proper: ship what the string lookup will ask for."""
    assert 'collect_submodules("encodings")' in SPEC, (
        "the packaged app is back to having only the codecs some module imports"
    )


def test_a_missing_codec_costs_the_entity_and_nothing_else(monkeypatch):
    """The belt. The parser raises what it raised in the wild - mid-document,
    on an entity - and the description still comes back.

    The codec cannot be taken away from inside a test: Python resolves it below
    `codecs.lookup`, in the decoder bs4 calls. So the failure is injected where
    it surfaced, at the parser call, with the exception it actually threw."""
    def no_codec(*_a, **_kw):
        raise LookupError("unknown encoding: windows-1252")

    monkeypatch.setattr(base, "BeautifulSoup", no_codec)

    text = strip_html("<p>Product Manager &#150; Berlin</p><ul><li>Hybrid</li></ul>")

    assert "Product Manager" in text
    assert "Berlin" in text
    assert "Hybrid" in text
    assert "<p>" not in text


def test_the_parser_is_still_the_one_that_normally_runs():
    """The fallback must not become the everyday path - it keeps no structure."""
    text = strip_html("<p>Alpha</p><p>Beta</p>")
    assert text.splitlines() == ["Alpha", "Beta"]
    assert strip_html("<p>Berlin &#150; hybrid</p>") == "Berlin – hybrid"


def test_nothing_it_is_given_can_raise():
    for markup in (None, "", "<p", "&#150;", "<script>x</script>", "&lt;p&gt;"):
        strip_html(markup)


def test_the_fallback_resolves_the_entity_the_parser_choked_on(monkeypatch):
    """Not even the dash is lost. `html.unescape` carries its own table for the
    128-159 range, so it needs no codec to produce the character the parser was
    reaching for one \u2014 which is why the fallback is worth having rather than
    merely better than a traceback."""
    def no_codec(*_a, **_kw):
        raise LookupError("unknown encoding: windows-1252")

    monkeypatch.setattr(base, "BeautifulSoup", no_codec)

    assert strip_html("<p>Berlin &#150; hybrid</p>") == "Berlin \u2013 hybrid"
