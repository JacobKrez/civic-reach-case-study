"""W59 golden transcripts for social acknowledgements and process questions."""

from __future__ import annotations

import pytest

from src.guardrails.process import detect_process_question

from ._helpers import assert_universal_invariants, run_transcript, update


def test_social_acknowledgement_is_not_an_answer_but_still_advances():
    """A data-free acknowledgement must leave the next real field visible."""
    result = run_transcript(
        ["3", "thanks", "1800", "oh okay makes sense", "no one"],
        scripted_responses=[
            update(household_size=3),
            {"content": "", "tool_calls": []},
            update(gross_monthly_income=1800),
            {"content": "", "tool_calls": []},
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.final_state.missing_fields() == []
    assert_universal_invariants(result)


def test_privacy_question_is_answered_and_intake_continues():
    result = run_transcript(["do you save any of this?"])
    reply = result.last_assistant_reply.lower()
    assert "persistent" in reply
    assert "application" in reply
    assert "agency" in reply
    assert "how many people are in your household?" in reply
    assert result.calculator_calls == []
    assert_universal_invariants(result)


def test_application_question_is_answered_and_intake_continues():
    result = run_transcript(["is this the actual application?"])
    reply = result.last_assistant_reply.lower()
    assert "not" in reply and "application" in reply
    assert "does not submit" in reply
    assert "how many people are in your household?" in reply
    assert_universal_invariants(result)


def test_process_question_does_not_lock_a_completed_screen():
    result = run_transcript(
        ["3", "1800", "no", "are you going to share this with the county?"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.missing_fields() == []
    reply = result.last_assistant_reply.lower()
    assert "does not" in reply and "agency" in reply
    assert_universal_invariants(result)


@pytest.mark.parametrize(
    "question",
    [
        "why do you need that?",
        "how long does this take?",
        "is this secure?",
        "do you save any of this?",
        "is this the actual application?",
        "will this affect my case?",
        "do I have to answer that?",
        "what happens after this?",
        "can I do this later?",
        "are you going to share this with the county?",
        "what if I get it wrong?",
    ],
)
def test_all_named_meta_prompts_have_a_deterministic_route(question):
    category = detect_process_question(question)
    assert category is not None, question
    result = run_transcript([question])
    reply = result.last_assistant_reply.lower()
    assert reply.strip()
    assert "how many people are in your household?" in reply
    assert result.calculator_calls == []
    assert_universal_invariants(result)
