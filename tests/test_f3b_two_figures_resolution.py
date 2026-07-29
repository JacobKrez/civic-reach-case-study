"""F3b — a two-figures income clarification must be resolved by the
user's ANSWER, and must resolve nothing when the answer isn't one.

Live probe, 2026-07-28, against the real provider. The user said:

    "i was making 2500 but now i just get unemployment, like 300 a week
     i think"

F3 correctly detected two figures and asked "Which one is your current
gross monthly income?". Then the next message -- "my mom is 68 does that
matter", an elderly-field question with no income content at all --
resolved that clarification to **$10,833.33/month** and the agent
delivered a confident verdict of "likely ineligible".

Two defects compounded:

1. The held-context rejoin (built for "$15/hr" + "40 hours a week")
   fired on a message that answered nothing, because it never checked
   that the reply was an answer.
2. Rejoining "context, answer" and re-parsing the whole string cannot
   choose between two figures. It read the period cue attached to the
   SECOND figure ("300 a week") and applied it to the FIRST (2500):
   2500 * 52/12 = 10833.33. It returned that same value for *every*
   answer, the correct "the 300 one" included -- the answer never
   participated in the result.

A wrong income silently produces a wrong verdict, and a false denial is
the most harmful output this tool has. These tests pin both halves: the
answer selects the right figure with the right period, and a non-answer
resolves nothing.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent
from src.guardrails.ambiguity import (
    AMBIGUITY_TWO_FIGURES,
    classify_income_ambiguity,
    resolve_two_figures_answer,
)

TWO_FIGURES_MESSAGE = (
    "i was making 2500 but now i just get unemployment, like 300 a week i think"
)
# 300/week * 52/12 weeks per month.
EXPECTED_MONTHLY = 1300.0


def test_two_figures_message_is_classified_as_two_figures():
    assert classify_income_ambiguity(TWO_FIGURES_MESSAGE) == AMBIGUITY_TWO_FIGURES


@pytest.mark.parametrize(
    "answer",
    [
        "the 300 one",
        "300",
        "the unemployment one",
        "now",
        "currently",
        "the second one",
    ],
)
def test_answer_selecting_the_current_figure_applies_its_own_period(answer):
    """The selected figure keeps the period the user attached to IT.

    This is the half that produced 10833.33: the weekly cue belonged to
    300, not to 2500.
    """
    assert resolve_two_figures_answer(TWO_FIGURES_MESSAGE, answer) == pytest.approx(
        EXPECTED_MONTHLY
    )


@pytest.mark.parametrize("answer", ["the 2500", "before", "the first one"])
def test_answer_selecting_the_earlier_figure_returns_that_figure(answer):
    assert resolve_two_figures_answer(TWO_FIGURES_MESSAGE, answer) == pytest.approx(
        2500.0
    )


def test_restated_income_with_explicit_period_is_trusted():
    assert resolve_two_figures_answer(
        TWO_FIGURES_MESSAGE, "it's about $1,300 a month now"
    ) == pytest.approx(1300.0)


@pytest.mark.parametrize(
    "non_answer",
    [
        # The live failure: an elderly-field question containing a number.
        "my mom is 68 does that matter",
        "my kid is 12",
        "i'm not sure",
        "yes",
        "what do you mean",
        "",
    ],
)
def test_a_message_that_is_not_an_answer_resolves_nothing(non_answer):
    """``None`` is the whole point: the caller must then leave income
    unset and handle the message as whatever it actually was."""
    assert resolve_two_figures_answer(TWO_FIGURES_MESSAGE, non_answer) is None


def test_the_fabricated_value_is_never_produced_by_any_answer():
    """Regression pin on the exact observed number.

    10833.33 was reachable from every answer before the fix; it must now
    be reachable from none of them.
    """
    answers = [
        "my mom is 68 does that matter",
        "the 300 one",
        "300",
        "the 2500",
        "before",
        "now",
        "yes",
        "i don't know",
    ]
    for answer in answers:
        resolved = resolve_two_figures_answer(TWO_FIGURES_MESSAGE, answer)
        assert resolved != pytest.approx(10833.33, abs=0.5), (
            f"answer {answer!r} reproduced the fabricated 2500*52/12 reading"
        )


class _NoToolLLM:
    """A provider that answers in prose and never calls update_state.

    Reproduces the live provider's behaviour on the failing turn: the
    deterministic path is what must hold the line.
    """

    def __init__(self):
        self.calls = 0

    def chat(self, messages, system=None, tools=None):
        self.calls += 1
        return {"content": "Thanks, noted.", "tool_calls": []}


def _agent():
    return FoodPathAgent(llm_client=_NoToolLLM(), retriever=lambda _q: [])


def test_end_to_end_non_answer_does_not_fill_income_or_reach_a_verdict():
    """The full agent path, not just the resolver.

    Before the fix this sequence ended with gross_monthly_income set to
    10833.33 and an eligibility verdict on screen.
    """
    agent = _agent()
    agent.turn("there are 4 of us")
    assert agent.state.household_size == 4

    agent.turn(TWO_FIGURES_MESSAGE)
    assert agent.state.gross_monthly_income is None, (
        "an ambiguous two-figures message must not commit an income"
    )

    agent.turn("my mom is 68 does that matter")
    assert agent.state.gross_monthly_income is None, (
        "a message about a household member's age must never resolve a "
        "pending income clarification"
    )
    assert agent.eligibility_result is None, (
        "no verdict may be reached while income is still unknown"
    )


def test_end_to_end_real_answer_fills_income_with_the_converted_figure():
    agent = _agent()
    agent.turn("there are 4 of us")
    agent.turn(TWO_FIGURES_MESSAGE)
    agent.turn("the 300 one")
    assert agent.state.gross_monthly_income == pytest.approx(EXPECTED_MONTHLY)
