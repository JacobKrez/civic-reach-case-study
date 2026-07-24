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


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("call me at 555-123-4567", "call me at [REDACTED]"),
        ("call me at (555) 123-4567", "call me at [REDACTED]"),
        ("call me at (555)123-4567", "call me at [REDACTED]"),
        ("call me at 555.123.4567", "call me at [REDACTED]"),
        ("call me at 555 123 4567", "call me at [REDACTED]"),
        ("call me at +1 555 123 4567", "call me at [REDACTED]"),
        ("call me at 1-555-123-4567", "call me at [REDACTED]"),
        ("call me at +1-555-123-4567", "call me at [REDACTED]"),
        ("call me at 1 (555) 123-4567", "call me at [REDACTED]"),
        ("local number 555-1234 please", "local number [REDACTED] please"),
        ("phone: (704) 555-0199.", "phone: [REDACTED]."),
    ],
)
def test_phone_patterns_redacted(raw, expected):
    assert redact_for_logging(raw) == expected


def test_bare_10_digit_run_still_redacted():
    # No separators: the phone pattern deliberately does not match a
    # bare 10-digit run, but the existing 9+ digit identifier rule
    # still catches it. Redaction is what matters for logging safety.
    out = redact_for_logging("call me at 5551234567")
    assert "5551234567" not in out
    assert "[REDACTED]" in out


def test_multiple_phones_in_one_message_all_redacted():
    text = "home 704-555-0199 or cell (980) 555-0123"
    out = redact_for_logging(text)
    assert "704-555-0199" not in out
    assert "(980) 555-0123" not in out
    assert out.count("[REDACTED]") == 2


@pytest.mark.parametrize(
    "text",
    [
        # 5-digit zip, 4-digit years, dollar amounts, household counts
        "My zip is 28202 and my phone interview is in 2026.",
        "I was born in 1985, my kid was born in 2015.",
        "I make $1,800 a month and pay $400 in rent.",
        "There are 3 of us; the baby is 6 months old.",
        # a "1"-prefixed run too short to be +1 + 10-digit phone is not a
        # phone (and below the 10-digit long-number threshold)
        "order 1555123 ships monday",
    ],
)
def test_phone_pattern_does_not_over_redact(text):
    assert redact_for_logging(text) == text


# ---------------------------------------------------------------------------
# Email addresses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("email me at jane.doe@example.com", "email me at [REDACTED]"),
        ("my email is jdoe+snap@gmail.com.", "my email is [REDACTED]."),
        ("reach me at j_doe-work@mail.co.uk", "reach me at [REDACTED]"),
        ("a@b.io", "[REDACTED]"),
        ("contact John.Smith@dhhs.nc.gov today", "contact [REDACTED] today"),
    ],
)
def test_email_patterns_redacted(raw, expected):
    assert redact_for_logging(raw) == expected


def test_email_and_phone_in_one_message_both_redacted():
    text = "email jane@example.com or call 704-555-0199"
    out = redact_for_logging(text)
    assert "jane@example.com" not in out
    assert "704-555-0199" not in out
    assert out.count("[REDACTED]") == 2


# ---------------------------------------------------------------------------
# Street addresses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("I live at 123 Main St", "I live at [REDACTED]"),
        ("I live at 123 Main St.", "I live at [REDACTED]."),
        ("moved to 456 Oak Ave last week", "moved to [REDACTED] last week"),
        ("my address is 789 Pine Rd", "my address is [REDACTED]"),
        ("I live at 1600 Market Blvd apt 2", "I live at [REDACTED] apt 2"),
        ("we were at 42 Elm Dr when it happened", "we were at [REDACTED] when it happened"),
        ("I live at 9 Cedar Ln", "I live at [REDACTED]"),
        ("meet me at 7 Stone Way", "meet me at [REDACTED]"),
        ("court is at 200 Trade Ct", "court is at [REDACTED]"),
        ("office at 500 College Street", "office at [REDACTED]"),
        ("office at 500 College Avenue", "office at [REDACTED]"),
        ("I live at 123B Main St", "I live at [REDACTED]"),
        # sentence following the address must survive intact
        ("I live at 123 Main St. I pay $400 rent.", "I live at [REDACTED]. I pay $400 rent."),
    ],
)
def test_address_patterns_redacted(raw, expected):
    assert redact_for_logging(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        # bare suffix words with no number+name shape are not addresses
        "We live in Charlotte, NC.",
        "Our zip code is 28202.",
        "I was born in 1985 and have 2 kids.",
        "We were evicted last year.",
        # no house number -> not address-shaped, must stay
        "the office on Main St is closed",
        "somewhere on Oak Ave near the park",
    ],
)
def test_address_pattern_does_not_over_redact(text):
    assert redact_for_logging(text) == text


def test_new_patterns_keep_idempotency():
    text = "call 555-123-4567, email jane@example.com, I live at 123 Main St"
    once = redact_for_logging(text)
    twice = redact_for_logging(once)
    assert once == twice
    assert "555-123-4567" not in once
    assert "jane@example.com" not in once
    assert "123 Main St" not in once
