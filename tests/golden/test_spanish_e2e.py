"""Golden transcripts for a full Spanish end-to-end conversation.

The brief calls out a Spanish intake followed by a Terse English
'no' (or its Spanish equivalent) where the language mode must
STAY Spanish -- language stickiness is a hard contract (W19).
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


def test_full_spanish_intake_with_terse_no_afterward():
    """A complete Spanish intake followed by a Terse 'no' on a
    followup turn. The agent must stay in Spanish mode; the
    terse 'no' is correctly NOT interpreted as English."""
    result = run_transcript(
        [
            "somos tres en mi hogar",
            "ganamos 1500 al mes",
            "no",
            "no",
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1500),
            update(has_elderly_or_disabled_member=False),
            # Followup: terse "no" is correctly not a fresh
            # state-update; the agent renders the explain floor
            # in Spanish.
        ],
    )
    # Language mode is sticky once 'es' is detected.
    assert result.final_state.language == "es"
    # Intake completed.
    assert result.final_state.missing_fields() == []
    # Every assistant reply contains Spanish outcome vocabulary
    # (the universal invariant checks the Spanish outcome terms
    # table when language == 'es').
    assert_universal_invariants(result)
    # The last followup reply mentions the outcome in Spanish.
    last = result.assistant_replies[-1].lower()
    assert any(
        term in last
        for term in (
            "califica", "elegible", "elegibilidad", "evalu",
            "solicita", "ingreso", "limite", "no califica",
        )
    )
