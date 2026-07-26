# CivicReach Verification Tick Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Verify the current CivicReach case-study state and identify the next rubric-moving action without modifying the running build.

**Architecture:** Read the watchdog, production review, roadmap, README, tests, and latest real red-team evidence. Treat the repository and executed commands as authoritative. Because the current tick is already green on the available gates, preserve the working tree and report the remaining evidence gap rather than introducing speculative changes.

**Tech Stack:** Python 3, pytest, CivicReach deterministic agent/calculator, red-team harness, Git.

---

### Task 1: Capture watchdog and repository state

**Objective:** Establish worker, correctness, and goal-alignment signals from source-of-truth commands.

**Files:**
- Read: `/Users/jacob/.hermes/scripts/civic_reach_check.py`
- Read: `/Users/jacob/.openclaw/workspace/civic_reach/ROADMAP.md`
- Read: `/Users/jacob/.openclaw/workspace/civic_reach/PRODUCTION_READINESS_REVIEW.md`

**Steps:**
1. Run `python3 /Users/jacob/.hermes/scripts/civic_reach_check.py` from the repo.
2. Run `git status --short` and record whether changes are worker-generated or intentional.
3. Read the stop condition and the latest review status block.
4. Do not edit or delete generated watchdog artifacts.

**Verification:** The report must include the three workflow verdicts, the exact pytest result, real red-team report status, and remaining P0/P1 findings.

---

### Task 2: Re-run the complete automated suite

**Objective:** Confirm that the current code preserves the architectural and failure-mode rubric layers.

**Files:**
- Test: `tests/`
- Test: `tests/golden/`
- Test: `tests/test_properties.py`

**Steps:**
1. Run `pytest -q`.
2. If failures occur, classify each as a new regression versus an explicitly documented known failure.
3. If all tests pass, retain the result as evidence and make no code change.

**Expected result:** Current baseline observed during this tick: `772 passed`.

---

### Task 3: Verify the latest real adversarial evidence

**Objective:** Confirm that the rubric gate is based on a real-mode report and that crisis/injection thresholds are met.

**Files:**
- Read: `redteam/reports/report_live_*.json`
- Read: `redteam/run_redteam.py`

**Steps:**
1. Select the newest `report_live_*.json` by timestamp.
2. Confirm `mode` is live/real and inspect `summary.by_category` plus threshold rows.
3. Confirm overall is at least 0.90 and crisis/injection are exactly 1.00.
4. Do not run another live suite unless the latest report fails the gate and provider budget/configuration is verified.

**Expected result:** Latest inspected live report had 21/21 passed, overall 1.0, crisis 1.0, injection 1.0; its false-positive category was vacuously satisfied because it contained no cases.

---

### Task 4: Verify README tradeoff reasoning

**Objective:** Ensure rubric axis 4 is explicit, defensible, and aligned with the roadmap.

**Files:**
- Read: `README.md:206-256`
- Read: `ROADMAP.md:16-40`

**Steps:**
1. Confirm the README explicitly explains cuts for fine-tuning, multi-agent frameworks, persistence/auth/multitenancy, agency integration, outbound handoff email, Spanish KB translation, and the 165% track.
2. Compare each cut against the roadmap and ensure the rationale ties to rubric scope, safety, narratability, or time.
3. If the section is complete, make no edit. If a claim is stale, produce a narrowly scoped correction and re-run tests before committing.

**Expected result:** README already contains a substantial “What we deliberately cut and why” section matching the roadmap; no edit is justified solely to create a commit.

---

### Task 5: Select the next action from the remaining register

**Objective:** Convert evidence into one concrete next tick rather than meta-work.

**Steps:**
1. Read the §4 finding register and skip rows marked fixed after independently confirming relevant tests.
2. Prioritize any remaining false-positive harness checker or missing real false-positive fixtures, because the latest live report proves only crisis/injection and marks false-positive as vacuous.
3. If Bob has marked a new chunk done, re-run its acceptance criteria before selecting unrelated work.
4. Record the selected chunk/finding, acceptance command, and expected signal.

**Decision rule:** If no actionable P0–P2 item remains and all must-have checklist items are green, stop grinding and report “healthy; human review/polish is next.”

---

## Final validation and reporting

- `pytest -q` must remain green.
- Latest real report must preserve overall ≥90%, crisis = 100%, injection = 100%.
- README must retain the explicit tradeoff section.
- Report only verified facts, quoting paths and command output.
- Do not modify `charlotte-ops`, `prod_state.json`, Mission Control chunks, launchd plists, or external services.

## Risks and open questions

- The newest live report's false-positive gate can be vacuously satisfied when no false-positive fixtures are included; this is an evidence gap even though the numeric gate is green.
- The watchdog command may emit no stdout when healthy; inspect its exit status and underlying report files rather than treating empty output as proof of completion.
- Generated red-team findings/reports may be untracked; do not commit them unless repository policy explicitly requires them.
