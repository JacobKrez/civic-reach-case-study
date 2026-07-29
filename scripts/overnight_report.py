#!/usr/bin/env python3
"""Summarize an overnight simulator run into one reviewable page.

The continuous simulator already records every conversation
(``logs/sim_daemon.jsonl``) and deduplicates failures into
``redteam/findings/*.json``. Reading those raw is slow at 8am, and the
existing ``TOP.md`` ranks by *total historical count*, which buries a
finding that started three hours ago under one that was fixed days ago.

This report answers the only three questions that matter before deciding
what to commit:

  1. What ran, and how much of it passed?
  2. What failed **since a given cut-off** (default: the run you just
     did), ranked so the objective regressions come first?
  3. For each one, what is the exact message that triggers it, so it can
     be reproduced by hand in seconds?

Read-only: it writes nothing except the report file you ask for.

Usage::

    python3 scripts/overnight_report.py --since 8h
    python3 scripts/overnight_report.py --since 2026-07-29T02:00:00Z -o /tmp/r.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FINDINGS_DIR = REPO / "redteam" / "findings"
RUN_LOG = REPO / "logs" / "sim_daemon.jsonl"


def _parse_since(raw: str) -> datetime:
    """Accept an ISO timestamp or a relative window like ``8h`` / ``45m``."""
    relative = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", raw, re.IGNORECASE)
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2).lower()
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    text = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_runs(since: datetime) -> list[dict]:
    if not RUN_LOG.exists():
        return []
    runs = []
    for line in RUN_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = _read_timestamp(record.get("timestamp"))
        if stamp and stamp >= since:
            runs.append(record)
    return runs


def _load_findings(since: datetime) -> list[dict]:
    if not FINDINGS_DIR.exists():
        return []
    findings = []
    for path in FINDINGS_DIR.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        last_seen = _read_timestamp(record.get("last_seen"))
        if last_seen and last_seen >= since:
            record["_path"] = path.name
            findings.append(record)
    # Objective invariant violations (severity 2) before subjective judge
    # complaints (severity 1); then by how often it recurred in-window.
    findings.sort(
        key=lambda r: (-int(r.get("severity", 0)), -int(r.get("count", 0)))
    )
    return findings


def _percent(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "n/a"


def build_report(since: datetime) -> str:
    runs = _load_runs(since)
    findings = _load_findings(since)
    passed = sum(1 for r in runs if r.get("passed"))
    errored = sum(1 for r in runs if r.get("error"))

    out: list[str] = []
    out.append("# Overnight simulator report")
    out.append("")
    out.append(f"- Window: since **{since.isoformat()}**")
    out.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    out.append(f"- Conversations: **{len(runs)}**")
    out.append(f"- Passed: **{passed}** ({_percent(passed, len(runs))})")
    out.append(f"- Failed: **{len(runs) - passed}**")
    if errored:
        out.append(f"- Provider/harness errors: **{errored}** (not agent bugs)")
    out.append(f"- Distinct findings touched in window: **{len(findings)}**")
    out.append("")

    if runs:
        by_persona: dict[str, list[int]] = {}
        for record in runs:
            name = record.get("persona", "?")
            bucket = by_persona.setdefault(name, [0, 0])
            bucket[0] += 1
            if record.get("passed"):
                bucket[1] += 1
        out.append("## Coverage")
        out.append("")
        out.append("| persona | runs | passed | pass rate |")
        out.append("|---|---:|---:|---:|")
        for name in sorted(by_persona):
            total, ok = by_persona[name]
            out.append(f"| {name} | {total} | {ok} | {_percent(ok, total)} |")
        out.append("")

    if not findings:
        out.append("## Findings")
        out.append("")
        out.append("Nothing failed in this window.")
        out.append("")
        return "\n".join(out)

    out.append("## Findings, worst first")
    out.append("")
    out.append(
        "Severity 2 = objective invariant violation (a rule the agent "
        "provably broke). Severity 1 = subjective judge complaint. "
        "**Confirm every one against the live agent before writing a fix** "
        "-- a stale or false-positive finding is common, and the checker "
        "itself can be the thing that is wrong."
    )
    out.append("")

    for index, record in enumerate(findings, start=1):
        out.append(
            f"### {index}. {record.get('invariant', '?')} "
            f"(severity {record.get('severity', '?')}, "
            f"seen {record.get('count', '?')}x total)"
        )
        out.append("")
        out.append(f"- Finding id: `{record.get('id', '?')}`")
        out.append(
            f"- Persona / scenario / seed: "
            f"`{record.get('persona')}` / `{record.get('scenario')}` / "
            f"`{record.get('seed')}`"
        )
        out.append(f"- First seen: {record.get('first_seen')}")
        out.append(f"- Last seen: {record.get('last_seen')}")
        out.append("")
        out.append(f"**Reason:** {record.get('reason', '')}")
        out.append("")
        trigger = record.get("trigger_message")
        if trigger:
            out.append(f"**Trigger message:** `{trigger}`")
            out.append("")
        out.append("**Reproduce:**")
        out.append("")
        out.append("```bash")
        out.append(
            f"python3 scripts/sim_daemon.py --once "
            f"--personas {record.get('persona')} "
            f"--scenarios {record.get('scenario')} "
            f"--seed {record.get('seed')}"
        )
        out.append("```")
        out.append("")
        transcript = record.get("transcript") or []
        if transcript:
            out.append("<details><summary>Transcript</summary>")
            out.append("")
            out.append("```text")
            for turn in transcript:
                out.append(f"USER: {turn.get('user', '')}")
                out.append(f"ASSISTANT: {turn.get('assistant', '')}")
            out.append("```")
            out.append("")
            out.append("</details>")
            out.append("")
        out.append(f"<sub>raw: `redteam/findings/{record.get('_path')}`</sub>")
        out.append("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="12h",
        help="ISO timestamp or relative window (8h, 45m, 2d). Default 12h.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="write the report here instead of stdout",
    )
    args = parser.parse_args(argv)

    try:
        since = _parse_since(args.since)
    except (ValueError, KeyError):
        parser.error(f"could not parse --since {args.since!r}")

    report = build_report(since)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
