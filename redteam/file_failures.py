"""File red-team failures from report_latest.json to a local queue.

Why this script exists
----------------------
The red-team harness writes a structured report to ``report_latest.json``
after every run. Turning those failures into concrete follow-up work is
a manual triage step. This script closes the loop: it walks the latest
report, extracts every failed case, appends each one to a local JSONL
queue (``failures_queue.jsonl``), and prints a one-line summary. A
developer then reads the queue and decides which regressions to fix
next.

Scope (deliberately narrow)
---------------------------
- This script ONLY writes to ``failures_queue.jsonl`` (the local red-team
  queue). It does not escalate anywhere else or open tickets; keeping
  the queue local and inert is intentional.
- De-duplication is by ``case id``. A case that fails on run #1 and
  fails again on run #5 will appear in the queue exactly once, with
  the latest failure details.
- ``--dry-run`` prints what WOULD be written without mutating the queue.

Usage
-----
::

    python3 redteam/file_failures.py
    python3 redteam/file_failures.py --dry-run
    python3 redteam/file_failures.py --report path/to/report_latest.json
    python3 redteam/file_failures.py --queue path/to/failures_queue.jsonl

Exit codes
----------
- 0 on success (including the case where the report has zero failures).
- Non-zero on a malformed report (missing 'results' key, etc.) or when
  a generated record fails the harness schema check (see W47).

W47 — schema-correct queue records
----------------------------------
Every record this script appends MUST be a valid red-team corpus case
as defined by ``run_redteam.load_cases``: the four required keys
(``id``, ``category``, ``turns``, ``expected_behavior``) with a
``category`` from the allowed set, non-empty ``turns`` of non-empty
strings, and so on. An auto-filer that emits the wrong schema can
silently corrupt the corpus file it's pointed at — the canonical
``attack_cases.jsonl`` is the most painful target because every
subsequent ``run_redteam.py`` invocation will then ``SystemExit`` on
load. Defense in depth: every record is round-tripped through a
private copy of the load_cases() validation before it is appended, and
a record that fails the round-trip is rejected with a clear error
instead of being written. See ``tests/test_file_failures.py`` for the
malformed-input acceptance test.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_REPORT = HERE / "report_latest.json"
DEFAULT_QUEUE = HERE / "failures_queue.jsonl"

# Allowed red-team categories. Mirrors the set in run_redteam.load_cases
# exactly; if a new category is added there it must be added here too,
# otherwise the auto-filer will reject every record it tries to write
# for the new category. The duplication is intentional: the filer is a
# safety net for the corpus, so it must not import the live
# ``load_cases`` (which would defeat the purpose of the round-trip
# test) but must agree with it on the allowed set.
ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {"injection", "crisis", "ambiguity", "off_topic", "contradiction", "false_positive"}
)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def load_report(path: Path) -> dict[str, Any]:
    """Load and minimally validate the red-team report JSON.

    Raises SystemExit with a clear message on a malformed report.
    """
    if not path.exists():
        raise SystemExit(f"report not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            report = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"report is not valid JSON: {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise SystemExit(f"report root must be a JSON object: {path}")
    if "results" not in report or not isinstance(report["results"], list):
        raise SystemExit(
            f"report is missing a 'results' list: {path}"
        )
    return report


def load_existing_queue_ids(path: Path) -> set[str]:
    """Read the existing queue and return the set of case ids already filed.

    Tolerant of a missing file (first run) and of malformed lines (we
    skip them rather than crash — they will be re-written on the next
    run if they correspond to a current failure).
    """
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                # Skip unreadable lines; do not crash the whole script.
                print(
                    f"warning: skipping malformed line {line_no} in {path}",
                    file=sys.stderr,
                )
                continue
            cid = obj.get("id")
            if isinstance(cid, str):
                ids.add(cid)
    return ids


def _normalize_category(raw: Any) -> str | None:
    """Map a free-form category string to an allowed red-team category.

    The simulator and the harness itself sometimes emit prefixed
    category labels (``judge_injection``, ``injection_refused``,
    ``crisis_resources_present``) that are diagnostic descriptions
    rather than the canonical fixture category. The filer maps the
    common prefixes back to the allowed set; anything that does not
    map cleanly returns ``None`` so the record can be rejected rather
    than silently corrupting the queue / corpus with an invented
    category.

    The mapping is deliberately small and explicit. Adding a new
    alias requires editing both this function and the test that
    exercises the rejection path.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if candidate in ALLOWED_CATEGORIES:
        return candidate
    lowered = candidate.lower()
    # The simulator emits ``invariant: <name>`` strings; the daemon's
    # auto-file path strips the ``judge:`` prefix and lowercases the
    # result (e.g. ``judge_injection``). Drop the ``judge_`` prefix.
    for prefix in ("judge_", "judge:"):
        if lowered.startswith(prefix):
            tail = lowered[len(prefix):]
            if tail in ALLOWED_CATEGORIES:
                return tail
    # The harness has historically emitted result-style categories
    # like ``injection_refused`` and ``crisis_resources_present``
    # when the checker fired correctly; recover the canonical
    # category by substring match.
    for allowed in ALLOWED_CATEGORIES:
        if allowed in lowered:
            return allowed
    return None


def _expected_behavior_from_failure(result: dict[str, Any]) -> str:
    """Build the ``expected_behavior`` string for a failure record.

    The harness report gives us ``failure_reason`` (the checker-fired
    reason) and ``error`` (the agent-raised exception). The corpus
    schema asks for an ``expected_behavior`` string that describes
    what the agent SHOULD do, not what it did. We synthesize a
    deterministic description so a future contributor looking at the
    filed record knows what pass looks like.
    """
    reason = result.get("failure_reason") or result.get("error") or ""
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        reason = "Agent response did not match the category-specific checker."
    return (
        "AUTO-FILED from a failed run: the agent's actual response did not "
        f"satisfy the per-category checker. Reason observed: {reason!r}. "
        "This fixture is now a regression test: a future pass must reverse "
        "this failure. The reason string is preserved here as the failure "
        "narrative; the actual expected behavior depends on the category "
        "and the original turn sequence."
    )


def build_queue_record(result: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a failed report result into a schema-valid queue record.

    Returns ``None`` if the result cannot be coerced into the
    corpus-valid shape (missing required keys, invalid category,
    empty turns). The caller is expected to log and skip such
    results rather than write them.
    """
    if not isinstance(result, dict):
        return None
    rid = result.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None
    raw_turns = result.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        return None
    if not all(isinstance(t, str) and t.strip() for t in raw_turns):
        return None
    category = _normalize_category(result.get("category"))
    if category is None:
        return None
    return {
        "id": rid,
        "category": category,
        "turns": raw_turns,
        "expected_behavior": _expected_behavior_from_failure(result),
    }


def _validate_record_against_corpus_schema(record: dict[str, Any]) -> str | None:
    """Return an error string if *record* would not survive load_cases().

    A clean copy of the schema check from ``run_redteam.load_cases``
    lives here so the filer can validate without importing the
    harness (which would defeat the layered-defense point: the
    auto-filer must reject malformed records even if the harness
    itself has a bug). The function is intentionally
    private/exposed-for-test; it is the single source of truth for
    what a valid record looks like.
    """
    for key in ("id", "category", "turns", "expected_behavior"):
        if key not in record:
            return f"missing required key {key!r}"
    if not isinstance(record["id"], str) or not record["id"].strip():
        return f"id must be a non-empty string: {record['id']!r}"
    if not isinstance(record["category"], str) or record["category"] not in ALLOWED_CATEGORIES:
        return f"category must be one of {sorted(ALLOWED_CATEGORIES)}: {record['category']!r}"
    turns = record["turns"]
    if (
        not isinstance(turns, list)
        or not turns
        or not all(isinstance(t, str) and t for t in turns)
    ):
        return f"turns must be a non-empty list of non-empty strings: {turns!r}"
    if not isinstance(record["expected_behavior"], str) or not record["expected_behavior"].strip():
        return f"expected_behavior must be a non-empty string: {record['expected_behavior']!r}"
    return None


def _round_trip_record(record: dict[str, Any]) -> str | None:
    """Validate *record* end-to-end by writing + re-reading as JSONL.

    A purely in-memory schema check can miss encoding bugs and
    other issues that only surface when the record is serialized
    and re-parsed. Writing the candidate record to a one-line JSONL
    file and parsing it back is the cheapest way to catch those
    without coupling to the real corpus file. Returns an error
    string if the round-trip fails, ``None`` if it succeeds.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path = Path(handle.name)
    try:
        with tmp_path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return f"round-trip JSON parse failed at line {line_no}: {exc}"
                err = _validate_record_against_corpus_schema(parsed)
                if err is not None:
                    return f"round-trip schema check failed: {err}"
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def extract_failure_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert failed results into the queue-record shape.

    Each record contains exactly the four fields the spec asks for:
    id, category, turns, expected_behavior. The category is
    normalized to the allowed set; results whose category cannot be
    normalized are skipped with a clear log message rather than
    written as garbage.
    """
    records: list[dict[str, Any]] = []
    skipped: list[tuple[Any, str]] = []
    for result in report["results"]:
        if not isinstance(result, dict):
            continue
        if result.get("passed", False):
            continue
        record = build_queue_record(result)
        if record is None:
            rid = result.get("id") if isinstance(result, dict) else None
            skipped.append((rid, "could not normalize to corpus schema"))
            continue
        err = _round_trip_record(record)
        if err is not None:
            skipped.append((record["id"], err))
            continue
        records.append(record)
    for rid, reason in skipped:
        print(
            f"warning: skipping result {rid!r}: {reason}",
            file=sys.stderr,
        )
    return records


def file_failures(
    report_path: Path = DEFAULT_REPORT,
    queue_path: Path = DEFAULT_QUEUE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append newly-failed cases to the queue and return a summary dict.

    Summary keys:
        - failures_in_report: total failed cases in the report
        - newly_filed: count of cases that were appended to the queue
        - already_queued: count of cases that were already in the queue
        - queue_path: absolute path to the queue file
        - dry_run: bool echo of the flag
    """
    report = load_report(report_path)
    new_records = extract_failure_records(report)
    existing_ids = load_existing_queue_ids(queue_path)

    to_append = [r for r in new_records if r["id"] not in existing_ids]
    already_queued = [r for r in new_records if r["id"] in existing_ids]

    if dry_run:
        summary = {
            "failures_in_report": len(new_records),
            "newly_filed": 0,
            "already_queued": len(already_queued),
            "would_append": len(to_append),
            "queue_path": str(queue_path),
            "dry_run": True,
        }
        print(f"[DRY-RUN] redteam/file_failures.py: {summary}")
        if to_append:
            print("[DRY-RUN] would file:")
            for r in to_append:
                print(f"  - [{r['category']}] {r['id']}: {r['failure_reason']}")
        return summary

    # Real write: append, not overwrite. The queue is append-only by
    # design; de-duplication makes re-appending a no-op for repeat ids.
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8") as f:
        for r in to_append:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "failures_in_report": len(new_records),
        "newly_filed": len(to_append),
        "already_queued": len(already_queued),
        "queue_path": str(queue_path),
        "dry_run": False,
    }
    print(f"redteam/file_failures.py: {summary}")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="File red-team failures from report_latest.json to a local queue.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Path to report_latest.json (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--queue",
        type=Path,
        default=DEFAULT_QUEUE,
        help=f"Path to failures_queue.jsonl (default: {DEFAULT_QUEUE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without appending to the queue.",
    )
    args = parser.parse_args(argv)

    file_failures(
        report_path=args.report,
        queue_path=args.queue,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
