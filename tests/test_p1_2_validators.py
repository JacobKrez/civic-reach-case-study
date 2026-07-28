"""P1.2 tripwires \u2014 post-LLM output validators.

The doc (CONVERSATION_OVERHAUL.md \u00a73 P1.2) requires three validators:
  1. No invented phone numbers / hotlines.
  2. No confident eligibility verdict language unless the calculator
     has produced a result.
  3. No fabricated income limits that don't match
     ``income_limits(household_size)`` for the current household.

These tripwires assert each validator independently. Wiring into
the agent (fall-back policy on failure) is a separate concern.
"""

from __future__ import annotations

import pytest

from src.calculator import income_limits
from src.guardrails.crisis import CRISIS_RESOURCES
from src.validators import (
    ValidationFailure,
    check_all,
    check_income_limits,
    check_phone_numbers,
    check_verdict_language,
)


# ---------------------------------------------------------------------------
# Validator 1: phone numbers
# ---------------------------------------------------------------------------


def test_p1_2_phone_validator_passes_for_known_crisis_numbers():
    """The crisis response includes 911, 988, 1-800-799-7233,
    1-866-3-HUNGRY, 211, 1-866-348-6479. None of these should trip
    the validator (they're in CRISIS_RESOURCES)."""
    known = (
        "If this is a life-threatening emergency, call 911 now. "
        "For suicidal thoughts, call or text 988. "
        "For domestic violence, call 1-800-799-7233. "
        "For USDA Hunger Hotline, call 1-866-3-HUNGRY. "
        "For 211, dial 211."
    )
    assert check_phone_numbers(known) == [], (
        f"crisis response should not trip the phone validator: {check_phone_numbers(known)!r}"
    )


def test_p1_2_phone_validator_catches_fabricated_hotline():
    """A model reply that invents a new phone number (a fabrication)
    must trip the validator. The doc calls this a class of hallucination
    that the user must never see."""
    reply = "Call our support line at 1-555-867-5309 for help with your case."
    failures = check_phone_numbers(reply)
    assert len(failures) == 1, failures
    assert failures[0].kind == "phone"
    assert "1-555-867-5309" in failures[0].detail


def test_p1_2_phone_validator_catches_invented_local_office():
    """Another fabrication shape: a model-invented county office
    number. This is exactly the failure mode the doc is worried about
    (a model confidently quoting a number that doesn't exist)."""
    reply = "Your local DSS office can be reached at 555-123-4567."
    failures = check_phone_numbers(reply)
    assert len(failures) == 1, failures
    assert failures[0].kind == "phone"


def test_p1_2_phone_validator_passes_for_no_numbers():
    """A reply with no phone-shaped strings at all should pass."""
    assert check_phone_numbers("Your household size is 3. What is your income?") == []


# ---------------------------------------------------------------------------
# Validator 2: verdict language
# ---------------------------------------------------------------------------


def test_p1_2_verdict_validator_catches_pre_intake_verdict():
    """The most dangerous failure mode: the model declares the user
    eligible BEFORE the calculator has run. This is exactly the
    'no verdicts without the calculator' invariant the doc calls out."""
    reply = "Based on what you told me, you qualify for FNS."
    failures = check_verdict_language(reply, has_eligibility_result=False)
    assert len(failures) == 1, failures
    assert failures[0].kind == "verdict"


def test_p1_2_verdict_validator_allows_post_intake_verdict():
    """After the calculator has run, the model can state the verdict.
    The check is conditioned on has_eligibility_result."""
    reply = "Based on the calculator, you likely qualify for FNS."
    assert check_verdict_language(reply, has_eligibility_result=True) == []


def test_p1_2_verdict_validator_allows_hedges_pre_intake():
    """Hedges ('you may qualify', 'we'll see') are allowed pre-intake
    because they're not confident assertions. Only confident
    assertions trigger the validator."""
    hedges = (
        "You may qualify \u2014 let me confirm by asking a few more questions.",
        "We'll see if you qualify once I have a bit more information.",
        "I can't say yet whether you'll qualify.",
    )
    for reply in hedges:
        assert check_verdict_language(reply, has_eligibility_result=False) == [], (
            f"hedge should be allowed: {reply!r}"
        )


def test_p1_2_verdict_validator_catches_negative_verdict():
    """Negative confident verdicts ('you don't qualify') are also
    fabrications when the calculator hasn't run."""
    reply = "Based on your household size, you don't qualify for FNS."
    failures = check_verdict_language(reply, has_eligibility_result=False)
    assert len(failures) == 1, failures
    assert failures[0].kind == "verdict"


# ---------------------------------------------------------------------------
# Validator 3: income limits
# ---------------------------------------------------------------------------


def test_p1_2_income_limits_validator_passes_for_calculator_values():
    """A reply that quotes a number from income_limits() must pass.
    The calculator's gross/net/expanded values for the current
    household size are the only allowed dollar amounts (other than
    the user's own income)."""
    household_size = 3
    limits = income_limits(household_size)
    for key in ("gross", "net", "expanded"):
        value = int(round(limits[key]))
        # Format with comma separator the way the model would
        formatted = f"${value:,}"
        reply = f"The limit for your household is {formatted} per month."
        failures = check_income_limits(reply, household_size=household_size)
        assert failures == [], (
            f"valid limit for household_size={household_size} ({key}={value}) should pass: "
            f"{failures!r}"
        )


def test_p1_2_income_limits_validator_catches_fabricated_value():
    """A model-invented income limit that does not match the calculator's
    output must trip the validator. The doc explicitly calls this out
    as the most user-harmful hallucination class (wrong dollar amounts
    that the user might act on)."""
    household_size = 3
    # $9,999/mo is not a real NC FNS limit for any household size.
    reply = "The income limit for your household is $9,999 per month."
    failures = check_income_limits(reply, household_size=household_size)
    assert len(failures) == 1, failures
    assert failures[0].kind == "income_limit"
    assert "$9,999" in failures[0].detail


def test_p1_2_income_limits_validator_passes_when_household_size_unknown():
    """If household_size isn't filled yet, the validator has no
    reference values to compare against. Pass."""
    reply = "I'll need your household size to check the income limit."
    assert check_income_limits(reply, household_size=None) == []


def test_p1_2_income_limits_validator_allows_user_income_echo():
    """The validator must NOT flag the user's own income value when
    it appears in the reply (the citations suffix or the ack
    echoes it). It only flags numbers that don't match ANY of the
    three limit tracks for the current household. The user's
    income is *not* a limit and shouldn't be checked against them.

    The validator uses a small list of "user-voice" markers
    ("your income is", "you told me", "you said your", etc.) to
    recognize echoes and skip them. The P1.2 design choice:
    validator suppression at the message level rather than the
    W62 stripper at the state level.
    """
    household_size = 1
    limits = income_limits(household_size)
    allowed = {int(round(limits["gross"])), int(round(limits["net"])), int(round(limits["expanded"]))}
    # Pick a value that is definitely not in the allowed set.
    user_income_candidates = [n for n in (100, 200, 500, 1000, 2500, 4000, 8000) if n not in allowed]
    assert user_income_candidates, "test setup: pick a value not in the limit set"
    user_income = user_income_candidates[0]
    reply = f"You told me your income is ${user_income:,} per month."
    # The validator suppresses user-income echoes: no failures.
    assert check_income_limits(reply, household_size=household_size) == []


# ---------------------------------------------------------------------------
# Composite: check_all
# ---------------------------------------------------------------------------


def test_p1_2_check_all_aggregates_failures():
    """The composite check_all returns the union of all three
    validators' failures. Empty list = pass."""
    # All three fail at once: a model reply that invents a phone
    # number, asserts a confident verdict pre-intake, and quotes
    # a fabricated income limit.
    reply = (
        "Based on your household, you qualify. "
        "Call our office at 1-555-867-5309. "
        "The income limit is $9,999."
    )
    failures = check_all(reply, has_eligibility_result=False, household_size=3)
    kinds = {f.kind for f in failures}
    assert "phone" in kinds, failures
    assert "verdict" in kinds, failures
    assert "income_limit" in kinds, failures


def test_p1_2_check_all_passes_for_clean_end_of_intake_explanation():
    """The end-of-intake reply (calculator has run, valid limit
    numbers, no invented phones) should pass all three validators."""
    household_size = 3
    limits = income_limits(household_size)
    valid_gross = int(round(limits["gross"]))
    reply = (
        f"Based on the calculator, your household of {household_size} with "
        f"this income is below the gross limit of ${valid_gross:,} per month. "
        f"This is an initial screen, not an official decision."
    )
    assert check_all(reply, has_eligibility_result=True, household_size=household_size) == []
