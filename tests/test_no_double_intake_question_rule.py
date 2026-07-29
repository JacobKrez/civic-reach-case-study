"""F1b regression coverage: the ``no_double_intake_question`` simulator
rule must catch the genuine double-question bug but MUST NOT flag a
single question that incidentally mentions a field name elsewhere in
the reply.

Live regression, 2026-07-28: the gate ran G5 simulator at 0.938
(300/320) after the uncommitted simulate.py changes added the new
``no_double_intake_question`` rule. All 20 failing transcripts were
the drifter persona's out-of-scope redirect, which legitimately
enumerates all three intake fields in one declarative sentence and
then has the W53 floor append the canonical next-field question. The
original rule counted raw marker occurrences; the field name appeared
twice in the reply (once in the enumeration, once in the appended
question), so the rule flagged it as a double-question. The
enumeration is intentional drifter behaviour, not a duplicate question.

The fix: count *question forms* (marker occurrences followed by '?'
within a short window), not raw mentions. The drifter reply's
enumeration is not a question form (no '?'), so the rule passes.

These tests pin both halves so the rule can't silently flip back to
the raw-count behavior on a future refactor.
"""

from __future__ import annotations

from scripts.simulate import (
    _check_no_double_intake_question,
    _count_question_forms,
)


# --- _count_question_forms primitive ---------------------------------------


def test_marker_immediately_followed_by_question_mark_counts_as_a_question_form():
    assert (
        _count_question_forms(
            "What is your gross monthly income?",
            ("gross monthly income",),
        )
        == 1
    )


def test_bare_mention_with_no_question_mark_does_not_count():
    assert (
        _count_question_forms(
            "I need household size, gross monthly income, and whether anyone is 60+.",
            ("gross monthly income",),
        )
        == 0
    )


def test_marker_followed_by_question_within_window_counts():
    # "gross monthly income" -> "and whether" -> "?" — long enough that the
    # marker is in a clause that ends with '?'.
    text = (
        "Can you tell me your gross monthly income and whether anyone in "
        "your household is 60 or older?"
    )
    assert (
        _count_question_forms(text, ("gross monthly income",))
        == 1
    )


def test_two_question_forms_for_the_same_field_are_detected():
    text = (
        "How many people are in your household? And how many people buy "
        "and prepare food together?"
    )
    assert (
        _count_question_forms(text, ("how many people",))
        == 2
    )


def test_marker_beyond_window_does_not_count():
    # Window is 60 chars AFTER the marker ENDS (idx + len(marker) + 60).
    # Marker "gross monthly income" (20 chars) ends at 221, window ends
    # at 281; place '?' at position 300 so it falls outside the window.
    text = "x" * 200 + " gross monthly income " + "y" * 100 + "?"
    assert (
        _count_question_forms(text, ("gross monthly income",))
        == 0
    )


# --- _check_no_double_intake_question end-to-end ---------------------------


def test_drifter_redirect_enumeration_is_not_a_double_question():
    """The live regression: drifter redirect enumerates the three fields,
    W53 floor appends the canonical next-field question, the field name
    appears twice but only ONE question is asked.
    """
    replies = [
        "Thanks — 2 people buy and prepare food together. What is your household's gross monthly income?",
        "I understand transportation can be hard — that's outside what FoodPath can help with. "
        "This initial screen only needs three pieces of information: household size, gross monthly "
        "income, and whether anyone in the household is 60+ or disabled. Let's get back to the "
        "eligibility questions. What is your household's gross monthly income?",
    ]
    passed, reason = _check_no_double_intake_question(replies, [False, False])
    assert passed, reason


def test_process_redirect_enumeration_is_not_a_double_question():
    """Same enumeration pattern, different guardrail (process.py "why" reply)."""
    replies = [
        "I only need three pieces of information for this initial screen: household size, gross "
        "monthly income, and whether anyone is 60 or older or disabled. I do not need a Social "
        "Security number, address, or phone number. What is your household's gross monthly income?",
    ]
    passed, reason = _check_no_double_intake_question(replies, [False])
    assert passed, reason


def test_actual_double_question_still_caught():
    """The bug the rule exists to catch: the model asks for the same field
    twice in one bubble (two paraphrased questions for the same field)."""
    replies = [
        "How many people live with you? And how many people total are in your household?",
    ]
    passed, reason = _check_no_double_intake_question(replies, [False])
    assert not passed
    assert "household_size" in reason


def test_guardrail_claimed_turn_is_skipped():
    """A guardrail-claimed turn (crisis / injection / out_of_scope) is
    skipped before the rule runs, because the canned block legitimately
    enumerates the fields."""
    replies = [
        # Out-of-scope drifter redirect enumerating all three fields,
        # twice (would be flagged if guardrail_claimed_history didn't skip).
        "household size, gross monthly income, and whether. household size, gross monthly income, "
        "and whether. household size, gross monthly income, and whether.",
    ]
    passed, reason = _check_no_double_intake_question(replies, [True])
    assert passed, reason
