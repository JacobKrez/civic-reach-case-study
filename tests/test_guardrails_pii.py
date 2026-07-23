"""PII guardrail tests.

The agent must:
- redact SSN-shaped 9-digit patterns (###-##-#### or 9 consecutive digits)
- redact any "full account number"-shaped long numeric run
- leave ordinary in-scope messages (mentioning income, household size, etc.)
  untouched
- never crash on weird input
"""

import pytest

from src.guardrails.pii import redact_for_logging


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("my ssn is 123-45-6789", "my ssn is [REDACTED]"),
        ("SSN: 987-65-4321.", "SSN: [REDACTED]."),
        ("  555-44-3333  ", "  [REDACTED]  "),
        ("call me at 123456789", "call me at [REDACTED]"),
        ("dod 900 65 4321 yes", "dod [REDACTED] yes"),
        ("my social is 123456789", "my social is [REDACTED]"),
    ],
)
def test_ssn_patterns_redacted(raw, expected):
    assert redact_for_logging(raw) == expected


def test_multiple_ssn_patterns_in_one_message_all_redacted():
    text = "first 123-45-6789 and second 987-65-4321 plus raw 123456789"
    out = redact_for_logging(text)
    assert "123-45-6789" not in out
    assert "987-65-4321" not in out
    assert "123456789" not in out
    assert out.count("[REDACTED]") == 3


def test_account_number_with_label_redacted():
    assert "12345678" not in redact_for_logging("my account number is 12345678")
    assert "1234567890" not in redact_for_logging("account: 1234567890")
    assert "1234567890" not in redact_for_logging("acct #1234567890")
    assert "1234567890" not in redact_for_logging("routing 1234567890")


def test_long_bare_digit_run_redacted():
    # 10+ consecutive digits -> redacted as a likely account number.
    out = redact_for_logging("phone ref 1234567890")
    assert "1234567890" not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize(
    "text",
    [
        "my household has 3 people",
        "We make $1,800 gross monthly.",
        "I have 2 kids and my grandmother is 67.",
        "We live in Charlotte, NC.",
        "Our zip code is 28202.",
        "I had a baby last month.",
        "I lost my job in June.",
        "We were evicted last year.",
        "Income is $0 right now.",
        "I get $300 a month in child support.",
    ],
)
def test_in_scope_messages_not_over_redacted(text):
    # Conversation text that looks like ordinary FNS intake speech must
    # round-trip through redact_for_logging unchanged. No SSN, no long
    # account numbers, no PII-shaped patterns in any of these.
    assert redact_for_logging(text) == text


def test_redact_handles_empty_and_non_string():
    assert redact_for_logging("") == ""
    assert redact_for_logging("   ") == "   "
    assert redact_for_logging(None) == ""
    assert redact_for_logging(123) == ""
    assert redact_for_logging([]) == ""


def test_redact_preserves_5_digit_zip():
    # 5-digit zip codes must NOT be redacted (they are not PII on their own
    # in this context and over-redacting zip codes would destroy legitimate
    # in-scope conversation text).
    text = "My zip is 28202 and I have 3 people in the household."
    assert redact_for_logging(text) == text


def test_redact_preserves_4_digit_year():
    text = "I was born in 1985 and have 2 kids."
    assert redact_for_logging(text) == text


def test_redact_preserves_short_dollar_amounts():
    text = "I make $1,800 a month and pay $400 in rent."
    assert redact_for_logging(text) == text


def test_redact_idempotent():
    # Running the redactor twice must produce the same result as running
    # it once (important: redacted output appears in logs and may be
    # re-processed).
    text = "my ssn is 123-45-6789 and account 1234567890"
    once = redact_for_logging(text)
    twice = redact_for_logging(once)
    assert once == twice
