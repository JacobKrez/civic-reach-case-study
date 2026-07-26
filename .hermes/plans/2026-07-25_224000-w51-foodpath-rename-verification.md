# W51 FoodPath Rename Acceptance Verification Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Independently verify that Bob's repeatedly completed W51 chunk made CivicReach → FoodPath brand skew impossible on the reviewer-facing surface.

**Architecture:** Treat branding as an invariant rather than a visual spot-check. Re-run the existing acceptance target that scans reviewer-facing source, templates, tests, and docs for stale product-name literals, then run the full regression suite and inspect the live entry surfaces. Do not alter production code unless a failing acceptance check proves a concrete gap.

**Tech Stack:** Python 3, pytest, git, FastAPI/Jinja/static assets.

---

## Current context / assumptions

- Bob's stdout records W51 (`chk_3f9a3591b86ccd`) as `done` repeatedly, most recently at `2026-07-25 22:38:26`, but no supervisor-verification line follows it.
- Current HEAD before verification: `bf34ad9ba5855f7eff6c06e2404f63a8993aaf9e`.
- The working tree contains generated red-team artifacts; verification must not stage, delete, or modify them.
- README already uses FoodPath and includes a substantial `What we deliberately cut and why` section.

### Task 1: Identify the exact W51 acceptance contract

**Objective:** Recover the chunk's acceptance criteria from Mission Control or the worker/chunk specification.

**Files:**
- Read: `/Users/jacob/.hermes/workspace/bob-civicreach.stdout.log`
- Read: the Mission Control chunk record for `chk_3f9a3591b86ccd`

**Step 1:** Query the CivicReach Mission Control chunk by ID and record every acceptance command verbatim.

**Step 2:** If the API is unavailable, search the repository and worker logs for the chunk ID/title and recover the acceptance commands from the local specification.

**Step 3:** Do not infer or auto-pass missing checks; report a blocked verification if the contract cannot be recovered.

### Task 2: Re-run the branding invariant

**Objective:** Prove that stale `CivicReach` product branding cannot appear on reviewer-facing surfaces.

**Files:**
- Inspect: `src/branding.py`
- Inspect: `src/web.py`
- Inspect: `src/cli.py`
- Inspect: `templates/**` and `static/**`
- Test: the exact W51 acceptance test(s) recovered in Task 1

**Step 1:** Run each exact W51 acceptance command.

**Expected:** Every command exits 0; no reviewer-facing stale-brand match is reported.

**Step 2:** Run the narrow branding tests directly if they are not already included.

**Expected:** All branding tests pass.

**Step 3:** Inspect any allowed historical/internal `CivicReach` occurrences and document why each is excluded from the user-facing invariant.

### Task 3: Run regression validation

**Objective:** Ensure the rename did not regress the graded demo path.

**Files:**
- Test: `tests/`

**Step 1:** Run `pytest -q`.

**Expected:** `772 passed` with exit code 0 (or a higher all-passing count if Bob lands new work first).

**Step 2:** Run `python3 redteam/run_redteam.py --dry-run --no-gate`.

**Expected:** Harness exits 0 with no traceback; do not interpret dry-run pass rates as real guardrail evidence.

**Step 3:** Re-read the latest real report under `redteam/reports/report_live_*.json`.

**Expected:** Overall ≥90%, crisis 100%, injection 100%.

### Task 4: Record verification outcome

**Objective:** Produce auditable evidence without changing production code unnecessarily.

**Files:**
- Modify only if a real branding gap is found: the exact affected source/test file
- Do not modify: generated `redteam/findings/**`, `redteam/reports/**`, or queue artifacts

**Step 1:** If all checks pass, report W51 independently verified with chunk ID, exact commands, results, and current HEAD. No commit is required for verification-only work.

**Step 2:** If a gap is found, write a failing test that reproduces the stale-brand surface.

**Step 3:** Implement the smallest centralized fix using `src/branding.py`; do not duplicate product-name literals.

**Step 4:** Re-run the narrow test, full pytest, and dry-run harness.

**Step 5:** Commit only the focused fix and test:

```bash
git add <exact-test-file> <exact-source-file>
git commit -m "fix(branding): prevent stale CivicReach product copy"
```

## Risks / tradeoffs / open questions

- W51's repeated `done` entries may indicate reconciliation/requeue churn rather than repeated code changes; only the acceptance contract can distinguish those states.
- Historical references in planning artifacts or the case-study prompt may legitimately retain `CivicReach`; the invariant should target reviewer-facing product copy, not blindly rewrite source provenance.
- Generated red-team files are currently untracked/modified and must remain untouched.
