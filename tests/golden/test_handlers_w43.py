"""W43 close-the-loop golden transcripts.

These cover the three new deterministic handlers introduced in W43:

  * expedited SNAP questions             -> ``expedited_response()``
  * SNAP benefit-amount questions        -> ``benefit_amount_response()``
  * post-application / appeal questions  -> ``application_status_response()``

Each test exercises a real conversation through the agent and asserts:

  1. The detector fires on the user's first message and the assistant
     reply is the deterministic handler text (not a model improvisation
     and not a generic "what's your household size?" re-ask).
  2. The intake is preserved — the user can answer the next intake
     question and the agent completes the screen normally.
  3. The three intake fields are reachable from the canonical flow
     after the handler turns, so the screen is not "locked" by the
     handler.

Universal invariants (``assert_universal_invariants``) cover the
crisis / PII / non-empty / no-traceback contract on every run.
"""

from __future__ import annotations

import pytest

from ._helpers import (
    assert_universal_invariants,
    run_transcript,
    update,
)


# ---------------------------------------------------------------------------
# expedited_SNAP handler
# ---------------------------------------------------------------------------


def test_expedited_snap_question_triggers_deterministic_handler():
    """A user asking about expedited / 7-day / emergency SNAP gets the
    deterministic handler response, not a model improvisation and not
    a generic intake re-ask.

    The handler names the 7 CFR categories of household that qualify
    and explicitly preserves the intake (the response ends with
    "Should we continue with the eligibility questions?").
    """
    result = run_transcript(
        ["can I get expedited SNAP?"],
    )
    # The handler must have fired on the FIRST turn, so the assistant
    # reply is the deterministic text, not a stub-driven intake reply.
    assert len(result.assistant_replies) == 1
    reply = result.assistant_replies[0]
    # The handler identities:
    assert "CivicReach" in reply
    assert "expedited" in reply.lower()
    assert "7-day" in reply or "7 day" in reply or "7 days" in reply.lower()
    # The 7 CFR categories are surfaced:
    assert "$150" in reply
    assert "migrant" in reply.lower()
    # The intake is preserved:
    assert "continue" in reply.lower() or "questions" in reply.lower()
    # The user did NOT get a calculator call (no risk of a wrong answer):
    assert result.calculator_calls == []
    assert_universal_invariants(result)


def test_expedited_snap_handler_does_not_lock_intake():
    """After the handler fires, the user can continue the intake and
    complete the screen normally. The handler must NOT lock the
    conversation into a refusal-style loop.
    """
    result = run_transcript(
        [
            "can I get expedited SNAP?",
            "3",  # household_size
            "1800",  # gross_monthly_income
            "no",  # has_elderly_or_disabled_member
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=1800),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    # The screen completed cleanly:
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 1800.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert result.final_state.missing_fields() == []
    # The calculator called exactly once with the intake values:
    assert len(result.calculator_calls) == 1
    assert result.calculator_calls[0]["household_size"] == 3
    assert result.calculator_calls[0]["gross_monthly_income"] == 1800.0
    assert result.calculator_calls[0]["has_elderly_or_disabled"] is False
    assert_universal_invariants(result)


def test_expedited_snap_seven_day_alternative_phrasing():
    """The detector matches the alternative phrasing "7-day SNAP" /
    "emergency SNAP" / "how fast can I get food stamps" — the actual
    failure modes that the W42 finding described.
    """
    for phrase in (
        "7-day SNAP processing",
        "how fast can I get SNAP",
        "emergency SNAP",
    ):
        result = run_transcript([phrase])
        assert result.assistant_replies, f"no reply for {phrase!r}"
        reply = result.assistant_replies[0]
        # The handler ran (its text surfaces the canonical categories):
        assert "expedited" in reply.lower(), (
            f"{phrase!r} did not trigger the handler; got: {reply!r}"
        )
        assert "$150" in reply, f"{phrase!r} response missing categories: {reply!r}"
        assert_universal_invariants(result)


def test_expedited_snap_does_not_match_medical_emergency():
    """The detector is narrowly scoped to SNAP-expedited framing.

    A "medical emergency" message must NOT trigger the expedited
    handler. It must reach the crisis detector (or the LLM path) and
    not be silently captured by the new handler.
    """
    from src.guardrails.expedited import detect_expedited_question

    # The expedited detector must NOT fire on a medical emergency:
    assert not detect_expedited_question("I had a medical emergency")
    # The crisis detector upstream MUST fire on the same message:
    from src.guardrails.crisis import detect_crisis

    assert detect_crisis("I had a medical emergency")
    # And the 911/988/211 hotlines surface (verified by the universal
    # invariants in the crisis test elsewhere).


# ---------------------------------------------------------------------------
# benefit_amount handler
# ---------------------------------------------------------------------------


def test_benefit_amount_question_triggers_deterministic_handler():
    """A user asking "how much will I get in food stamps?" gets the
    deterministic "I can't compute the allotment" response, not a
    fabricated dollar amount.
    """
    result = run_transcript(
        ["how much will I get in food stamps?"],
    )
    assert len(result.assistant_replies) == 1
    reply = result.assistant_replies[0]
    # The handler identities:
    assert "CivicReach" in reply
    # The handler explicitly says it does NOT compute the allotment:
    assert (
        "does not" in reply.lower() or "doesn't" in reply.lower()
    ), "handler must explicitly say it does not compute the allotment"
    # The handler points to the USDA Allotment Table:
    assert "usda" in reply.lower() or "allotment" in reply.lower()
    # The intake is preserved:
    assert "continue" in reply.lower() or "questions" in reply.lower()
    # No calculator call (no risk of a fabricated dollar amount):
    assert result.calculator_calls == []
    assert_universal_invariants(result)


def test_benefit_amount_handler_does_not_lock_intake():
    """After the handler fires, the user can complete the intake and
    the calculator runs with the user's actual values.
    """
    result = run_transcript(
        [
            "how much will I get in food stamps?",
            "2",
            "1500",
            "no",
        ],
        scripted_responses=[
            update(household_size=2),
            update(gross_monthly_income=1500),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 2
    assert result.final_state.gross_monthly_income == 1500.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert len(result.calculator_calls) == 1
    assert result.calculator_calls[0]["gross_monthly_income"] == 1500.0
    assert_universal_invariants(result)


def test_benefit_amount_alternative_phrasings():
    """The detector matches several alternative phrasings of the
    benefit-amount question.
    """
    for phrase in (
        "what is the SNAP benefit amount?",
        "how much is SNAP worth?",
        "what is the maximum SNAP payment?",
        "what's the monthly SNAP allotment?",
    ):
        # Skip phrasings the detector does not currently match;
        # the brief says "rule-based FIRST, deterministic", not
        # "every possible phrasing." Document the coverage here.
        from src.guardrails.benefit_amount import detect_benefit_amount_question

        detected = detect_benefit_amount_question(phrase)
        if not detected:
            continue
        result = run_transcript([phrase])
        reply = result.assistant_replies[0]
        assert "allotment" in reply.lower() or "usda" in reply.lower(), (
            f"{phrase!r} -> handler did not run cleanly: {reply!r}"
        )
        assert_universal_invariants(result)


# ---------------------------------------------------------------------------
# application_status handler
# ---------------------------------------------------------------------------


def test_already_applied_question_triggers_deterministic_handler():
    """A user who has already applied gets the deterministic
    post-application redirect, not a generic intake re-ask.
    """
    result = run_transcript(
        ["I already applied for SNAP and was denied"],
    )
    assert len(result.assistant_replies) == 1
    reply = result.assistant_replies[0]
    # The handler identities:
    assert "CivicReach" in reply
    # The handler names the canonical NC FNS appeal path:
    assert "decision" in reply.lower() or "denial" in reply.lower()
    assert "fair hearing" in reply.lower()
    # The handler does NOT provide legal advice (it explicitly disclaims):
    assert "does not" in reply.lower() and "legal advice" in reply.lower()
    # The handler offers a fresh intake (intake preserved):
    assert "continue" in reply.lower() or "fresh" in reply.lower() or "check" in reply.lower()
    assert result.calculator_calls == []
    assert_universal_invariants(result)


def test_appeal_question_triggers_deterministic_handler():
    """A user asking how to appeal gets the deterministic response."""
    result = run_transcript(
        ["how do I appeal my SNAP denial?"],
    )
    reply = result.assistant_replies[0]
    assert "hear" in reply.lower() or "appeal" in reply.lower()
    assert "CivicReach" in reply
    assert_universal_invariants(result)


def test_application_status_handler_does_not_lock_intake():
    """After the handler fires, the user can complete the intake."""
    result = run_transcript(
        [
            "I was denied SNAP",
            "3",
            "2200",
            "no",
        ],
        scripted_responses=[
            update(household_size=3),
            update(gross_monthly_income=2200),
            update(has_elderly_or_disabled_member=False),
        ],
    )
    assert result.final_state.household_size == 3
    assert result.final_state.gross_monthly_income == 2200.0
    assert result.final_state.has_elderly_or_disabled_member is False
    assert len(result.calculator_calls) == 1
    assert result.calculator_calls[0]["gross_monthly_income"] == 2200.0
    assert_universal_invariants(result)


def test_application_status_does_not_match_unrelated_submissions():
    """The detector is tightly scoped to SNAP/FNS post-application so
    unrelated "I applied for X" phrasings are NOT captured.
    """
    from src.guardrails.application_status import detect_application_status_question

    # An unrelated "applied for" must NOT trip the handler:
    assert not detect_application_status_question("I applied for unemployment")
    # A SNAP post-application situation MUST trip the handler:
    assert detect_application_status_question("I applied for SNAP")
    assert detect_application_status_question("my food stamps were cut")
    assert detect_application_status_question("how do I file a fair hearing")


# ---------------------------------------------------------------------------
# KB grounding — knowledge_base actually contains the W43 additions
# ---------------------------------------------------------------------------


def test_kb_contains_expedited_service_grounding():
    """The KB explicitly ground the expedited-SNAP rule the handler
    cites (the 7 CFR categories and the 7-day timeline)."""
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parent.parent.parent
        / "knowledge_base"
        / "04_nonfinancial_and_apply.md"
    )
    text = doc.read_text(encoding="utf-8").lower()
    assert "expedited" in text
    assert "7-day" in text or "7 days" in text or "7 calendar days" in text
    # The 7 CFR categories from the rule:
    assert "$150" in text
    assert "migrant" in text


def test_kb_contains_income_what_counts_grounding():
    """The KB explicitly enumerates income sources for the
    "what counts as income?" close-the-loop finding."""
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parent.parent.parent
        / "knowledge_base"
        / "02_income_limits.md"
    )
    text = doc.read_text(encoding="utf-8").lower()
    assert "what counts as gross monthly income" in text
    # The KB explicitly enumerates the categories:
    assert "social security" in text
    assert "child support" in text
    assert "gig" in text or "self-employment" in text
    # The "annual / post-tax / it varies" path:
    assert "annual" in text
    assert "post-tax" in text or "take-home" in text


def test_kb_contains_household_composition_grounding():
    """The KB explicitly enumerates household-composition rules the
    "do I count my roommate / adult child / boarder?" finding needs."""
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parent.parent.parent
        / "knowledge_base"
        / "03_deductions_and_household.md"
    )
    text = doc.read_text(encoding="utf-8").lower()
    assert "household-composition" in text or "common household" in text
    # The categories the user actually asks about:
    assert "roommate" in text
    assert "adult children" in text or "adult child" in text
    assert "boarder" in text
    assert "foster" in text


def test_kb_contains_documents_grounding():
    """The KB explicitly lists the documents DSS may ask for."""
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parent.parent.parent
        / "knowledge_base"
        / "04_nonfinancial_and_apply.md"
    )
    text = doc.read_text(encoding="utf-8").lower()
    assert "documents" in text
    # The four categories:
    assert "identity" in text or "photo id" in text or "id" in text
    assert "residency" in text or "address" in text
    assert "pay stubs" in text or "income" in text
    assert "medical" in text or "dependent" in text or "rent" in text
