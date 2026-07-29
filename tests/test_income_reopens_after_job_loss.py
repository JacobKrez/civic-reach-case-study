"""A recorded income the user has just disowned must be re-asked.

Live, 2026-07-29, from the web UI:

    USER: 1k
    ASST: Right — $1,000 per month. Now think about the people you
          included. Is anyone age 60 or older...?
    USER: wait i just lost my job, what time frame do i use for income?
    ASST: [explains the rule correctly] ... Now think about the people you
          included. Is anyone age 60 or older...?
    USER: 0
    ASST: Got it — $0 a month going forward. Now back to the question I
          asked: is anyone age 60 or older...?

State still read ``"gross_monthly_income": 1000``.

The agent explained that the recorded figure was wrong and then **moved
on**, because income was already filled and the next missing field was the
elderly question. It never re-asked for the number it had just invalidated.
So when the user typed "0" meaning income, the agent's last question had
been about age, the answer had nowhere to go, and $1,000 survived into the
verdict.

A figure the user has disowned is worse than a missing one: it is wrong,
it looks confirmed, and here it is wrong in the direction that denies
benefits to a household that has just lost its income.

Clearing it puts income back at the front of ``missing_fields`` so the
ordinary intake asks again — no special-case reply, no new conversational
state.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent


class _NoToolLLM:
    def chat(self, messages, system=None, tools=None):
        return {"content": "", "tool_calls": []}


class _RecordsIncome:
    def __init__(self, value):
        self.value = value

    def chat(self, messages, system=None, tools=None):
        return {
            "content": "ok",
            "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": self.value}}
            ],
        }


def _agent(client=None):
    agent = FoodPathAgent(
        llm_client=client or _NoToolLLM(), retriever=lambda _q: []
    )
    agent.state.household_size = 3
    agent.state.gross_monthly_income = 1000.0
    return agent


# A change the user presents as CURRENT, or one they are visibly unsure
# how to report. Both are claims that the recorded figure is now wrong.
REOPENS = [
    "wait i just lost my job, what time frame do i use for income?",
    "i just lost my job",
    "my husband just lost his job",
    "i just got laid off",
    "i am no longer working",
    "my hours got cut, what do i put now?",
    "i lost my job recently",
]

# Someone else's news, or nothing at all. The household's own income is
# unaffected and must survive.
LEAVES_ALONE = [
    # Someone whose income is not part of this household.
    "my landlord lost his job",
    "my neighbor got laid off",
    "my boss quit his job",
    "my friend was fired",
    # Background: how the household got here, not a withdrawal of the
    # figure it just gave. The simulator caught this shape as a re-ask of
    # a filled field across 10 transcripts.
    "My partner lost his job in March so things are tight.",
    "We had a car repair that wiped out what savings we had.",
    "everything is fine",
    "what documents do i need?",
]


@pytest.mark.parametrize("message", REOPENS)
def test_a_disowned_income_is_cleared_so_it_gets_asked_again(message):
    agent = _agent()
    agent.turn(message)
    assert agent.state.gross_monthly_income is None, message
    assert "gross_monthly_income" in agent.state.missing_fields(), message


@pytest.mark.parametrize("message", LEAVES_ALONE)
def test_someone_elses_job_loss_does_not_clear_the_household_income(message):
    agent = _agent()
    agent.turn(message)
    assert agent.state.gross_monthly_income == 1000.0, message


def test_a_replacement_figure_in_the_same_message_is_kept_not_cleared():
    """"I lost my job, now I get $300 a week" is a correction. Clearing
    would throw away the figure the user just supplied."""
    agent = _agent(_RecordsIncome(1300))
    agent.turn("i lost my job, now i get 300 a week")
    assert agent.state.gross_monthly_income == pytest.approx(1300.0)


def test_a_stale_verdict_is_invalidated_too():
    agent = _agent()
    agent.state.has_elderly_or_disabled_member = False
    agent._calculate_once()
    assert agent.eligibility_result is not None
    agent.turn("i just lost my job")
    assert agent.eligibility_result is None
    assert agent.state.gross_monthly_income is None


def test_the_reopen_is_recorded_in_the_audit_trail():
    agent = _agent()
    agent.turn("i just lost my job")
    events = [entry.get("event") for entry in agent._audit]
    assert "income_reopened_after_change" in events


def test_nothing_happens_when_no_income_was_recorded_yet():
    agent = FoodPathAgent(llm_client=_NoToolLLM(), retriever=lambda _q: [])
    agent.state.household_size = 3
    agent.turn("i just lost my job")
    assert agent.state.gross_monthly_income is None


def test_end_to_end_the_zero_answer_lands_after_the_reopen():
    """The full sequence from the report: the screen must finish on the
    corrected figure, not the abandoned one."""
    agent = _agent()
    agent.turn("i just lost my job, what time frame do i use?")
    assert agent.state.gross_monthly_income is None

    agent.llm_client = _RecordsIncome(0)
    agent.turn("0")
    assert agent.state.gross_monthly_income == 0.0
