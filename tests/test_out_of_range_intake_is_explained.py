"""An intake answer the schema rejects must be explained, not swallowed.

Reported from live use, 2026-07-29: "it keeps asking the question over and
over". Reproduced exactly — a household size above the screener's cap
(``max_household_size()``, 20) fails Pydantic validation, the update is
discarded, ``household_size`` stays ``None``, and the agent re-asks the
identical question. The user answers again, and the loop never ends.
Nothing in the reply hints that the number was the problem.

This is the worst shape of bug for a live demo: the screen is
unfinishable, and it looks like the agent simply is not listening.

The fix records the rejected value before validation drops it, and the
reply names the number, names the limit, says whose limit it is (SNAP has
no maximum household size — the cap belongs to this tool), points a
genuinely larger household at their county DSS, and still asks the
question so the intake can continue.
"""

from __future__ import annotations

import pytest

from src.agent import FoodPathAgent
from src.calculator import max_household_size

CAP = max_household_size()


class _ModelRecords:
    """A provider that records whatever value it is constructed with."""

    def __init__(self, field: str, value):
        self.field = field
        self.value = value

    def chat(self, messages, system=None, tools=None):
        return {
            "content": "Got it.",
            "tool_calls": [
                {"name": "update_state", "input": {self.field: self.value}}
            ],
        }


def _agent(field, value):
    return FoodPathAgent(llm_client=_ModelRecords(field, value), retriever=lambda _q: [])


@pytest.mark.parametrize("value", [CAP + 1, 27, 100, 999])
def test_over_cap_household_is_explained_not_silently_dropped(value):
    agent = _agent("household_size", value)
    reply = agent.turn(str(value))

    assert agent.state.household_size is None, "an invalid value must never land"
    # Names the user's number and the limit, so the answer is actionable.
    assert str(value) in reply or f"{value:,}" in reply, reply
    assert str(CAP) in reply, reply


def test_the_explanation_does_not_imply_snap_has_a_maximum():
    """Honesty matters here: SNAP has no maximum household size. A user
    with a large household must not be left thinking they are ineligible
    because of one."""
    agent = _agent("household_size", 27)
    reply = agent.turn("27").lower()
    assert "not of snap" in reply or "snap has no maximum" in reply, reply
    assert "dss" in reply, reply


def test_the_turn_still_asks_the_question_so_intake_can_continue():
    agent = _agent("household_size", 27)
    reply = agent.turn("27")
    assert reply.rstrip().endswith("?"), reply


def test_a_corrected_answer_is_accepted_immediately_afterwards():
    """The whole point: the user must be able to escape."""
    agent = _agent("household_size", 27)
    agent.turn("27")
    agent.llm_client = _ModelRecords("household_size", 4)
    agent.turn("4")
    assert agent.state.household_size == 4


def test_zero_and_negative_household_get_their_own_explanation():
    for value in (0, -3):
        agent = _agent("household_size", value)
        reply = agent.turn(str(value)).lower()
        assert agent.state.household_size is None
        assert "at least one person" in reply, reply


def test_negative_income_never_reaches_state():
    """Income is guarded upstream, not by the range check.

    ``normalize_income`` refuses a negative number outright and reads
    "-50" as 50, so a negative can never land. The range table therefore
    covers household_size only -- listing income there would look like
    protection while never firing.
    """
    agent = _agent("gross_monthly_income", -50)
    agent.turn("-50")
    assert agent.state.gross_monthly_income is None


def test_the_rejection_is_recorded_in_the_audit_trail():
    agent = _agent("household_size", 27)
    agent.turn("27")
    events = [entry.get("event") for entry in agent._audit]
    assert "intake_value_out_of_range" in events


def test_the_explanation_does_not_leak_into_a_later_unrelated_turn():
    """The message belongs to the turn that caused it."""
    agent = _agent("household_size", 27)
    agent.turn("27")
    agent.llm_client = _ModelRecords("household_size", 3)
    second = agent.turn("3")
    assert str(CAP) not in second, second
    assert agent.state.household_size == 3


def test_valid_values_are_completely_unaffected():
    for value in (1, 2, CAP):
        agent = _agent("household_size", value)
        reply = agent.turn(str(value))
        assert agent.state.household_size == value
        assert "only goes up to" not in reply, reply


def test_spanish_explanation_is_rendered_in_spanish():
    agent = _agent("household_size", 27)
    agent.state.language = "es"
    reply = agent.turn("27")
    assert "27" in reply
    assert str(CAP) in reply
    # No English boilerplate leaking into a Spanish conversation.
    assert "This screen only goes up to" not in reply, reply
