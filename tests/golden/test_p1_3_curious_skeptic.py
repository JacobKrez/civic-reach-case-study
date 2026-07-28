"""P1.3 tripwires — conversational realism + a chatty persona.

The P1 spec (CONVERSATION_OVERHAUL.md §3) requires a ``curious_skeptic``
persona to the simulator fixtures and a golden transcript asserting:
  - intake completes in <= 10 turns
  - zero "I appreciate you sharing that" style non-sequiturs
  - every reply ends with exactly one question until intake completes

These tripwires test the *behavioral contract* the new persona is
supposed to satisfy. The persona itself is added to
``scripts/simulate.py`` and is exercised by the simulator's
``golden_civic`` script (out of scope for this golden test, but
the persona must be importable and have the documented shape).

The golden transcript below drives a *stub* LLM that does not produce
"I appreciate you sharing that" non-sequiturs; the assertion is that
the P0.2 short identity response, the P0.3 steering hint, and the
P0.4 W53 fold together never produce the legacy non-sequitur at any
intake step. The persona's existence in ``PERSONAS`` is asserted
separately to keep this test independent of the simulator's runtime
state.

P1.1 contributions to this test (will land in a follow-up patch): the
new conversational system prompt will be exercised here too once
P1.1 lands. For now the P0.2-P0.4 changes are sufficient to clear
the tripwire.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


def test_curious_skeptic_intake_completes_in_ten_turns():
    """P1.3 tripwire: a chatty user (greets, asks who/what, questions
    legitimacy, drifts once, then completes intake) must finish the
    FNS assessment in <= 10 turns. The persona is documented in
    CONVERSATION_OVERHAUL.md §3 P1.3.

    Stub LLM echoes the next field's value so every assistant turn
    lands the intake progress; the P0.2-P0.4 changes ensure the
    agent never produces a canned non-sequitur on the chatty turns
    (the identity / steering / fold paths keep it conversational).
    """
    # 10 turns max. Every turn the model echoes the next missing
    # field. The transcript finishes at household + income + elderly.
    result = run_transcript(
        [
            "hi",                              # 1: greet
            "who are you",                     # 2: identity -> short answer
            "what is this for",                # 3: still meta -> short answer
            "is this a real caseworker?",       # 4: not_a_person -> full block
            "ok 3 people",                     # 5: fill household
            "$1500 a month",                   # 6: fill income
            "no elderly",                      # 7: fill elderly/disabled
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    # Intake completed in 7 turns (<= 10, tripwire satisfied).
    assert result.completed is True, (
        f"intake did not complete; final state: {result.final_state.model_dump()}"
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1500.0
    assert result.final_state.has_elderly_or_disabled_member is False
    # No legacy non-sequitur anywhere in the assistant replies.
    for i, reply in enumerate(result.assistant_replies):
        assert "I appreciate you sharing that" not in reply, (
            f"turn {i+1} reply contains the legacy non-sequitur: {reply!r}"
        )
    assert_universal_invariants(result)


def test_curious_skeptic_identity_questions_never_trigger_canned_redirect():
    """P1.3 tripwire: every identity-class question the chatty
    persona asks must be caught by BOTH detectors (the strict
    identity subset for routing, the broader
    detect_not_a_person_request for safety-net fallback). And
    every legitimacy-class question (broader, non-identity) must
    be caught by the broader detector so it cannot fall through to
    the classifier either."""
    from src.guardrails.scope import (
        detect_identity_question,
        detect_not_a_person_request,
    )
    # Identity-class (P0.2 short response): all must hit BOTH.
    identity_questions = [
        "who are you",
        "what is this",
        "what is foodpath",
        "how does this work",
        "what do you do",
    ]
    for q in identity_questions:
        assert detect_identity_question(q) is True, f"identity miss: {q!r}"
        assert detect_not_a_person_request(q) is True, (
            f"broader detector miss on identity {q!r} — identity questions "
            "must be caught by both, not just the strict subset"
        )
    # Legitimacy-class (not_a_person, full block): must hit the
    # broader detector, MUST NOT hit the identity strict subset
    # (so they get the full 4-channel block, not the short answer).
    legitimacy_questions = [
        "is this a real caseworker?",
        "is this 911?",
        "is this the ebt line?",
        "are you a real person?",
    ]
    for q in legitimacy_questions:
        assert detect_identity_question(q) is False, (
            f"legitimacy question wrongly hit identity strict subset: {q!r}"
        )
        assert detect_not_a_person_request(q) is True, (
            f"legitimacy question missed by broader detector: {q!r}"
        )


def test_curious_skeptic_drift_once_does_not_corrupt_state():
    """P1.3 tripwire: the chatty persona drifts ONCE (per the doc)
    on a non-eligibility aside. The drifter handler must acknowledge
    without losing collected state, and the W53 floor must append
    the next missing field's question."""
    result = run_transcript(
        [
            "hi",
            "ok 3 people",                     # fill household
            "my bus route just changed and it takes forever to get downtown",  # drift
            "$1500 a month",                   # fill income
            "no",                              # fill elderly/disabled
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    # State was not destroyed by the drift.
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1500.0
    assert result.final_state.has_elderly_or_disabled_member is False
    # Intake completed.
    assert result.completed is True
    # No legacy non-sequitur on the drift turn.
    drift_reply = result.assistant_replies[2]  # turn 3 is the drift
    assert "I appreciate you sharing that" not in drift_reply, drift_reply
    # The drifter handler returned a trigger-aware reply (transit
    # shape) — it should acknowledge the aside ("I understand
    # transportation can be hard" is the canned acknowledgement).
    assert "transportation" in drift_reply.lower() or "i understand" in drift_reply.lower(), (
        f"drift turn should acknowledge the transit aside: {drift_reply!r}"
    )
    assert_universal_invariants(result)


def test_curious_skeptic_persona_registered_in_simulator():
    """P1.3 tripwire: the curious_skeptic persona is registered in
    scripts/simulate.py's PERSONAS dict. The doc says it must be
    added; this test imports the simulator and asserts the entry
    exists and has the documented shape (a reply callable that
    accepts the standard 4-arg persona signature).

    The persona exercises the full P1.3 behavior contract: greets,
    asks who/what/why, questions legitimacy, drifts once, completes
    intake. The reply function is exercised by the simulator's
    nightly runs; here we just verify it is callable with the
    standard signature so the simulator can run it.
    """
    import importlib
    sim = importlib.import_module("scripts.simulate")
    assert "curious_skeptic" in sim.PERSONAS, (
        "P1.3: curious_skeptic persona must be registered in "
        f"scripts.simulate.PERSONAS; existing keys: {list(sim.PERSONAS.keys())}"
    )
    persona = sim.PERSONAS["curious_skeptic"]
    # The persona has the standard shape (name, description, reply callable).
    assert hasattr(persona, "name")
    assert persona.name == "curious_skeptic"
    assert hasattr(persona, "description")
    assert hasattr(persona, "reply")
    assert callable(persona.reply)
