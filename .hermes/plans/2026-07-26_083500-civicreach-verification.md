# CivicReach / FoodPath Verification Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Close the remaining rubric gap by producing a verified real red-team gate and keeping the demo-path evidence and tradeoff narrative submission-ready.

**Architecture:** Preserve the explicit typed state machine, deterministic calculator, owned guardrails, and local cited retrieval. Do not expand core scope; spend the remaining work on live adversarial evidence, acceptance verification, and reviewer-facing documentation.

**Tech Stack:** Python 3, pytest, existing `agentic.prompts.call_minimax` model wrapper, `redteam/run_redteam.py`, Markdown documentation.

---

## Current verified context

- `pytest -q` currently returns **779 passed in 7.34s**.
- The two previously known tests both pass: **2 passed in 0.65s**.
- `redteam/report_latest.json` is `mode: dry-run`, 93/93; this is not rubric evidence.
- Latest inspected live report, `redteam/reports/report_live_2026-07-25.json`, is 89/92 (96.7%), with crisis 12/12 and injection 30/30; ambiguity is 11/12 and off_topic is 8/10.
- Bob’s log shows repeated W51 dispatches and one supervisor timeout against `localhost:8181`; the latest visible attempt ended `done`, but acceptance must be rerun independently.
- `README.md` currently has no visible “What we cut and why” heading in its opening section; roadmap text is the source for the required tradeoff content.

## Stop condition and priority

Do not chase commit count. The stop condition is the must-have checklist green plus a **real-mode** adversarial report with overall >=90%, crisis 100%, and injection 100%.

### Task 1: Re-run the two targeted regression tests

**Objective:** Confirm the known Spanish/web regressions remain green before changing documentation or red-team behavior.

**Files:** None.

Run:

```bash
pytest -q tests/test_spanish_conversation.py::TestSpanishConversationStaysInSpanish::test_full_spanish_intake_uses_spanish_prompt_on_every_turn tests/test_web.py::test_structured_limit_row_appears_with_kind_and_snippet
```

Expected: `2 passed`.

### Task 2: Independently verify W51 acceptance

**Objective:** Determine whether W51’s repeated “done” status corresponds to actual repository behavior, not merely a worker marker.

**Files:** `src/`, `tests/`, and the W51 acceptance criteria stored in Mission Control/worker state; do not edit while Bob owns the tree.

1. Read the W51 chunk specification and list each acceptance criterion.
2. Locate every CivicReach brand string and verify the intended FoodPath rename behavior with repository searches.
3. Run the exact targeted tests named by the chunk, then run `pytest -q`.
4. Record pass/fail evidence and any uncommitted changes. Do not auto-pass a criterion that was not exercised.

Expected: every criterion has a command/output or an explicit blocker.

### Task 3: Inspect and classify live red-team failures

**Objective:** Turn the current live failures into the smallest possible fixes or a documented operator rerun.

**Files:** `redteam/reports/report_live_*.json`, `redteam/run_redteam.py`, fixture files, guardrail implementation and tests.

1. Parse the latest live report and print the failed case IDs, category, input, expected behavior, and observed behavior.
2. Check whether failures are deterministic guardrail bugs, model variance, fixture/checker defects, or missing live evidence.
3. If a code defect is proven, write a focused regression test first, implement the minimal fix through the existing guardrail path, and run the focused test plus full pytest.
4. If the report is stale or the model is unavailable, do not fabricate a result: run the approved live command only when configured and budgeted, otherwise leave a precise rerun command and blocker note.

Expected: a new live report meeting the stop condition, or a reproducible blocker with failure IDs.

### Task 4: Add the graded tradeoff section to README

**Objective:** Make rubric axis 4 explicit and reviewer-readable without adding scope.

**Files:** Modify `README.md` near the scope/architecture explanation.

Add a heading exactly equivalent to `## What we cut and why`, covering:

- Fine-tuning/LoRA/RunPod training: cut because prompt engineering plus tool calling is sufficient and rushed synthetic fine-tuning risks regression on novel inputs and safety behavior.
- LangGraph/AutoGen-style multi-agent frameworks: cut because the explicit state machine is more transparent and narratable for this case study.
- Auth, persistence beyond a session, multi-tenancy, deployment/admin infrastructure: cut because they are explicitly outside the graded surface.
- Real government scraping or application submission: cut because the case study forbids it.
- Engineering rigor remains in scope: deterministic calculation, owned guardrails, cited retrieval, tests, and red-team hardening.

Preserve the distinction between eligibility screening and an official determination. Run README lint/checks if present, then `pytest -q`.

### Task 5: Final cold-path verification and submission record

**Objective:** Verify the artifact a reviewer will actually run and capture the final gate state.

**Files:** README, roadmap, latest red-team report; no unrelated scope changes.

1. Run the full test suite.
2. Run the web/CLI smoke path with the repository’s deterministic or configured test harness, exercising intake completion, calculator invocation, verdict rendering, citations, crisis, and injection behavior.
3. Verify latest live report mode and thresholds from JSON, not dry-run output.
4. Review `git diff`, ensure no generated reports/secrets/unrelated worker artifacts are staged.
5. Commit only intentional repository changes with a descriptive message.

Expected: full tests green; smoke path renders a deterministic verdict; real report satisfies >=90% overall and 100% crisis/injection; clean, reviewable diff.

## Risks and tradeoffs

- Live red-team execution consumes model budget and can vary by provider; preserve report provenance and distinguish model variance from code defects.
- Bob’s worker has produced false “done” signals and encountered Mission Control timeouts; independent acceptance verification is mandatory.
- Generated simulator/findings files may be untracked or modified; do not commit them unless the repository’s established workflow explicitly requires them.
- README edits improve rubric axis 4 but do not substitute for the real adversarial gate.

## Handoff

Execute one task per tick, starting with live-failure classification only after confirming worker ownership and tree status. After each code change, run focused tests and then the full suite; stop when the rubric gate is green and move to human rehearsal rather than adding features.
