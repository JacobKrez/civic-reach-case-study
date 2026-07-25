# CivicReach — NC FNS Eligibility Agent

## What this is

CivicReach is a proof-of-concept text agent that walks a North Carolina
resident through an *initial* Food and Nutrition Services (FNS / SNAP)
eligibility screen. It collects three intake fields across a multi-turn
conversation, grounds every answer in a small retrieval-augmented knowledge
base of public eligibility documents, and returns a deterministic
likely-eligible / maybe-eligible / likely-ineligible verdict plus
visible citations. It also layers deterministic guardrails (crisis,
prompt-injection, out-of-scope, PII redaction) and a secondary LLM
intent classifier in front of the main agent loop.

This is a case-study deliverable: scope is deliberately narrow. It is not
a real application, not connected to any agency, and the case study
explicitly forbids that.

## Quickstart

Five steps, no external services to set up — just Python and one LLM API key.

```bash
# 1. Create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Configure your environment
cp .env.example .env
```

Now open `.env` and do two things:

1. Fill in **exactly one** provider key: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `MINIMAX_API_KEY`, or `OPENROUTER_API_KEY`.
2. Set `LLM_PROVIDER` to match that key (`anthropic` / `openai` / `minimax` /
   `openrouter`). `.env.example` defaults to `anthropic` — change it if you
   used a different key.

That's it — everything else in `.env` is optional (see **Troubleshooting**
below for `LLM_FALLBACK_PROVIDER`).

```bash
# 4a. Talk to the agent in the terminal
python3 -m src.cli

# 4b. OR run the local web UI (single-process, sessions live in memory)
uvicorn src.web:app --reload
# then open http://127.0.0.1:8000
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
  decision is a table lookup plus arithmetic against HHS 2025 poverty
  guidelines x 1.30 (gross track) or x 1.00 (net track, for households
  with an elderly or disabled member). All arithmetic happens here, not
  in the prompt.
* *Retrieval + visible citations.* Every final answer is appended with a
  code-templated `(Sources: ...)` line naming the documents retrieved for
  the applicable income track. The model does not write this line — it
  cannot be hallucinated.

## What we deliberately cut and why

* **Fine-tuning.** Prompt + tools + a deterministic calculator already
  outperforms a rushed LoRA on this problem. A small fine-tune on
  synthetic data risks degrading exactly the robustness being tested
  (crisis / injection / scope) and would consume most of the 20-hour
  budget before the first user turn. The trade is not worth it.
* **Multi-agent frameworks.** An explicit state machine with a single
  `update_state` tool is fully narratable in the review meeting —
  reviewer asks "what happens if X", we can answer in one breath.
  Replacing it with a planner / critic / verifier graph would buy us
  nothing demonstrable and bury the guardrail ordering.
* **Auth, persistence, multi-tenancy.** The case study asks for scope
  discipline. Web sessions live in the process only; restarting the
  server clears them. Users can explicitly discard one with **Start a new
  screening** (or `new` / `restart` in the CLI). To prevent an unattended
  PoC process from growing without limit, the in-memory registry expires a
  session after **one hour without access** and keeps at most **100 live
  sessions**, evicting the least recently used entry before admitting the
  next one. `/health` exposes the current live-session count. This TTL/LRU
  policy is a deliberate PoC-scoped safety bound—not persistence or
  multi-tenancy—and it matches the case study's "do not submit real
  applications, do not contact a real agency" guardrail.
* **Real agency integration.** Explicitly forbidden by the case study
  prompt. We never call, scrape, or submit anywhere.

## Guardrails

| Layer | What it catches | Why deterministic |
|---|---|---|
| Crisis keyword (`detect_crisis`) | Suicidal ideation, self-harm, domestic-violence disclosures (EN/ES), food-on-table emergencies, "I can't go on" phrasings | Returns a hardcoded response: **911** for a life-threatening emergency (first line, phrased for a text interface — "call 911 now", there is no phone leg), **988** (call/text), the **National Domestic Violence Hotline 1-800-799-7233** (or text START to 88788), the USDA hunger hotline, and **211**. The model is never allowed to write this turn. |
| Hostility keyword (`detect_hostility`) | Profanity/insults directed at the agent ("fuck you", "stupid bot", "you're useless") — NOT profanity about the user's situation | Non-blocking, deliberately. Sets a per-turn flag, prepends one calm de-escalation instruction to the system prompt for that turn only (stay professional, don't mirror tone, stay on task), and writes a history-only audit note. Never refuses or ends the conversation. **Crisis always wins**: the detector runs only after crisis/injection/scope have cleared, so a hostile *and* crisis-flagged message still gets the full 911/988/DV/211 response. |
| Prompt-injection keyword (`detect_injection`) | "ignore previous instructions", "you are now DAN", "system:" spoofing, role-override attempts | Hardcoded refusal. The flag is recorded but does not lock the conversation into refusal mode — the next normal message is evaluated normally. |
| Out-of-scope keyword (`detect_scope`) | Legal advice, unrelated benefit programs, immigration questions, tax / housing / Medicaid | Polite redirect back to FNS questions. State is preserved; nothing already collected is reset. |
| PII redactor (`redact_for_logging`) | SSN-shaped, account-shaped, routing-shaped patterns | Strips from conversation history before it's logged, so any later dump is safe by construction. |
| LLM intent classifier (`classify_intent`) | Novel phrasings of the three above that the keyword lists miss | Defense in depth. Failure mode is fail-open: on any error or "normal" label, the agent proceeds to the normal LLM call. |
| First-turn AI / not-a-person disclosure (`first_turn_disclosure`) | A user who might otherwise not realize they're talking to an unmonitored AI text tool that cannot see them, dispatch anyone, or take any real-world action | The very first assistant message of every conversation (rendered verbatim as the first web-UI bubble and the CLI banner) names CivicReach as an automated AI assistant, states the initial SCREEN scope, lists what it CANNOT do (see, call, dispatch, take real-world action), and points to 911 / 988 for emergencies. Hard-coded, not LLM-generated, so the framing cannot drift. Mirrors how trained 911 / 988 operators explicitly triage at the start of every call and how EBT card-services lines identify themselves ("we only handle card issues; for eligibility you need your state SNAP agency, not this line"). CA SB 243 / WA HB 2225 require this disclosure for conversational bots; the FTC treats non-disclosure of AI-vs-human as potentially deceptive. |
| Not-a-person disambiguation (`detect_not_a_person_request`) | A user who explicitly asks "is this a real person" / "are you calling someone" / "is this 911" / "is this the EBT line" | Hardcoded "what this is NOT" block naming each adjacent real channel — NC DSS (caseworker), 911, 988 live counselors, EBT card-services line — with a one-line pointer to the real channel for each. Runs strictly below crisis / injection / scope in the guardrail chain, so a crisis disclosure still wins. |
| `update_state` validation (Pydantic) | Out-of-range household_size, malformed income strings, type confusion | Drops the bad update, keeps state valid, re-asks the field next turn. |

A `GUARDRAIL_CLASSIFIER=off` environment variable disables the classifier
layer for tests, offline runs, and the model bake-off. The keyword
layers cannot be disabled — they are the floor.

## Known limitations

* **Initial screen, not a determination.** The agent returns a likely /
  maybe / unlikely verdict against the federal gross / net income tests.
  It is not a final agency determination, and the conversation banner
  and system prompt both say so. Real applicants still need to file with
  their county DSS.
* **Net-income deductions are simplified.** The federal net-income test
  subtracts the SNAP standard deduction, earned-income deduction,
  dependent-care deduction, medical deduction (elderly / disabled), and
  shelter deduction. The calculator folds the standard deduction into
  the limit and ignores the rest. The outcome band (130–200% of poverty)
  is widened to compensate: anyone in that band is told "maybe, talk to
  a case worker" rather than auto-denied.
* **Keyword + classifier is still not exhaustive.** New injection or
  crisis phrasings will keep arriving. The red-team suite (`redteam/`)
  is the regression net; new failure modes must be filed into
  `redteam/file_failures.py` and added to `attack_cases.jsonl` before the
  next iteration.
* **Income limits need an annual refresh.** HHS publishes new poverty
  guidelines late January / early February. The 2025 figures are in
  `src/calculator.py`; the calculator has a "see also" comment naming
  every constant to update. If the table falls behind, the calculator is
  slightly more permissive than the actual program — a known trade-off
  of the deterministic design.
* **Spanish coverage is heuristic, PoC-grade.** Language detection is a
  keyword/character heuristic (W15), and the Spanish knowledge-base
  translation covers only the program-overview doc — Spanish answers on
  income limits, deductions, and how-to-apply are still grounded in the
  English corpus. The Spanish system prompt and Spanish guardrail
  renderings landed in W19; production would add full KB translations
  and a reviewed language identifier.
* **PII redactor is regex-only.** It catches SSN-shaped and
  account-shaped patterns; it does not catch free-form names, addresses,
  or phone numbers. Conversation history is therefore not fully
  anonymized — it just doesn't contain the worst-pattern identifiers.

## Production roadmap

What we would build next, in priority order, given more time:

1. **Real deduction math.** Compute the federal net-income test properly:
   standard deduction + earned-income deduction + dependent-care +
   medical (elderly / disabled) + shelter. This tightens the outcome
   band back to 130/100% and removes the "maybe" carve-out.
2. **Case-worker handoff.** A "talk to a human" handoff that produces a
   print-ready summary of the intake and the rule citations behind the
   screen, in plain English, so a DSS case worker can pick up mid-flow.
   The on-screen summary is the reliable, dependency-free path (what gets
   demoed live). On top of that, a best-effort notification fires only if
   SMTP env vars are configured — it demonstrates the *mechanism* a real
   deployment would use to alert a case worker's queue, not a real
   integration; the recipient is read from env only and is never a real
   agency address.
3. **Deeper Spanish localization.** The Spanish system prompt and Spanish
   guardrail renderings landed in W19; remaining gaps are full KB
   translations beyond the overview doc and a production-grade language
   identifier. The most-requested gap for any NC benefits tool.
4. **Telemetry with PII redaction.** First-class event log of every
   turn with the redactor applied at write-time, so we can see failure
   modes in production without ever persisting raw user input.
5. **Eval harness in CI.** Partially shipped: GitHub Actions runs the
   full pytest suite plus a red-team `--dry-run` on every push/PR (see
   the CI section above). Remaining: a scheduled live (non-dry-run)
   red-team pass with a pass/fail threshold on outcome-band stability,
   using a provider key stored in repo secrets. Catches regressions
   before they reach a live demo.

## Repository layout

```
src/
  agent.py            # conversation loop, tool handling, correction tracking
  calculator.py       # deterministic eligibility arithmetic (HHS 2025)
  retrieval.py        # RAG over knowledge_base/
  llm_client.py       # provider-agnostic client (anthropic / openai / minimax / openrouter)
  state.py            # typed Pydantic AssessmentState
  cli.py              # `python3 -m src.cli`
  web.py              # FastAPI UI: POST /chat, GET /, GET /health
  guardrails/
    crisis.py         # detect_crisis, crisis_response (911 / 988 / DV hotline 1-800-799-7233 / USDA / 211)
    hostility.py      # detect_hostility + per-turn de-escalation context (non-blocking; crisis wins)
    injection.py      # detect_injection, injection_response
    scope.py          # detect_out_of_scope, scope_redirect_message
    classifier.py     # LLM intent classifier (defense in depth)
    pii.py            # redact_for_logging
    ambiguity.py      # normalize_income ("$2,500", "$600/week" -> monthly float)

knowledge_base/       # 4 markdown chunks of public NC FNS rules
redteam/              # attack_cases.jsonl + run_redteam + failure file
tests/                # pytest suite (calculator, guardrails, agent, web)
scripts/              # one-off generators (KB income table, etc.)
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
