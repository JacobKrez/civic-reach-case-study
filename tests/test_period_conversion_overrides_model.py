"""The user's stated pay period beats the model's arithmetic.

Two failures found on 2026-07-28, both of which put a number in the
verdict that the user never said:

1. **The model converts, and converts wrong.** "i get 300 a week" arrived
   as ``update_state(gross_monthly_income=1200)`` -- 300 x 4, the
   intuitive-but-wrong month -- instead of 1300 (300 x 52/12). The agent
   confirmed "$1,200 per month". Normalizing the model's *value* can
   never catch this: 1200 is a perfectly plausible monthly income. Only
   the raw message shows the mistake.

2. **"300 weekly" parsed as $300/month.** The weekly hint pattern
   required "per/a/each" before "week", so the bare adverb matched
   nothing and the figure was read as already-monthly -- a quarter of the
   real income. Every other period pattern (hourly, yearly, daily)
   already carried its standalone adverb; weekly was the only gap.

The system prompt has always said the model must not do arithmetic. The
tool-call *value* was the hole in that rule.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent
from src.guardrails.ambiguity import normalize_income, period_converted_income

WEEKS_PER_MONTH = 52 / 12


class _ModelSays:
    """A provider that records whatever monthly figure it is told to."""

    def __init__(self, value):
        self.value = value

    def chat(self, messages, system=None, tools=None):
        return {
            "content": "Got it.",
            "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": self.value}}
            ],
        }


def _agent(model_value):
    agent = FoodPathAgent(llm_client=_ModelSays(model_value), retriever=lambda _q: [])
    agent.state.household_size = 4
    return agent


# --- bug 2: the bare "weekly" adverb -------------------------------------


@pytest.mark.parametrize(
    "text",
    ["300 weekly", "$300 weekly", "i get paid 300 weekly", "300 a week", "300/week"],
)
def test_weekly_phrasings_all_convert_to_monthly(text):
    assert normalize_income(text) == pytest.approx(300 * WEEKS_PER_MONTH)


def test_biweekly_is_not_swallowed_by_the_weekly_pattern():
    """Every-two-weeks is a different conversion and must not be silently
    treated as weekly."""
    assert normalize_income("300 biweekly") != pytest.approx(300 * WEEKS_PER_MONTH)


# --- bug 1: the model's arithmetic ---------------------------------------


@pytest.mark.parametrize(
    "message, model_value, expected",
    [
        # The live failure: 300 x 4 instead of 300 x 52/12.
        ("i get 300 a week from unemployment", 1200, 300 * WEEKS_PER_MONTH),
        ("300 weekly", 300, 300 * WEEKS_PER_MONTH),
        # Annual: the model divides by 10, or forgets entirely.
        ("$42,000 a year", 42000, 3500.0),
        ("i make 42000 a year", 4200, 3500.0),
    ],
)
def test_stated_period_overrules_a_wrong_model_conversion(
    message, model_value, expected
):
    agent = _agent(model_value)
    agent.turn(message)
    assert agent.state.gross_monthly_income == pytest.approx(expected)


def test_the_override_is_recorded_in_the_audit_trail():
    agent = _agent(1200)
    agent.turn("i get 300 a week")
    events = [e.get("event") for e in agent._audit]
    assert "model_period_conversion_overruled" in events


# --- the override must stay narrow ---------------------------------------


@pytest.mark.parametrize(
    "message, model_value",
    [
        # Already monthly: nothing to convert, model's reading stands.
        ("i make 2500 a month", 2500),
        ("2500", 2500),
        # Multi-number sentence with no converting period: picking the
        # right number is the model's job, not this guard's.
        ("1800 and I pay 400 for daycare", 1800),
        ("my rent is 900, income is 2000", 2000),
    ],
)
def test_ordinary_readings_are_left_alone(message, model_value):
    agent = _agent(model_value)
    agent.turn(message)
    assert agent.state.gross_monthly_income == pytest.approx(float(model_value))


def test_a_message_naming_two_periods_is_not_overridden():
    """"2500 a month but paid 600 a week" is ambiguous about which figure
    is the income; the guard must not pick one."""
    assert period_converted_income("i make 2500 a month but paid 600 a week") is None


def test_helper_returns_none_without_a_converting_period():
    assert period_converted_income("1800") is None
    assert period_converted_income("") is None
    assert period_converted_income("2500 a month") is None
