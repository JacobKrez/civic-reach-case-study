#!/usr/bin/env python3
"""W45 -- Recursive self-improvement for the FoodPath build loop.

This script is the missing link in the "close the loop" claim. The red-team
harness (W7/W7.1/W11) finds problems; the persona simulator (W37) finds more
at scale; the structured turn telemetry (W41) records what the live agent
actually does. W45 reads all of that evidence, ranks the *residual* gaps by
``severity x frequency x rubric_weight``, and -- when run with ``--apply`` --
writes correctly-specified Mission Control chunks for the top N gaps so a
future Loop B execution can close them.

W45 is **read-mostly**. It NEVER edits FoodPath code; it ONLY writes new
chunks into the Mission Control database. The default mode is ``--dry-run``,
which prints the proposed chunks and writes an audit log without touching
the database. ``--apply`` is required to enqueue.

Hard guardrails (all of these are non-negotiable; see :func:`_enforce_guardrails`):

* never enqueue more than ``--max`` chunks per run (default 3)
* never enqueue when the ``queued`` queue depth is already > ``--queue-ceiling`` (default 5)
* never enqueue work outside the case-study rubric -- an explicit deny-list
  rejects anything that smells like auth, persistence, deployment, agency
  integration, or new external services
* never enqueue a chunk that would relax an existing gate threshold
  (we read the ``thresholds`` block in the red-team report and refuse any
  candidate chunk that asks for a lower bar)
* every generated chunk MUST ship with a TRIPWIRE acceptance criterion --
  a runnable shell check that provably FAILS on the current checkout and
  would pass after the fix. A chunk without a failing-before tripwire is
  dropped, not enqueued, with the reason logged.

Every decision (gap, score, chunk written or rejected and why) is appended
to ``logs/self_improve.jsonl`` so a human can later ask "the loop decided
this on its own, why?" and get a real answer.

Usage::

    python3 scripts/self_improve.py                 # dry-run, default --max 3
    python3 scripts/self_improve.py --apply         # actually enqueue chunks
    python3 scripts/self_improve.py --max 1 --apply # smaller batch
    python3 scripts/self_improve.py --max 5         # bigger dry-run

Exit codes::

    0  -- ran successfully (including intentional queue backpressure)
    1  -- an unexpected policy guardrail failure prevented the run
    2  -- an input was missing or malformed; nothing was enqueued

See ``ARCHITECTURE.md`` section 6 for how this fits the larger recursive
improvement story.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Paths -- resolved relative to this file so the script is location-stable.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
REDTEAM_REPORT = REPO_ROOT / "redteam" / "report_latest.json"
REDTEAM_FINDINGS = REPO_ROOT / "redteam" / "findings" / "TOP.md"
GATE_SCRIPT = REPO_ROOT / "scripts" / "gate.py"
LOGS_DIR = REPO_ROOT / "logs"
TELEMETRY_GLOB = "turns-*.jsonl"
SELF_IMPROVE_LOG = LOGS_DIR / "self_improve.jsonl"
MC_DB = Path(
    os.environ.get(
        "MISSION_CONTROL_DB",
        "/Users/jacob/.openclaw/workspace/mission-control-v2/data/mc.db",
    )
)
PROJECT_ID = "civic_reach"
ROADMAP_ID_FALLBACK = "rmp_mrxjk1haonuas6"
ASSIGNEE = "bob-civicreach"

# ---------------------------------------------------------------------------
# Rubric weights for the four graded criteria in the live review.
#
# These are the weights used when ranking residual gaps. They are
# deliberately hand-coded (not learned) so the loop's reasoning is
# inspectable -- a future reviewer should be able to read this constant
# and ask "why is guardrail thinking weighted 1.5?" and get an answer.
#
# The weights were chosen to mirror the way the live review appears to
# weight questions: architectural judgment and guardrail thinking
# dominate, failure-mode reasoning matters, and tradeoff reasoning is
# the tiebreaker. They are not sacred; bump them in this file, commit,
# and the audit log will record the change.
# ---------------------------------------------------------------------------
RUBRIC_WEIGHTS: dict[str, float] = {
    "architectural_judgment": 1.5,
    "guardrail_thinking": 1.5,
    "failure_mode_reasoning": 1.2,
    "tradeoff_reasoning": 1.0,
}

# ---------------------------------------------------------------------------
# Deny-list -- case-study boundary.
#
# Any candidate chunk whose title+description matches a deny-list regex is
# REJECTED with reason logged. The deny-list is intentionally explicit
# (per the chunk spec) so a future maintainer can read the categories and
# understand the case-study boundary without inferring it from rejections.
# Order does not matter; every regex is checked.
# ---------------------------------------------------------------------------
DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bauth\b", re.I),                # login, oauth, sso, etc.
    re.compile(r"\b(authentication|authorization)\b", re.I),
    re.compile(r"\b(persistence|database|postgres|mysql|redis)\b", re.I),
    re.compile(r"\b(deployment|docker|kubernetes|helm|ci/cd)\b", re.I),
    re.compile(r"\b(agency integration|api integration|third[- ]party)\b", re.I),
    re.compile(r"\b(stripe|twilio|sendgrid|openai api|anthropic api)\b", re.I),
    re.compile(r"\b(blockchain|web3|crypto)\b", re.I),
)

# Allowed categories. A candidate chunk's rubric tag MUST be in this set.
ALLOWED_RUBRICS = set(RUBRIC_WEIGHTS)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Gap:
    """One residual gap observed in the evidence substrate."""

    gap_id: str
    category: str
    description: str
    severity: float              # 0..1, how bad the failure is if it fires
    frequency: int               # how many times the evidence shows it
    rubric_tag: str              # which graded criterion this gap exercises
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def score(self) -> float:
        """``severity * frequency * rubric_weight``.

        ``severity`` is a 0..1 unit-less score; ``frequency`` is a raw
        count; the rubric weight is a unit-less multiplier from
        :data:`RUBRIC_WEIGHTS`. The product is what the ranking sort
        uses. We log the full decomposition so the audit trail can
        answer "why was this gap ranked #1?" without re-running the
        script.
        """
        weight = RUBRIC_WEIGHTS.get(self.rubric_tag, 1.0)
        return self.severity * self.frequency * weight

    def score_decomposition(self) -> dict[str, float]:
        """Return the score inputs as a dict for the audit log."""
        return {
            "severity": self.severity,
            "frequency": self.frequency,
            "rubric_weight": RUBRIC_WEIGHTS.get(self.rubric_tag, 1.0),
            "score": self.score,
        }


@dataclass(frozen=True)
class Chunk:
    """One Mission Control chunk the self-improvement loop proposes."""

    title: str
    description: str                 # "WHERE: ... WHY: ... WHAT: ..."
    acceptance_criteria: tuple[dict[str, str], ...]   # each = {type, cmd, description}
    gap: Gap
    deny_list_hits: tuple[str, ...] = field(default_factory=tuple)

    def has_tripwire(self) -> bool:
        """A chunk is enqueueable only if it carries at least one shell AC
        whose ``cmd`` is a runnable check. A non-shell AC (file_exists, http)
        is allowed but does NOT count as the tripwire.
        """
        return any(
            ac.get("type") == "shell" and ac.get("cmd", "").strip()
            for ac in self.acceptance_criteria
        )

    def relax_gate(self, current_thresholds: dict[str, Any]) -> str | None:
        """If the chunk's description asks to relax an existing gate
        threshold, return the gate name; otherwise return ``None``.

        We do a soft scan for phrases like "lower threshold", "relax
        crisis pass rate", "make injection gate < 100%", etc. The
        thresholds we know about come from the red-team report
        (``thresholds.rows``).
        """
        text = (self.title + " " + self.description).lower()
        for gate_name, info in current_thresholds.items():
            # Skip the overall gate -- it is the composite, not a real one.
            if gate_name == "overall":
                continue
            cur = info.get("threshold") if isinstance(info, dict) else None
            if cur is None:
                continue
            # Look for phrases that would lower the bar.
            bad_phrases = (
                f"lower {gate_name}",
                f"relax {gate_name}",
                f"reduce {gate_name}",
                f"{gate_name} below",
                f"{gate_name} <",
                f"{gate_name} threshold to",
            )
            for phrase in bad_phrases:
                if phrase in text:
                    return gate_name
        return None


# ---------------------------------------------------------------------------
# Evidence readers
# ---------------------------------------------------------------------------
def _read_redteam_report() -> dict[str, Any]:
    """Load ``redteam/report_latest.json`` if it exists, else ``{}``.

    The red-team harness writes a structured report with a ``summary``
    block, a per-category breakdown, a ``thresholds`` block, and a
    ``results`` list. We use the first two to build gaps; the third to
    enforce the no-relaxing-gates guardrail.
    """
    if not REDTEAM_REPORT.exists():
        return {}
    try:
        return json.loads(REDTEAM_REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_redteam_findings_md() -> str:
    """Return the markdown of ``redteam/findings/TOP.md`` if present."""
    if not REDTEAM_FINDINGS.exists():
        return ""
    try:
        return REDTEAM_FINDINGS.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_telemetry() -> list[dict[str, Any]]:
    """Read every ``logs/turns-*.jsonl`` and return a list of events.

    Skips malformed lines silently -- the telemetry log is best-effort
    by design (see ``src/telemetry.py``) and a single bad line should
    not break the self-improvement loop.
    """
    if not LOGS_DIR.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(LOGS_DIR.glob(TELEMETRY_GLOB)):
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return events


def _read_gate_output() -> dict[str, Any] | None:
    """Run ``scripts/gate.py`` and parse its JSON output, if it exists.

    W44 (the gate) is not always present in the working tree. We
    therefore call this defensively: a missing script is *not* an
    error, it just means we have one less signal. A failing script is
    also not an error -- it means a gate is currently red, which is
    itself a signal the self-improvement loop can use.
    """
    if not GATE_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(GATE_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Prefer stdout; fall back to stderr; tolerate a non-zero exit
    # (gates can legitimately fail; that is a signal, not an error).
    for stream in (proc.stdout, proc.stderr):
        if not stream:
            continue
        # The first JSON object in the stream wins.
        for line in stream.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


# ---------------------------------------------------------------------------
# Gap extraction
# ---------------------------------------------------------------------------
def _gaps_from_redteam(report: dict[str, Any]) -> list[Gap]:
    """One gap per failing red-team category, weighted by fail count.

    These are the canonical residual gaps the red-team harness already
    identified. Each gap is tagged with the rubric criterion it
    exercises:

    * crisis -> guardrail_thinking (a missed crisis resource is a
      guardrail failure)
    * injection -> guardrail_thinking (missed injection refusal)
    * off_topic -> guardrail_thinking (scope redirect missed)
    * ambiguity / contradiction -> failure_mode_reasoning (the
      calculator or state machine let an ambiguous input through)
    """
    if not report:
        return []
    summary = report.get("summary", {}) or {}
    by_cat: dict[str, dict[str, Any]] = summary.get("by_category", {}) or {}
    gaps: list[Gap] = []
    for cat, info in by_cat.items():
        failed = int(info.get("failed", 0) or 0)
        if failed <= 0:
            continue
        # Severity 1.0 for crisis (a missed crisis resource is
        # the highest-stakes failure), 0.9 for injection (data
        # exfiltration), 0.7 for off_topic (poor UX, not unsafe),
        # 0.6 for ambiguity/contradiction (calculator sanity).
        sev_map = {"crisis": 1.0, "injection": 0.9, "off_topic": 0.7}
        severity = sev_map.get(cat, 0.6)
        rubric_map = {
            "crisis": "guardrail_thinking",
            "injection": "guardrail_thinking",
            "off_topic": "guardrail_thinking",
            "ambiguity": "failure_mode_reasoning",
            "contradiction": "failure_mode_reasoning",
        }
        rubric = rubric_map.get(cat, "tradeoff_reasoning")
        gaps.append(
            Gap(
                gap_id=f"redteam:{cat}",
                category=f"redteam:{cat}",
                description=(
                    f"{failed} of {info.get('total', '?')} red-team cases in "
                    f"category '{cat}' failed ({info.get('pass_rate', 0):.1%} pass rate)."
                ),
                severity=severity,
                frequency=failed,
                rubric_tag=rubric,
                evidence_paths=("redteam/report_latest.json",),
            )
        )
    return gaps


def _gaps_from_telemetry(events: list[dict[str, Any]]) -> list[Gap]:
    """Telemetry gaps: layer distribution skew, low-score retrieval, latency.

    These are things the red-team harness does not see because the red
    team fires crafted attacks. Telemetry sees the *real* traffic
    shape: which guardrail layer is doing most of the work, whether
    the retrieval floor is too low, whether the LLM layer is
    dominating latency, etc.
    """
    if not events:
        return []
    gaps: list[Gap] = []

    # 1. Layer skew -- if one guardrail layer is doing almost all the
    # work, the others are misconfigured or mis-trusted. (Mostly an
    # architectural-judgment gap.)
    layer_counts = Counter(e.get("layer", "?") for e in events)
    total = sum(layer_counts.values()) or 1
    dominant_layer, dominant_count = layer_counts.most_common(1)[0]
    dominance = dominant_count / total
    if dominance > 0.90 and "llm" in dominant_layer:
        gaps.append(
            Gap(
                gap_id="telemetry:layer_skew_llm_dominant",
                category="telemetry:layer_skew",
                description=(
                    f"In {total} telemetry events, the LLM layer handled "
                    f"{dominance:.1%} of turns (the rest was crisis/injection/"
                    f"scope/hostility guardrails). Keyword guardrails are "
                    f"under-trusted; either the keyword coverage is too "
                    f"narrow, or the LLM classifier is being invoked when "
                    f"it does not need to be."
                ),
                severity=0.6,
                frequency=dominant_count,
                rubric_tag="architectural_judgment",
                evidence_paths=("logs/turns-*.jsonl",),
            )
        )

    # 2. Retrieval floor -- count turns where ANY retrieval hit scored
    # below 0.3. A persistent low-score is a sign the embedder is
    # returning noise the agent is using as grounding. (Failure-mode
    # reasoning gap.)
    low_score_turns = 0
    for e in events:
        hits = e.get("retrieval_hits") or []
        for h in hits:
            if h.get("score", 1.0) < 0.3:
                low_score_turns += 1
                break
    if low_score_turns > 0 and low_score_turns > total * 0.10:
        gaps.append(
            Gap(
                gap_id="telemetry:retrieval_low_score_floor",
                category="telemetry:retrieval_quality",
                description=(
                    f"{low_score_turns} of {total} turns ({low_score_turns / total:.1%}) "
                    f"had at least one retrieval hit scoring below 0.3 -- the "
                    f"agent is being fed low-quality grounding. Either the "
                    f"embedder needs re-tuning, the floor needs raising in "
                    f"src/retrieval.py, or the agent needs a deterministic "
                    f"fallback when no hit clears the floor."
                ),
                severity=0.7,
                frequency=low_score_turns,
                rubric_tag="failure_mode_reasoning",
                evidence_paths=("logs/turns-*.jsonl", "src/retrieval.py"),
            )
        )

    # 3. Fallback fire rate -- if ``fallback_fired`` is non-zero, the
    # system is recovering from a model/tool failure. Even a small
    # rate is a finding because the telemetry floor is a designed
    # property.
    fallback_count = sum(1 for e in events if e.get("fallback_fired"))
    if fallback_count > 0:
        gaps.append(
            Gap(
                gap_id="telemetry:fallback_fired",
                category="telemetry:fallback_fired",
                description=(
                    f"{fallback_count} turns exercised a deterministic "
                    f"fallback (e.g. provider failover, schema translation, "
                    f"empty-reply recovery). This is a designed-in safety net, "
                    f"not a bug -- but the rate and the specific fallback "
                    f"names should be tracked so a regression in a primary "
                    f"path shows up as a fallback-rate spike."
                ),
                severity=0.5,
                frequency=fallback_count,
                rubric_tag="tradeoff_reasoning",
                evidence_paths=("logs/turns-*.jsonl",),
            )
        )

    return gaps


def _gaps_from_findings_md(md: str) -> list[Gap]:
    """Heuristic gap extraction from ``redteam/findings/TOP.md``.

    The findings doc is hand-written prose; we look for ``| # |``
    table rows and count them as one gap each, with a conservative
    severity (we cannot infer the count from prose) and a
    guardrail_thinking rubric tag. This is a SAFETY NET for the case
    where someone added a finding to TOP.md that is not yet covered
    by the structured report.
    """
    if not md:
        return []
    # Crude: count ``| # |`` rows in the first markdown table.
    rows = re.findall(r"^\|\s*#\d+\s*\|", md, re.M)
    if not rows:
        return []
    return [
        Gap(
            gap_id=f"findings_md:row_{i + 1}",
            category="findings_md:table",
            description=(
                f"Top finding #{i + 1} from redteam/findings/TOP.md has not "
                f"yet been verified as closed in the structured report."
            ),
            severity=0.8,
            frequency=1,
            rubric_tag="guardrail_thinking",
            evidence_paths=("redteam/findings/TOP.md",),
        )
        for i in range(len(rows))
    ]


def _gaps_from_gate(gate: dict[str, Any] | None) -> list[Gap]:
    """Read scripts/gate.py's structured output and emit a gap per red gate.

    If the gate is missing, this returns ``[]`` -- no gate means we
    cannot assert a regression, so we have no gap. If the gate is
    present and any sub-gate is red, we surface it.
    """
    if not gate:
        return []
    rows = gate.get("rows") or gate.get("gates") or {}
    gaps: list[Gap] = []
    for name, info in rows.items():
        passed = bool(info.get("passed"))
        if passed:
            continue
        gaps.append(
            Gap(
                gap_id=f"gate:{name}",
                category=f"gate:{name}",
                description=(
                    f"scripts/gate.py reports sub-gate '{name}' as red: "
                    f"{info.get('reason', 'no reason given')}"
                ),
                severity=0.9,
                frequency=1,
                rubric_tag="tradeoff_reasoning",
                evidence_paths=("scripts/gate.py",),
            )
        )
    return gaps


def collect_gaps(
    report: dict[str, Any],
    findings_md: str,
    events: list[dict[str, Any]],
    gate: dict[str, Any] | None,
) -> list[Gap]:
    """Run every gap extractor and return a deduplicated, ranked list."""
    raw = (
        _gaps_from_redteam(report)
        + _gaps_from_findings_md(findings_md)
        + _gaps_from_telemetry(events)
        + _gaps_from_gate(gate)
    )
    # Deduplicate by gap_id (a single failure mode may surface in two
    # extractors; we keep the highest-severity version).
    by_id: dict[str, Gap] = {}
    for g in raw:
        if g.gap_id not in by_id or g.severity > by_id[g.gap_id].severity:
            by_id[g.gap_id] = g
    return sorted(by_id.values(), key=lambda g: g.score, reverse=True)


# ---------------------------------------------------------------------------
# Chunk synthesis
#
# We turn a Gap into a Mission Control chunk spec. The format mirrors
# W24-W44: title starts with "W##", description starts with "WHERE: ... WHY:
# ... WHAT: ...", acceptance criteria is a JSON list of {type, cmd, description}.
#
# The TRIPWIRE -- the runnable check that provably fails on the current
# checkout -- is the linchpin. Without it, we cannot tell a chunk "your
# acceptance criteria will reject you if you do not actually fix the
# thing." We synthesize tripwires from the gap's category:
#
#   * redteam:<cat>       -> a JSON-driven check on the actual pass rate
#                            read from redteam/report_latest.json (fails
#                            today when the pass rate is below 100%)
#   * telemetry:*         -> a value check on the relevant field
#   * gate:<name>         -> the gate check itself
#   * findings_md:row_*   -> a grep for the chosen response in code
#
# CRITICAL: a tripwire must FAIL on the current checkout. The
# :func:`_check_tripwire` guardrail will refuse to enqueue a chunk
# whose tripwire already passes -- that is how we prevent the loop
# from queuing no-op work and burning a queue slot.
# ---------------------------------------------------------------------------
def _rt_rate_tripwire(category: str, label: str) -> tuple[str, str, str]:
    """Build a shell command that fails if the red-team category's
    pass rate is below 100%, given the current report.
    """
    return (
        "shell",
        (
            "cd /Users/jacob/.openclaw/workspace/civic_reach && "
            "python3 -c \""
            "import json;"
            "r=json.load(open('redteam/report_latest.json'));"
            f"info=r['summary']['by_category'].get('{category}');"
            f"assert info is not None, 'no redteam category: {category}';"
            f"pr=info['pass_rate'];"
            f"assert pr >= 1.0, 'pass rate is ' + str(round(pr, 3)) + ' (<100%)'\""
        ),
        f"{label} red-team category reaches 100% pass rate",
    )


_RT_TRIPWIRE = {
    "redteam:crisis": _rt_rate_tripwire("crisis", "crisis"),
    "redteam:injection": _rt_rate_tripwire("injection", "injection"),
    "redteam:off_topic": _rt_rate_tripwire("off_topic", "off_topic"),
    "redteam:ambiguity": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && "
        "python3 -c \"import json;"
        "r=json.load(open('redteam/report_latest.json'));"
        "info=r['summary']['by_category'].get('ambiguity');"
        "assert info is not None, 'no ambiguity category';"
        "pr=info['pass_rate'];assert pr>=1.0, 'ambiguity pass rate is ' + str(round(pr,3)) + ' (<100%)'\"",
        "ambiguity red-team category reaches 100% pass rate",
    ),
    "redteam:contradiction": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && "
        "python3 -c \"import json;"
        "r=json.load(open('redteam/report_latest.json'));"
        "info=r['summary']['by_category'].get('contradiction');"
        "assert info is not None, 'no contradiction category';"
        "pr=info['pass_rate'];assert pr>=1.0, 'contradiction pass rate is ' + str(round(pr,3)) + ' (<100%)'\"",
        "contradiction red-team category reaches 100% pass rate",
    ),
    "telemetry:retrieval_low_score_floor": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 -c \""
        "import re;"
        "src=open('src/retrieval.py').read();"
        "assert re.search(r'(?:FLOOR|MIN_SCORE|min_score|score_floor|MIN_RELEVANCE)\\s*=', src), "
        "'no retrieval score floor constant in src/retrieval.py'\"",
        "src/retrieval.py has an explicit score-floor constant",
    ),
    "telemetry:fallback_fired": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 -c \""
        "import json;"
        "from pathlib import Path;"
        "ev=sum(([json.loads(l) for l in p.open() if l.strip()] for p in Path('logs').glob('turns-*.jsonl')),[]);"
        "tot=len(ev);fb=sum(1 for e in ev if e.get('fallback_fired'));"
        "rate=fb/tot if tot else 0;"
        "assert rate < 0.01, f'fallback rate {rate:.1%} (>=1%)'\"",
        "fallback fire rate stays under 1% of all turns",
    ),
    "telemetry:layer_skew_llm_dominant": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 -c \""
        "import json;"
        "from collections import Counter;"
        "from pathlib import Path;"
        "ev=sum(([json.loads(l) for l in p.open() if l.strip()] for p in Path('logs').glob('turns-*.jsonl')),[]);"
        "c=Counter(e.get('layer','?') for e in ev);"
        "t=sum(c.values()) or 1;"
        "llm=c.get('llm',0);"
        "ratio=llm/t;"
        "assert ratio < 0.90, f'LLM layer still does {ratio:.1%} of turns (>=90%)'\"",
        "no single guardrail layer dominates >90% of turns",
    ),
    "gate:crisis": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 scripts/gate.py",
        "scripts/gate.py reports overall PASS",
    ),
    "gate:injection": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 scripts/gate.py",
        "scripts/gate.py reports overall PASS",
    ),
    "gate:off_topic": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 scripts/gate.py",
        "scripts/gate.py reports overall PASS",
    ),
    "gate:overall": (
        "shell",
        "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 scripts/gate.py",
        "scripts/gate.py reports overall PASS",
    ),
}


def _next_w_number(db: sqlite3.Connection) -> int:
    """Return ``max(existing W##) + 1`` so we never collide.

    Counts every ``W(\\d+)`` in every title. Saturates at 999; if the
    project ever hits W1000 we have other problems.
    """
    nums: list[int] = []
    for (t,) in db.execute(
        "SELECT title FROM chunks WHERE project_id=?", (PROJECT_ID,)
    ).fetchall():
        m = re.search(r"W(\d+)", t or "")
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) if nums else 0) + 1


def _queue_depth(db: sqlite3.Connection) -> int:
    """How many chunks are already ``status='queued'`` for this project."""
    return int(
        db.execute(
            "SELECT count(*) FROM chunks WHERE project_id=? AND status='queued'",
            (PROJECT_ID,),
        ).fetchone()[0]
    )


def _active_chunk_for_gap(db: sqlite3.Connection, gap_id: str) -> tuple[str, str] | None:
    """Return an existing active chunk for *gap_id*, if one exists.

    Scheduled self-improvement must be idempotent. A still-failing tripwire
    can otherwise enqueue the same finding every hour until the queue ceiling
    is reached, creating parallel no-op work and obscuring the real signal.
    """
    needle = f"close '{gap_id}'"
    row = db.execute(
        """
        SELECT id, status
          FROM chunks
         WHERE project_id = ?
           AND status IN ('queued', 'claimed', 'running', 'done')
           AND title LIKE ?
         ORDER BY updated_at DESC
         LIMIT 1
        """,
        (PROJECT_ID, f"%{needle}%"),
    ).fetchone()
    return (str(row[0]), str(row[1])) if row else None


def _build_chunk(gap: Gap, w_number: int) -> Chunk:
    """Synthesize a Mission Control chunk spec from a gap.

    The WHERE / WHY / WHAT preamble follows the project convention
    established in W24..W44: WHERE names the file, WHY names the
    rubric criterion and the evidence, WHAT lists the change. The
    acceptance criteria MUST contain a tripwire that fails on the
    current checkout -- this is enforced by :func:`_enforce_guardrails`.
    """
    tripwire = _RT_TRIPWIRE.get(gap.gap_id)
    if tripwire:
        tripwire_ac = {"type": tripwire[0], "cmd": tripwire[1], "description": tripwire[2]}
        tripwire_clause = (
            f"A tripwire acceptance criterion ships with this chunk and "
            f"provably FAILS on the current checkout. The tripwire is: "
            f"`{tripwire[1]}` -- a real {tripwire[0]} check that must "
            f"pass after the fix lands. A chunk without a failing-before "
            f"tripwire is not enqueueable; this one has one."
        )
    else:
        # No tripwire available -- we still synthesise a marker AC so
        # ``has_tripwire()`` is False and the guardrail layer will
        # reject this chunk (with reason logged).
        tripwire_ac = {"type": "shell", "cmd": "", "description": "no tripwire synthesized"}
        tripwire_clause = (
            "WARNING: no automated tripwire was synthesized for this gap. "
            "The guardrail layer in scripts/self_improve.py will refuse "
            "to enqueue this chunk until a runnable check is provided."
        )

    ac: list[dict[str, str]] = [tripwire_ac]
    # Always include the green-build and the commit-message ACs; the
    # project convention from W24 onward treats these as universal.
    ac.append(
        {
            "type": "shell",
            "cmd": "cd /Users/jacob/.openclaw/workspace/civic_reach && python3 -m pytest -q",
            "description": "full suite green",
        }
    )
    ac.append(
        {
            "type": "shell",
            "cmd": (
                f"cd /Users/jacob/.openclaw/workspace/civic_reach && "
                f"git log --oneline | grep -q 'W{w_number}'"
            ),
            "description": f"committed (git log contains 'W{w_number}')",
        }
    )

    title = f"W{w_number} -- Auto-queued: close '{gap.gap_id}' ({gap.rubric_tag})"
    evidence_str = ", ".join(gap.evidence_paths) or "(none)"
    description = (
        f"WHERE: {gap.category} (evidence: {evidence_str}). "
        f"WHY: closes a residual gap that the red-team harness / "
        f"telemetry / gate layer surfaced; rubric criterion "
        f"'{gap.rubric_tag}' is exercised by this gap. "
        f"Gap score decomposition: severity={gap.severity:.2f}, "
        f"frequency={gap.frequency}, rubric_weight="
        f"{RUBRIC_WEIGHTS.get(gap.rubric_tag, 1.0):.2f}, "
        f"score={gap.score:.2f}. "
        f"Original gap description: {gap.description} "
        f"WHAT: implement the smallest correct fix that turns the "
        f"tripwire green; this is generated by scripts/self_improve.py "
        f"(W45) and the human/agent handler should treat it as "
        f"directional -- if the tripwire passes, the gap is closed "
        f"regardless of implementation choice. "
        f"{tripwire_clause}"
    )
    return Chunk(
        title=title,
        description=description,
        acceptance_criteria=tuple(ac),
        gap=gap,
    )


# ---------------------------------------------------------------------------
# Guardrail enforcement
# ---------------------------------------------------------------------------
@dataclass
class GuardrailVerdict:
    ok: bool
    reason: str = ""


def _check_deny_list(chunk: Chunk) -> GuardrailVerdict:
    """Reject any chunk whose title+description hits a deny pattern."""
    text = f"{chunk.title} {chunk.description}"
    hits = [p.pattern for p in DENY_PATTERNS if p.search(text)]
    if hits:
        return GuardrailVerdict(
            ok=False,
            reason=f"deny-list match: {hits}",
        )
    return GuardrailVerdict(ok=True)


def _check_relaxes_gate(
    chunk: Chunk, current_thresholds: dict[str, Any]
) -> GuardrailVerdict:
    """Reject any chunk that would lower a gate threshold."""
    gate = chunk.relax_gate(current_thresholds)
    if gate:
        return GuardrailVerdict(
            ok=False,
            reason=(
                f"chunk appears to relax the '{gate}' gate threshold; "
                f"self-improvement is not allowed to lower the bar"
            ),
        )
    return GuardrailVerdict(ok=True)


def _check_tripwire(chunk: Chunk) -> GuardrailVerdict:
    """Reject any chunk that does not ship a runnable tripwire."""
    if not chunk.has_tripwire():
        return GuardrailVerdict(
            ok=False,
            reason="missing tripwire: no runnable shell AC",
        )
    # And the tripwire must actually fail on the current checkout --
    # we run it in dry-run-mode-of-the-guardrail and require non-zero
    # exit. We do NOT block enqueue on the tripwire passing
    # (it must fail today, by construction); we just verify it is a
    # real, runnable check.
    tripwire = next(
        ac for ac in chunk.acceptance_criteria
        if ac.get("type") == "shell" and ac.get("cmd", "").strip()
    )
    try:
        proc = subprocess.run(
            tripwire["cmd"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GuardrailVerdict(
            ok=False,
            reason=f"tripwire did not run: {exc!r}",
        )
    if proc.returncode == 0:
        # Tripwire passes today -- the gap is not what we thought.
        # Either the evidence is stale or the gap is already closed.
        # We refuse to enqueue on the principle that a no-op chunk
        # is worse than no chunk: it consumes a queue slot and
        # produces no closure.
        return GuardrailVerdict(
            ok=False,
            reason=(
                "tripwire passed on current checkout -- the gap is "
                "already closed or the evidence is stale; refusing to "
                "enqueue a no-op chunk"
            ),
        )
    return GuardrailVerdict(ok=True)


def _check_allowlist(chunk: Chunk) -> GuardrailVerdict:
    """The rubric tag must be one we recognize."""
    if chunk.gap.rubric_tag not in ALLOWED_RUBRICS:
        return GuardrailVerdict(
            ok=False,
            reason=f"rubric tag '{chunk.gap.rubric_tag}' is not in allow-list",
        )
    return GuardrailVerdict(ok=True)


def _enforce_guardrails(
    chunk: Chunk,
    current_thresholds: dict[str, Any],
) -> GuardrailVerdict:
    """Run every guardrail. First failure short-circuits."""
    for check in (
        lambda: _check_allowlist(chunk),
        lambda: _check_deny_list(chunk),
        lambda: _check_tripwire(chunk),
        lambda: _check_relaxes_gate(chunk, current_thresholds),
    ):
        v = check()
        if not v.ok:
            return v
    return GuardrailVerdict(ok=True)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _generate_chunk_id() -> str:
    """``chk_`` + 14 lowercase hex chars (matches existing IDs)."""
    import secrets
    return "chk_" + secrets.token_hex(7)


def _roadmap_id(db: sqlite3.Connection) -> str:
    """Find any roadmap already associated with this project's chunks."""
    r = db.execute(
        "SELECT roadmap_id FROM chunks WHERE project_id=? LIMIT 1",
        (PROJECT_ID,),
    ).fetchone()
    return r[0] if r else ROADMAP_ID_FALLBACK


def _enqueue_chunk(db: sqlite3.Connection, chunk: Chunk, w_number: int) -> str:
    """Insert one chunk with status='queued', assignee='bob-civicreach'.

    Returns the generated chunk id so the audit log can record it.
    The handler_type is 'codegen' to match the project convention
    (chunks W24..W44 are all codegen). Params is NULL because W45
    does not parameterize the next chunk; it leaves that to the
    handler.
    """
    chunk_id = _generate_chunk_id()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO chunks (
            id, roadmap_id, project_id, order_idx, title, description,
            acceptance_criteria, handler_type, params, status, assignee,
            claimed_by, started_at, attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk_id,
            _roadmap_id(db),
            PROJECT_ID,
            w_number,                                # use the W## as order_idx
            chunk.title,
            chunk.description,
            json.dumps(list(chunk.acceptance_criteria)),
            "codegen",
            None,
            "queued",
            ASSIGNEE,
            None,
            None,
            0,
            now,
            now,
        ),
    )
    return chunk_id


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _audit(event: dict[str, Any]) -> None:
    """Append one JSONL line to ``logs/self_improve.jsonl``.

    Best-effort: a logging failure must never break the run. This is
    the same posture as ``src/telemetry.py``.
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"timestamp": datetime.now(timezone.utc).isoformat(), **event},
            ensure_ascii=False,
            default=str,
        )
        with SELF_IMPROVE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_chunk_table(chunks_with_meta: list[tuple[Chunk, str, str]]) -> None:
    """Render a plain-text table of proposed chunks for the terminal."""
    if not chunks_with_meta:
        print("No chunks proposed.")
        return
    # Column widths
    rank_w = 4
    title_w = max(len(c.title) for c, _, _ in chunks_with_meta)
    title_w = max(title_w, len("TITLE"))
    score_w = max(len(f"{c.gap.score:8.2f}") for c, _, _ in chunks_with_meta)
    score_w = max(score_w, len("SCORE"))
    decision_w = 10
    reason_w = 30
    header = (
        f"{'RANK':<{rank_w}}  {'TITLE':<{title_w}}  {'SCORE':>{score_w}}  "
        f"{'DECISION':<{decision_w}}  REASON"
    )
    print(header)
    print("-" * len(header))
    for i, (chunk, decision, reason) in enumerate(chunks_with_meta, 1):
        print(
            f"{i:<{rank_w}}  {chunk.title:<{title_w}}  {chunk.gap.score:>{score_w}.2f}  "
            f"{decision:<{decision_w}}  {reason[:reason_w]}"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="self_improve.py",
        description=(
            "FoodPath recursive self-improvement: read evidence, "
            "rank residual gaps, optionally enqueue Mission Control "
            "chunks for the top N."
        ),
    )
    p.add_argument(
        "--apply", action="store_true",
        help="actually insert chunks into mc.db (default: dry-run only)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="explicit dry-run flag (this is the default; the flag exists "
             "so callers can be explicit and so acceptance criteria can "
             "assert 'dry-run is the default').",
    )
    p.add_argument(
        "--max", type=int, default=3,
        help="max chunks to enqueue in this run (default 3)",
    )
    p.add_argument(
        "--queue-ceiling", type=int, default=5,
        help="refuse to enqueue if queue depth already exceeds this (default 5)",
    )
    p.add_argument(
        "--show-all-gaps", action="store_true",
        help="print every gap, not just the ones we considered for chunks",
    )
    args = p.parse_args(argv)

    if args.max < 1 or args.max > 10:
        print(f"--max must be 1..10 (got {args.max})", file=sys.stderr)
        return 2

    # 1. Read evidence.
    report = _read_redteam_report()
    findings_md = _read_redteam_findings_md()
    events = _read_telemetry()
    gate = _read_gate_output()

    if not (report or findings_md or events or gate):
        print(
            "WARN: no evidence substrate found (no red-team report, no "
            "findings, no telemetry, no gate output). The loop has nothing "
            "to reason about. Run the red-team harness or generate some "
            "telemetry first.",
            file=sys.stderr,
        )
        # Still log and return 0 -- "no work to do" is not an error.
        _audit({"event": "no_evidence", "report": bool(report),
                "findings_md": bool(findings_md), "events": len(events),
                "gate": bool(gate)})
        return 0

    # 2. Extract and rank gaps.
    gaps = collect_gaps(report, findings_md, events, gate)
    _audit({"event": "gaps_collected", "count": len(gaps),
            "by_id": {g.gap_id: g.score_decomposition() for g in gaps}})

    if args.show_all_gaps:
        print(f"Total residual gaps: {len(gaps)}")
        for g in gaps:
            d = g.score_decomposition()
            print(
                f"  {g.gap_id:40s} score={d['score']:8.2f} "
                f"(sev={d['severity']:.2f} freq={d['frequency']} "
                f"rw={d['rubric_weight']:.2f})"
            )
        print()

    # 3. Build chunk specs. ``--max`` limits accepted proposals, not gaps
    # inspected: a stale top-ranked gap must not starve valid lower-ranked
    # work forever.
    if not MC_DB.exists():
        print(f"ERROR: Mission Control DB not found at {MC_DB}", file=sys.stderr)
        _audit({"event": "no_mc_db", "path": str(MC_DB)})
        return 2

    db = sqlite3.connect(str(MC_DB), timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    try:
        depth = _queue_depth(db)
        _audit({"event": "queue_depth", "depth": depth})
        if args.apply and depth > args.queue_ceiling:
            print(
                f"BACKPRESSURE: queue depth {depth} > ceiling "
                f"{args.queue_ceiling}; no new chunks enqueued "
                f"(run --dry-run to inspect without writing).",
                file=sys.stderr,
            )
            _audit({"event": "refused_queue_depth", "depth": depth,
                    "ceiling": args.queue_ceiling})
            return 0

        w_start = _next_w_number(db)
        thresholds = (report.get("thresholds", {}) or {}).get("rows", {}) or {}

        chunks_with_meta: list[tuple[Chunk, str, str]] = []
        enqueued_ids: list[str] = []
        accepted = 0
        for gap in gaps:
            chunk = _build_chunk(gap, w_start + accepted)
            existing = _active_chunk_for_gap(db, gap.gap_id)
            verdict = (
                GuardrailVerdict(
                    ok=False,
                    reason=(
                        f"duplicate suppressed: gap already has active chunk "
                        f"{existing[0]} ({existing[1]})"
                    ),
                )
                if existing
                else _enforce_guardrails(chunk, thresholds)
            )
            decision = "ENQUEUE" if verdict.ok and args.apply else (
                "WOULD-ENQUEUE" if verdict.ok else "REJECT"
            )
            reason = verdict.reason or ("dry-run" if not args.apply else "ok")
            chunks_with_meta.append((chunk, decision, reason))
            _audit({
                "event": "chunk_decision",
                "gap_id": gap.gap_id,
                "title": chunk.title,
                "score": gap.score,
                "score_decomp": gap.score_decomposition(),
                "decision": decision,
                "reason": reason,
                "apply": args.apply,
            })
            if verdict.ok and args.apply:
                chunk_id = _enqueue_chunk(db, chunk, w_start + accepted)
                enqueued_ids.append(chunk_id)
                _audit({"event": "chunk_enqueued", "id": chunk_id,
                        "title": chunk.title})
            if verdict.ok:
                accepted += 1
                if accepted >= args.max:
                    break
        db.commit()
    finally:
        db.close()

    # 4. Print and log summary.
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== W45 self_improve -- {mode} -- max={args.max} "
          f"queue_depth={depth} ===")
    _print_chunk_table(chunks_with_meta)
    if enqueued_ids:
        print(f"\nEnqueued {len(enqueued_ids)} chunk(s): {enqueued_ids}")
    elif args.apply:
        print("\nNo chunks enqueued (all rejected by guardrails).")
    else:
        print("\nDry-run only; nothing written to mc.db. "
              "Re-run with --apply to enqueue.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
