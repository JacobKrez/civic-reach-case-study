"""Continuous MiniMax/user-simulator red-team lane (W42).

The daemon deliberately reuses the shipped simulator's conversation and grading
functions instead of reproducing them. It never patches ``src/`` and never
commits. Daemon-owned state is limited to ``redteam/findings`` and ``logs``;
the permanent regression queue is updated through the existing file_failures
adapter as required by the red-team workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from redteam.file_failures import file_failures  # noqa: E402
from scripts.simulate import (  # noqa: E402
    PERSONAS,
    SCENARIOS,
    _run_llm_judge,
    _run_one,
)

FINDINGS_DIR = REPO / "redteam" / "findings"
LOG_DIR = REPO / "logs"
TOP_PATH = FINDINGS_DIR / "TOP.md"
MAX_TOP = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise(text: str) -> str:
    """Make trigger text stable across harmless whitespace/case changes."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _dedupe_key(persona: str, invariant: str, trigger: str) -> str:
    payload = "\x1f".join((_normalise(persona), _normalise(invariant), _normalise(trigger)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _read_finding(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _finding_path(key: str) -> Path:
    return FINDINGS_DIR / f"{key}.json"


def _failed_invariants(outcome: Any) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for name, result in outcome.rule_results.items():
        if not result.get("passed", False):
            failures.append((name, str(result.get("reason", ""))))
    for name, result in outcome.judge_results.items():
        if not result.get("passed", False):
            failures.append((f"judge:{name}", str(result.get("reason", ""))))
    if not failures and not outcome.passed:
        failures.append(("conversation", outcome.failure_reason or outcome.error or "unknown failure"))
    return failures


def _trigger_for(outcome: Any, invariant: str, reason: str) -> str:
    """Use the message most relevant to a failure, with a stable fallback."""
    if invariant.startswith("judge:"):
        return outcome.user_messages[-1] if outcome.user_messages else reason
    for message, reply in zip(outcome.user_messages, outcome.assistant_replies):
        if reason and _normalise(reason) in _normalise(reply):
            return message
    return outcome.user_messages[-1] if outcome.user_messages else reason


def _severity(invariant: str) -> int:
    # Universal/persona rule violations are objective regressions; subjective
    # judge complaints remain useful but rank below them.
    return 2 if not invariant.startswith("judge:") else 1


def _record_failure(outcome: Any, invariant: str, reason: str) -> dict[str, Any]:
    trigger = _trigger_for(outcome, invariant, reason)
    key = _dedupe_key(outcome.persona, invariant, trigger)
    path = _finding_path(key)
    old = _read_finding(path) or {}
    record = {
        "id": f"sim_{key}",
        "dedupe_key": key,
        "persona": outcome.persona,
        "scenario": outcome.scenario_id,
        "seed": outcome.seed,
        "invariant": invariant,
        "reason": reason,
        "trigger_message": trigger,
        "count": int(old.get("count", 0)) + 1,
        "first_seen": old.get("first_seen", _utc_now()),
        "last_seen": _utc_now(),
        "severity": _severity(invariant),
        "transcript": [
            {"user": user, "assistant": reply}
            for user, reply in zip(outcome.user_messages, outcome.assistant_replies)
        ],
        "rule_results": outcome.rule_results,
        "judge_results": outcome.judge_results,
    }
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def _all_findings() -> list[dict[str, Any]]:
    records = []
    for path in FINDINGS_DIR.glob("*.json"):
        record = _read_finding(path)
        if record and record.get("dedupe_key"):
            records.append(record)
    return sorted(
        records,
        key=lambda item: (-int(item.get("severity", 0)), -int(item.get("count", 0)), str(item.get("id", ""))),
    )


def _write_top() -> list[dict[str, Any]]:
    findings = _all_findings()
    lines = [
        "# Continuous red-team triage",
        "",
        f"Updated: {_utc_now()}",
        "",
        "Rank = objective invariant violation before judge complaint, then frequency.",
        "Each entry includes the trigger and a complete reproducing transcript.",
        "",
        "## Worst offenders",
        "",
    ]
    if not findings:
        lines.append("No failures recorded.")
    for index, item in enumerate(findings[:MAX_TOP], 1):
        lines.extend([
            f"### {index}. {item.get('id')} — {item.get('invariant')}",
            f"- Severity: {item.get('severity')} | Count: {item.get('count')}",
            f"- Persona/scenario/seed: {item.get('persona')} / {item.get('scenario')} / {item.get('seed')}",
            f"- Reason: {item.get('reason')}",
            f"- Trigger: {item.get('trigger_message')}",
            "",
            "```text",
        ])
        for turn in item.get("transcript", []):
            lines.append(f"USER: {turn.get('user', '')}")
            lines.append(f"ASSISTANT: {turn.get('assistant', '')}")
        lines.extend(["```", ""])
    TOP_PATH.write_text("\n".join(lines), encoding="utf-8")
    return findings


def _auto_file(findings: list[dict[str, Any]]) -> int:
    """File top findings through the existing JSONL failure adapter."""
    if not findings:
        return 0
    results = []
    for item in findings[:MAX_TOP]:
        results.append({
            "id": item["id"],
            "category": item["invariant"].replace("judge:", "judge_"),
            "passed": False,
            "failure_reason": item["reason"],
            "turns": [t.get("user", "") for t in item.get("transcript", [])],
            "replies": [t.get("assistant", "") for t in item.get("transcript", [])],
        })
    report = {"summary": {"source": "sim_daemon"}, "results": results}
    queue = REPO / "redteam" / "attack_cases.jsonl"
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=LOG_DIR, delete=False, encoding="utf-8") as handle:
        json.dump(report, handle)
        report_path = Path(handle.name)
    try:
        summary = file_failures(report_path=report_path, queue_path=queue, dry_run=False)
        return int(summary.get("newly_filed", 0))
    finally:
        report_path.unlink(missing_ok=True)


def _log_run(record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "sim_daemon.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _choose(rng: random.Random, personas: list[str], scenarios: list[Any], seed: int) -> tuple[str, Any, int]:
    return rng.choice(personas), rng.choice(scenarios), seed


def run_once(args: argparse.Namespace, rng: random.Random, ordinal: int) -> dict[str, Any]:
    persona_name, scenario, seed = _choose(rng, args.personas, args.scenarios, ordinal)
    persona = PERSONAS[persona_name]
    started = time.monotonic()
    try:
        outcome = _run_one(persona, scenario, seed, dry_run=args.dry_run)
        if not args.dry_run:
            outcome.judge_results = _run_llm_judge(outcome, persona, scenario)
            outcome.passed = outcome.passed and all(x.get("passed", False) for x in outcome.judge_results.values())
        failures = _failed_invariants(outcome)
        touched = [_record_failure(outcome, invariant, reason) for invariant, reason in failures]
        findings = _write_top()
        filed = _auto_file(findings)
        result = {
            "timestamp": _utc_now(), "persona": persona_name, "scenario": scenario.sid,
            "seed": seed, "dry_run": args.dry_run, "passed": outcome.passed,
            "failures": [name for name, _ in failures], "new_findings": [x["id"] for x in touched],
            "finding_count": len(findings), "filed": filed,
            "elapsed_sec": round(time.monotonic() - started, 3),
        }
    except Exception as exc:  # daemon must keep operating after a bad run
        result = {"timestamp": _utc_now(), "persona": persona_name, "scenario": scenario.sid,
                  "seed": seed, "passed": False, "error": f"{type(exc).__name__}: {exc}",
                  "elapsed_sec": round(time.monotonic() - started, 3)}
    _log_run(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W42 continuous read-only simulator and red-team triage daemon")
    parser.add_argument("--once", action="store_true", help="run exactly one conversation")
    parser.add_argument("--dry-run", action="store_true", help="use simulator's deterministic offline LLM")
    parser.add_argument("--max-conversations", type=int, default=0, help="stop after N conversations; 0 means unbounded")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between conversations")
    parser.add_argument("--backoff", type=float, default=2.0, help="error backoff multiplier")
    parser.add_argument("--seed", type=int, default=42, help="selection RNG seed")
    parser.add_argument("--personas", default=",".join(sorted(PERSONAS)), help="comma-separated persona names")
    parser.add_argument("--scenarios", default="", help="comma-separated scenario ids")
    args = parser.parse_args(argv)
    args.personas = [x.strip() for x in args.personas.split(",") if x.strip()]
    unknown = [x for x in args.personas if x not in PERSONAS]
    if unknown:
        parser.error(f"unknown personas: {unknown}")
    wanted = {x.strip() for x in args.scenarios.split(",") if x.strip()}
    args.scenarios = [x for x in SCENARIOS if not wanted or x.sid in wanted]
    if not args.scenarios:
        parser.error("no scenarios selected")
    if args.max_conversations < 0 or args.interval < 0 or args.backoff < 1:
        parser.error("max-conversations/interval must be non-negative and backoff must be >= 1")
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    limit = 1 if args.once else (args.max_conversations or None)
    ordinal = 0
    delay = args.interval
    while limit is None or ordinal < limit:
        ordinal += 1
        result = run_once(args, rng, ordinal)
        if result.get("error"):
            time.sleep(delay)
            delay = max(args.interval, delay * args.backoff)
        else:
            delay = args.interval
            if limit is None or ordinal < limit:
                time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
