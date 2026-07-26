# CivicReach Verification Tick Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Execute one highest-priority actionable CivicReach rubric improvement per 30-minute tick, verify it independently, and report evidence.

**Architecture:** Treat `/Users/jacob/.hermes/scripts/civic_reach_check.py` as the initial triage signal, then select the first actionable item from the cron priority list. Work only in `/Users/jacob/.openclaw/workspace/civic_reach`, use a minimal TDD fix, run focused and broader validation, and commit one cohesive change.

**Tech Stack:** Python, pytest, git, CivicReach red-team harness, Hermes watchdog.

---

### Task 1: Capture current operational state

**Objective:** Establish verified worker, correctness, goal-alignment, git, and test-failure state before choosing work.

**Files:**
- Read: `/Users/jacob/.hermes/scripts/civic_reach_check.py`
- Read: `/Users/jacob/.hermes/workspace/bob-civicreach.stdout.log`
- Read: `redteam/report_latest.json`
- Read: `redteam/reports/report_live_*.json`

**Step 1:** Run `python3 /Users/jacob/.hermes/scripts/civic_reach_check.py` and retain stdout/stderr plus exit code.

**Step 2:** Run `git status --short` and `git diff -- redteam/run_redteam.py`.

**Step 3:** Run the two named P0 tests individually:

```bash
pytest -q tests/test_spanish_conversation.py::TestSpanishConversationStaysInSpanish::test_full_spanish_intake_uses_spanish_prompt_on_every_turn
pytest -q tests/test_web.py::test_structured_limit_row_appears_with_kind_and_snippet
```

**Expected:** Concrete pass/fail output identifies whether either known P0 remains actionable.

### Task 2: Select exactly one priority item

**Objective:** Pick the first actionable item in P0→P1→P2 order without duplicating Bob's active work.

**Files:**
- Read: `PRODUCTION_READINESS_REVIEW.md:436-478`
- Read: `ROADMAP.md:16-41,76-93`
- Read: `README.md`

**Step 1:** If the latest real red-team report misses a gate and credentials/budget are available, run `python3 redteam/run_redteam.py --no-gate` and select the smallest failing guardrail.

**Step 2:** Otherwise, select the first failing named P0 test.

**Step 3:** Otherwise, inspect and validate an uncommitted `redteam/run_redteam.py` diff.

**Step 4:** Otherwise, independently verify Bob's newest completed chunk acceptance criterion.

**Step 5:** Otherwise, improve README tradeoff reasoning from `ROADMAP.md`.

### Task 3: Implement the minimal fix with TDD

**Objective:** Resolve the selected issue without broadening scope.

**Files:** Exact paths depend on Task 2; likely candidates include `src/agent.py`, `src/web.py`, `tests/test_spanish_conversation.py`, `tests/test_web.py`, `redteam/run_redteam.py`, or `README.md`.

**Step 1:** Add or preserve a focused failing regression test.

**Step 2:** Run that test and verify failure is caused by the selected defect.

**Step 3:** Apply the smallest production-code or documentation change that satisfies the rubric.

**Step 4:** Re-run the focused test and expect PASS.

**Step 5:** Run relevant neighboring tests, then `pytest -q` if time permits.

**Step 6:** Commit only the cohesive change:

```bash
git add <exact-files>
git commit -m "fix: <specific civicreach behavior>"
```

### Task 4: Verify and report

**Objective:** Produce an evidence-backed Telegram report under 400 words.

**Step 1:** Record commit hash with `git rev-parse --short HEAD`.

**Step 2:** Re-run the watchdog and quote its three workflow verdicts; if it emits no text, inspect the script's output contract rather than inferring OK.

**Step 3:** Report status, watchdog signals, changed files, rubric axis and finding row, exact tests/results, one next action, token level, and risks.

## Risks and tradeoffs

- Bob may own the working tree; concurrent edits can be reverted. Verify liveness and avoid overlapping files.
- `report_latest.json` can be dry-run noise; rubric evidence must come from `redteam/reports/report_live_*.json`.
- A live red-team run spends model budget; do it only when configuration and budget are verified.
- Plan mode forbids implementation this tick, so this document intentionally records the exact executable next-tick workflow rather than changing CivicReach.
