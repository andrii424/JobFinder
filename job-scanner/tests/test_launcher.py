"""Desktop launch guards.

Regression for a shipped Windows bug: the shortcut runs `pythonw.exe`, which
gives the process no console - Python then sets sys.stdout and sys.stderr to
None. uvicorn's log formatter calls `sys.stdout.isatty()`, so the app died with
"ValueError: Unable to configure formatter 'default'" before serving anything.
"""

import logging.config
import sys

import pytest

from desktop.launcher import _ensure_std_streams


@pytest.fixture
def no_console(monkeypatch):
    """Exactly what pythonw.exe hands the process."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)


def test_missing_streams_are_replaced(no_console):
    _ensure_std_streams()

    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.write("must not raise")


def test_existing_streams_are_left_alone():
    before_out, before_err = sys.stdout, sys.stderr

    _ensure_std_streams()

    assert sys.stdout is before_out
    assert sys.stderr is before_err


def test_uvicorn_logging_configures_without_a_console(no_console):
    """THE regression: this raised ValueError and killed the Windows launch."""
    from uvicorn.config import LOGGING_CONFIG

    _ensure_std_streams()

    logging.config.dictConfig(LOGGING_CONFIG)   # must not raise


def test_uvicorn_server_config_builds_without_a_console(no_console):
    """The path the app actually takes: our own logging, none from uvicorn."""
    import uvicorn
    from fastapi import FastAPI

    _ensure_std_streams()
    config = uvicorn.Config(FastAPI(), host="127.0.0.1", port=8123,
                            log_level="info", log_config=None)
    config.configure_logging()


# --------------------------------------------------------------------------- #
# Scheduler startup - regression for the 1.0.4 Windows crash                  #
# --------------------------------------------------------------------------- #

def test_scheduler_survives_a_missing_timezone_database(monkeypatch):
    """Windows ships no system tz database. Without the `tzdata` package both
    tzlocal AND APScheduler's own lookup raise ZoneInfoNotFoundError, which used
    to kill app startup entirely: 'No time zone found with key Europe/Kiev'.
    A bad timezone must degrade to UTC, never take the app down."""
    from zoneinfo import ZoneInfoNotFoundError

    import apscheduler.schedulers.base as base
    import tzlocal

    def boom(*a, **kw):
        raise ZoneInfoNotFoundError("No time zone found with key Europe/Kiev")

    monkeypatch.setattr(tzlocal, "get_localzone", boom)
    monkeypatch.setattr(base, "get_localzone", boom)

    from scheduler.jobs import create_scheduler

    scheduler = create_scheduler()

    assert scheduler is not None, "app startup would have crashed"
    assert scheduler.timezone is not None


def test_scheduler_uses_the_local_timezone_when_available(monkeypatch):
    from scheduler.jobs import create_scheduler

    scheduler = create_scheduler()

    assert scheduler is not None
    assert str(scheduler.timezone) != ""


def test_scheduler_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("JOBFINDER_NO_SCHEDULER", "1")
    from scheduler.jobs import create_scheduler

    assert create_scheduler() is None


# --------------------------------------------------------------------------- #
# Port selection - the extension has to be able to find the app               #
# --------------------------------------------------------------------------- #

def test_port_stays_in_the_range_the_extension_probes():
    """Regression: when 8000 was taken the app grabbed a RANDOM high port, so
    the extension (which posts to 8000) silently sent every scan into the void.
    The extension probes 8000-8010, so the app must stay inside that window."""
    import socket

    from desktop.launcher import _preferred_port

    held, taken = [], set()
    try:
        # Hold whatever we can - the real app may already own 8000 on this
        # machine, and the test must not fail because of that.
        for wanted in (8000, 8001, 8002):
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", wanted))
                sock.listen(1)
                held.append(sock)
                taken.add(wanted)
            except OSError:
                sock.close()
                taken.add(wanted)      # someone else holds it; still unavailable

        port = _preferred_port()

        assert 8000 <= port <= 8010, f"port {port} is outside the extension's probe range"
        assert port not in taken, "returned a port that is already in use"
    finally:
        for sock in held:
            sock.close()


# --------------------------------------------------------------------------- #
# The window itself                                                           #
# --------------------------------------------------------------------------- #

def test_the_window_lets_text_be_selected():
    """pywebview defaults `text_select` to False, and that default injects
    `body { user-select: none }` into the page - nothing in the app could be
    selected, so nothing could be copied out of it. Structural, because the
    only way to observe it otherwise is to open the built app and try."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "desktop" / "launcher.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    opts = [n for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "window_opts" for t in n.targets)]
    assert opts, "the window is no longer built from window_opts"
    passed = {kw.arg: kw.value for kw in opts[0].value.keywords}
    assert "text_select" in passed, "text_select must be set, not left to default"
    assert passed["text_select"].value is True


@pytest.mark.parametrize("line, secret", [
    ("GET https://api.telegram.org/bot123456:ABCdefGhiJKLmnoPQRstuVWXyz01234/sendMessage",
     "ABCdefGhiJKLmnoPQRstuVWXyz01234"),
    ("Authorization failed for sk-proj-abcdefghijklmnopqrstuvwx",
     "abcdefghijklmnopqrstuvwx"),
    # Google's REST API carries the key in the URL, and httpx logs URLs whole.
    ("GET https://generativelanguage.googleapis.com/v1beta/models?key=AIzaTESTKEYTESTKEYTESTKEYTESTKEY0000",
     "TESTKEYTESTKEYTESTKEYTESTKEY0000"),
])
def test_credentials_never_reach_the_log_file(line, secret):
    """The log gets attached to bug reports. A provider added later must not
    be the one whose key survives in it."""
    from desktop.launcher import _SecretsFilter

    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, line, None, None)
    text = _SecretsFilter.Formatter("%(message)s").format(record)

    assert secret not in text
    assert "<redacted>" in text


def test_the_context_menu_is_restored_on_macos():
    """Copy in the right-click menu is the other half: pywebview's Cocoa backend
    empties the native menu unless it is in debug mode."""
    import inspect

    from desktop import launcher

    body = inspect.getsource(launcher._enable_context_menu)
    assert "willOpenMenu_withEvent_" in body
    assert "darwin" in body
