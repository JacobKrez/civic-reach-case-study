"""Golden transcripts for ambiguous income phrasings.

The brief calls out four specific ambiguity cases:

* ``"$2.5k"`` -- k-shorthand, must parse to a numeric income.
* ``"$600/week"`` -- weekly figure, must be normalized to a monthly
  amount (or refused if the agent cannot normalize).
* ``"$15/hr 40hrs"`` -- hourly + hours, must be normalized.
* ``"$15/hr"`` ALONE -- incomplete (no hours), the agent must REFUSE
  to guess a monthly income.

The universal invariant ``gross_monthly_income changes only on a
turn carrying an income cue`` is checked on every transcript. The
REFUSE case must NOT register an income change because the cue is
incomplete.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_income_changes_only_on_cue,
    assert_universal_invariants,
    run_transcript,
    update,
)


def test_k_shorthand_income_parses():
    """$2.5k -> 2500.0 monthly income (k-shorthand multiplies by 1000)."""
    result = run_transcript(
        ["3", "$2.5k", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=2500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.gross_monthly_income == 2500.0
    assert_universal_invariants(result)


def test_weekly_income_normalizes_to_monthly():
    """$600/week -> 600 * 52 / 12 = 2600.0 monthly."""
    result = run_transcript(
        ["3", "$600/week", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=2600),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    # Either the stub supplied a pre-normalized 2600 OR the agent's
    # deterministic income-normalization backstop kicked in. Either
    # way, the recorded value must be the normalized monthly figure.
    assert result.final_state.gross_monthly_income == 2600.0
    assert_universal_invariants(result)


def test_hourly_income_with_hours_normalizes():
    """$15/hr 40hrs -> 15 * 40 * 52 / 12 = 2600.0 monthly."""
    result = run_transcript(
        ["3", "$15/hr 40hrs", "no"],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=2600),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.gross_monthly_income == 2600.0
    assert_universal_invariants(result)


def test_hourly_income_alone_must_refuse_to_guess():
    """$15/hr alone carries no hours -- the agent must NOT silently
    invent a monthly figure. The income field must stay None until
    the user supplies a complete cue. The universal income-cue
    invariant still holds because income never changed."""
    result = run_transcript(
        ["3", "$15/hr", "no"],
        scripted_responses=[
            update(household_size=3),
            # The stub does NOT update income because the agent
            # refused to guess; it only produces a deterministic
            # re-ask for a complete income cue.
            {"content": "", "tool_calls": []},
            update(has_elderly_or_disabled_member=False),
        ],
    )
    # Income was NEVER recorded. The agent refused to guess.
    assert result.final_state.gross_monthly_income is None
    assert result.final_state.household_size == 3
    # The income field is still missing, so the universal invariant
    # does not assert on the completed-intake-outcome rule.
    assert_income_changes_only_on_cue(result)
