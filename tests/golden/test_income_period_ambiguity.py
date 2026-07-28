"""Income pay-period handling: convert what the documents define, ask for the rest.

Found in live human review on 2026-07-27. The screen collects a single
**gross monthly** figure, but people answer with whatever period they get
paid in. Three failure shapes existed, all silent:

1. **Annual read as monthly.** "$42,000 a year" was stored as $42,000 a
   MONTH. KB/02 ("Annual / post-tax / 'it varies'") states the conversion
   explicitly -- divide by 12 -- and the code simply did not implement it.
   A household at $3,500/month is likely eligible; at $42,000/month it is
   denied. This produced a false denial from a perfectly ordinary answer,
   which is the most harmful output this tool can produce.

2. **Daily read as monthly.** "$70 a day" was stored as $70/month.

3. **A bare number was never questioned.** "70" was stored as $70/month
   with no acknowledgement that it could as easily have been per day or
   per week, and "70000" was stored as $70,000/month rather than being
   recognised as an obvious annual figure.

The rule this file pins down: **convert where the source documents define
a conversion (annual, weekly, hourly-with-hours), and ASK where they do
not (a daily rate with no days, an hourly rate with no hours, a bare
number whose magnitude makes "per month" implausible).** Guessing is never
an option, because a wrong income silently produces a wrong verdict.

The asking must also be *useful*: a clarification that just re-asks "what
is your gross monthly income?" is the dead-end loop that made the screen
impossible to finish. Each clarification names the specific choice, and
the answer is rejoined with the half the user already gave.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent
from src.guardrails.ambiguity import (
    AMBIGUITY_DAILY_NO_DAYS,
    AMBIGUITY_HOURLY_NO_HOURS,
    AMBIGUITY_PERIOD_UNCLEAR_HIGH,
    AMBIGUITY_PERIOD_UNCLEAR_LOW,
    classify_income_ambiguity,
    normalize_income,
)

_WEEKS_PER_MONTH = 52 / 12


class SilentProvider:
    """Force every transition through the deterministic backstops.

    The conversions and clarifications must hold with no model help at
    all -- they are the floor under a weak or unavailable provider.
    """

    def chat(self, *_args, **_kwargs):
        return {"content": "", "tool_calls": []}


def _drive(messages: list[str]) -> FoodPathAgent:
    agent = FoodPathAgent(llm_client=SilentProvider())
    for message in messages:
        agent.turn(message)
    return agent


# ---------------------------------------------------------------------------
# 1. Conversions the knowledge base defines.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # KB/02: "If the user reports 'I make $42,000 a year', the monthly
        # figure is $3,500." That worked example is the tripwire.
        ("$42,000 a year", 3500.0),
        ("42000 per year", 3500.0),
        ("42000/yr", 3500.0),
        ("$70,000 annually", 70_000 / 12),
        ("70k a year", 70_000 / 12),
        # Weekly and hourly conversions already existed; pinned so the
        # annual/daily additions cannot disturb them.
        ("$600/week", 600 * _WEEKS_PER_MONTH),
        ("$15/hr, 40 hours a week", 15 * 40 * _WEEKS_PER_MONTH),
        ("$70 a day, 5 days a week", 70 * 5 * _WEEKS_PER_MONTH),
    ],
)
def test_stated_period_is_converted_to_monthly(text, expected):
    assert normalize_income(text) == pytest.approx(expected, abs=0.01)


def test_annual_is_not_read_as_monthly_end_to_end():
    """The reported bug, at the agent level: a $42k salary must not be
    screened as a $42k monthly income."""
    agent = _drive(["3", "$42,000 a year"])
    assert agent.state.gross_monthly_income == pytest.approx(3500.0, abs=0.01)


# ---------------------------------------------------------------------------
# 2. Ambiguity is classified rather than guessed at.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,reason",
    [
        ("$15/hr", AMBIGUITY_HOURLY_NO_HOURS),
        ("$15 hourly", AMBIGUITY_HOURLY_NO_HOURS),
        ("$70 a day", AMBIGUITY_DAILY_NO_DAYS),
        ("70 daily", AMBIGUITY_DAILY_NO_DAYS),
        # Implausible as a monthly figure in either direction.
        ("70", AMBIGUITY_PERIOD_UNCLEAR_LOW),
        ("70000", AMBIGUITY_PERIOD_UNCLEAR_HIGH),
    ],
)
def test_ambiguous_answers_are_classified(text, reason):
    assert classify_income_ambiguity(text) == reason


@pytest.mark.parametrize(
    "text",
    [
        "2500",              # plainly plausible as monthly
        "$1,800",
        "0",                 # "no income" is a real answer, never questioned
        "$2,500 a month",    # period stated outright
        "$600/week",         # period stated outright
        "$15/hr, 40 hours a week",
    ],
)
def test_unambiguous_answers_are_left_alone(text):
    """Asking has a cost: it is one more turn between a hungry person and
    an answer. It must fire only when the alternative is a guess."""
    assert classify_income_ambiguity(text) is None


def test_a_better_reading_than_the_naive_one_is_never_second_guessed():
    """When something upstream understood the message better than a
    re-parse of it would, that reading wins.

    "2 of us, $1000/mo" leads with a household size, so a naive parse
    reads the income as $2. "$15/hr 40hrs" stores the hours in a shape the
    regexes miss. In both cases the extracted value is correct and must
    not be dropped in favour of a clarifying question.
    """
    assert classify_income_ambiguity("2 of us, $1000/mo", value=1000.0) is None
    assert classify_income_ambiguity("$15/hr 40hrs", value=2600.0) is None


# ---------------------------------------------------------------------------
# 3. The clarification actually resolves the ambiguity.
# ---------------------------------------------------------------------------


def test_bare_number_asks_which_period_and_does_not_store_a_guess():
    agent = _drive(["3"])
    reply = agent.turn("70")

    assert agent.state.gross_monthly_income is None, "a guess was stored"
    lowered = reply.lower()
    assert "per month" in lowered and "per week" in lowered and "per day" in lowered
    assert "$70" in reply, "the clarification must quote the user's own figure back"


def test_implausibly_large_bare_number_asks_year_or_month():
    agent = _drive(["3"])
    reply = agent.turn("70000").lower()
    assert "per year" in reply and "per month" in reply


def test_clarification_is_not_a_verbatim_re_ask_of_the_same_question():
    """The failure this replaces: answering the income question produced
    the income question again, verbatim, forever."""
    agent = _drive(["3"])
    reply = agent.turn("70")
    assert "what is your household's gross monthly income" not in reply.lower()


@pytest.mark.parametrize(
    "opening,answer,expected",
    [
        # The reply carries only half the answer; it is meaningless unless
        # rejoined with the half already given. Read alone, "40" would be
        # stored as $40/month instead of $2,600.
        ("$15/hr", "40", 15 * 40 * _WEEKS_PER_MONTH),
        ("$15/hr", "40 hours a week", 15 * 40 * _WEEKS_PER_MONTH),
        ("$70 a day", "5", 70 * 5 * _WEEKS_PER_MONTH),
        ("$70 a day", "5 days a week", 70 * 5 * _WEEKS_PER_MONTH),
        ("70000", "per year", 70_000 / 12),
        ("70", "per month", 70.0),
    ],
)
def test_answer_to_the_clarification_is_rejoined_with_what_came_before(
    opening, answer, expected
):
    agent = _drive(["3", opening, answer])
    assert agent.state.gross_monthly_income == pytest.approx(expected, abs=0.01)


def test_clarification_can_take_more_than_one_exchange():
    """"70" -> "a day" -> "5" carries one fragment per turn; every
    fragment has to survive to the end."""
    agent = _drive(["3", "70", "a day", "5"])
    assert agent.state.gross_monthly_income == pytest.approx(
        70 * 5 * _WEEKS_PER_MONTH, abs=0.01
    )


def test_resolved_income_reaches_the_verdict():
    """End to end: an ambiguous answer, clarified, produces a screening
    result computed from the CLARIFIED figure."""
    agent = _drive(["3", "$15/hr", "40 hours a week", "no"])
    assert agent.state.gross_monthly_income == pytest.approx(2600.0, abs=0.01)
    assert agent.eligibility_result is not None
    assert agent.eligibility_result["income_considered"] == pytest.approx(2600.0, abs=0.01)


def test_zero_income_is_never_questioned():
    """"0" is one of the most common truthful answers this screen gets and
    must go straight through."""
    agent = _drive(["3", "0"])
    assert agent.state.gross_monthly_income == 0.0


class GuessingProvider:
    """A model that confidently commits to a period the user never gave.

    This is the live failure mode, not a hypothetical: the weaker the
    provider, the more likely it answers "70" with "Got it - $70 per
    month." and a matching tool call. The deterministic layer has to win
    that disagreement, because the model's confidence is not evidence.
    """

    def __init__(self) -> None:
        self.turns = 0

    def chat(self, messages, system=None, tools=None):
        self.turns += 1
        if self.turns == 1:
            return {
                "content": "Thanks!",
                "tool_calls": [{"name": "update_state", "input": {"household_size": 3}}],
            }
        return {
            "content": "Got it — $70 per month.",
            "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": 70}}
            ],
        }


def test_a_confident_model_guess_is_overruled_not_stored():
    agent = FoodPathAgent(llm_client=GuessingProvider())
    agent.turn("3")
    reply = agent.turn("70")

    assert agent.state.gross_monthly_income is None, (
        "the model's guessed period was written to state"
    )
    assert "per week" in reply.lower() and "per day" in reply.lower()


def test_the_overruled_guess_is_not_still_asserted_in_the_reply():
    """The model's sentence claimed the figure we are questioning. Shipping
    both would tell the user their answer was accepted and then ask them to
    re-state it -- the reply must not contradict itself."""
    agent = FoodPathAgent(llm_client=GuessingProvider())
    agent.turn("3")
    reply = agent.turn("70")

    assert "got it — $70 per month" not in reply.lower()


def test_clarification_is_audited():
    """The turn log has to show why the agent asked, so a transcript is
    debuggable after the fact."""
    agent = _drive(["3", "70"])
    events = [entry.get("event") for entry in agent._audit]
    assert "income_ambiguous_clarification_requested" in events
