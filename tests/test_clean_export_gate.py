"""Clean-export regression contract for the reviewer-facing G5 gate."""

from pathlib import Path

from scripts.release_check import REQUIRED_TRACKED, run_clean_export


def test_required_release_artifacts_are_declared():
    assert "redteam/reports/simulator_sanitized_2026-07-26.json" in REQUIRED_TRACKED
    assert all(Path(item).suffix or item == ".env.example" for item in REQUIRED_TRACKED)


def test_clean_export_reproduces_g5():
    code, output = run_clean_export(only="G5")
    assert code == 0, output
    assert "G5 simulator" in output
    assert "320/320" in output
    assert "signature match" in output
