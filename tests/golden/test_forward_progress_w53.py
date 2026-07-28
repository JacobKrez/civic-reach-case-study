"""W53 forward-progress golden transcripts.

These tests exercise the exact failure Jacob hit on 2026-07-26 in
the live UI: a chatty model that records a field and then ends the
turn with a question mark that asks nothing useful (a rhetorical
question, a re-confirmation, or no question at all). The user is
left staring at an acknowledgement with nothing to answer, and in
the live review meeting this reads as the agent hanging.

The W53 floor (in ``src/agent._w53_fold_ask``) extends the W24
backstop so it fires on non-empty replies too: whenever intake is
incomplete after the decide phase, the reply the user sees MUST
end with the question for the first missing field. If the model
reply already asked for that field, the floor is a no-op; if it
did not, the canonical next-field question is appended in code.

These tests cover the three pathological shapes the chunk brief
calls out explicitly:

  (1) acknowledge-without-asking: the model records the field and
      replies with a bare acknowledgement ("Got it, 17 people.")
      that contains no question at all.
  (2) acknowledge-and-ask-something-else: the model records the
      field and asks a non-FNS question ("What's your ZIP code?").
  (3) acknowledge-and-ask-a-rhetorical-question: the model records
      the field and ends with a real '?' that asks nothing useful
      ("Were you just tossing out a number?") -- the exact live
      failure on 2026-07-26.

Each test uses a chatty scripted LLM that returns a content reply
matching one of these shapes, plus the right ``update_state`` tool
call, and asserts:

  * the field was recorded correctly (the chatty acknowledgement
    does not undo the extraction);
  * the user-facing reply ends with the canonical next-field
    question (the W53 floor appended it);
  * the universal forward-progress invariant (added to
    ``_helpers.assert_universal_invariants``) passes.

The Spanish path is exercised once to prove the bilingual floor
fires correctly across both languages.
"""

from __future__ import annotations

import json

import pytest

from src.agent import FoodPathAgent
from src.guardrails.classifier import CLASSIFIER_SYSTEM_PROMPT

from ._helpers import (
    _w53_asks_for_field,
    assert_universal_invariants,
    run_transcript,
)


# ---------------------------------------------------------------------------
# Shared chatty scripted LLMs
# ---------------------------------------------------------------------------


class _AcknowledgeOnlyLLM:
    """Pathological model: records the field, replies with a bare
    acknowledgement that contains NO question at all.

    The W24 backstop (which only fired on empty content) left the
    user staring at "Got it, 17 people." with no next question to
    answer. W53's floor must append the canonical next-field
    question to this reply.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system})
        ctx = json.loads(messages[-1]["content"])
        missing = ctx.get("missing_fields") or []
        if missing and missing[0] == "household_size":
            return {
                "content": "Got it, 17 people.",
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 17}}
                ],
            }
        return {"content": "Thanks!", "tool_calls": []}


class _AskSomethingElseLLM:
    """Pathological model: records the field, then asks a question
    for a different (or non-existent) field, leaving the intake
    without a real next step.

    W53's floor must recognize that the question does not match
    the canonical next-field question for ``missing_fields[0]``
    and append the canonical question.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system})
        ctx = json.loads(messages[-1]["content"])
        missing = ctx.get("missing_fields") or []
        if missing and missing[0] == "household_size":
            return {
                # Asks about a non-FNS topic AND narrates an internal
                # limit (the second anti-pattern W53's prompt
                # tightening calls out). Neither question advances
                # the intake.
                "content": (
                    "Got it. The max I can record is 20 people, so 17 is fine. "
                    "What's your ZIP code?"
                ),
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 17}}
                ],
            }
        return {"content": "Thanks!", "tool_calls": []}


class _RhetoricalQuestionLLM:
    """The exact failure Jacob reproduced on 2026-07-26 in the live
    UI against the running server: the model records the field and
    then ends with a real '?' that asks nothing useful.

    "Were you just tossing out a number?" is the literal phrase
    from the live failure. The reply contains a '?' so the
    naive "presence of a question mark" check (which the chunk
    brief explicitly warns against) would have passed.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system})
        ctx = json.loads(messages[-1]["content"])
        missing = ctx.get("missing_fields") or []
        if missing and missing[0] == "household_size":
            return {
                "content": (
                    "A couple of quick notes, then I'll record it:\n"
                    "- The max I can record for household size on this screen is 20 -- so 17 is fine.\n"
                    "- Also, since you mentioned earlier that 'zero' was the answer for something else, "
                    "I want to make sure: is 17 the size of your household, or were you just tossing "
                    "out a number? I'll go ahead and record it as 17 unless you tell me otherwise."
                ),
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 17}}
                ],
            }
        return {"content": "Thanks!", "tool_calls": []}


class _SpanishRhetoricalLLM:
    """Same pathological shape as ``_RhetoricalQuestionLLM`` but
    in Spanish, to prove the W53 floor's bilingual path fires."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._first_turn = True

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system})
        ctx = json.loads(messages[-1]["content"])
        # Spanish detection: the W19 heuristic flips state.language
        # to "es" on a Spanish message. The scripted response
        # below has to assume the conversation has already flipped.
        if self._first_turn:
            self._first_turn = False
            return {
                "content": (
                    "Entendido, 17 personas. El máximo que puedo registrar es 20, "
                    "así que está bien. ¿Sólo estabas tirando un número?"
                ),
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 17}}
                ],
            }
        return {"content": "¡Gracias!", "tool_calls": []}


# ---------------------------------------------------------------------------
# (1) acknowledge-without-asking
# ---------------------------------------------------------------------------


def test_w53_acknowledge_without_asking_appends_next_field():
    """(1) Acknowledge-without-asking. The model records
    household_size=17 and replies with a bare acknowledgement
    that contains no question. W53's floor must append the
    canonical next-field question (income) so the user has
    something concrete to answer."""

    llm = _AcknowledgeOnlyLLM()
    agent = FoodPathAgent(llm_client=llm)
    reply = agent.turn("i have 17")

    # The chatty model recorded the field correctly.
    assert agent.state.household_size == 17
    missing = agent.state.missing_fields()
    assert missing and missing[0] == "gross_monthly_income", missing

    # The reply ends with the canonical next-field question.
    lowered = reply.lower()
    assert "what is your household's gross monthly income" in lowered, (
        f"reply does not ask for the next field: {reply!r}"
    )
    # The acknowledgement is rendered from stored state, not provider prose.
    assert "17 people" in lowered


def test_w53_acknowledge_without_asking_full_transcript_passes_universal():
    """(1) The full universal invariant set must pass on a chatty
    transcript that exhibits the acknowledge-without-asking
    pattern on turn 1."""

    # Build a scripted LLM that returns the chatty acknowledgement
    # on turn 1 (household_size fill), then well-formed bare
    # tool-call responses on turns 2-3 to land the intake.
    class _ScriptedChatty:
        def __init__(self) -> None:
            self.responses = [
                {
                    "content": "Got it, 17 people.",
                    "tool_calls": [
                        {"name": "update_state", "input": {"household_size": 17}}
                    ],
                },
                {"content": "", "tool_calls": [
                    {"name": "update_state", "input": {"gross_monthly_income": 1800}}
                ]},
                {"content": "", "tool_calls": [
                    {"name": "update_state", "input": {"has_elderly_or_disabled_member": False}}
                ]},
            ]

        def chat(self, messages, system=None, tools=None):
            if self.responses:
                return self.responses.pop(0)
            return {"content": "", "tool_calls": []}

    result = run_transcript(
        ["i have 17", "1800", "no"],
        scripted_responses=[
            {
                "content": "Got it, 17 people.",
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 17}}
                ],
            },
            {"content": "", "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": 1800}}
            ]},
            {"content": "", "tool_calls": [
                {"name": "update_state", "input": {"has_elderly_or_disabled_member": False}}
            ]},
        ],
    )

    # The intake completed.
    assert result.final_state.missing_fields() == []
    assert result.final_state.household_size == 17
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False

    # The chatty turn's reply ended with the canonical next-field
    # question -- the W53 floor appended it.
    first_reply = result.assistant_replies[0].lower()
    assert "what is your household's gross monthly income" in first_reply

    # The full universal invariant set (including the new W53
    # forward-progress invariant) passes.
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# (2) acknowledge-and-ask-something-else
# ---------------------------------------------------------------------------


def test_w53_ask_something_else_appends_next_field():
    """(2) Acknowledge-and-ask-something-else. The model records
    household_size=17 and then asks "What's your ZIP code?" — a
    non-FNS question that does not advance the intake. W53's floor
    must recognize that "What's your ZIP code?" is not the
    canonical next-field question and append the canonical
    income question."""

    llm = _AskSomethingElseLLM()
    agent = FoodPathAgent(llm_client=llm)
    reply = agent.turn("i have 17")

    # The chatty model recorded the field correctly.
    assert agent.state.household_size == 17
    missing = agent.state.missing_fields()
    assert missing and missing[0] == "gross_monthly_income", missing

    # The reply contains the canonical next-field question.
    lowered = reply.lower()
    assert "what is your household's gross monthly income" in lowered, (
        f"reply does not ask for the next field: {reply!r}"
    )
    # Provider-written PII questions are discarded once state advances.
    assert "zip code" not in lowered


# ---------------------------------------------------------------------------
# (3) acknowledge-and-ask-a-rhetorical-question — the live failure
# ---------------------------------------------------------------------------


def test_w53_rhetorical_question_appends_next_field():
    """(3) Acknowledge-and-ask-a-rhetorical-question. The model
    records household_size=17 and ends with a real '?' that asks
    nothing useful — "were you just tossing out a number?" This is
    the exact failure Jacob hit on 2026-07-26 in the live UI
    against the running server.

    The naive "reply contains a '?'" check would have passed; W53
    detects "already asked" on the FIELD (a real question for the
    specific missing field), not on the presence of '?'. The
    canonical next-field question must therefore be appended."""

    llm = _RhetoricalQuestionLLM()
    agent = FoodPathAgent(llm_client=llm)
    reply = agent.turn("i have 17")

    # The chatty model recorded the field correctly.
    assert agent.state.household_size == 17
    missing = agent.state.missing_fields()
    assert missing and missing[0] == "gross_monthly_income", missing

    # The reply contains the canonical next-field question.
    lowered = reply.lower()
    assert "what is your household's gross monthly income" in lowered, (
        f"W53 LIVE FAILURE: the rhetorical question 'were you just "
        f"tossing out a number?' was NOT followed by a real next "
        f"question. reply={reply!r}"
    )
    # The provider's broken rhetorical question is replaced by a truthful
    # state-derived transition.
    assert "tossing out a number" not in lowered


def test_w53_rhetorical_question_acceptance_repro():
    """The exact reproduction case from the W53 chunk brief:

        USER: i have 17
        AGENT: "A couple of quick notes, then I'll record it: ... "
        state after: household_size=17, missing_fields=['gross_monthly_income', 'has_elderly_or_disabled_member']
        Then: conversation DEAD-ENDS (the reply never asks for income).

    After W53, the agent's reply MUST end with the income question
    so the user has a real next step."""

    llm = _RhetoricalQuestionLLM()
    agent = FoodPathAgent(llm_client=llm)
    reply = agent.turn("i have 17")

    # State matches the chunk brief exactly.
    assert agent.state.household_size == 17
    assert agent.state.missing_fields() == [
        "gross_monthly_income",
        "has_elderly_or_disabled_member",
    ], agent.state.missing_fields()

    # Reply asks for income (the chunk brief's hard requirement).
    assert "income" in reply.lower(), (
        f"DEAD END: reply never asks for the next field (income). reply={reply!r}"
    )


# ---------------------------------------------------------------------------
# Spanish: the bilingual floor fires the same way
# ---------------------------------------------------------------------------


def test_w53_spanish_rhetorical_question_appends_next_field():
    """The W53 floor must also work in Spanish. A Spanish-mode
    conversation that records household_size and then ends with a
    rhetorical question must have the Spanish canonical next-field
    question appended."""

    llm = _SpanishRhetoricalLLM()
    agent = FoodPathAgent(llm_client=llm)
    # The Spanish heuristic (``detect_spanish``) must flip the
    # conversation into Spanish mode on the first user message so
    # the W53 floor renders the Spanish next-field question.
    reply = agent.turn("Somos diecisiete en mi hogar")

    # The conversation is in Spanish mode.
    assert agent.state.language == "es"

    # The Spanish question was appended (the floor's "es" branch).
    assert "¿Cuál es el ingreso mensual bruto de su hogar?" in reply, (
        f"reply does not end with the Spanish next-field question: {reply!r}"
    )


# ---------------------------------------------------------------------------
# Negative control: a well-behaved model that ASKS for the right
# field is left alone (the floor is a no-op when the reply already
# contains the canonical next-field question).
# ---------------------------------------------------------------------------


def test_w53_well_behaved_model_left_alone():
    """Negative control: a model that records the field and ASKS
    for the canonical next-field question is left alone by the
    W53 floor (no double-question)."""

    class _WellBehaved:
        def chat(self, messages, system=None, tools=None):
            ctx = json.loads(messages[-1]["content"])
            missing = ctx.get("missing_fields") or []
            if missing and missing[0] == "household_size":
                return {
                    "content": (
                        "Got it -- 17 people in the household. "
                        "What is your household's gross monthly income?"
                    ),
                    "tool_calls": [
                        {"name": "update_state", "input": {"household_size": 17}}
                    ],
                }
            return {"content": "Thanks!", "tool_calls": []}

    llm = _WellBehaved()
    agent = FoodPathAgent(llm_client=llm)
    reply = agent.turn("i have 17")

    # The canonical question appears EXACTLY ONCE -- the floor
    # must not double-append when the model already asked.
    count = reply.lower().count("what is your household's gross monthly income")
    assert count == 1, f"floor double-appended: {reply!r}"


# ---------------------------------------------------------------------------
# W53b (2026-07-28): the household_size question is TWO sentences ("How
# many people ... together?" then "If you usually buy and cook just for
# yourself, answer 1."). Matching the floor's "already asked" check
# against the full two-sentence string meant any paraphrase of the SECOND
# sentence -- which the P1.1 conversational prompt actively encourages --
# broke the match and duplicated the whole question. Live-probed against
# the real MiniMax classifier: a plain "hi" produced this on 5 of 6 runs,
# on the very first turn of the interview.
# ---------------------------------------------------------------------------


def test_w53_paraphrased_instructional_tail_is_not_duplicated():
    """The model asks the real question but rephrases the trailing
    "answer 1" instruction. The floor must recognize the question was
    already asked and must NOT append the canonical text again."""

    class _ParaphrasedTail:
        def chat(self, messages, system=None, tools=None):
            if system == CLASSIFIER_SYSTEM_PROMPT:
                return {"content": "normal", "tool_calls": []}
            return {
                "content": (
                    "Hi! How many people, including you, usually buy and "
                    "prepare food together? If it's just you, answer 1."
                ),
                "tool_calls": [],
            }

    agent = FoodPathAgent(llm_client=_ParaphrasedTail())
    reply = agent.turn("hi there")
    count = reply.lower().count("buy and prepare food together")
    assert count == 1, f"floor duplicated a paraphrased-tail reply: {reply!r}"


def test_w53_word_inserted_mid_question_is_not_duplicated():
    """The model asks the real question but inserts a word or two of its
    own mid-sentence ("... together IN YOUR HOUSEHOLD?") -- a contiguous
    substring match breaks on this even though the question was asked."""

    class _WordInserted:
        def chat(self, messages, system=None, tools=None):
            if system == CLASSIFIER_SYSTEM_PROMPT:
                return {"content": "normal", "tool_calls": []}
            return {
                "content": (
                    "Hey! How many people, including you, usually buy and "
                    "prepare food together in your household? If it's just "
                    "you, answer 1."
                ),
                "tool_calls": [],
            }

    agent = FoodPathAgent(llm_client=_WordInserted())
    reply = agent.turn("hi there")
    count = reply.lower().count("buy and prepare food together")
    assert count == 1, f"floor duplicated a word-inserted reply: {reply!r}"


class _SilentProvider:
    """Forces every transition through the deterministic backstops."""

    def chat(self, messages, system=None, tools=None):
        return {"content": "", "tool_calls": []}


def test_w53_anchor_does_not_suppress_income_question_in_canned_redirect():
    """Regression guard for a bug caught during review, before it shipped:
    the anchor fallback must NOT be broad enough to match the canned
    scope-redirect text merely DESCRIBING what the screen still needs
    ("...I just need your household size, your gross monthly income, and
    whether anyone...") and mistake that description for an already-asked
    question. That would suppress the real question entirely -- a dead
    end with nothing to answer, which is worse than the duplicate this
    file exists to prevent. Anchors are scoped to household_size only;
    this test pins that scope so it cannot silently widen back out."""
    agent = FoodPathAgent(llm_client=_SilentProvider())
    agent.turn("3")  # household filled; missing[0] is now income
    reply = agent.turn("should I sue my landlord?")  # canned scope redirect
    assert "what is your household's gross monthly income" in reply.lower(), (
        f"the canned redirect's description of the income field suppressed "
        f"the real income question: {reply!r}"
    )


def test_w53_anchor_does_not_suppress_elderly_question_in_canned_redirect():
    agent = FoodPathAgent(llm_client=_SilentProvider())
    agent.turn("3")
    agent.turn("2500")  # household + income filled; missing[0] is now elderly/disabled
    reply = agent.turn("should I sue my landlord?")
    assert "does anyone in your household have an elderly" in reply.lower(), (
        f"the canned redirect's description of the elderly field suppressed "
        f"the real elderly question: {reply!r}"
    )


# ---------------------------------------------------------------------------
# W70b: the SIMULATOR's own copy of this matching logic (_w53_asks_for_field
# in ._helpers, used by assert_forward_progress) is a deliberately-duplicated
# mirror of src/agent.py's anchor-phrase floor above -- kept separate so the
# test helper file stays import-free of agent internals. Duplication means
# drift: the agent's own anchor-phrase fix (word-inserted-mid-question, the
# tests above) never propagated to this copy, so the continuous simulator
# kept flagging genuinely correct household_size paraphrases as forward-
# progress violations. Found live 2026-07-28 via redteam/findings/, not a
# human transcript this time. These tests pin the helper's copy directly.
# ---------------------------------------------------------------------------


def test_w53_helper_anchor_recognizes_word_inserted_household_question():
    """The exact live simulator finding: "in your household" inserted
    mid-sentence must still count as asking for household_size."""
    reply = (
        "Sounds good — let's get started. How many people, including "
        "you, usually buy and prepare food together in your household? "
        "If you usually buy and cook just for yourself, answer 1."
    )
    assert _w53_asks_for_field(reply, "household_size", "en") is True


def test_w53_helper_anchor_recognizes_empathetic_lead_in():
    """A second live simulator finding, same root cause: an empathetic
    lead-in before the real question must not defeat the match."""
    reply = (
        "No worries at all — this kind of thing can feel like a lot "
        "of questions. We can stop whenever you want, and nothing is "
        "recorded until you say so.\n\nIf you'd like to keep going, I'll "
        "just need one small thing: how many people, including you, "
        "usually buy and prepare food together in your household?"
    )
    assert _w53_asks_for_field(reply, "household_size", "en") is True


def test_w53_helper_still_matches_the_unmodified_canonical_question():
    """Non-regression: the exact canonical sentence, with no paraphrase
    at all, must still match (this is the original, pre-anchor behavior
    and must keep working)."""
    reply = (
        "Got it. How many people, including you, usually buy and prepare "
        "food together? If you usually buy and cook just for yourself, "
        "answer 1."
    )
    assert _w53_asks_for_field(reply, "household_size", "en") is True


def test_w53_helper_anchor_does_not_match_a_canned_redirect_description():
    """Same false-positive risk src/agent.py's anchor already guards
    against: a reply merely LISTING household size as one of the things
    still needed must not count as asking the real question."""
    reply = (
        "I can't help with that. I just need your household size, your "
        "gross monthly income, and whether anyone in your household is "
        "elderly or disabled."
    )
    assert _w53_asks_for_field(reply, "household_size", "en") is False


def test_w53_helper_anchor_scope_is_household_size_only():
    """The anchor fallback must stay scoped to household_size -- the
    other two fields still require the full canonical sentence, same as
    src/agent.py's own scoping and for the same reason (a short anchor
    for income/elderly would risk matching a canned redirect that merely
    describes the remaining fields)."""
    redirect = (
        "I can't help with that. I just need your household size, your "
        "gross monthly income, and whether anyone in your household is "
        "elderly or disabled."
    )
    assert _w53_asks_for_field(redirect, "gross_monthly_income", "en") is False
    assert _w53_asks_for_field(redirect, "has_elderly_or_disabled_member", "en") is False


def test_w53_helper_anchor_recognizes_spanish_word_inserted_question():
    reply = (
        "Perfecto — empecemos. ¿Cuántas personas, "
        "incluyéndote, compran y preparan comida juntas en su hogar? "
        "Si normalmente compras y cocinas solo para ti, responde 1."
    )
    assert _w53_asks_for_field(reply, "household_size", "es") is True
