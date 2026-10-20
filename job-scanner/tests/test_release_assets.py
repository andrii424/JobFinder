"""What a release must carry before it is allowed out.

Four releases in a row shipped macOS-only. The Windows installer builds fine on
this machine; it was not built, and the publish command named two files instead
of three. This is that mistake turned into something that fails loudly.
"""

from __future__ import annotations

import pathlib
import time

from desktop import check_release_assets as gate

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _fake_dist(tmp_path, names, size=6_000_000, age=0.0):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    for name in names:
        f = dist / name
        f.write_bytes(b"x" * size)
        if age:
            old = time.time() - age
            import os
            os.utime(f, (old, old))
    return dist


ALL = [a.name for a in gate.REQUIRED]


def test_windows_is_not_optional():
    """The whole point. If this list loses the installer, Windows users get
    another release that does not exist for them."""
    assert "JobFinder-Setup.exe" in ALL
    assert "JobFinder-macOS.zip" in ALL
    assert "JobFinder.dmg" in ALL


def test_a_complete_dist_passes(tmp_path):
    assert gate.problems(_fake_dist(tmp_path, ALL)) == []


def test_a_missing_windows_installer_stops_the_release(tmp_path):
    dist = _fake_dist(tmp_path, ["JobFinder-macOS.zip", "JobFinder.dmg"])

    found = gate.problems(dist)

    assert len(found) == 1
    assert "JobFinder-Setup.exe" in found[0]
    assert "build_windows_installer_macos.sh" in found[0], (
        "saying what is missing without saying how to build it is half a message"
    )


def test_a_truncated_build_is_caught(tmp_path):
    """A few kilobytes means the build died partway, and the file existing is
    exactly what makes that invisible."""
    dist = _fake_dist(tmp_path, ALL)
    (dist / "JobFinder-Setup.exe").write_bytes(b"x" * 1000)

    found = gate.problems(dist)

    assert len(found) == 1 and "TOO SMALL" in found[0]


def test_last_months_build_is_not_shipped_as_this_months(tmp_path):
    """`dist/JobFinder-Setup.exe` from an earlier release really is sitting on
    this machine, so 'the file is there' proves nothing whatsoever."""
    dist = _fake_dist(tmp_path, ALL, age=60 * 60 * 24 * 30)

    found = gate.problems(dist, root=ROOT)

    assert len(found) == 3
    assert all("STALE" in line for line in found)


def test_a_fresh_build_is_not_called_stale(tmp_path):
    dist = _fake_dist(tmp_path, ALL)

    assert gate.problems(dist, root=ROOT) == []


def test_the_gate_exits_non_zero_so_a_script_stops(tmp_path):
    """It is called from preflight.sh, which decides by exit code."""
    incomplete = _fake_dist(tmp_path, ["JobFinder.dmg"])

    assert gate.main(["--dist", str(incomplete)]) == 1
    assert gate.main(["--dist", str(_fake_dist(tmp_path, ALL))]) == 0


def test_outputs_are_ignored_when_looking_for_the_newest_source(tmp_path):
    """dist/ and build/ are outputs. Counting them would make every build stale
    against itself."""
    newest, _ = gate.newest_source(ROOT)

    assert newest is not None
    assert not any(part in gate._SKIP for part in newest.parts)


def test_the_runbook_and_the_gate_agree(tmp_path):
    """A checklist that names different files from the check is the original
    failure in a new place."""
    runbook = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    for asset in ALL:
        assert asset in runbook, f"{asset} is not in RELEASE.md"
    assert "build_windows_installer_macos.sh" in runbook
    assert "preflight.sh" in runbook


def test_the_project_instructions_point_at_the_runbook():
    """The instruction that used to name only the macOS build is what produced
    four incomplete releases."""
    claude = (ROOT.parent / "CLAUDE.md").read_text(encoding="utf-8")

    assert "RELEASE.md" in claude
    assert "JobFinder-Setup.exe" in claude
