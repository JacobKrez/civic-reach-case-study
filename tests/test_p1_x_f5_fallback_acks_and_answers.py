"""F5 tripwires \u2014 fallback replies must keep the ack and answer the question.

The doc (CONVERSATION_OVERHAUL.md \u00a79.3 F5) names a specific failure
mode: the agent's intake conversation has multiple ways to end up
with a fallback that ships the bare canonical question when the
user just asked something. Two pathways in particular:

  1. P1.2 validators reject mid-intake \u2192 the fallback path
     builds a templated reply consisting of just the
     next-field question (no answer to what the user asked).
  2. The model returns empty content + no tool calls (the
     \u00a79.1 turn-4 shape) \u2192 the agent's reply becomes a bare
     re-ask of the next field.

F5 closes both: when the user\u2019s message ends in ``?`` and matches
a known informational class (elderly-relevance, the doc\u2019s only
named class), the fallback reply must contain an affirmative
answer AND (when F4 fired in the same turn) record the fact AND
re-ask the next field.

The probe tripwire (\u00a79.1 turn-4): "my mom is 68 does that matter?"
\u2014 the user asked AND stated an intake fact. The post-F5 reply
must contain:
  * the affirmative 60+ rule ("Yes \u2014 someone 60+ changes which
    income test applies" or substantively equivalent),
  * the recorded ``has_elderly_or_disabled_member=True`` (F4),
  * the next-field question (income).

Pre-F5: the reply is "How many people...?" (the wrong field\u2019s
question) or some other bare re-ask; nothing addresses the
user\u2019s question.

This file\u2019s tripwires:
  (a) Probe tripwire: \u00a79.1 turn-4 reply shape with F4 AND F5
      engaged together.
  (b) Negative: when the message is NOT a question, the
      fallback does NOT add a fake answer.
  (c) Out-of-scope question: a question not matching any
      known informational class is NOT answered.
  (d) Empty-content path: when the model returns empty content
      and the user asked elderly-relevance, the reply still
      contains the affirmative answer.
  (e) Spanish parity: the affirmative answer exists in ES too.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _enable_classifier_skip_and_f4_for_f5_tests():
    """F5 tripwires drive a 4-turn conversation that ends with the
    \u00a79.1 probe message. Stub LLMs are used throughout, so we set
    AGENT_SKIP_CLASSIFIER_ON_CLEAN=1 (so the classifier doesn\u2019t
    call the stub) and F4_EXTRACTION=1 (so the F4 wiring fires
    when the probe hits it). Both are restored on teardown.
    """
    prev_skip = os.environ.get("AGENT_SKIP_CLASSIFIER_ON_CLEAN")
    prev_f4 = os.environ.get("F4_EXTRACTION")
    os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = "1"
    os.environ["F4_EXTRACTION"] = "1"
    try:
        yield
    finally:
        if prev_skip is None:
            os.environ.pop("AGENT_SKIP_CLASSIFIER_ON_CLEAN", None)
        else:
            os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = prev_skip
        if prev_f4 is None:
            os.environ.pop("F4_EXTRACTION", None)
        else:
            os.environ["F4_EXTRACTION"] = prev_f4


# Stub LLM shape used across F5 tripwires.
# Turn 1: model emits household_size tool call.
# Turn 2: model emits gross_monthly_income tool call.
# Turn 3 (probe): model returns EMPTY content + no tool calls.
#   The probe message is the \u00a79.1 message "my mom is 68 does that
#   matter". Both F4 (extraction) and F5 (affirmative answer) must
#   engage on this turn.
# Stub extraction_call_payload is the tool_calls the F4
# extraction call will return.
EXTRACTION_PROMPT_MARKER = "extract only facts the user explicitly stated about"


class _FourTurnProbeStub:
    """Stub for the \u00a79.1 four-message probe.

    Returns household_size=3 then gross_monthly_income=2200 from
    the main pass. On turn 4 (probe), the main pass returns
    empty content + no tool calls; the F4 extraction pass is
    expected to fire and return the elderly tool call. The
    agent\u2019s reply should then include the affirmative 60+
    answer and the income next-field question.
    """

    def __init__(self, *, extraction_payload=None,
                 turn_4_main_content=""):
        self.chat_calls = []
        self.extraction_payload = extraction_payload or [
            {
                "name": "update_state",
                "input": {"has_elderly_or_disabled_member": True},
            }
        ]
        self.turn_4_main_content = turn_4_main_content

    def chat(self, messages, system=None, tools=None):
        self.chat_calls.append({
            "system_first40": (system or "")[:40],
        })
        # F4 extraction pass detection.
        if system and EXTRACTION_PROMPT_MARKER in system:
            return {
                "content": "",
                "tool_calls": list(self.extraction_payload),
            }
        # Main conversational pass.
        return {
            "content": self.turn_4_main_content,
            "tool_calls": [],
        }


def _drive_three_pre_probe_turns(agent):
    """Drive the first three turns of the probe conversation to
    match the §9.1 transcript exactly: household_size=3 (set by
    F2 in turn 2), gross_monthly_income=None (the §9.1
    drop-the-income turn), has_elderly_or_disabled_member=None.
    """
    agent.state.household_size = 3
    agent.state.gross_monthly_income = None  # §9.1 transcript
    agent.state.has_elderly_or_disabled_member = None
    agent.state.turn_count = 3


def _drive_drive_with_income_set(agent):
    """Alternate helper: same as the above but
    gross_monthly_income is also set. Tests that need to verify the
    F5 probe shape when the intake is fully pre-populated use
    this. The doc says the tripwire is "probe message 4 → reply
    contains affirmative AND records fact (with F4) AND asks
    income" -- "asks income" is the canonical FALLBACK the bot
    emits whenever the model content is missing/empty, NOT a
    rigid claim that income must still be missing from state.
    """
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 2200.0
    agent.state.has_elderly_or_disabled_member = None
    agent.state.turn_count = 3


# ---------------------------------------------------------------------------
# (a) Probe tripwire: \u00a79.1 turn-4 reply shape with F4 AND F5
# ---------------------------------------------------------------------------


def test_f5_probe_reply_contains_affirmative_60plus_answer():
    """The \u00a79.1 turn-4 tripwire, in full. The user asked AND
    stated an intake fact ("my mom is 68 does that matter?"). The
    reply must contain:
      (i) the affirmative 60+ rule,
      (ii) the recorded fact (F4 set the elderly field),
      (iii) the next-field question (income, since it\u2019s the only
            one still missing).
    Pre-F5 the reply is either the generic re-ask or the wrong
    field\u2019s question; nothing addresses the user\u2019s actual
    question.
    """
    from src.agent import FoodPathAgent

    stub = _FourTurnProbeStub()
    agent = FoodPathAgent(llm_client=stub)
    _drive_three_pre_probe_turns(agent)

    reply = agent.turn("my mom is 68 does that matter?")

    # (i) Affirmative 60+ answer \u2014 detect via vocabulary, not
    # exact phrasing (the exact template is up to F5 to choose).
    lowered = reply.lower()
    has_yes = any(w in lowered for w in ["yes", "correcto", "s\u00ed", "s\u00ed,"])
    has_elderly_fact = any(
        w in lowered
        for w in ["60+", "elderly", "senior", "mayor", "older adult"]
    )
    has_change_acknowledgment = any(
        w in lowered
        for w in ["changes", "switches", "qualifies", "applies", "aplica",
                  "cambia", "qualifica", "relevant", "relevante"]
    )
    assert has_yes, (
        f"F5: reply must start with an affirmative answer to the "
        f"60+ question; got {reply!r}"
    )
    assert has_elderly_fact, (
        f"F5: reply must reference the 60+/elderly fact; got {reply!r}"
    )
    assert has_change_acknowledgment, (
        f"F5: reply must acknowledge that 60+ changes the test; "
        f"got {reply!r}"
    )

    # (ii) F4 recorded the fact.
    assert agent.state.has_elderly_or_disabled_member is True, (
        "F5 tripwire depends on F4 having committed the field; "
        "the combined F4+F5 contract requires both"
    )

    # (iii) Next-field question (income, the only missing one).
    assert "monthly income" in lowered, (
        f"F5: reply must re-ask the income question; got {reply!r}"
    )


# ---------------------------------------------------------------------------
# (b) Negative: when the message is not a question, no fake answer
# ---------------------------------------------------------------------------


def test_f5_no_fake_answer_when_message_is_not_a_question():
    """A non-question user message ("by the way my mom is 68")
    must NOT trigger an unsolicited answer. The reply should
    still re-ask intake / commit whatever the extraction found,
    but never open with "Yes \u2014" or fabricate an answer to a
    question that wasn\u2019t asked.
    """
    from src.agent import FoodPathAgent

    stub = _FourTurnProbeStub()
    agent = FoodPathAgent(llm_client=stub)
    _drive_three_pre_probe_turns(agent)

    reply = agent.turn("by the way my mom is 68")  # NOT a question
    lowered = reply.lower()
    # The reply must NOT open with an answer to a non-question.
    # If the elderly fact was extracted by F4 (it should be \u2014
    # the vocabulary matches the household-composition gate),
    # the reply\u2019s affirmative phrasing should not appear.
    if "yes" in lowered[:50]:
        # Acceptable if it\u2019s mid-sentence (e.g. "Got it \u2014 yes I
        # noted that"). Reject only if it OPENS with "yes".
        first_word = reply.split(",", 1)[0].strip().lower()
        assert not first_word.startswith("yes"), (
            f"F5: a non-question must not open with an answer; "
            f"got {reply!r}"
        )


# ---------------------------------------------------------------------------
# (c) Out-of-scope question is NOT answered
# ---------------------------------------------------------------------------


def test_f5_no_answer_for_unrelated_questions():
    """A question not matching any known informational class
    ("what\u2019s your favorite color?", "can I get food stamps
    tomorrow?", etc.) must NOT receive a fabricated answer.
    The fallback should re-ask the missing field without
    pretending to know.
    """
    from src.agent import FoodPathAgent

    stub = _FourTurnProbeStub(
        # F4 extraction returns nothing for unrelated questions.
        extraction_payload=[],
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_three_pre_probe_turns(agent)

    reply = agent.turn("what is your favorite color?")
    lowered = reply.lower()
    # Must NOT contain a "Yes" opening (there\u2019s no informational
    # class match for "favorite color").
    first_word = reply.split(",", 1)[0].strip().lower()
    assert not first_word.startswith("yes"), (
        f"F5: out-of-scope question must not receive a fake answer; "
        f"got {reply!r}"
    )


# ---------------------------------------------------------------------------
# (d) Empty-content path: validator-fallback must also answer
# ---------------------------------------------------------------------------


def test_f5_empty_model_content_still_answers_user_question():
    """When the model returns empty content + no tool calls (the
    \u00a79.1 turn-4 shape), the agent\u2019s reply path goes through the
    W53 floor and may also trigger the validator fallback. F5\u2019s
    affirmative answer must surface through that path too \u2014 the
    user asked a question, and the user\u2019s question deserves an
    answer regardless of why the model emitted nothing.
    """
    from src.agent import FoodPathAgent

    # Stub model returns empty content on turn 4 + F4 extracts the
    # elderly fact. This is the canonical \u00a79.1 failure shape.
    stub = _FourTurnProbeStub(
        extraction_payload=[
            {"name": "update_state",
             "input": {"has_elderly_or_disabled_member": True}},
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    _drive_three_pre_probe_turns(agent)

    reply = agent.turn("my mom is 68 does that matter?")
    # Even though the model returned empty content, the user\u2019s
    # question was answered.
    assert "yes" in reply.lower()[:50], (
        f"F5: empty-model-content path must still answer the "
        f"user\u2019s question; got {reply!r}"
    )


# ---------------------------------------------------------------------------
# (e) Spanish parity: affirmative answer exists in ES too
# ---------------------------------------------------------------------------


def test_f5_spanish_parity_affirmative_answer():
    """F5\u2019s affirmative answer has a Spanish equivalent. The
    probe message in Spanish ("mi mam\u00e1 tiene 68 a\u00f1os \u00bfimporta?")
    triggers the same answer path.
    """
    from src.agent import FoodPathAgent

    stub = _FourTurnProbeStub(
        extraction_payload=[
            {"name": "update_state",
             "input": {"has_elderly_or_disabled_member": True}},
        ],
    )
    agent = FoodPathAgent(llm_client=stub)
    # Force Spanish by hitting the language-detection trigger
    # with a Spanish phrase first.
    agent.state.household_size = 3
    # Match the \u00a79.1 transcript: income drops on the second
    # main turn (post-F3, the bot now surfaces a clarification
    # instead of dropping, but for F5’s contract test we set the
    # same final-3 state the \u00a79.1 transcript exhibits).
    agent.state.gross_monthly_income = None
    agent.state.has_elderly_or_disabled_member = None
    agent.state.turn_count = 3
    # Run a Spanish-language turn first to set the state language.
    class SpanishTrigger:
        def chat(self, messages, system=None, tools=None):
            if system and EXTRACTION_PROMPT_MARKER in system:
                return {"content": "", "tool_calls": list(stub.extraction_payload)}
            return {"content": "Perfecto.", "tool_calls": []}
    agent._llm_client = SpanishTrigger()
    agent.turn("tengo tres personas en mi casa")  # sets language=es

    # Now match the §9.1 transcript state (income=None, elderly=None)
    # and drive the probe in Spanish.
    agent.state.has_elderly_or_disabled_member = None
    reply = agent.turn("mi mamá tiene 68 años ¿importa?")
    lowered = reply.lower()
    # Spanish affirmative (\"s\u00ed\" or \"correcto\") and the elderly
    # fact referenced (\"mayor\", \"60\"), and the income question
    # (which is the next missing field for ES too).
    has_yes = any(w in lowered for w in ["s\u00ed", "correcto", "s\u00ed."])
    has_elderly = any(
        w in lowered
        for w in ["mayor", "60", "elderly", "senior"]
    )
    has_income_q = (
        "ingreso" in lowered
        or "mensual" in lowered
        or "monthly income" in lowered
    )
    assert has_yes, (
        f"F5 ES: reply must contain Spanish affirmative; got {reply!r}"
    )
    assert has_elderly, (
        f"F5 ES: reply must reference the elderly fact; got {reply!r}"
    )
    assert has_income_q, (
        f"F5 ES: reply must re-ask the income question; got {reply!r}"
    )
