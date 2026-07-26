# CivicReach Build Tick Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Move CivicReach from a passing local suite to a rubric-ready demo by resolving the remaining adversarial failures and verifying the real-mode stop condition.

**Architecture:** Preserve the explicit state-machine, deterministic guardrail, and calculator boundaries. Work only in `civic_reach`; do not broaden scope or alter Rescue Ops state. Prioritize measured rubric gaps over cleanup.

**Tech Stack:** Python 3, pytest, existing `src/` agent/guardrails, `redteam/run_redteam.py`, JSON reports.

---

## Current context

- Source: `/Users/jacob/.openclaw/workspace/civic_reach`.
- Watchdog command returned no output and exit code 0, so no watchdog alert was emitted.
- `pytest -q` returned `779 passed in 7.84s`.
- Git has uncommitted watchdog/red-team artifacts: modified `redteam/findings/TOP.md`, untracked `.hermes/`, failure queue, finding JSONs, simulator reports, and a fixture. Do not commit these unless their provenance and intended ownership are established.
- Latest real reports differ: `redteam/reports/report_live_20260725T201937Z.json` is 21/21 with crisis and injection both 100%; the older `report_live_2026-07-25.json` is 89/92 (96.7%), with crisis/injection 100%, ambiguity 91.7%, off-topic 80%. Use the newest timestamped report as current evidence, but preserve the older report for regression comparison.
- README already contains a substantial “What we deliberately cut and why” section (line 206 onward), satisfying the previously thin documentation gap; do not rewrite it without a measured deficiency.
- Production review says remaining historical false positives were queued as W27, but current tests and the newest crisis/injection report do not prove the full 92-case gate.

## Stop condition and acceptance

1. All must-have checklist items in `ROADMAP.md` are green.
2. A real-mode report exists with overall >=90%, crisis =100%, injection =100%.
3. Full pytest remains green.
4. Every claim is backed by a command output or report path; no dry-run pass rate is treated as real evidence.

---

### Task 1: Establish a clean evidence baseline

**Objective:** Identify the canonical current real report and separate generated artifacts from intentional source changes.

**Files:**
- Read: `redteam/reports/report_live_*.json`
- Read: `git status --short`, `ROADMAP.md`, `PRODUCTION_READINESS_REVIEW.md`

**Steps:**
1. Enumerate all timestamped real reports and parse `mode`, totals, category rates, crisis failures, and injection failures.
2. Inspect `git diff -- redteam/findings/TOP.md` and list untracked artifact timestamps.
3. Confirm whether any source or test files are modified; do not alter generated artifacts in this task.
4. Record the baseline in the next build-agent report.

**Validation:** The selected report must be the newest timestamped `report_live_*.json`, and its summary must be quoted by path. If it is only a crisis/injection subset, mark the overall gate as unverified.

---

### Task 2: Reproduce the remaining full-suite red-team failures

**Objective:** Convert the older 3 failures into explicit reproducible test cases before changing code.

**Files:**
- Read: `redteam/reports/report_live_2026-07-25.json`
- Inspect: `redteam/run_redteam.py`, fixture files, checker registrations
- Test target: the smallest relevant red-team fixture/checker command

**Steps:**
1. Extract the three failed result records from the 92-case report.
2. Map each failure to its fixture, detector, and checker.
3. Run each failing case independently and capture the exact observed output and expected assertion.
4. Verify whether the failures are already fixed in current HEAD or remain reproducible.

**Validation:** Each case has a command and a concrete pass/fail result. If no full-suite live report can be safely rerun because credentials/budget are unavailable, state that as an evidence gap rather than inferring success.

---

### Task 3: Fix one remaining red-team failure with a regression test

**Objective:** Fix the highest-impact reproducible failure, preferably an ordinary in-scope message incorrectly classified as off-topic.

**Files:**
- Modify: the guardrail module identified in Task 2 (likely `src/guardrails/scope.py` or adjacent detector)
- Test: existing guardrail/red-team test file identified from the repository

**Steps:**
1. Write a focused failing regression test for the exact input and expected normal routing.
2. Run only that test and confirm RED.
3. Implement the smallest boundary-aware detector change; avoid broad keyword deletion that could weaken genuine legal/out-of-scope detection.
4. Run the focused test and then the relevant guardrail test module.
5. Run `pytest -q` and inspect the diff.
6. Commit only intentional source/test changes with a focused message.

**Validation:** Focused regression passes; full suite reports zero failures; detector behavior for genuine out-of-scope examples remains covered.

---

### Task 4: Verify the full real-mode adversarial gate

**Objective:** Produce current evidence for all rubric categories, not merely a crisis/injection subset.

**Files:**
- Generate: `redteam/reports/report_live_*.json`
- Read: `redteam/report_latest.json`

**Steps:**
1. Confirm provider configuration and budget before running the live suite.
2. Run the repository’s documented real command with `--no-gate` if gate behavior would stop collection.
3. Parse the resulting report by category, including total, passed, failed, overall rate, crisis failures, and injection failures.
4. If any category is below threshold, return to Task 2/3 with the exact failures.

**Validation:** Current report is `mode: real`, covers the full intended fixture set, overall >=90%, crisis 100%, injection 100%, and the command/report path is recorded.

---

### Task 5: Final rubric and demo verification

**Objective:** Confirm the case-study deliverable is ready for human review without adding non-rubric scope.

**Files:**
- Read: `ROADMAP.md`, `README.md`, `tests/golden/`, `tests/test_properties.py`
- Run: `pytest -q`

**Steps:**
1. Check each must-have item in the roadmap against a concrete artifact or test.
2. Run the complete pytest suite.
3. Inspect README sections “What we deliberately cut and why” and “Known limitations” for consistency with current behavior.
4. Review `git status` and ensure generated logs/reports are not accidentally committed.
5. Report remaining risks as explicit evidence gaps, not assumptions.

**Validation:** Clean intentional diff, full pytest green, current real-mode gate green, README tradeoff rationale present, and no unverified “done” claims.

## Risks and tradeoffs

- Running the full live red-team suite consumes provider budget and may be impossible without a configured model; do not substitute dry-run evidence.
- The newest real report is a 21-case crisis/injection subset, so it proves those categories only, not the overall 92-case gate.
- Generated red-team artifacts currently pollute the working tree; committing them could obscure the actual source change.
- Deadline favors a narrow, test-backed fix and demo-path reliability over new features such as the 165% separate-household track.

## Open questions

- Which exact three records failed in the older 92-case report, and do they still reproduce on current HEAD?
- Is the full 92-case live fixture runnable with the configured provider and budget?
- Which generated artifacts are intentionally tracked by the build workflow versus disposable simulator output?
