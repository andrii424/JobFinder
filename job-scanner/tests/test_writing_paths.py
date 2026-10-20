"""The parts of the app a person presses and then waits for.

Every bug guarded here shipped, and each one looked like the app being broken
rather than the app being slow or misconfigured: an internal error on the cover
letter, a minute of nothing on HR questions, a button that did nothing at all,
and a letterhead greeting the user with "Your Name".
"""

from __future__ import annotations


import letter_template as lt
import providers


# --------------------------------------------------------------------------- #
# How long writing is allowed to take                                          #
# --------------------------------------------------------------------------- #

def test_writing_gets_longer_than_scoring():
    """Scoring wants a number quickly; writing is prose someone waits for.

    generate.py always used 60 seconds. When one shared `timeout` began being
    passed to every client, the scorer's 18 replaced it - and with two retries
    on top, HR questions spent a minute failing."""
    for key in providers.PROVIDERS:
        row = {"ai_provider": key, "writer_provider": key}
        scoring = providers.timeout_for(row, providers.SCAN)
        writing = providers.timeout_for(row, providers.WRITE)
        assert writing > scoring, f"{key}: writing must get more time than scoring"
        assert writing >= 60, f"{key}: {writing}s is less than generate.py's own floor"


def test_a_thinking_provider_gets_even_longer_to_write():
    """It thinks before it writes, and writing is the longest thing it does."""
    assert providers.timeout_for({"writer_provider": "deepseek"}, providers.WRITE) >= 120


def test_the_timeout_reaching_the_client_is_the_roles_own():
    """Reading it off the provider directly is what dropped writing to 18s."""
    import main

    row = {"ai_provider": "openai", "writer_provider": "deepseek",
           "openai_api_key": "sk", "deepseek_api_key": "ds"}

    assert main._ai(row, providers.SCAN)["timeout"] == providers.get("openai").timeout
    assert main._ai(row, providers.WRITE)["timeout"] == providers.get("deepseek").write_timeout


# --------------------------------------------------------------------------- #
# The letterhead                                                               #
# --------------------------------------------------------------------------- #

_CV = """
<div class="cv-name">ALEX MORGAN</div>
<div class="cv-subtitle"><strong>Product Operations Manager</strong> | Tallinn, Estonia | EU Remote</div>
<div class="cv-contact">+34 600 123 456 | <a href="#">linkedin.com/in/alex-morgan</a> | alex.morgan@example.com</div>
<h2>Professional Summary</h2><p>Text.</p>
"""


def test_the_letterhead_is_filled_in_from_the_cv():
    """It shipped saying "Your Name" and "you@example.com", which reads as a
    broken document rather than an invitation to edit one."""
    head = lt.default_template(_CV)

    assert "Alex Morgan" in head          # and not the CV's shouted caps
    assert "ALEX MORGAN" not in head
    assert "Tallinn, Estonia" in head
    assert "alex.morgan@example.com" in head
    assert "+34 600 123 456" in head
    assert "linkedin.com/in/alex-morgan" in head
    assert "Your Name" not in head and "you@example.com" not in head


def test_the_job_title_from_the_cv_stays_off_the_letter():
    """A letterhead carries where to reach you, not what you call yourself."""
    assert "Product Operations Manager" not in lt.default_template(_CV)


def test_the_placeholders_survive_the_new_sender_block():
    """The rest of the letterhead still has to work as a template."""
    head = lt.default_template(_CV)

    for token in ("{{date}}", "{{company}}", "{{company_address}}",
                  "{{job_title}}", "{{body}}"):
        assert token in head, token


def test_no_cv_still_gets_a_usable_letterhead():
    """Markers are right when there is nothing to fill them from - they tell
    the user what to replace."""
    for empty in ("", "   ", None):
        assert lt.default_template(empty) == lt.DEFAULT_TEMPLATE


def test_a_cv_without_a_name_falls_back_rather_than_half_filling():
    """A letterhead with a city and no name is worse than an honest template."""
    assert lt.default_template("<p>just some text</p>") == lt.DEFAULT_TEMPLATE


def test_a_sender_with_odd_characters_cannot_break_the_document():
    cv = '<div class="cv-name">Smith &amp; Sons</div><div class="cv-contact">a@b.com</div>'

    assert "Smith &amp; Sons" in lt.default_template(cv)


# --------------------------------------------------------------------------- #
# Find people                                                                  #
# --------------------------------------------------------------------------- #

async def test_find_people_opens_the_users_own_browser(db, monkeypatch):
    """The page called `window.open` after an await, which the app's native
    webview ignores - the button did nothing, three presses in a row. And it
    could never have worked: the app's own window has no Chrome extension in
    it, and the extension is the entire point of the button."""
    from fastapi.testclient import TestClient

    import main
    from db import queries
    from scrapers.base import JobListing

    await queries.insert_job(JobListing(
        source="linkedin", external_id="p1", title="Product Ops",
        company="Vondel Venture", location="Amsterdam", url="https://e.test/p1",
        description="x"))
    import aiosqlite
    from config import settings
    async with aiosqlite.connect(settings.db_path) as conn:
        job_id = (await (await conn.execute("select id from jobs limit 1")).fetchone())[0]

    opened: list[str] = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    with TestClient(main.app) as client:
        body = client.post(f"/jobs/{job_id}/people/start").json()

    assert body["opened"] is True, "the app did not open a browser"
    assert opened and "linkedin.com/search/results/people" in opened[0]
    assert "Vondel" in opened[0], "the search must be for this company"


async def test_find_people_says_so_when_no_browser_could_be_opened(db, monkeypatch):
    """Reporting success while nothing happened is the original bug in a new
    costume."""
    from fastapi.testclient import TestClient

    import main
    from db import queries
    from scrapers.base import JobListing

    await queries.insert_job(JobListing(
        source="linkedin", external_id="p2", title="Ops", company="Acme",
        location="Berlin", url="https://e.test/p2", description="x"))
    import aiosqlite
    from config import settings
    async with aiosqlite.connect(settings.db_path) as conn:
        job_id = (await (await conn.execute("select id from jobs limit 1")).fetchone())[0]

    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)

    with TestClient(main.app) as client:
        body = client.post(f"/jobs/{job_id}/people/start").json()

    assert body["opened"] is False
    assert body["url"], "the address must still be handed back so it can be shown"


def test_the_page_no_longer_tries_to_open_the_window_itself():
    """Structural: the call the webview ignores must not come back."""
    import pathlib

    page = (pathlib.Path(__file__).resolve().parents[1]
            / "templates" / "job_detail.html").read_text(encoding="utf-8")
    people = page[page.index("async function findPeople"):]
    people = people[:people.index("async function draft")]

    assert "window.open" not in people


async def test_the_pdf_and_the_editor_both_use_the_cv_filled_letterhead(db):
    """Building it correctly is worthless if the callers keep the old default.
    Mutation testing caught exactly that: reverting the two call sites broke
    nothing, because only the function itself was covered."""
    from db import queries

    await queries.save_cv_html(_CV, set_original=True)

    # What the cover-letter PDF prints on.
    printed = await queries.get_letter_template()
    assert "Alex Morgan" in printed
    assert "Your Name" not in printed

    # What Profile → Letter template shows for editing.
    from fastapi.testclient import TestClient

    import main
    with TestClient(main.app) as client:
        page = client.get("/profile?tab=letter").text
    assert "Alex Morgan" in page
    assert "you@example.com" not in page


async def test_an_edited_letterhead_is_never_overwritten_by_the_cv(db):
    """Once it is the user's document, the CV stops having an opinion."""
    from db import queries

    await queries.save_cv_html(_CV, set_original=True)
    await queries.save_letter_template("<div class='sender'>My own header</div>")

    assert await queries.get_letter_template() == "<div class='sender'>My own header</div>"


# --------------------------------------------------------------------------- #
# A frozen app whose bundle changes underneath it                              #
# --------------------------------------------------------------------------- #

def test_the_pages_do_not_read_the_bundle_at_request_time():
    """Modules imported inside a route handler are read from the app bundle at
    the moment the button is pressed. That is fine until the bundle is replaced
    while the app runs - which is exactly what the auto-updater does, swap the
    .app and relaunch.

    A real one: `_profile_context` lazily imported `letter_template`, so opening
    Profile after an update raised `zlib.error: unknown compression method` and
    the page 500'd. Modules loaded at startup are already in memory and cannot
    fail this way, so the app's own modules belong at the top of the file."""
    import ast
    import pathlib

    OURS = {"cv_content", "letter_template", "pipeline", "providers",
            "extension_health", "config", "db", "matcher", "scrapers"}
    source = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")

    offenders = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.col_offset == 0:
                continue
            name = (node.module if isinstance(node, ast.ImportFrom)
                    else node.names[0].name) or ""
            if name.split(".")[0] in OURS:
                offenders.append(f"{fn.name}() imports {name} (line {node.lineno})")

    assert not offenders, (
        "these are read from the bundle when the route runs:\n  "
        + "\n  ".join(offenders))
