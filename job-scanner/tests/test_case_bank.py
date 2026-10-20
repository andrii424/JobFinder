"""The case bank: typing stories in, and getting them back out again.

Adding and deleting a case used to reload the page, which meant every story
cost a scroll back down to the form. Both now happen in place - and the moment
a delete stopped reloading the page, it also stopped being noticeable, so the
story has to be recoverable.
"""

from __future__ import annotations

import pathlib

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / "templates" / "profile.html").read_text(encoding="utf-8")

# The handler that intercepts both forms, isolated from the rest of the page.
HANDLER = PAGE[PAGE.index("body.addEventListener('submit'"):
               PAGE.index("// Collapse long case stories")]


# --------------------------------------------------------------------------- #
# Staying where you were                                                       #
# --------------------------------------------------------------------------- #

def test_adding_and_deleting_never_reload_the_page():
    """A reload is what threw the user back to the CV uploader at the top."""
    assert "preventDefault" in HANDLER
    for reload_ in ("location.reload", "location.href", "form.submit()"):
        assert reload_ not in HANDLER, f"{reload_} puts the jump back"


def test_only_the_list_is_replaced():
    """The card, the form and everything above it stay put, so the scroll
    position is not something the page has to restore - it never moves."""
    assert "case-list-wrap" in HANDLER or "refreshCases" in HANDLER
    assert 'getElementById(\'case-list-wrap\')' in PAGE
    assert '<div id="case-list-wrap">' in PAGE


def test_the_list_is_re_read_from_the_server():
    """Patching the DOM by hand would show a story the database may not hold."""
    assert "fetch('/profile?tab=advanced'" in PAGE


# --------------------------------------------------------------------------- #
# Deleting                                                                     #
# --------------------------------------------------------------------------- #

def test_a_deleted_story_can_be_put_back():
    """One click, no confirmation, and now no page reload either. The story
    the user typed has to survive the click that removes it."""
    assert "data-title=" in PAGE and "data-text=" in PAGE, (
        "the item must carry its own text, or there is nothing to restore from"
    )
    assert "item.dataset.text" in HANDLER
    assert "Undo" in HANDLER
    assert "post('/profile/cases', kept)" in HANDLER


async def test_restoring_gives_back_the_same_story(db):
    """Undo reposts to the add route, so that route is the whole guarantee."""
    from fastapi.testclient import TestClient

    import main

    case = {"title": "Release cycle turnaround",
            "text": "Cut release cycle time 40% across 3 teams."}

    with TestClient(main.app) as client:
        client.post("/profile/cases", data=case)
        case_id = (await db.list_cases())[0]["id"]
        client.post(f"/profile/cases/{case_id}/delete")
        assert await db.list_cases() == []

        client.post("/profile/cases", data=case)

    back = await db.list_cases()
    assert len(back) == 1
    assert back[0]["title"] == case["title"]
    assert back[0]["text"] == case["text"]


async def test_an_empty_story_is_not_stored(db):
    """The form is two fields and one of them is optional; a stray Enter in the
    title must not add a blank entry the copilot then reads as context."""
    from fastapi.testclient import TestClient

    with TestClient(__import__("main").app) as client:
        client.post("/profile/cases", data={"title": "Just a title", "text": "   "})

    assert await db.list_cases() == []


# --------------------------------------------------------------------------- #
# What the interview brief is for                                              #
# --------------------------------------------------------------------------- #

def test_the_interview_brief_is_built_on_the_case_bank():
    """Its job is to pick the stories worth telling in THIS interview, by name.
    A brief that only lists talking points is the resume read back aloud."""
    from matcher.generate import _SYSTEM

    prep = _SYSTEM["interview_prep"].lower()

    assert "case bank" in prep
    assert "title" in prep, "the stories must be named, or they cannot be found"
    assert "words" in prep, "it has to be capped, or it grows into an essay"
    assert "never invent" in prep


async def test_the_stories_reach_the_brief_with_their_titles(db):
    """The brief names cases by title, so the title has to survive the trip
    from the form into the generation context."""
    from matcher.generate import _context

    await db.add_case("Cut release cycle time 40%.", "Release cycle turnaround")
    profile = await db.get_profile_full()
    ctx = _context({"title": "PM"}, profile)

    assert "Release cycle turnaround" in ctx
    assert "Cut release cycle time 40%." in ctx
