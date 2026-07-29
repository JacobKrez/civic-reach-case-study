"""Clean-export regression contract for the reviewer-facing G5 gate."""

from pathlib import Path

import pytest

from scripts.release_check import (
    REQUIRED_TRACKED,
    GitUnavailable,
    run_clean_export,
)


def test_required_release_artifacts_are_declared():
    assert "redteam/reports/simulator_sanitized_2026-07-26.json" in REQUIRED_TRACKED
    assert all(Path(item).suffix or item == ".env.example" for item in REQUIRED_TRACKED)


def test_clean_export_reproduces_g5():
    """This check needs a usable git to export HEAD.

    Skipped rather than failed when the environment blocks git. A
    reviewer on Windows, 2026-07-29, cloned the repo into a directory
    owned by ``BUILTIN\\Administrators`` and git refused to touch it
    ("detected dubious ownership"). Nothing was wrong with the clone or
    the code -- but ``setup.bat`` runs the suite as its smoke check, so
    the first thing that reviewer saw was a red failure on a working
    install. A check that cannot run is not a check that failed, and the
    artifact guarantee is still enforced by the filesystem fallback in
    ``_tracked_contract``.
    """
    try:
        code, output = run_clean_export(only="G5")
    except GitUnavailable as exc:
        pytest.skip(f"git cannot read this checkout in this environment: {exc}")
    assert code == 0, output
    assert "G5 simulator" in output
    assert "320/320" in output
    assert "signature match" in output
