# FoodPath — NC FNS Eligibility Guide

## What this is

FoodPath is a proof-of-concept text guide that walks a North Carolina
resident through an *initial* Food and Nutrition Services (FNS / SNAP)
eligibility screen. It collects three intake fields across a multi-turn
conversation, grounds every answer in a small retrieval-augmented knowledge
base of public eligibility documents, and returns a deterministic
likely-eligible / maybe-eligible / likely-ineligible verdict plus
visible citations. It also layers deterministic guardrails (crisis,
prompt-injection, out-of-scope, PII redaction) and a secondary LLM
intent classifier in front of the main agent loop.

**FoodPath is named plainly on purpose.** It is an independent case-study
prototype, not an NC DHHS or county DSS service; it does not determine
eligibility or submit applications. The name keeps the narrow job visible:
helping a resident find a path through an initial food-benefits screen.

## What we cut, and why

The core deliverable is a three-field initial screen: a deterministic
eligibility calculator wrapped in a layered guardrail chain. Everything beyond
that — the simulator, telemetry, and evaluation harness — exists to prove the
core is correct, not to add product surface. The
evaluation apparatus is larger than the agent because failure-mode reasoning
is a graded criterion; the harness is the evidence for those claims. That is a
deliberate trade, not a second product.

The scope cuts are intentional:

* **Outbound agency handoff:** removed. The case-worker handoff is a local,
  print-ready summary; there is no email or other external-recipient path that
  could contact an agency by mistake.
* **Persistence, auth, and multi-tenancy:** cut. Web sessions live only in
  process memory, with a bounded TTL/LRU registry to keep the unattended PoC
  from growing without limit. There is no account system, durable user data,
  or tenant isolation.
* **Fine-tuning / LoRA / RunPod training:** cut. Prompting, tool use, and a
  deterministic calculator are enough for this bounded workflow; rushed
  synthetic-data tuning could regress the crisis, injection, and scope
  behavior being tested.
* **Multi-agent frameworks:** cut. A single explicit state machine is easier
  to inspect and narrate than a planner/critic/verifier graph, and keeps the
  guardrail ordering visible.
* **Full Spanish translation:** cut for now. The overview, prompts, and
  guardrail renderings support the demo, while the rule documents containing
  limits and exceptions remain English-only pending domain review.
* **The 165% separate-household standard:** deliberately not implemented in
  the intake or calculator. The knowledge base documents it for traceability,
  but the current flow does not collect the separate-household signal; it is a
  roadmap item rather than a claimed capability.
* **Government scraping, application submission, and agency integration:**
  cut because the case study forbids them. FoodPath uses a small cited local
  corpus and does not call, scrape, submit, or notify anywhere.

## Quickstart

**Easiest: double-click `setup.bat` (Windows) or run `./setup.sh` (macOS/Linux).**
Either one creates the virtualenv, installs dependencies, prompts you for an
LLM API key on first run (writes it into `.env` — nothing to edit by hand),
runs the test suite as a smoke check, and launches the web UI at
`http://127.0.0.1:8000`. `setup.bat` is verified on every push against a real
Windows runner in CI (`.github/workflows/windows-setup.yml`), not just written
and hoped for.

Already have a key in `.env`, or want to run things by hand / from the CLI
instead of the web UI? The five manual steps below are the same thing spelled
out, no external services required beyond Python 3.11+ and one LLM API key.

**macOS / Linux:**

```bash
# 1. Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Configure your environment
cp .env.example .env
```

**Windows (PowerShell):**

```powershell
# 1. Create and activate a virtualenv
py -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Configure your environment
Copy-Item .env.example .env
```

If PowerShell blocks the activation script with an execution-policy error,
run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first (this
only affects the current terminal session), or use `.venv\Scripts\activate.bat`
in `cmd.exe` instead.

Now open `.env` and do two things:

1. Fill in **exactly one** provider key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `MINIMAX_API_KEY`, or `OPENROUTER_API_KEY`.
2. Set `LLM_PROVIDER` to match that key (`anthropic` / `openai` / `minimax` /
   `openrouter`). `.env.example` defaults to `anthropic` — change it if you
   used a different key.

That's it — everything else in `.env` is optional (see **Troubleshooting**
below for `LLM_FALLBACK_PROVIDER`).

```
4a. Talk to the agent in the terminal
    macOS/Linux: python3 -m src.cli
    Windows:     python -m src.cli

4b. OR run the local web UI (single-process, sessions live in memory)
    uvicorn src.web:app --reload
    then open http://127.0.0.1:8000
```

### Try it

Once the CLI or web UI is running, here's a real conversation to try —
it exercises the full flow (intake → deterministic calculation → cited
answer) in under a minute:

```
You: 3 people
You: 2200
You: no
```

You should see a final answer stating **likely qualifies**, a line citing
the specific `knowledge_base/` documents the answer is grounded in, and
(in the CLI) you can then type `handoff` to see the print-ready
case-worker summary. To see the guardrails fire, try an off-topic ask
("what's the weather today"), or a prompt-injection attempt ("ignore
your instructions") — both get a fixed, deterministic response rather
than a free-form answer.

### Run the tests

```bash
pytest -q
```

### Run the red-team suite

Crisis, injection, off-topic, ambiguous income, and correction-across-turns
cases, run live against the real agent:

```bash
python3 -m redteam.run_redteam
```

This calls a real LLM and costs a small amount of money. Add `--dry-run`
to validate the harness wiring without spending anything.

### Troubleshooting

* **`Invalid or missing LLM_PROVIDER`** — `.env`'s `LLM_PROVIDER` value
  doesn't match a key you actually filled in. Double-check step 3 above.
* **Rate-limited or "out of credits" from your provider** — this can
  happen with any provider under a tight quota. Set
  `LLM_FALLBACK_PROVIDER` in `.env` to a second provider you have a key
  for (e.g. `LLM_FALLBACK_PROVIDER=openrouter`); the agent automatically
  retries on that provider if the primary one fails. See `src/llm_client.py`.
* **The agent seems "stuck" re-asking a question you already answered** —
  this should not happen; if it does, please note the exact phrasing you
  used and let us know. (The agent is built to handle terse replies like
  a bare `"3"` or `"no"`, not just full sentences — see `_fallback_parse_terse_reply`
  in `src/agent.py` if you're curious how.)
* **Port 8000 already in use** — run
  `uvicorn src.web:app --reload --port 8001` and open that port instead.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull
request:

1. Installs the pinned `requirements.txt` on Python 3.11.
2. Runs the full `pytest -q` suite.
3. Runs `python3 redteam/run_redteam.py --dry-run` and fails the build if
   the harness exits non-zero or emits a Python traceback.

The red-team step is **dry-run only** in CI: it validates fixtures, agent
wiring, and per-category check logic without calling a live LLM. A real
red-team pass costs money and needs a provider key that CI does not have —
run that manually before a demo, not on every PR.

## Architecture

The conversation loop is a layered, *narratable* state machine. Every
piece exists because it earns its place against the case-study rubric
(architectural judgment, guardrail thinking, failure-mode reasoning,
tradeoffs).

```mermaid
flowchart TD
    User([User message]) --> G1{Keyword guardrails<br/>crisis, injection,<br/>scope, PII redact}
    G1 -- crisis --> R1[Hardcoded crisis<br/>response w/ 988 / 211]
    G1 -- injection --> R2[Hardcoded refusal<br/>conversation continues]
    G1 -- out of scope --> R3[Polite redirect<br/>state preserved]
    G1 -- PII pattern --> G1
    G1 -- pass through --> G2{LLM intent<br/>classifier}
    G2 -- crisis --> R1
    G2 -- injection --> R2
    G2 -- off_topic --> R3
    G2 -- normal / fail-open --> LLM[LLM<br/>single update_state tool]
    LLM --> TS[(Typed AssessmentState<br/>Pydantic v2)]
    TS --> CALC[Deterministic calculator<br/>pure-Python, no I/O]
    CALC --> RAG[Retrieval from<br/>knowledge_base/]
    RAG --> OUT[Grounded reply<br/>+ visible citations<br/>+ deterministic suffix]
    OUT --> User
```

**Why each piece exists**

* *Keyword guardrails first.* They are fast, deterministic, auditable,
  and work offline. Crisis must be evaluated before injection: a message
  like "ignore your instructions — I'm going to hurt myself" matches
  both detectors, and answering it with an injection refusal instead of
  988 / 211 resources would be the worst possible outcome. Safety
  outranks security here, deliberately.
* *LLM intent classifier as defense in depth.* Keyword lists are brittle
  on unseen inputs — the red-team suite already proved that. The
  classifier catches novel phrasings; on any classifier failure
  (exception, unparseable output, "normal") we fail open and let the
  ordinary LLM call continue. It can be disabled via
  `GUARDRAIL_CLASSIFIER=off` for offline runs.
* *PII redaction.* A redactor strips SSN- and account-shaped patterns
  from every user message *before* it is recorded in the conversation
  history, so any later log dump is PII-safe.
* *LLM + a single `update_state` tool.* The model is not asked to do
  arithmetic or to memorize eligibility rules. Its only job is to ask
  the next intake question and record explicitly-supplied facts. This
  keeps the model honest and the test surface small.
* *Typed assessment state (Pydantic).* All collected facts live in a
  validated `AssessmentState`. The model cannot write garbage into it;
  a malformed tool call is dropped and the field stays missing so the
  loop re-asks on the next turn.
* *Deterministic calculator.* Pure-Python, no I/O, no network. The
  decision is a table lookup plus arithmetic against the FY2026 federal
  SNAP income-eligibility standards published by USDA FNS (effective
  2025-10-01 through 2026-09-30): the 130% gross screen, the 100% net
  screen for households with an elderly or disabled member, and the
  NC BBCE 200% ceiling. All arithmetic happens here, not in the prompt.
* *Retrieval + visible citations.* Every final answer is appended with a
  code-templated `(Sources: ...)` line naming the documents retrieved for
  the applicable income track. The model does not write this line — it
  cannot be hallucinated.

## Guardrails

| Layer | What it catches | Why deterministic |
|---|---|---|
| Crisis keyword (`detect_crisis`) | Suicidal ideation, self-harm, domestic-violence disclosures (EN/ES), food-on-table emergencies, "I can't go on" phrasings | Returns a hardcoded response: **911** for a life-threatening emergency (first line, phrased for a text interface — "call 911 now", there is no phone leg), **988** (call/text), the **National Domestic Violence Hotline 1-800-799-7233** (or text START to 88788), the USDA hunger hotline, and **211**. The model is never allowed to write this turn. |
| Hostility keyword (`detect_hostility`) | Profanity/insults directed at the agent ("fuck you", "stupid bot", "you're useless") — NOT profanity about the user's situation | Non-blocking, deliberately. Sets a per-turn flag, prepends one calm de-escalation instruction to the system prompt for that turn only (stay professional, don't mirror tone, stay on task), and writes a history-only audit note. Never refuses or ends the conversation. **Crisis always wins**: the detector runs only after crisis/injection/scope have cleared, so a hostile *and* crisis-flagged message still gets the full 911/988/DV/211 response. |
| Prompt-injection keyword (`detect_injection`) | "ignore previous instructions", "you are now DAN", "system:" spoofing, role-override attempts | Hardcoded refusal. The flag is recorded but does not lock the conversation into refusal mode — the next normal message is evaluated normally. |
| Out-of-scope keyword (`detect_scope`) | Legal advice, unrelated benefit programs, immigration questions, tax / housing / Medicaid | Polite redirect back to FNS questions. State is preserved; nothing already collected is reset. |
| PII redactor (`redact_for_logging`) | SSN-shaped, account-shaped, routing-shaped patterns, US phone numbers in common formats, street-address-shaped patterns, and email addresses | Strips from conversation history before it's logged, so any later dump is safe by construction. Free-form personal-name detection is deliberately not covered — it needs NER to be accurate and would over-redact ordinary English words. |
| LLM intent classifier (`classify_intent`) | Novel phrasings of the three above that the keyword lists miss | Defense in depth. Failure mode is fail-open: on any error or "normal" label, the agent proceeds to the normal LLM call. **Authority is narrow**: `crisis` and `injection` still short-circuit into the canned response; `off_topic` does NOT — it sets a per-turn steering flag so the main LLM gets the turn with a single-line hint, and the model handles the actual reply (acknowledge in ≤1 short clause, answer any real question, return to the next missing field). The historical `is_out_of_scope` state flag is preserved for red-team attribution. |
| First-turn AI / not-a-person disclosure (`first_turn_disclosure`) | A user who might otherwise not realize they're talking to an unmonitored AI text tool that cannot see them, dispatch anyone, or take any real-world action | The very first assistant message of every conversation (rendered verbatim as the first web-UI bubble and the CLI banner) names FoodPath as an automated AI assistant, states the initial SCREEN scope, lists what it CANNOT do (see, call, dispatch, take real-world action), and points to 911 / 988 for emergencies. Hard-coded, not LLM-generated, so the framing cannot drift. Mirrors how trained 911 / 988 operators explicitly triage at the start of every call and how EBT card-services lines identify themselves ("we only handle card issues; for eligibility you need your state SNAP agency, not this line"). CA SB 243 / WA HB 2225 require this disclosure for conversational bots; the FTC treats non-disclosure of AI-vs-human as potentially deceptive. |
| Not-a-person disambiguation (`detect_not_a_person_request`) | A user who explicitly asks "is this a real person" / "are you calling someone" / "is this 911" / "is this the EBT line" | Hardcoded "what this is NOT" block naming each adjacent real channel — NC DSS (caseworker), 911, 988 live counselors, EBT card-services line — with a one-line pointer to the real channel for each. Runs strictly below crisis / injection / scope in the guardrail chain, so a crisis disclosure still wins. |
| `update_state` validation (Pydantic) | Out-of-range household_size, malformed income strings, type confusion | Drops the bad update, keeps state valid, re-asks the field next turn. |
| Income pay-period check (`classify_income_ambiguity`) | An income answer whose pay period is missing or implausible — a bare `70`, `70000`, `$15/hr` with no hours, `$70 a day` with no days | The screen needs a **gross monthly** figure, but people answer in whatever period they are paid in. Where the knowledge base defines a conversion (annual ÷ 12, weekly × 52/12, hourly × hours × 52/12) it is applied deterministically; where it does not, the agent **asks instead of guessing**, naming the specific choice ("is $70 per month, per week, or per day?") and rejoining the answer with the half already given. A confident model guess is overruled: recording a per-year or per-day figure as per-month silently produces the wrong verdict, and a false denial is the most harmful output this tool has. |

A `GUARDRAIL_CLASSIFIER=off` environment variable disables the classifier
layer for tests, offline runs, and the model bake-off. The keyword
layers cannot be disabled — they are the floor.

The pytest suite is gated to pass under BOTH modes. ``tests/conftest.py``
installs an autouse fixture that pins ``GUARDRAIL_CLASSIFIER=off`` (the
documented offline mode) at the start of every test that does not opt
out, and tests that encode "classifier layer is running" semantics opt
in explicitly with ``monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "on")``.
CI (``.github/workflows/ci.yml``) runs two pytest legs — one with the
classifier on and one with it off — so a regression in either mode
breaks the build rather than slipping through as a different test
count on a different shell.

## Known limitations

* **Initial screen, not a determination.** The agent returns a likely /
  maybe / unlikely verdict against the federal gross / net income tests.
  It is not a final agency determination, and the conversation banner
  and system prompt both say so. Real applicants still need to file with
  their county DSS.
* **Net-income deductions are computed, not stubbed.** The federal
  net-income test subtracts the SNAP standard deduction (FY2026
  USDA-published amounts by household size), the 20% earned-income
  deduction, the dependent-care deduction, the excess medical
  deduction (elderly / disabled), and the excess shelter deduction
  (capped at the FY2026 48-states & DC maximum). The calculator
  honors all of these when the applicant volunteers the underlying
  fields (`earned_monthly_income`, `dependent_care_monthly`,
  `medical_expenses_monthly`, `shelter_costs_monthly`); when none are
  volunteered it falls back to the gross test. No "maybe" band is
  faked to compensate.
* **Keyword + classifier is still not exhaustive.** New injection or
  crisis phrasings will keep arriving. The red-team suite (`redteam/`)
  is the regression net; new failure modes captured by the continuous
  simulator land in `redteam/failures_queue.jsonl` (the local
  append-only triage queue, populated by `redteam/file_failures.py`
  and by `scripts/sim_daemon.py`). Promoting a local finding into
  `redteam/attack_cases.jsonl` (the committed human-curated corpus
  that `run_redteam.load_cases()` reads) is a manual triage step:
  copy the record out, fill in `expected_behavior`, append, commit.
  The auto-filer never writes to `attack_cases.jsonl` — that
  separation is the W47 footgun guard, so a bad auto-filer can't
  silently corrupt the corpus.
* **Income limits need an annual refresh.** HHS publishes new poverty
  guidelines late January / early February. The 2025 figures are in
  `src/calculator.py`; the calculator has a "see also" comment naming
  every constant to update. If the table falls behind, the calculator is
  slightly more permissive than the actual program — a known trade-off
  of the deterministic design.
* **Spanish coverage is partial by design.** Language detection is a
  keyword/character heuristic (W15), the Spanish system prompt and
  Spanish guardrail renderings landed in W19, and only
  `01_program_overview.md` is translated to Spanish
  (`01_program_overview.es.md`). The income-limits, deductions, and
  how-to-apply KB docs are English-only, so Spanish answers on those
  topics are still grounded in the English corpus (the agent quotes
  the English source path in its citations). A clean EN/ES split is
  better than a half-translated bilingual surface that mixes the two
  — see the "Full Spanish translation" bullet in **What we cut, and why**.
  A production deployment would translate
  the rest of the KB with a domain reviewer and add a real language
  identifier.
* **PII redactor is regex-only, by design.** It catches SSN-shaped,
  account-shaped, routing-shaped, email, US-phone, and street-address
  patterns — the formats that have a precise enough shape for a
  regex to be reliable. It deliberately does not catch free-form
  personal names; that needs NER/ML to be even remotely accurate and
  produces too many false positives on ordinary English words. So
  conversation history is hardened against the worst-pattern
  identifiers, not anonymized — the system prompt also forbids asking
  for free-form PII in the first place.

## Production roadmap

What we would build next, in priority order, given more time:

1. **165% track for separate elderly/disabled households.** The
   FY2026 memo defines a third gross-income standard for households
   where an elderly or disabled member buys and prepares food
   separately. The knowledge base documents it (kb/03) so a reviewer
   can trace the rules; the calculator and intake flow do not yet
   collect the "separate household" signal, and the standard 100%
   net path is what the agent computes today. Adding this is the
   next milestone in the deduction-chain work that W18/W31 started.
2. **Deeper Spanish localization.** The Spanish system prompt and
   Spanish guardrail renderings landed in W19; remaining gaps are
   full KB translations beyond the overview doc and a
   production-grade language identifier. The most-requested gap for
   any NC benefits tool — see the "Full Spanish KB translation" bullet
   in **What we deliberately cut and why**.
3. **Telemetry with PII redaction.** First-class event log of every
   turn with the redactor applied at write-time, so we can see failure
   modes in production without ever persisting raw user input.
4. **Eval harness in CI.** Partially shipped: GitHub Actions runs the
   full pytest suite plus a red-team `--dry-run` on every push/PR (see
   the CI section above). Remaining: a scheduled live (non-dry-run)
   red-team pass with a pass/fail threshold on outcome-band stability,
   using a provider key stored in repo secrets. Catches regressions
   before they reach a live demo.

## Repository layout

```
src/
  agent.py            # conversation loop, tool handling, correction tracking
  calculator.py       # deterministic eligibility arithmetic (FY2026 SNAP standards)
  retrieval.py        # RAG over knowledge_base/
  llm_client.py       # provider-agnostic client (anthropic / openai / minimax / openrouter)
  state.py            # typed Pydantic AssessmentState
  cli.py              # `python3 -m src.cli`
  web.py              # FastAPI UI: POST /chat, POST /handoff, GET /, GET /health
  handoff.py          # W17 case-worker handoff: print-ready summary + banner
  branding.py         # product name + UI labels
  telemetry.py        # per-turn structured event log (PII-redacted at write time)
  guardrails/
    crisis.py         # detect_crisis, crisis_response (911 / 988 / DV hotline 1-800-799-7233 / USDA / 211)
    hostility.py      # detect_hostility + per-turn de-escalation context (non-blocking; crisis wins)
    injection.py      # detect_injection, injection_response
    scope.py          # detect_out_of_scope, scope_redirect_message
    classifier.py     # LLM intent classifier (defense in depth)
    pii.py            # redact_for_logging (SSN, account, routing, email, phone, address)
    ambiguity.py      # normalize_income ("$2,500", "$600/week" -> monthly float)

knowledge_base/       # FY2026 NC FNS rules (English); 01 is also translated to Spanish
redteam/              # attack_cases.jsonl (corpus) + failures_queue.jsonl (local triage) + run_redteam + file_failures + findings/ (gitignored)
tests/                # pytest suite (calculator, guardrails, agent, web, golden transcripts, red-team signatures)
scripts/              # KB table generator, simulator daemon, live red-team runner
static/               # web UI static assets (manifest + service worker)
```

## Safety

* The agent never asks for or stores a Social Security number, date of
  birth, home address, phone number, bank account number, driver's
  license number, or immigration status. The system prompt forbids it
  explicitly, and the `update_state` schema rejects it structurally.
* The agent does not submit applications, contact any agency, or
  scrape any government system.
* On crisis detection the agent returns 911 (life-threatening emergencies,
  first line), 988 (Suicide & Crisis Lifeline), the National Domestic
  Violence Hotline 1-800-799-7233, the USDA hunger hotline, and 211
  (United Way resources) and stops; it does not attempt to
  continue the FNS intake on the same turn.
