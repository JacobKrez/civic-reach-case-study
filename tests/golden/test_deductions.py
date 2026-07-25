"""Golden transcripts for the four deduction fields the agent accepts
via ``update_state``: rent / shelter, dependent care, medical, and
earned-income (wages). Each deduction can be volunteered during
intake (before the three required fields are filled) or after a
verdict is on screen. The agent must accept the structured update,
the calculator must see the new value, and the universal invariants
must fire on every transcript.

The stub NEVER authors prose the assertion can latch onto -- every
scripted response is a tool call only. The test asserts on the
agent's ``AssessmentState`` and on the recorded calculator calls, so
the contract is checked from code-generated output.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


# ---------------------------------------------------------------------------
# Rent / shelter volunteered during intake
# ---------------------------------------------------------------------------


def test_rent_deduction_volunteered_during_intake():
    """User mentions rent on turn 2, between household-size and the
    elderly/disabled question. The agent must record
    shelter_costs_monthly; the calculator must see the value when
    intake later completes."""
    result = run_transcript(
        ["3", "1500 a month and rent is 800", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1500, shelter_costs_monthly=800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1500.0
    assert result.final_state.shelter_costs_monthly == 800.0
    assert result.calculator_calls[-1]["shelter_costs_monthly"] == 800.0
    assert_universal_invariants(result)


def test_rent_deduction_volunteered_post_verdict():
    """User completes intake, then on a followup turn says their rent
    is 950. The post-verdict update path must accept the structured
    update and route it to the calculator."""
    result = run_transcript(
        ["3", "1500", "no", "oh and rent is 950"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1500),
            update(has_elderly_or_disabled_member=False),
            update(shelter_costs_monthly=950),
        ],
    )
    assert result.final_state.shelter_costs_monthly == 950.0
    assert result.calculator_calls[-1]["shelter_costs_monthly"] == 950.0
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Childcare / dependent care
# ---------------------------------------------------------------------------


def test_childcare_deduction_volunteered_during_intake():
    result = run_transcript(
        ["3", "1800 and I pay 400 for daycare", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800, dependent_care_monthly=400),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.dependent_care_monthly == 400.0
    assert result.calculator_calls[-1]["dependent_care_monthly"] == 400.0
    assert_universal_invariants(result)


def test_childcare_deduction_volunteered_post_verdict():
    result = run_transcript(
        ["3", "1800", "no", "also I pay 400/mo for childcare"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            update(dependent_care_monthly=400),
        ],
    )
    assert result.final_state.dependent_care_monthly == 400.0
    assert result.calculator_calls[-1]["dependent_care_monthly"] == 400.0
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Medical (only deductible when elderly/disabled = True)
# ---------------------------------------------------------------------------


def test_medical_deduction_volunteered_with_elderly_member():
    """Medical expenses are only deductible when an elderly/disabled
    member is present. The user volunteers both across the
    intake -- household on turn 1, income + elderly + medical
    on turn 2."""
    result = run_transcript(
        ["2", "we make 1200 a month, my mom lives with us and has 200 in medical bills"],
        scripted_responses=[
            update(household_size=2),
            update(
                gross_monthly_income=1200,
                has_elderly_or_disabled_member=True,
                medical_expenses_monthly=200,
            ),
        ],
    )
    assert result.final_state.has_elderly_or_disabled_member is True
    assert result.final_state.medical_expenses_monthly == 200.0
    assert result.calculator_calls[-1]["medical_expenses_monthly"] == 200.0
    assert_universal_invariants(result)


def test_medical_deduction_volunteered_post_verdict_with_elderly():
    result = run_transcript(
        ["2", "1200", "yes, my mom lives with us", "medical bills are 200 a month"],
        scripted_responses=[
            update(household_size=2),
            update(gross_monthly_income=1200),
            update(has_elderly_or_disabled_member=True),
            update(medical_expenses_monthly=200),
        ],
    )
    assert result.final_state.medical_expenses_monthly == 200.0
    assert result.calculator_calls[-1]["medical_expenses_monthly"] == 200.0
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Wages / earned income (20% deduction under federal net-income chain)
# ---------------------------------------------------------------------------


def test_wages_deduction_volunteered_during_intake():
    result = run_transcript(
        ["2", "1800 gross, all wages", "no"],
        scripted_responses=[
            update(household_size=2),
            update(gross_monthly_income=1800, earned_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.earned_monthly_income == 1800.0
    assert result.calculator_calls[-1]["earned_monthly_income"] == 1800.0
    assert_universal_invariants(result)


def test_wages_deduction_volunteered_post_verdict():
    result = run_transcript(
        ["2", "1800", "no", "actually all of that is wages"],
        scripted_responses=[
            update(household_size=2),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            update(earned_monthly_income=1800),
        ],
    )
    assert result.final_state.earned_monthly_income == 1800.0
    assert result.calculator_calls[-1]["earned_monthly_income"] == 1800.0
    assert_universal_invariants(result)
