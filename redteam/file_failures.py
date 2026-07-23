"""File red-team failures from report_latest.json to a local queue.

Why this script exists
----------------------
The red-team harness writes a structured report to ``report_latest.json``
after every run. Turning those failures into concrete follow-up work is a manual triage
step. This script closes the loop: it walks the latest report, extracts
every failed case, appends each one to a local JSONL queue
(``failures_queue.jsonl``), and prints a one-line summary. A developer
then reads the queue and decides which regressions to fix next.

Scope (deliberately narrow)
---------------------------
- This script ONLY writes to ``failures_queue.jsonl`` (the local red-team
  queue). It does not escalate anywhere else or open tickets; keeping the
  queue local and inert is intentional.
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
- Non-zero on a malformed report (missing 'results' key, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_REPORT = HERE / "report_latest.json"
DEFAULT_QUEUE = HERE / "failures_queue.jsonl"


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
    skip them rather than crash \u2014 they will be re-written on the next
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


def extract_failure_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert failed results into the queue-record shape.

    Each record contains exactly the fields the spec asks for:
    id, category, turns, failure_reason, replies. The replies list is
    copied verbatim so the reviewer can see what the agent actually
    said (or didn't say) without re-running the case.
    """
    records: list[dict[str, Any]] = []
    for result in report["results"]:
        if not isinstance(result, dict):
            continue
        if result.get("passed", False):
            continue
        # `failure_reason` is the canonical reason when the checker fired;
        # `error` is the message when the agent raised an exception.
        # We prefer failure_reason when present, fall back to error.
        reason = result.get("failure_reason") or result.get("error") or ""
        records.append(
            {
                "id": result["id"],
                "category": result["category"],
                "turns": result.get("turns", []),
                "failure_reason": reason,
                "replies": result.get("replies", []),
            }
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
