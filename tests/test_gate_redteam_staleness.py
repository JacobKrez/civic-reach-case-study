"""G4 reports how old its live red-team evidence is.

A live report describes the agent that existed when it ran. If ``src/``
changed after the run, the gate must say so instead of presenting old
pass rates as current. These tests pin the behaviour without a network
call: the committed reports are read as-is and only the "last src/
change" timestamp is substituted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts import gate


def test_report_older_than_src_change_warns(monkeypatch) -> None:
    monkeypatch.setattr(
        gate, "_last_src_change", lambda: datetime(2999, 1, 1, tzinfo=timezone.utc)
    )
    result = gate.gate_redteam()
    assert result.status == "WARN"
    assert "STALE" in result.detail
    assert "run 20" in result.detail
    assert result.ok


def test_report_newer_than_src_change_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        gate, "_last_src_change", lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    result = gate.gate_redteam()
    assert result.status == "PASS"
    assert "STALE" not in result.detail


def test_unknown_src_change_does_not_warn(monkeypatch) -> None:
    # Source archive or shallow clone: no basis for a staleness claim.
    monkeypatch.setattr(gate, "_last_src_change", lambda: None)
    assert gate.gate_redteam().status == "PASS"


def test_report_run_time_is_parsed_and_made_aware() -> None:
    assert gate._report_run_at({"run_started_at": "2026-07-25T19:31:56"}).tzinfo is not None
    assert gate._report_run_at({}) is None
    assert gate._report_run_at({"run_started_at": "not a date"}) is None


def test_latest_report_is_chosen_by_run_time_not_filename() -> None:
    report = gate._latest_redteam_report()
    assert report is not None
    assert report.name.startswith("report_live_")
