import sqlite3

from scripts import self_improve


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute(
        """
        CREATE TABLE chunks (
            id TEXT,
            project_id TEXT,
            title TEXT,
            status TEXT,
            updated_at TEXT
        )
        """
    )
    return db


def test_active_gap_chunk_is_detected():
    db = _db()
    db.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
        (
            "chk_existing",
            self_improve.PROJECT_ID,
            "W46 -- Auto-queued: close 'redteam:injection' (guardrail_thinking)",
            "queued",
            "2026-07-25T12:00:00Z",
        ),
    )
    assert self_improve._active_chunk_for_gap(db, "redteam:injection") == (
        "chk_existing",
        "queued",
    )


def test_terminal_gap_chunk_does_not_block_new_evidence():
    db = _db()
    db.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
        (
            "chk_old",
            self_improve.PROJECT_ID,
            "W46 -- Auto-queued: close 'redteam:injection' (guardrail_thinking)",
            "verified",
            "2026-07-25T12:00:00Z",
        ),
    )
    assert self_improve._active_chunk_for_gap(db, "redteam:injection") is None
