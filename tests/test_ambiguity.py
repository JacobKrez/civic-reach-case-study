"""Tests for the income ambiguity normalizer.

The job of ``normalize_income`` is to coerce fuzzy phrasings the user
might actually type into a single monthly gross-income float. The
calculator downstream (C2) only accepts a float; if the agent pipes
``"$600/week"`` straight in, the user gets an eligibility decision
based on roughly 1/13th of their actual monthly income. So this
normalizer is the gatekeeper.

The tests below are deliberately table-driven: one parametrize block
covers the supported families (plain, k-shorthand, weekly, hourly
with hours, monthly hint), and a separate block proves the
``None``-on-unparseable contract. Acceptance target: at least 8
fuzzy-income parsing cases must pass.
"""

from __future__ import annotations

import math

import pytest

from src.guardrails.ambiguity import normalize_income


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Exact numeric expectations. We compare with math.isclose rather than
# ``==`` because the weekly/hourly paths multiply by 52/12 and float
# arithmetic can introduce tiny rounding noise.
def _close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return math.isclose(actual, expected, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# Supported phrasings — every row must round-trip to the expected float.
# ---------------------------------------------------------------------------

# Computed by hand so the test isn't accidentally self-confirming:
#   600/week * 52/12 = 2600.0
#   1500/week * 52/12 = 6500.0
#   15/hr * 40 hrs/week * 52/12 = 2600.0
#   20/hr * 35 hrs/week * 52/12 = 3033.3333...
#   2.5k monthly = 2500.0
#   1.2K monthly = 1200.0
#   $2,500 monthly = 2500.0
#   $2,500.00 monthly = 2500.0
_TABLE_DRIVEN_CASES = [
    # Plain numbers (treated as monthly).
    pytest.param("$2,500", 2500.0, id="plain_dollar_with_comma"),
    pytest.param("2500", 2500.0, id="plain_integer"),
    pytest.param("$2,500.00", 2500.0, id="plain_with_cents"),
    pytest.param("about $1,800", 1800.0, id="plain_with_filler"),
    pytest.param("I make $2,100/month before taxes", 2100.0, id="plain_with_monthly_hint"),
    # k-shorthand.
    pytest.param("about $2.5k", 2500.0, id="k_shorthand_decimal"),
    pytest.param("$2K", 2000.0, id="k_shorthand_clean"),
    pytest.param("1.2k", 1200.0, id="k_shorthand_no_dollar"),
    pytest.param("around $3K monthly", 3000.0, id="k_shorthand_with_hint"),
    # Weekly.
    pytest.param("$600/week", 600 * 52 / 12, id="weekly_slash"),
    pytest.param("$600 a week", 600 * 52 / 12, id="weekly_a_week"),
    pytest.param("I make $1,500 per week", 1500 * 52 / 12, id="weekly_per_week"),
    pytest.param("850/wk", 850 * 52 / 12, id="weekly_wk_abbrev"),
    # Hourly with hours (the only legal hourly phrasing).
    pytest.param("$15/hr, 40 hours a week", 15 * 40 * 52 / 12, id="hourly_with_hours"),
    pytest.param("20/hr for 35 hours per week", 20 * 35 * 52 / 12, id="hourly_with_hours_no_dollar"),
    pytest.param("$25 an hour, 40 hours a week", 25 * 40 * 52 / 12, id="hourly_an_hour"),
]


@pytest.mark.parametrize("raw,expected", _TABLE_DRIVEN_CASES)
def test_normalize_income_supported_phrasings(raw, expected):
    actual = normalize_income(raw)
    assert _close(actual, expected), (
        f"normalize_income({raw!r}) returned {actual!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Unparseable input contract — must return None, never raise, never guess.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "asdf",              # no money-shaped token at all
        "",                  # empty string
        "   ",               # whitespace only
        "hello world",       # text, no number
        "about my job",      # text without a number
        "$15/hr",            # hourly rate with NO hours-per-week -> refuse to guess
        "$15 hourly",        # hourly rate declared, no hours -> refuse to guess
        "depends",           # vague answer
        None,                # non-string input
        [],                  # non-string input
    ],
)
def test_normalize_income_returns_none_on_unparseable(raw):
    """Truly unparseable input must return None rather than raise or guess.

    This is the safety contract: when the user gives us something we
    cannot pin down to a monthly float, the agent must ask again
    instead of writing garbage into state. The normalizer's job is
    to refuse silently so the caller can decide what follow-up
    question to ask.
    """
    assert normalize_income(raw) is None


# ---------------------------------------------------------------------------
# Non-float numeric input is also accepted (the agent may pass a number
# directly through the tool call, so we shouldn't reject it).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        (2500, 2500.0),
        (2500.0, 2500.0),
        (0, 0.0),
        (0.0, 0.0),
        (1800.5, 1800.5),
    ],
)
def test_normalize_income_accepts_numeric_inputs(raw, expected):
    assert _close(normalize_income(raw), expected)


# ---------------------------------------------------------------------------
# Whitespace and capitalization tolerance.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  $2,500  ", 2500.0),
        ("$2,500\n", 2500.0),
        ("$2,500\tper month", 2500.0),
        ("$2,500 PER MONTH", 2500.0),
        ("$2,500 Monthly", 2500.0),
    ],
)
def test_normalize_income_handles_whitespace_and_case(raw, expected):
    assert _close(normalize_income(raw), expected)


# ---------------------------------------------------------------------------
# Total-function guarantee: the function NEVER raises on any input.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "$",
        "$/",
        "$$$",
        "$$$,$$$",
        "k",
        "$k",
        "$2k5",
        "12345678901234567890.1234567890",
        r"-$500",
        r"($1,000)",
        "twenty thousand dollars",
        r"!@#$%^&*()",
        "🇺🇸",
        "\x00\x01\x02",
    ],
)
def test_normalize_income_never_raises(raw):
    """Totality guarantee: any input, any state, the function returns."""
    try:
        result = normalize_income(raw)
    except Exception as exc:  # pragma: no cover - the test fails in this branch
        pytest.fail(f"normalize_income({raw!r}) raised {exc!r}")
    # We don't assert what the value IS — only that we got one back.
    # Some of these inputs legitimately parse to a number (e.g. "12345..."
    # without a dollar sign parses as a plain integer), so the function
    # is allowed to return a float here. It just must not raise.
    assert result is None or isinstance(result, float)
