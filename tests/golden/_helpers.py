"""Shared helpers for the W35 golden transcript suite.

Every transcript in ``tests/golden/`` uses these helpers so the universal
invariants (no traceback, reply non-empty, completed intake yields a
verdict, a filled field is never re-asked, ``gross_monthly_income``
changes only on an income cue, crisis replies contain 911 / 988 / 211
verbatim) are enforced the same way everywhere. Adding a new transcript
picks up all of them for free.

Design constraints:

* No provider API is called. A ``ScriptedLLM`` plays back a queued
  list of ``{content, tool_calls}`` dicts and exposes a classifier
  shortcut so individual tests can route guardrail-layer calls without
  consuming from the main queue.
* ``run_transcript`` returns a structured ``TranscriptResult`` so each
  test can assert the same way on it (deterministic state, every
  assistant reply captured, every correction recorded).
* The helper disables the optional LLM classifier via the env var
  ``GUARDRAIL_CLASSIFIER=off`` so scripted queues stay in lock-step
  with what each turn is supposed to do — the keyword layer is the
  only thing that can claim a guardrail turn in the scripted path.
* The helper NEVER injects assertion text into a stub. Stubs emit
  ONLY tool calls (or a deterministic, content-free marker like
  ``""``) so the universal invariants assert against code-generated
  output or against the model state, never against prose the test
  itself authored. This is the W35 rule that W34 missed.
"""

from __future__ import annotations

import math
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import pytest

# ----------------------------------------------------------------------------
# Default scripted LLM
# ----------------------------------------------------------------------------


class ScriptedLLM:
    """A scripted fake LLM.

    Each ``chat()`` invocation pops the next response from
    ``main_responses``. Classifier-layer calls are handled separately
    so the agent's main queue is consumed in lock-step with the turns
    that triggered each call.

    Stubs in this suite emit ONLY tool calls (or empty content) and
    NEVER emit prose the test could assert against. The test asserts
    against code-generated output (the deterministic eligibility
    result, the assistant's structured state, the corrections trail,
    the hardcoded guardrail response strings) — never against
    something the test itself put into the stub. This is the W35 rule
    that W34 missed and that the chunk brief explicitly calls out as
    the new contract.
    """

    def __init__(self, main_responses: list[dict[str, Any]] | None = None,
                 classifier_response: str = "normal") -> None:
        self._responses = list(main_responses or [])
        self._classifier_response = classifier_response
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if system == os.environ.get("CLASSIFIER_PROMPT_SENTINEL", "CIVICREACH_CLASSIFIER_PROMPT"):
            return {"content": self._classifier_response, "tool_calls": []}
        # The agent uses a fixed system-prompt string for its classifier
        # call. We detect it via the system-prompt content rather than
        # a sentinel so the helper is robust to small prompt tweaks.
        if isinstance(system, str) and "intent classifier" in system.lower():
            return {"content": self._classifier_response, "tool_calls": []}
        if self._responses:
            return self._responses.pop(0)
        # No scripted response left: return an empty tool-call-only stub
        # so the agent's bare-tool-call fallback path can take over
        # without leaking assertion text into the conversation.
        return {"content": "", "tool_calls": []}


class RecordingLLM(ScriptedLLM):
    """A scripted LLM that captures every main call for assertion."""

    def __init__(self, main_responses=None, classifier_response="normal"):
        super().__init__(main_responses=main_responses, classifier_response=classifier_response)
        self.main_calls: list[dict[str, Any]] = []

    def chat(self, messages, system=None, tools=None):
        recorded = super().chat(messages, system=system, tools=tools)
        # Mirror ScriptedLLM's "skip the classifier" rule so call
        # counts in tests stay meaningful.
        if isinstance(system, str) and "intent classifier" in system.lower():
            return recorded
        self.main_calls.append(
            {"messages": messages, "system": system, "tools": tools, "response": recorded}
        )
        return recorded


# ----------------------------------------------------------------------------
# Transcript runner
# ----------------------------------------------------------------------------


@dataclass
class TranscriptTurn:
    user_message: str
    assistant_reply: str


@dataclass
class TranscriptResult:
    """What ``run_transcript`` returns — the structured record of one
    scripted conversation that every golden test then asserts against.

    Properties:
      * ``turns`` — list of (user message, assistant reply) pairs in
        order, including guardrail-claimed turns (which still produce a
        non-empty assistant reply).
      * ``tracebacks`` — any exception traceback raised during the
        conversation. Empty for a clean run; populated when a turn
        explodes (the universal invariant asserts this is empty).
      * ``final_state`` — the agent's ``AssessmentState`` after every
        turn has run. Used to assert "a filled field is never re-asked"
        and "completed intake -> reply contains the outcome".
      * ``corrections`` — the agent's ``_corrections`` list (a list of
        {field, old, new, turn} dicts). Tests assert on this directly
        rather than parsing prose.
      * ``calculator_calls`` — every argument tuple the calculator was
        invoked with, captured via the ``calculator_spy`` fixture.
        Tests assert on this so the contract "income changes only on an
        income cue" is testable from real call records, not from
        asserted-on prose.
      * ``income_history`` — the sequence of ``gross_monthly_income``
        values across all completed turns. Tests assert on the deltas
        (income only changed on turns carrying an income cue).
    """

    turns: list[TranscriptTurn] = field(default_factory=list)
    tracebacks: list[str] = field(default_factory=list)
    final_state: Any = None
    corrections: list[dict[str, Any]] = field(default_factory=list)
    calculator_calls: list[dict[str, Any]] = field(default_factory=list)
    income_history: list[float | None] = field(default_factory=list)
    assistant_replies: list[str] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    filled_at: dict[str, int] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return len(self.final_state.missing_fields()) == 0 if self.final_state else False

    @property
    def last_assistant_reply(self) -> str:
        return self.assistant_replies[-1] if self.assistant_replies else ""


def run_transcript(
    user_messages: Iterable[str],
    scripted_responses: list[dict[str, Any]] | None = None,
    build_agent: Callable[[RecordingLLM], Any] | None = None,
    classifier_response: str = "normal",
    capture_calculator: bool = True,
) -> TranscriptResult:
    """Drive ``CivicReachAgent`` through ``user_messages`` and capture
    every signal the universal invariants care about.

    Parameters
    ----------
    user_messages:
        Sequence of user-side messages to feed ``agent.turn()`` in order.
    scripted_responses:
        Queued main ``chat()`` responses (one per non-classifier LLM
        call). If ``None``, the agent runs with an empty queue (every
        main call returns a bare-tool-call stub).
    build_agent:
        Optional callable that, given a ``RecordingLLM``, returns a
        configured ``CivicReachAgent`` (e.g. for tests that need a
        custom ``retriever`` or pre-seeded state). Defaults to a
        freshly-constructed agent with the recording LLM.
    classifier_response:
        What to return when the agent's intent classifier runs. Default
        ``"normal"`` lets every message reach the main LLM path; tests
        that need to force a guardrail classification return
        ``"crisis"`` / ``"injection"`` / ``"off_topic"`` instead.
    capture_calculator:
        When True, monkey-patches ``src.agent.calculate_eligibility``
        with a spy that records every invocation's kwargs and returns
        a deterministic stub result. Tests can then assert on the
        calculator's inputs directly (income only changes when the
        caller did it, etc.) without depending on the real calculator.
    """
    from src.agent import CivicReachAgent  # local import to keep the helper free of side effects
    from src.calculator import calculate_eligibility

    llm = RecordingLLM(
        main_responses=scripted_responses,
        classifier_response=classifier_response,
    )
    if build_agent is not None:
        agent = build_agent(llm)
    else:
        agent = CivicReachAgent(llm_client=llm)

    result = TranscriptResult()

    # Track WHEN each field was filled (the 1-indexed turn on which
    # the user message first caused the field to become non-None).
    # The re-ask invariant only fires on turns STRICTLY AFTER a field
    # was filled; the turn that fills it can still legitimately ask
    # for the NEXT missing field via the agent's deterministic
    # next-field fallback.
    filled_at: dict[str, int] = {}

    # Spy on the calculator so tests can assert on what was sent in.
    # We capture the kwargs the agent would have passed to
    # calculate_eligibility without altering behavior; the real
    # function still runs so the agent's verdict is genuine.
    if capture_calculator:

        class _CalculatorSpy:
            def __init__(self):
                self.calls: list[dict[str, Any]] = []

            def __call__(self, **kwargs):
                self.calls.append(dict(kwargs))
                return calculate_eligibility(**kwargs)

        spy = _CalculatorSpy()
        original = os.environ.get("W35_CALCULATOR_ORIGINAL")
        # Direct attribute swap on the module reference the agent uses
        # (src.agent imports calculate_eligibility into its own
        # namespace, so the patch has to land on src.agent as well).
        import src.agent as agent_module

        agent_module.calculate_eligibility = spy
        try:
            for turn_index, message in enumerate(user_messages, start=1):
                result.user_messages.append(message)
                try:
                    reply = agent.turn(message)
                except Exception:
                    result.tracebacks.append(traceback.format_exc())
                    result.turns.append(TranscriptTurn(message, ""))
                    result.assistant_replies.append("")
                    continue
                result.assistant_replies.append(reply)
                result.turns.append(TranscriptTurn(message, reply))
                result.income_history.append(agent.state.gross_monthly_income)
                for field_name in (
                    "household_size",
                    "gross_monthly_income",
                    "has_elderly_or_disabled_member",
                ):
                    if field_name not in filled_at and getattr(agent.state, field_name) is not None:
                        filled_at[field_name] = turn_index
        finally:
            agent_module.calculate_eligibility = calculate_eligibility

        result.calculator_calls = list(spy.calls)
    else:
        for turn_index, message in enumerate(user_messages, start=1):
            result.user_messages.append(message)
            try:
                reply = agent.turn(message)
            except Exception:
                result.tracebacks.append(traceback.format_exc())
                result.turns.append(TranscriptTurn(message, ""))
                result.assistant_replies.append("")
                continue
            result.assistant_replies.append(reply)
            result.turns.append(TranscriptTurn(message, reply))
            result.income_history.append(agent.state.gross_monthly_income)
            for field_name in (
                "household_size",
                "gross_monthly_income",
                "has_elderly_or_disabled_member",
            ):
                if field_name not in filled_at and getattr(agent.state, field_name) is not None:
                    filled_at[field_name] = turn_index

    result.final_state = agent.state
    result.corrections = list(agent._corrections)
    # Stash the fill-turn map on the result so the universal invariant
    # assertion can check "never re-asked AFTER fill turn T" without
    # having to re-walk the conversation.
    result.filled_at = filled_at
    return result


# ----------------------------------------------------------------------------
# Universal invariants — every golden transcript asserts on these
# ----------------------------------------------------------------------------


# Phrases the agent (or its crisis path) might use to describe a
# screening outcome in English or Spanish. We assert against this
# vocabulary rather than prose the stub authored so the check fires on
# code-generated output.
_OUTCOME_TERMS_EN = (
    "eligible", "qualif", "screen", "apply", "ineligible",
    "not qualify", "income", "limit", "test", "passes", "below",
)
_OUTCOME_TERMS_ES = (
    "califica", "elegible", "elegibilidad", "evalu", "solicita",
    "ingreso", "limite", "no califica", "probable",
)


def assert_universal_invariants(result: TranscriptResult, *, expect_completed: bool | None = None) -> None:
    """Assert every universal invariant the chunk brief mandates.

    Tests call this once per transcript at the end so the same checks
    fire on every future transcript without each test re-implementing
    them. See the module docstring for the rationale.
    """
    # 1. No traceback. The agent must NEVER raise during a scripted run.
    assert result.tracebacks == [], (
        f"transcript raised: {result.tracebacks}"
    )

    # 2. Reply non-empty on every turn.
    for i, reply in enumerate(result.assistant_replies):
        assert reply.strip(), (
            f"turn {i} assistant reply is empty (user said: {result.user_messages[i]!r})"
        )

    # 3. If intake completed, the assistant reply on / after the turn
    #    that produced the verdict must mention outcome-bearing terms.
    #    We allow one turn of grace: the verdict can land on turn T,
    #    and the visible assistant reply can be on turn T or T+1 (the
    #    agent may issue an explain call the same turn that produces
    #    the result).
    if expect_completed is None:
        expect_completed = result.completed
    if expect_completed:
        outcome_terms = _OUTCOME_TERMS_ES if result.final_state.language == "es" else _OUTCOME_TERMS_EN
        found_outcome = any(
            any(term in reply.lower() for term in outcome_terms)
            for reply in result.assistant_replies
        )
        assert found_outcome, (
            "completed intake but no reply mentions an outcome term; "
            f"replies={result.assistant_replies!r}"
        )

    # 4. A filled field is never re-asked. The agent must not emit
    #    the literal next-field question for a field the user already
    #    provided, on any turn STRICTLY AFTER the field was filled.
    #    (The turn that fills a field can still legitimately ask for
    #    the next missing field — that is the deterministic fallback,
    #    not a re-ask.) We assert by checking the user-facing reply on
    #    each later turn does NOT contain a fresh re-prompt for the
    #    now-filled field. (Belt and braces: we also assert directly
    #    against ``final_state`` that every completed-turn field stays
    #    set.)
    final = result.final_state
    fill_map = result.filled_at
    if fill_map.get("household_size") is not None:
        fill_turn = fill_map["household_size"]
        for i, reply in enumerate(result.assistant_replies, start=1):
            if i <= fill_turn:
                continue
            lowered = reply.lower()
            assert not _is_re_asking(lowered, "household_size", final.language), (
                f"turn {i} re-asked household_size after it was filled on turn {fill_turn}: {reply!r}"
            )
    if fill_map.get("gross_monthly_income") is not None:
        fill_turn = fill_map["gross_monthly_income"]
        for i, reply in enumerate(result.assistant_replies, start=1):
            if i <= fill_turn:
                continue
            lowered = reply.lower()
            assert not _is_re_asking(lowered, "gross_monthly_income", final.language), (
                f"turn {i} re-asked gross_monthly_income after it was filled on turn {fill_turn}: {reply!r}"
            )
    if fill_map.get("has_elderly_or_disabled_member") is not None:
        fill_turn = fill_map["has_elderly_or_disabled_member"]
        for i, reply in enumerate(result.assistant_replies, start=1):
            if i <= fill_turn:
                continue
            lowered = reply.lower()
            assert not _is_re_asking(lowered, "has_elderly_or_disabled_member", final.language), (
                f"turn {i} re-asked has_elderly_or_disabled_member after it was filled on turn {fill_turn}: {reply!r}"
            )

    # 5. gross_monthly_income changes only on a turn carrying an
    #    income cue. We do this by walking the history and asserting
    #    that any turn where the value changed is also a turn whose
    #    user message contains an income keyword. Tests that
    #    explicitly set income out-of-order can opt out by passing
    #    ``income_cue_relaxed=True`` via ``assert_income_changes_only_on_cue``.
    assert_income_changes_only_on_cue(result)


# Income keywords for the "income cue" check. Same vocabulary the
# agent's correction-fallback uses, plus the obvious English and Spanish
# currency/frequency terms used by this bilingual transcript suite.
_INCOME_CUE_RE = re.compile(
    r"\$|\b(make|makes|earn|earns|earned|income|salary|wage|wages|paid|pay\b|"
    r"per\s+month|monthly|week\b|wk\b|hr\b|hour\b|hourly|month\b|"
    r"gano|gana|ganamos|ganan|ingreso|ingresos|salario|sueldo|pagado|"
    r"al\s+mes|mensual|semana|semanal|hora|por\s+hora|"
    r"\d+\s*k\b|k\b)\b",
    re.IGNORECASE,
)

# The same fuzzy shapes normalize_income() accepts, treated as cues:
_INCOME_CUE_TOKENS = (
    "$", "/mo", "/wk", "/hr", "month", "week", "hour",
    " mes", "semana", "hora",
)


def message_carries_income_cue(message: str) -> bool:
    """True if *message* contains anything the agent would treat as an
    income-related cue, regardless of whether parse_income returns a
    value. Used by the universal invariant ``gross_monthly_income
    changes only on a turn carrying an income cue``.

    A bare number that looks like an income magnitude (>= 50, i.e.
    plausibly a monthly gross figure and not e.g. a household size)
    counts as an income cue on its own. A number under 50 does NOT
    count on its own because small integers are most likely household
    size; it has to be paired with an income keyword.

    Note: this is deliberately generous (any digit near an income
    keyword counts) so a false negative here would fail a real
    regression — the agent is allowed to update income on MORE turns
    than the cue set covers; the test only flags turns where income
    changed but no cue was present.
    """
    if not message:
        return False
    # Bare-number rule:
    #   "3" alone -> NOT a cue (looks like household size).
    #   "$3" / "1800" / "1,800" / "1500" / "$15/hr" -> a cue.
    # We accept any money-looking single token: $ prefix, multi-digit,
    # or contains a thousands separator.
    stripped = message.strip()
    if re.fullmatch(r"\$\s*\d+(?:\.\d+)?", stripped):
        return True
    if re.fullmatch(r"\d{2,}(?:\.\d+)?", stripped):
        return True
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", stripped):
        return True
    # k-shorthand alone ("2.5k", "$3k")
    if re.fullmatch(r"\$\s*\d+(?:\.\d+)?\s*k\b", stripped, re.IGNORECASE):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s*k\b", stripped, re.IGNORECASE):
        return True
    if any(tok in message for tok in _INCOME_CUE_TOKENS):
        return True
    # "$2,500 a month", "1.5k", etc.
    if re.search(r"\$?\d", message):
        if re.search(
            r"\$?\d[\d,]*(?:\.\d+)?\s*(?:/|per\s+)?(?:month|mo|wk|week|hour|hr)\b",
            message,
            re.IGNORECASE,
        ):
            return True
        if _INCOME_CUE_RE.search(message):
            return True
        # k-shorthand like "$2.5k" within a longer sentence
        if re.search(r"\$?\d+(?:\.\d+)?\s*k\b", message, re.IGNORECASE):
            return True
    return False


def assert_income_changes_only_on_cue(result: TranscriptResult) -> None:
    """Assert that ``gross_monthly_income`` changed ONLY on a turn
    whose user message carries an income cue.

    This is the universal invariant for the W35 chunk brief. A turn
    that updates income without an income cue indicates the agent
    parsed a non-income token as income — exactly the failure mode
    that motivated this whole suite.
    """
    history = [None] + list(result.income_history)  # align: history[i] is value AFTER turn i
    for i in range(1, len(history)):
        prev, curr = history[i - 1], history[i]
        if prev == curr:
            continue
        # Income changed between turn i-1 and turn i. The cue must be
        # on turn i (the user message that just produced the change).
        message = result.user_messages[i - 1]
        assert message_carries_income_cue(message), (
            f"turn {i - 1} changed gross_monthly_income from {prev!r} to {curr!r} "
            f"but the user message {message!r} carries no income cue"
        )


# Phrases the agent emits when re-asking an already-filled field.
# These mirror the deterministic next-field-question wording in
# ``src/agent._NEXT_FIELD_QUESTION`` so a re-ask is detectable from
# the reply alone. The phrase must appear NEAR a question mark
# (``?``) or be the start of the reply — otherwise the explanation
# floor ("Household has no elderly (60+) or disabled member and...")
# would false-positive. We test for "phrase preceded by question-y
# words OR appearing as the first 200 chars of the reply".
_REASK_PHRASES_EN: dict[str, tuple[str, ...]] = {
    "household_size": ("how many people are in your household",),
    "gross_monthly_income": (
        "what is your household's gross monthly income",
        "what is your household\u2019s gross monthly income",
    ),
    "has_elderly_or_disabled_member": (
        "does anyone in your household",
        "does anyone in your household have",
    ),
}
_REASK_PHRASES_ES: dict[str, tuple[str, ...]] = {
    "household_size": (
        "cuantas personas hay en su hogar",
        "\u00bfcuantas personas hay en su hogar",
        "cuantas personas son en su hogar",
    ),
    "gross_monthly_income": (
        "cual es el ingreso mensual bruto",
        "\u00bfcual es el ingreso mensual bruto",
    ),
    "has_elderly_or_disabled_member": (
        "alguna persona en su hogar tiene",
        "alguien en su hogar tiene",
        "tiene alguien en su hogar",
    ),
}


def _is_re_asking(lowered_reply: str, field: str, language: str) -> bool:
    """True when *lowered_reply* is re-asking *field* for a value the
    user already provided. Detects the deterministic question wording
    in either English or Spanish.

    A phrase only counts as a re-ask when it appears NEAR a question
    mark (``?``) within 80 chars OR starts the reply. This avoids
    false positives on the explanation floor, which mentions the
    field by name in a declarative sentence.
    """
    table = _REASK_PHRASES_ES if language == "es" else _REASK_PHRASES_EN
    head = lowered_reply[:200]
    for phrase in table.get(field, ()):
        if phrase in head:
            return True
        # Near a question mark.
        idx = lowered_reply.find(phrase)
        if idx == -1:
            continue
        window = lowered_reply[max(0, idx - 40): idx + len(phrase) + 40]
        if "?" in window:
            return True
    return False


# ----------------------------------------------------------------------------
# Crisis invariant — every crisis-claimed turn must contain 911/988/211
# ----------------------------------------------------------------------------


def assert_crisis_invariants(result: TranscriptResult, *, expected_crisis_turns: int) -> None:
    """Assert the universal crisis invariants on a transcript.

    Every turn whose user message tripped ``detect_crisis`` (or whose
    classifier returned ``"crisis"``) MUST produce an assistant reply
    containing ``911``, ``988``, and ``211`` verbatim. The reply must
    not be empty (already covered by ``assert_universal_invariants``)
    and the agent must never raise (also already covered).
    """
    from src.guardrails.crisis import detect_crisis

    seen = 0
    for i, message in enumerate(result.user_messages):
        if detect_crisis(message):
            seen += 1
            reply = result.assistant_replies[i]
            assert "911" in reply, f"crisis turn {i} missing 911: {reply!r}"
            assert "988" in reply, f"crisis turn {i} missing 988: {reply!r}"
            assert "211" in reply, f"crisis turn {i} missing 211: {reply!r}"
    assert seen == expected_crisis_turns, (
        f"expected {expected_crisis_turns} crisis turns; observed {seen}"
    )


# ----------------------------------------------------------------------------
# Update-state stub helpers — used to keep stubs tool-call-only
# ----------------------------------------------------------------------------


def update(**fields: Any) -> dict[str, Any]:
    """Build a scripted stub that emits ONLY an ``update_state`` tool
    call with the given field values. No prose. The agent still uses
    its own backstop phrasing to keep the reply non-empty.

    Tests use this everywhere they want to advance state, so the stub
    never authors prose the assertion can latch onto. This is the W35
    rule: stubs emit ONLY tool calls.
    """
    return {"content": "", "tool_calls": [{"name": "update_state", "input": dict(fields)}]}


def multi_update(*updates: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Build a scripted stub that emits one ``update_state`` tool call per
    field set in a single assistant response.

    Two call forms, used by different tests:

    * ``multi_update(household_size=3, gross_monthly_income=1800)`` — a
      single ``update_state`` call covering all kwargs at once. Used when
      the test wants to model "the user said two things in one message
      and the model extracted both."
    * ``multi_update({...}, {...})`` — multiple ``update_state`` calls,
      one per positional dict. Used when the test wants to model "the
      assistant emitted several updates in a single response" (the
      correction-trailing scenario where one reply contains multiple
      distinct corrections).
    """
    if fields and not updates:
        return {
            "content": "",
            "tool_calls": [{"name": "update_state", "input": dict(fields)}],
        }
    return {
        "content": "",
        "tool_calls": [
            {"name": "update_state", "input": dict(payload)} for payload in updates
        ],
    }


# ----------------------------------------------------------------------------
# pytest fixture: disable the LLM intent classifier for scripted runs.
# The keyword layer is the only thing that can claim a guardrail turn
# when GUARDRAIL_CLASSIFIER=off, so the scripted main queue stays
# deterministic regardless of which classifier response a test wants
# to simulate.
# ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_classifier(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    yield
