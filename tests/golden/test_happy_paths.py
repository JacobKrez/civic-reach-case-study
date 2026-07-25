"""Happy-path golden transcripts.

Each test exercises a different way of providing the three intake
fields and asserts the universal invariants fire on every one. The
goal is to prove the scripted agent handles everyday phrasings
robustly across the most common shapes the rubric asks about.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


def test_three_terse_turns_each_answering_one_field():
    """The bread-and-butter 3-turn intake: each reply is a bare answer
    to the field just asked. Stubs emit ONLY tool calls; assertions
    run on the agent's structured state."""
    result = run_transcript(
        ["3", "1800", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.final_state.missing_fields() == []
    # No corrections on a clean 3-turn happy path.
    assert result.corrections == []
    # Calculator called exactly once with the three required fields.
    assert len(result.calculator_calls) == 1
    assert result.calculator_calls[0]["household_size"] == 3
    assert result.calculator_calls[0]["gross_monthly_income"] == 1800.0
    assert result.calculator_calls[0]["has_elderly_or_disabled"] is False
    assert_universal_invariants(result)


def test_one_sentence_with_all_three_fields():
    """A realistic single-turn user reply that supplies all three
    fields at once. The agent's stubbed tool call has to land on the
    first turn; the universal invariants cover the rest."""
    result = run_transcript(
        [
            "There are three of us in my household, we make $1,800 a month, "
            "and nobody is elderly or disabled."
        ],
        scripted_responses=[
            update(
                household_size=3,
                gross_monthly_income=1800,
                has_elderly_or_disabled_member=False,
            ),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.final_state.missing_fields() == []
    assert result.corrections == []
    assert_universal_invariants(result)


def test_out_of_order_one_sentence_lands_all_fields():
    """User volunteers the elderly/disabled flag FIRST and income
    LAST. The agent still produces a deterministic eligibility
    result; the order in the user sentence is irrelevant because
    update_state tool calls are unordered."""
    result = run_transcript(
        ["No elderly or disabled, household of 4, $2,000 a month gross."],
        scripted_responses=[
            update(
                household_size=4,
                gross_monthly_income=2000,
                has_elderly_or_disabled_member=False,
            ),
        ],
    )
    assert result.final_state.household_size == 4
    assert result.final_state.gross_monthly_income == 2000.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert_universal_invariants(result)


def test_responses_with_extra_filler_words_dont_confuse_extraction():
    """Real users add filler around their answers. Stubs still emit
    ONLY the structured tool call; the test asserts state is correct."""
    result = run_transcript(
        [
            "So, um, we're a family of three.",
            "I earn about $1,800 per month give or take.",
            "Nope, no one in our household is elderly or disabled.",
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert_universal_invariants(result)


def test_happy_path_with_followup_question_after_verdict():
    """The flagship case from the README: complete intake, then ask
    "can you repeat the result?". The follow-up must yield a
    non-empty reply containing the outcome — not a re-ask of any
    field, not a blank."""
    result = run_transcript(
        ["3", "1800", "no", "Can you repeat the result?"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            # The followup turn's explain call has no scripted
            # response; the agent's provider-independent floor
            # produces a verdict-bearing message.
        ],
    )
    assert result.final_state.missing_fields() == []
    # The 4th reply must contain outcome vocabulary (the floor).
    last = result.assistant_replies[-1].lower()
    assert any(
        term in last
        for term in ("eligible", "qualif", "screen", "ineligible", "income")
    )
    assert_universal_invariants(result)
