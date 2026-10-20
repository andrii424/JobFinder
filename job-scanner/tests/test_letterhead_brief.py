"""Describing the letterhead instead of writing HTML for it.

The letterhead was the one thing in the app a person had to edit as raw HTML,
which is a fair thing to ask of nobody. It is now described in a line of
English - and the two things that must not be left to the model are enforced
here: the length of that line, and the fact that the letter itself survives.
"""

from __future__ import annotations

import pathlib

import letter_template as lt
from matcher import generate as gen

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / "templates" / "profile.html").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The cap                                                                      #
# --------------------------------------------------------------------------- #

async def test_a_long_brief_is_refused_before_it_reaches_the_model():
    """200 characters is the feature, not a technical limit: past that, people
    describe the letter rather than the page it sits on. Enforced in code -
    maxlength on the input is a suggestion to anything but a browser."""
    html, error = await gen.letterhead_from_brief("x" * (lt.BRIEF_MAX + 1), current="")

    assert html == ""
    assert str(lt.BRIEF_MAX) in error


async def test_an_empty_brief_says_so_rather_than_calling_the_model():
    html, error = await gen.letterhead_from_brief("   ", current="")

    assert html == "" and error


def test_the_page_shows_the_same_limit_the_server_enforces():
    """Two numbers that can drift apart would let the box accept what the
    server then rejects."""
    assert 'maxlength="{{ letter_brief_max }}"' in PAGE
    assert "{{ letter_brief_max }} characters" in PAGE

    import main

    assert main.letter_template.BRIEF_MAX == lt.BRIEF_MAX


# --------------------------------------------------------------------------- #
# The letter has to survive the redraw                                         #
# --------------------------------------------------------------------------- #

def test_the_body_placeholder_is_the_one_that_cannot_be_lost():
    """Without it the PDF prints a heading and no letter, and nothing says so
    until the PDF is opened."""
    assert lt.REQUIRED == "body"
    assert "body" in lt.PLACEHOLDERS


def test_missing_tokens_reports_exactly_what_went():
    full = lt.DEFAULT_TEMPLATE
    assert lt.missing_tokens(full) == []

    without_date = full.replace("{{date}}", "")
    assert lt.missing_tokens(without_date) == ["date"]

    bare = "<div>just a name</div>"
    assert set(lt.missing_tokens(bare)) == set(lt.PLACEHOLDERS)


async def test_a_layout_that_dropped_the_letter_is_refused(monkeypatch):
    """The model is asked to keep {{body}} and the code checks that it did."""
    class _Msg:
        content = '<div class="sender">Name</div><div>{{date}}</div>'

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    class _Client:
        def __init__(self, *a, **k):
            self.chat = type("Chat", (), {"completions": self})()

        async def create(self, **kwargs):
            return _Resp()

    monkeypatch.setattr(gen, "AsyncOpenAI", _Client)
    html, error = await gen.letterhead_from_brief(
        "no addressee", current=lt.DEFAULT_TEMPLATE, api_key="sk-test")

    assert html == "", "a letterhead with no letter must not reach the editor"
    assert "letter itself" in error


async def test_dropping_the_date_is_allowed_because_it_was_asked_for(monkeypatch):
    """Only {{body}} is non-negotiable. Someone who says 'no date' means it."""
    kept = '<div class="sender">Name</div><div class="body">{{body}}</div>'

    class _Msg:
        content = kept

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    class _Client:
        def __init__(self, *a, **k):
            self.chat = type("Chat", (), {"completions": self})()

        async def create(self, **kwargs):
            return _Resp()

    monkeypatch.setattr(gen, "AsyncOpenAI", _Client)
    html, error = await gen.letterhead_from_brief(
        "no date", current=lt.DEFAULT_TEMPLATE, api_key="sk-test")

    assert error == ""
    assert html == kept
    assert "date" in lt.missing_tokens(html)


async def test_a_fenced_answer_is_unwrapped(monkeypatch):
    """Models open with ```html however firmly they are told not to, and a
    fence saved into the template prints as literal text on the letter."""
    class _Msg:
        content = '```html\n<div class="body">{{body}}</div>\n```'

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})()]

    class _Client:
        def __init__(self, *a, **k):
            self.chat = type("Chat", (), {"completions": self})()

        async def create(self, **kwargs):
            return _Resp()

    monkeypatch.setattr(gen, "AsyncOpenAI", _Client)
    html, error = await gen.letterhead_from_brief(
        "tighter", current=lt.DEFAULT_TEMPLATE, api_key="sk-test")

    assert error == ""
    assert "```" not in html


# --------------------------------------------------------------------------- #
# Nothing is stored until the user says so                                     #
# --------------------------------------------------------------------------- #

async def test_describing_does_not_save_anything(db, monkeypatch):
    """The redraw lands in the editor. A layout nobody has looked at yet must
    not replace the one their letters are already printing on."""
    from fastapi.testclient import TestClient

    import main

    await db.save_letter_template("<div>MINE</div>{{body}}")

    async def _fake(*a, **k):
        return '<div class="body">{{body}}</div>', ""

    monkeypatch.setattr(main.gen, "letterhead_from_brief", _fake)

    with TestClient(main.app) as client:
        body = client.post("/profile/letter-template/describe",
                           data={"brief": "cleaner", "current": ""}).json()

    assert body["ok"] and "{{body}}" in body["html"]
    assert await db.get_letter_template() == "<div>MINE</div>{{body}}"


async def test_the_writing_service_draws_it_not_the_scanner(db, monkeypatch):
    """It is a document a person sends to an employer, so it belongs on the
    writing provider - the same rule the rest of the letter follows."""
    import main
    import providers
    from fastapi.testclient import TestClient

    await db.save_ai_provider("deepseek", "sk-deepseek")
    seen = {}

    async def _fake(brief, current="", sender_block="", **kw):
        seen.update(kw)
        return '<div class="body">{{body}}</div>', ""

    monkeypatch.setattr(main.gen, "letterhead_from_brief", _fake)
    with TestClient(main.app) as client:
        client.post("/profile/letter-template/describe", data={"brief": "x"})

    row = await db.get_app_settings()
    assert seen["model"] == providers.resolve_model(row, "write")
    assert seen["timeout"] == providers.timeout_for(row, providers.WRITE)


# --------------------------------------------------------------------------- #
# Where the page's scripts live                                                #
# --------------------------------------------------------------------------- #

def test_no_executable_script_lives_inside_the_tab_body():
    """Switching tabs replaces #profile-body with innerHTML, and a <script>
    inserted that way is NEVER executed. Anything defined in there is dead for
    everyone who arrives by clicking a tab rather than by loading its URL - the
    character counter did nothing, the Redraw button fell through to a plain GET
    and put the description in the address bar, and Save changes on the CV
    called a function that was not there."""
    body = PAGE[PAGE.index('<div id="profile-body">'):PAGE.index("/#profile-body")]

    offenders = [line.strip() for line in body.splitlines()
                 if "<script" in line and 'type="application/json"' not in line]
    assert not offenders, f"script inside the swapped body: {offenders}"


def test_the_counter_and_the_form_are_wired_from_page_level():
    """Delegated on #profile-body, which survives the swap, so neither needs
    re-initialising when a tab is clicked."""
    tail = PAGE[PAGE.index("/#profile-body"):]

    assert "ev.target.id !== 'letter_brief'" in tail, "the counter is not delegated"
    assert "form.id !== 'letter-brief-form'" in tail, "the form is not delegated"
    assert "function initLetter()" in tail
    assert tail.count("initLetter();") >= 2, "initLetter must run on load AND after a tab swap"


def test_the_letterhead_preview_is_on_the_page():
    """The redraw used to land in a folded-away textarea, so nothing visible
    changed and the only way to check the result was to print a PDF."""
    assert 'id="letter-preview"' in PAGE
    assert 'id="letter-preview-src"' in PAGE
    assert "frame.srcdoc" in PAGE


# --------------------------------------------------------------------------- #
# Seeing the PDF before it is written                                          #
# --------------------------------------------------------------------------- #

async def test_the_letter_preview_is_the_same_document_the_pdf_prints(db, add_job):
    """Built by the same call the PDF renderer makes. A preview assembled by a
    second code path is a picture of something else."""
    from fastapi.testclient import TestClient

    import main

    job_id = await add_job(score=90, company="Vondel Ventures", title="Product Ops")
    await db.save_asset(job_id, "cover_letter", "Dear Hiring Team,\n\nMy letter.")
    await db.save_asset(job_id, main.CL_ADDRESS_KIND, "Keizersgracht 241")
    await db.save_letter_template(
        '<div class="sender">Alex Morgan</div><div>{{company}}</div>'
        '<div>{{company_address}}</div><div>{{job_title}}</div><div>{{body}}</div>')

    with TestClient(main.app) as client:
        body = client.post(f"/jobs/{job_id}/cover-letter/preview").json()

    assert body["ok"]
    for expected in ("Alex Morgan", "Vondel Ventures", "Keizersgracht 241",
                     "Product Ops", "My letter."):
        assert expected in body["html"], expected
    # It is a standalone document, so the print stylesheet comes with it.
    assert "@page" in body["html"]


async def test_previewing_uses_what_is_on_screen(db, add_job):
    """Same contract as Save PDF: the text posted from the editor wins over the
    stored version, or the preview shows something the PDF will not."""
    from fastapi.testclient import TestClient

    import main

    job_id = await add_job(score=90)
    await db.save_asset(job_id, "cover_letter", "the stored one")

    with TestClient(main.app) as client:
        body = client.post(f"/jobs/{job_id}/cover-letter/preview",
                           data={"text": "the edited one"}).json()

    assert "the edited one" in body["html"]
    assert "the stored one" not in body["html"]


async def test_previewing_writes_nothing_to_disk(db, add_job, monkeypatch):
    """It is the step before committing, so it must not be a commit."""
    from fastapi.testclient import TestClient

    import main

    job_id = await add_job(score=90)
    await db.save_asset(job_id, "cover_letter", "text")

    def _boom(*a, **k):
        raise AssertionError("the preview saved a file")

    monkeypatch.setattr(main, "_save_pdf_to_downloads", _boom)
    with TestClient(main.app) as client:
        assert client.post(f"/jobs/{job_id}/cover-letter/preview").json()["ok"]


async def test_previewing_without_a_letter_says_so(db, add_job):
    from fastapi.testclient import TestClient

    import main

    job_id = await add_job(score=90)
    with TestClient(main.app) as client:
        r = client.post(f"/jobs/{job_id}/cover-letter/preview")

    assert r.status_code == 400
    assert "Generate" in r.json()["error"]


def test_saving_a_pdf_announces_itself():
    """The inline status line sits under a full page of letter and is missed -
    which reads as the button having done nothing."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "templates" / "job_detail.html").read_text(encoding="utf-8")
    fn = page[page.index("async function saveCvPdf"):page.index("async function saveAssetText")]

    assert fn.count("showToast(") >= 3, "success, failure and error must all be announced"
    assert "PDF saved to " in fn
    assert "previewLetterPdf" in page


# --------------------------------------------------------------------------- #
# The CV, previewed on the page where it is downloaded                         #
# --------------------------------------------------------------------------- #

async def test_the_cv_preview_is_the_document_the_pdf_renders(db):
    """One builder for both, so the preview cannot drift from the print."""
    from fastapi.testclient import TestClient

    import main

    await db.save_cv_html('<div class="cv-name">Alex Morgan</div><p>Summary.</p>',
                          set_original=True)

    with TestClient(main.app) as client:
        body = client.post("/cv/preview").json()

    assert body["ok"]
    assert body["html"] == main._cv_document(
        '<div class="cv-name">Alex Morgan</div><p>Summary.</p>')
    assert "@page" in body["html"] and "Alex Morgan" in body["html"]


async def test_the_tailored_cv_previews_its_own_edits(db):
    """That column exists only on screen until it is saved, so a preview that
    read the stored CV would show the wrong document."""
    from fastapi.testclient import TestClient

    import main

    await db.save_cv_html("<p>the stored CV</p>", set_original=True)

    with TestClient(main.app) as client:
        body = client.post("/cv/preview", data={"html": "<p>the tailored CV</p>"}).json()

    assert "the tailored CV" in body["html"]
    assert "the stored CV" not in body["html"]


def test_every_pdf_button_on_the_job_page_has_a_preview_beside_it():
    """Structural: a download with no way to look first is the thing that was
    being asked for. Three PDFs live on this page - the CV, the tailored CV and
    the cover letter."""
    page = (pathlib.Path(__file__).resolve().parents[1]
            / "templates" / "job_detail.html").read_text(encoding="utf-8")

    assert page.count("Save PDF</button>") == 3
    assert page.count("Preview PDF</button>") == 3
