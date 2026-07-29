"""A bare answer to the question the agent just asked must be recorded.

Live, 2026-07-29, from the web UI:

    ASST: ...what is your household's gross monthly income?
    USER: 1k
    ASST: OK — $1,000 per month. ...
    USER: wait i just lost my job, what time frame do i use for income?
    ASST: ...use what you expect to receive now... If nothing is coming in,
          count $0. So right now, what's your household's gross monthly
          income — unemployment (if any), any other earnings, or $0?
    USER: 0
    ASST: Got it — $0 right now. I had $1,000 noted from before, but if
          that's no longer the case I'll update it.

The state pane still read ``"gross_monthly_income": 1000``. The model had
emitted the correct ``update_state(gross_monthly_income=0)`` and the W55
cue gate filtered it out: a bare "0" carries no income vocabulary, and
the ``asked_field`` escape hatch only covers fields still in
``missing_fields`` — income was already filled, so neither applied.

The agent asked the question and then discarded the answer, while its own
prose claimed it had updated. Zero is the value this matters most for: it
is the answer of a household with no income at all, which is the one most
likely to qualify.

The gate still does its real job — a number in a message about something
else must not become income — so the acceptance is narrow: the message
must be *nothing but* a number, and the assistant's own previous turn must
have asked about that specific field.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent


class _RecordsField:
    def __init__(self, field, value):
        self.field, self.value = field, value

    def chat(self, messages, system=None, tools=None):
        return {
            "content": "Got it.",
            "tool_calls": [
                {"name": "update_state", "input": {self.field: self.value}}
            ],
        }


def _agent(field, value, *, asked: str):
    agent = FoodPathAgent(
        llm_client=_RecordsField(field, value), retriever=lambda _q: []
    )
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 1000.0
    agent._messages.append({"role": "assistant", "content": asked})
    return agent


INCOME_QUESTION = (
    "So right now, what's your household's gross monthly income — "
    "unemployment (if any), any other earnings, or $0?"
)
ELDERLY_QUESTION = "Is anyone age 60 or older, or does anyone have a disability?"


@pytest.mark.parametrize(
    "answer,expected",
    [("0", 0.0), ("$0", 0.0), ("0.", 0.0), ("1500", 1500.0), ("$1,200", 1200.0)],
)
def test_a_bare_number_answers_the_income_question_just_asked(answer, expected):
    agent = _agent("gross_monthly_income", expected, asked=INCOME_QUESTION)
    agent.turn(answer)
    assert agent.state.gross_monthly_income == expected


def test_zero_replaces_a_previously_recorded_income():
    """The live case: $1,000 recorded, job lost, user answers 0."""
    agent = _agent("gross_monthly_income", 0, asked=INCOME_QUESTION)
    agent.turn("0")
    assert agent.state.gross_monthly_income == 0.0


def test_an_age_answering_the_elderly_question_never_becomes_income():
    """The guard the cue gate exists for. The assistant asked about age,
    so a bare number is not an income answer no matter what the model
    tries to record."""
    agent = _agent("gross_monthly_income", 72, asked=ELDERLY_QUESTION)
    agent.turn("72")
    assert agent.state.gross_monthly_income == 1000.0


def test_a_non_bare_message_still_needs_an_income_cue():
    """Acceptance is limited to messages that are only a number."""
    agent = _agent("gross_monthly_income", 72, asked=INCOME_QUESTION)
    agent.turn("my grandmother is 72 and lives with us")
    assert agent.state.gross_monthly_income == 1000.0


def test_no_prior_assistant_question_means_no_acceptance():
    agent = FoodPathAgent(
        llm_client=_RecordsField("gross_monthly_income", 5),
        retriever=lambda _q: [],
    )
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 1000.0
    agent.turn("5")
    assert agent.state.gross_monthly_income == 1000.0


def test_the_summary_and_the_state_agree_after_the_correction():
    """The reported symptom was the on-screen summary disagreeing with the
    reply. Both read from state, so the fix is that state is right."""
    agent = _agent("gross_monthly_income", 0, asked=INCOME_QUESTION)
    agent.turn("0")
    assert agent.state.model_dump()["gross_monthly_income"] == 0.0


def test_the_handoff_summary_is_branded_with_the_product_not_a_third_party():
    """The header hardcoded an organization name this tool is not
    affiliated with, on a page a case worker reads as a record of the
    screening."""
    from src.branding import PRODUCT_NAME

    agent = FoodPathAgent(llm_client=_RecordsField("household_size", 3), retriever=lambda _q: [])
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 1000.0
    agent.state.has_elderly_or_disabled_member = False
    agent._calculate_once()
    summary = agent.handoff_summary()["summary"]
    assert summary.splitlines()[0].startswith(PRODUCT_NAME.upper())
    assert "CIVICREACH" not in summary.upper()
