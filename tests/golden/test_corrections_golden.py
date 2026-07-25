"""Golden transcripts for the correction contract.

A correction is a non-None -> DIFFERENT non-None change to one of
the three intake fields. The agent must:

* acknowledge the change with a deterministic, code-templated suffix
  (not model-generated, so the assertion is on the agent's
  structured state and the corrections trail, NOT on prose the stub
  authored);
* record it in an auditable corrections trail;
* NOT treat first-time provision of a field (None -> value) as a
  correction;
* invalidate a cached eligibility result so the next ``_calculate_once``
  recomputes against corrected state — i.e. mid-intake and
  post-verdict corrections both trigger a fresh DECIDE.

These tests cover all three intake fields, in both mid-intake and
post-verdict positions, plus a same-value-restated-is-not-a-correction
case to make sure the dedup is real.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    multi_update,
    run_transcript,
    update,
)


# ---------------------------------------------------------------------------
# Household-size corrections
# ---------------------------------------------------------------------------


def test_correction_household_size_mid_intake():
    """User provides household_size=2 then on turn 2 says "actually 5".
    The correction must be recorded, the suffix appended, and the
    calculator must see the corrected value when intake completes."""
    result = run_transcript(
        ["2 people", "actually 5", "1800", "no"],
        scripted_responses=[
            update(household_size=2),
            update(household_size=5),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 5
    assert result.corrections == [
        {"field": "household_size", "old": 2, "new": 5, "turn": 2}
    ]
    # The calculator was called with the corrected value.
    assert result.calculator_calls[-1]["household_size"] == 5
    assert_universal_invariants(result)


def test_correction_household_size_post_verdict():
    """User completes intake, then on a followup turn says "wait, we
    actually have 6 people". A cached eligibility result must be
    invalidated and a fresh DECIDE produced."""
    result = run_transcript(
        ["3", "1800", "no", "wait, actually there are 6 of us"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            update(household_size=6),
        ],
    )
    assert result.final_state.household_size == 6
    assert result.corrections == [
        {"field": "household_size", "old": 3, "new": 6, "turn": 4}
    ]
    # At least two calculator invocations: original 3-person + corrected
    # 6-person recompute.
    assert len(result.calculator_calls) >= 2
    assert result.calculator_calls[-1]["household_size"] == 6
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Income corrections
# ---------------------------------------------------------------------------


def test_correction_income_mid_intake():
    """User provides income, then on the NEXT turn supplies a
    different income (corrects the previous one). The correction is
    recorded; the next explain call uses the corrected value."""
    result = run_transcript(
        ["3", "$1800", "actually it's $2,500", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(gross_monthly_income=2500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.gross_monthly_income == 2500.0
    assert result.corrections == [
        {"field": "gross_monthly_income", "old": 1800.0, "new": 2500.0, "turn": 3}
    ]
    assert result.calculator_calls[-1]["gross_monthly_income"] == 2500.0
    assert_universal_invariants(result)


def test_correction_income_post_verdict():
    """Income correction AFTER a verdict is the most consequential
    case: the cached result must be invalidated and the calculator
    re-run with the corrected value."""
    result = run_transcript(
        ["3", "1800", "no", "actually I make $2,500 a month"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
            update(gross_monthly_income=2500),
        ],
    )
    assert result.final_state.gross_monthly_income == 2500.0
    assert result.corrections == [
        {"field": "gross_monthly_income", "old": 1800.0, "new": 2500.0, "turn": 4}
    ]
    assert len(result.calculator_calls) >= 2
    assert result.calculator_calls[-1]["gross_monthly_income"] == 2500.0
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Elderly/disabled corrections
# ---------------------------------------------------------------------------


def test_correction_elderly_disabled_mid_intake():
    """User initially says "yes" (household has elderly member) then
    on the next turn says "actually no". The False -> True flip
    counts as a correction. The calculator's track changes from net
    to gross and back."""
    result = run_transcript(
        ["3", "1800", "yes", "actually no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=True),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.corrections == [
        {
            "field": "has_elderly_or_disabled_member",
            "old": True,
            "new": False,
            "turn": 4,
        }
    ]
    assert_universal_invariants(result)


def test_correction_elderly_disabled_post_verdict():
    """Same correction but AFTER a verdict has been produced."""
    result = run_transcript(
        ["3", "1800", "yes", "wait actually, nobody is elderly or disabled"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=True),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.corrections == [
        {
            "field": "has_elderly_or_disabled_member",
            "old": True,
            "new": False,
            "turn": 4,
        }
    ]
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Same-value restated is NOT a correction
# ---------------------------------------------------------------------------


def test_same_value_restated_is_not_a_correction():
    """Repeating the same value (1800 -> 1800) must NOT register as
    a correction. The corrections list stays empty."""
    result = run_transcript(
        ["3", "$1,800", "yes, $1,800", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.corrections == []
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# Multi-field correction in one turn
# ---------------------------------------------------------------------------


def test_multiple_corrections_in_one_turn():
    """Two corrections land in the same turn via a single assistant
    message emitting two update_state calls. Both must be recorded."""
    result = run_transcript(
        [
            "3 people and $1800 a month",
            "wait, 5 of us and I make $2500",
            "no",
        ],
        scripted_responses=[
            multi_update(household_size=3, gross_monthly_income=1800),
            multi_update(household_size=5, gross_monthly_income=2500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 5
    assert result.final_state.gross_monthly_income == 2500.0
    # Two corrections, one per field, both on turn 2.
    assert len(result.corrections) == 2
    fields = sorted(c["field"] for c in result.corrections)
    assert fields == ["gross_monthly_income", "household_size"]
    for c in result.corrections:
        assert c["turn"] == 2
    assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# First-time fill is NOT a correction
# ---------------------------------------------------------------------------


def test_first_time_provision_is_not_a_correction():
    """Regression: the first turn that fills a field must NOT register
    as a correction (the user is providing the field, not changing it).
    This locks down the None -> value distinction."""
    result = run_transcript(
        ["3 people", "1800", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.corrections == []
    assert_universal_invariants(result)
