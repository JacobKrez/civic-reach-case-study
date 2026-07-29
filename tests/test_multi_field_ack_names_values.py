"""A multi-field confirmation must name every value it recorded.

Live web probe, 2026-07-28: "its me and my two kids, and my mom stays
with us most of the time" landed BOTH ``household_size=4`` and
``has_elderly_or_disabled_member=True``, and the entire confirmation was:

    "I recorded those — details recorded."

Two problems, one cosmetic and one not:

* The rotation could pair the "I recorded those" opener with the
  "details recorded." tail, which restates itself.
* More seriously, the elderly/disabled flag was inferred from the phrase
  "my mom" -- her age was never stated -- and the user was never told it
  had been recorded. An invisible value cannot be corrected, and this one
  selects which income test the calculator applies. The enumerated-count
  confirmation already established the rule: state the value back so the
  user can veto it.

Both branches now render from the same per-field phrase helper, so the
single-field and multi-field wordings cannot drift apart.
"""

from __future__ import annotations

import pytest

from src.agent import _intake_progress_reply
from src.state import AssessmentState

MULTI = ["household_size", "has_elderly_or_disabled_member"]


def _state(**kwargs) -> AssessmentState:
    return AssessmentState(**kwargs)


@pytest.mark.parametrize("turn_count", [0, 1, 2, 3])
def test_multi_field_ack_names_both_values_on_every_rotation(turn_count):
    """The opener rotates; the substance must not depend on which one."""
    state = _state(
        household_size=4,
        has_elderly_or_disabled_member=True,
        turn_count=turn_count,
    )
    reply = _intake_progress_reply(state, MULTI, "en")
    assert "4 people" in reply, reply
    assert "60 or older" in reply, reply


def test_multi_field_ack_states_a_negative_elderly_value_too():
    state = _state(
        household_size=2, has_elderly_or_disabled_member=False, turn_count=0
    )
    reply = _intake_progress_reply(state, MULTI, "en")
    assert "no one is age 60 or older" in reply.lower(), reply


@pytest.mark.parametrize("turn_count", [0, 1, 2, 3])
def test_multi_field_ack_never_restates_itself(turn_count):
    """Regression pin on 'I recorded those — details recorded.'"""
    state = _state(
        household_size=4,
        has_elderly_or_disabled_member=True,
        turn_count=turn_count,
    )
    reply = _intake_progress_reply(state, MULTI, "en").lower()
    assert "details recorded" not in reply, reply
    assert "i recorded those" not in reply, reply


def test_sentences_after_the_first_are_capitalized():
    """The first phrase continues the opener's clause; later phrases
    start their own sentence."""
    state = _state(
        household_size=4, has_elderly_or_disabled_member=True, turn_count=0
    )
    reply = _intake_progress_reply(state, MULTI, "en")
    assert "together. Someone is" in reply, reply
    assert "together. someone is" not in reply, reply


def test_spanish_multi_field_ack_names_both_values():
    state = _state(
        household_size=4, has_elderly_or_disabled_member=False, turn_count=0
    )
    reply = _intake_progress_reply(state, MULTI, "es")
    assert "4 personas" in reply, reply
    assert "60 años" in reply, reply
    assert "registré esos datos" not in reply.lower(), reply


def test_single_and_multi_use_identical_wording_for_the_same_field():
    """The two branches share one phrase helper; if they ever diverge,
    the same value would be described two different ways in one
    conversation."""
    state = _state(
        household_size=4, has_elderly_or_disabled_member=True, turn_count=0
    )
    single = _intake_progress_reply(state, ["household_size"], "en")
    multi = _intake_progress_reply(state, MULTI, "en")
    assert "4 people buy and prepare food together." in single
    assert "4 people buy and prepare food together." in multi


def test_ack_still_ends_with_the_next_missing_field_question():
    state = _state(
        household_size=4, has_elderly_or_disabled_member=True, turn_count=0
    )
    reply = _intake_progress_reply(state, MULTI, "en")
    assert reply.rstrip().endswith("?"), reply
    assert "gross monthly income" in reply.lower(), reply
