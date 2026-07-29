"""The income-limit validator must flag limits, not every dollar sign.

Live, 2026-07-29. The user asked a real question:

    "if i lost my job, what does my income show as? or is it last year or
     last month for the pay? what time frame?"

The model answered it well:

    "SNAP looks at your household's current gross monthly income — what
     you expect to bring in this month and going forward. If you just lost
     your job, you'd use $0 right now, or whatever unemployment or other
     income you're actually receiving this month."

``check_income_limits`` rejected the whole reply because ``$0`` is not one
of the three values in ``income_limits(4)``, and the user got a bare
re-ask of the question they had just asked around. They rephrased. It
happened again.

The validator's own docstring already described the correct behaviour --
"the validator only checks against income *limits*, not against the user's
stated income" -- but the implementation flagged every dollar amount that
was not a limit, with a couple of narrow exceptions. Any explanatory
figure ("you'd use $0", "rent of $800 could be deducted") destroyed the
reply.

The protection that matters is unchanged: a fabricated *threshold* is
still caught. The asymmetry is deliberate — a missed fabrication costs a
wrong number in chat prose, while the verdict itself is code-templated
from the calculator and cannot be influenced by the model; a false
positive costs the entire answer.
"""

from __future__ import annotations

import pytest

from src.calculator import income_limits
from src.validators import check_income_limits

HOUSEHOLD = 4
LIMITS = income_limits(HOUSEHOLD)


# Explanatory or echoed amounts. None of these asserts a threshold.
NOT_LIMIT_CLAIMS = [
    "If you just lost your job, you'd use $0 right now, or whatever "
    "unemployment or other income you're actually receiving this month.",
    "Got it — $1,000 per month.",
    "For example, rent of $800 a month could be deducted.",
    "You said $2,500 before, and $300 now. Which one is current?",
    "Childcare of $450 and medical bills of $120 can both be subtracted.",
    "So that's $1,600 coming in each month — thanks.",
]

# Threshold assertions with a number the calculator never produced.
FABRICATED_LIMITS = [
    "The income limit for a household of 4 is $9,999.",
    "You qualify if you make under $8,888 a month.",
    "The cutoff is $1,234.",
    "Your income must be below $7,777 to be eligible.",
    "The maximum for your household size is $5,555.",
]


@pytest.mark.parametrize("reply", NOT_LIMIT_CLAIMS)
def test_explanatory_amounts_are_not_flagged(reply):
    assert check_income_limits(reply, household_size=HOUSEHOLD) == [], reply


@pytest.mark.parametrize("reply", FABRICATED_LIMITS)
def test_fabricated_thresholds_are_still_caught(reply):
    assert check_income_limits(reply, household_size=HOUSEHOLD), reply


@pytest.mark.parametrize("track", ["gross", "net", "expanded"])
def test_the_real_limits_pass(track):
    reply = f"The limit for a household of {HOUSEHOLD} is ${LIMITS[track]:,.2f}."
    assert check_income_limits(reply, household_size=HOUSEHOLD) == [], reply


def test_zero_is_a_legitimate_answer_and_never_a_fabricated_limit():
    """"$0" is what a person who just lost their job actually has, and it
    is the single figure most likely to appear in a helpful answer."""
    reply = "If you have no income at all right now, that's $0 — just tell me that."
    assert check_income_limits(reply, household_size=HOUSEHOLD) == []


def test_unknown_household_size_still_passes_everything():
    assert check_income_limits("anything $1,234", household_size=None) == []


def test_a_fabricated_limit_is_caught_even_next_to_an_explanatory_amount():
    """The scoping must not become a blanket exemption."""
    reply = (
        "If you just lost your job you'd use $0 right now. "
        "The income limit for a household of 4 is $9,999."
    )
    failures = check_income_limits(reply, household_size=HOUSEHOLD)
    assert failures, reply
    assert "9,999" in failures[0].detail
    assert "$0" not in failures[0].detail


def test_end_to_end_a_good_answer_survives_the_validator():
    """The reply the user should have received on the failing turn."""
    from src.validators import check_all

    reply = (
        "Good question. SNAP looks at your current gross monthly income — "
        "what you expect this month and going forward. If you just lost your "
        "job, you'd use $0 right now, or whatever unemployment you're "
        "receiving. So what is your household's gross monthly income?"
    )
    assert check_all(
        reply, has_eligibility_result=False, household_size=HOUSEHOLD
    ) == []


def test_single_digit_household_sizes_are_searchable():
    """The retriever's tokenizer dropped every one-character token, so a
    query naming a single-digit household size carried no token for the
    number and could not rank that table row.

    Single-character LETTERS remain noise and stay dropped; single-digit
    NUMBERS are the most common values in this domain.
    """
    from src.retrieval import _tokenize

    assert "2" in _tokenize("household size 2")
    assert "3" in _tokenize("a household of 3 people")
    # Single letters are still dropped.
    assert "a" not in _tokenize("a household")
    assert "i" not in _tokenize("i have 4 kids")
    assert "4" in _tokenize("i have 4 kids")


def test_retrieval_still_routes_questions_to_the_right_document():
    """The tokenizer change must not disturb ordinary routing."""
    from src.retrieval import retrieve

    expected = {
        "how do i apply for FNS": "04_nonfinancial_and_apply.md",
        "what is an EBT card": "01_program_overview.md",
        "elderly disabled net income deduction": "03_deductions_and_household.md",
        "income limit for a household of 3": "02_income_limits.md",
    }
    for query, doc in expected.items():
        hits = retrieve(query, k=3)
        assert hits, query
        assert hits[0]["doc"] == doc, f"{query!r} -> {hits[0]['doc']}"
