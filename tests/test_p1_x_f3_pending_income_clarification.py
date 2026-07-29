"""F3 tripwires \u2014 pending income clarification must always surface.

The doc (CONVERSATION_OVERHAUL.md \u00a79.3 F3) names a specific failure
mode: when the user states two income figures \u2014 one past, one
current \u2014 the agent must surface a clarification question that
quotes BOTH figures back, not a generic re-ask. The probe message
from \u00a79.1: "i was making 2500 but now i just get unemployment,
like 300 a week i think" \u2014 a household income changed mid-message,
exactly the case the brief calls out (\u00a79.3: "the brief's *named*
ambiguous-income case; the reviewer WILL type something like it").

Pre-F3: ``classify_income_ambiguity`` returns None for this
message (no reason code matches two-figure income), the income
update is silently dropped (no clarification is built), and the
user hears a generic re-ask of the income question they just
answered.

Post-F3: a new reason code (AMBIGUITY_TWO_FIGURES) catches the
two-figure case; a matching clarification question quotes both
figures and asks which is the current monthly figure; the income
clarification pipeline (already wired for the other reason codes)
now handles this case too.

This file's tripwires:
  (a) Detection: ``classify_income_ambiguity`` returns the new
      reason for the probe message and other two-figure messages.
  (b) Clarification: ``income_clarification_question`` returns a
      question that quotes BOTH figures back to the user.
  (c) End-to-end: the agent's reply for the probe message after
      household_size=4 ends with the two-figure clarification, not
      a generic income re-ask, and ``gross_monthly_income`` lands
      within one further turn when the user picks a figure.

All tripwires are failing-before on the pre-F3 code.
"""

from __future__ import annotations

import os
import re

import pytest

from src.agent import FoodPathAgent
from src.guardrails.ambiguity import (
    AMBIGUITY_HOURLY_NO_HOURS,
    AMBIGUITY_DAILY_NO_DAYS,
    AMBIGUITY_PERIOD_UNCLEAR_HIGH,
    AMBIGUITY_PERIOD_UNCLEAR_LOW,
    classify_income_ambiguity,
    income_clarification_question,
)


@pytest.fixture(autouse=True)
def _skip_classifier_for_f3_tests():
    """The F3 end-to-end tests use a stub LLM that returns no
    tool_calls and empty content. With the real classifier ON, the
    classifier calls the LLM on every turn, the stub returns
    empty content, the classifier returns some default, and the
    agent's tool-call path never fires — so the household_size
    record on turn 1 never applies. The F3 contract is the
    INTENDED behavior of the agent's income-clarification
    pipeline; the classifier-vs-stub interaction is an artifact of
    using a stub. Set AGENT_SKIP_CLASSIFIER_ON_CLEAN=1 for this
    test file only.

    See src/agent.py for the env var's behavior.
    """
    prev = os.environ.get("AGENT_SKIP_CLASSIFIER_ON_CLEAN")
    os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("AGENT_SKIP_CLASSIFIER_ON_CLEAN", None)
        else:
            os.environ["AGENT_SKIP_CLASSIFIER_ON_CLEAN"] = prev


# The probe message from \u00a79.1, verbatim.
PROBE = "i was making 2500 but now i just get unemployment, like 300 a week i think"


# ---------------------------------------------------------------------------
# (a) Detection tripwires
# ---------------------------------------------------------------------------


def test_f3_two_figures_probe_is_classified_as_ambiguous():
    """The \u00a79.1 probe message has two income figures (2500 past,
    300 a week current). It MUST be classified as ambiguous \u2014
    pre-F3 returns None and the bot drops the income silently.
    """
    result = classify_income_ambiguity(PROBE)
    assert result is not None, (
        f"F3: probe message must be classified as ambiguous; "
        f"pre-F3 returns None and the income is silently dropped. "
        f"message={PROBE!r}"
    )
    # The reason code must be a new "two figures" code. We don't
    # require the literal string \u2014 we require that the reason
    # code is distinct from the existing four codes AND that the
    # agent's reply pipeline treats it as a clarification (any
    # non-None, non-empty reason that drives a question works).
    assert result not in (
        AMBIGUITY_HOURLY_NO_HOURS,
        AMBIGUITY_DAILY_NO_DAYS,
        AMBIGUITY_PERIOD_UNCLEAR_HIGH,
        AMBIGUITY_PERIOD_UNCLEAR_LOW,
    ), (
        f"F3: two-figures case should have its own reason code, "
        f"not reuse an existing one. got={result!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        # The exact \u00a79.1 probe.
        "i was making 2500 but now i just get unemployment, like 300 a week i think",
        # Variant: swap the figure positions.
        "i get 300 a week now, was making 2500 before",
        # Variant: same idea, different tense phrasing.
        "i was making $2,500 but now i only get $300 a week",
        # Variant: present-tense only with the past.
        "before i got laid off i made 2500, now its 300 a week",
    ],
)
def test_f3_two_figures_variants_are_all_classified(text):
    """The two-figures pattern is past + present income. Multiple
    phrasings must all classify the same way.
    """
    result = classify_income_ambiguity(text)
    assert result is not None, (
        f"F3: two-figures variant must classify as ambiguous; "
        f"got None for {text!r}"
    )


# ---------------------------------------------------------------------------
# (b) Clarification-question tripwires
# ---------------------------------------------------------------------------


def test_f3_two_figures_clarification_quotes_both_figures():
    """The clarification question for the probe message must quote
    BOTH the past figure ($2500) and the current figure ($300)
    back to the user, so the user can pick the right one. A
    clarification that names only one figure defeats the purpose.
    """
    reason = classify_income_ambiguity(PROBE)
    assert reason is not None
    en_q = income_clarification_question(reason, "en", text=PROBE)
    es_q = income_clarification_question(reason, "es", text=PROBE)
    # Both figures must appear in the clarification (in either order).
    # Strip non-digits and check the rounded hundreds.
    for q, lang in ((en_q, "en"), (es_q, "es")):
        assert q, f"F3: clarification question is empty for {lang}"
        # Both 2500 and 300 should appear (or 2,500 and 300).
        has_2500 = "2,500" in q or "2500" in q
        has_300 = "300" in q
        assert has_2500, f"F3 ({lang}): clarification must quote $2500; got {q!r}"
        assert has_300, f"F3 ({lang}): clarification must quote $300; got {q!r}"


def test_f3_two_figures_clarification_mentions_current():
    """The clarification must distinguish past from present \u2014
    the user needs to know which figure is the "current" one to
    pick. The question should mention that the income changed.
    """
    reason = classify_income_ambiguity(PROBE)
    en_q = income_clarification_question(reason, "en").lower()
    # Look for the framing: "changed" / "different" / "now" / "before"
    # (any of these signals the two-figures framing).
    has_framing = (
        "changed" in en_q
        or "different" in en_q
        or "now" in en_q
        or "before" in en_q
    )
    assert has_framing, (
        f"F3: clarification must signal past vs. present (income "
        f"changed); got {en_q!r}"
    )


# ---------------------------------------------------------------------------
# (c) End-to-end tripwire: the agent's reply for the probe message
#     surfaces the clarification, not a generic income re-ask.
# ---------------------------------------------------------------------------


class _HappyPathHouseholdProvider:
    """A model that records household_size=3 when asked, then does
    nothing on the income turn (so the deterministic layer must
    surface the clarification). Mirrors the \u00a79.1 turn-3 failure
    shape (model fails to make a tool call for the income).
    """

    def __init__(self) -> None:
        self.turns = 0

    def chat(self, messages, system=None, tools=None):
        self.turns += 1
        if self.turns == 1:
            return {
                "content": "Got it.",
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 4}}
                ],
            }
        # Income turn: do nothing. Deterministic layer must fire.
        return {"content": "", "tool_calls": []}


def test_f3_probe_message_end_to_end_surfaces_two_figures_clarification():
    """End-to-end F3 tripwire: after household_size=4, the probe
    income message must NOT be silently dropped. The reply must
    surface a clarification that quotes both figures (pre-F3 this
    is a generic "what is your household's gross monthly income?"
    because the classifier returns None and no clarification is
    built).
    """
    llm = _HappyPathHouseholdProvider()
    agent = FoodPathAgent(llm_client=llm)
    agent.turn("its me, my two kids, and my mom")  # household_size=4
    reply = agent.turn(PROBE)

    # The reply must contain both figures quoted back.
    assert "2,500" in reply or "2500" in reply, (
        f"F3: reply must quote the past $2500 figure; got {reply!r}"
    )
    assert "300" in reply, (
        f"F3: reply must quote the current $300 figure; got {reply!r}"
    )
    # The reply must NOT be a generic re-ask of the income question.
    lowered = reply.lower()
    generic_ask = "what is your household's gross monthly income" in lowered
    assert not generic_ask, (
        f"F3: reply must NOT be the generic income re-ask; the "
        f"two-figures case requires the named clarification. "
        f"reply={reply!r}"
    )
    # The reply should not silently have set gross_monthly_income.
    assert agent.state.gross_monthly_income is None, (
        "F3: probe message must NOT silently commit an income; "
        "the clarification must land first"
    )


def test_f3_probe_clarification_can_be_resolved_in_one_further_turn():
    """End-to-end closure: after the clarification, the user's
    follow-up (\"$300 a week\") resolves to a real income figure
    (weekly \u00d7 52/12). The agent's intake progresses from
    household=4 to gross_monthly_income=1300 in 2 more turns.
    """
    llm = _HappyPathHouseholdProvider()
    agent = FoodPathAgent(llm_client=llm)
    agent.turn("its me, my two kids, and my mom")
    clarification_reply = agent.turn(PROBE)
    # The clarification must contain both figures.
    assert "2,500" in clarification_reply or "2500" in clarification_reply
    assert "300" in clarification_reply
    # Follow-up: user picks "$300 a week" as the current figure.
    # Use a model that records the conversion correctly.
    class _WeeklyIncomeProvider:
        def chat(self, messages, system=None, tools=None):
            return {
                "content": "Got it \u2014 $300 a week.",
                "tool_calls": [
                    {"name": "update_state", "input": {"gross_monthly_income": 300.0}}
                ],
            }

    agent_llm = _WeeklyIncomeProvider()
    # Swap the LLM so the second turn uses a model that commits the income
    # (in weekly dollars; the deterministic normalizer converts to monthly).
    agent._llm_client = agent_llm
    agent.turn("$300 a week")
    # F3 closure: the user picked a figure and the agent committed it.
    # The exact weekly-to-monthly conversion is covered by
    # test_income_period_ambiguity.py; here we just assert the
    # clarification->commit loop works end-to-end.
    assert agent.state.gross_monthly_income is not None, (
        "F3: gross_monthly_income must land after the user picks a figure; "
        f"got {agent.state.gross_monthly_income!r}"
    )
    assert agent.state.gross_monthly_income > 0, (
        "F3: gross_monthly_income must be a positive number"
    )
