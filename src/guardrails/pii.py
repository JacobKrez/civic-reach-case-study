"""PII redaction helpers for logging and printing raw user input.

The agent must never log or print personally identifiable information
verbatim. This module provides a single function, ``redact_for_logging``,
that masks SSN-shaped 9-digit patterns and any long numeric account
identifier that looks like a full account number.

The detector is intentionally conservative: it errs on the side of
redacting when in doubt, because the only consumer of the redacted text
is logging / print statements where false positives cost nothing but
false negatives leak PII.
"""

from __future__ import annotations

import re

# SSN-shaped 9-digit patterns.
# 1. Three-three-four with dashes: 123-45-6789
# 2. Three-three-four with spaces: 123 45 6789
# 3. Nine consecutive digits (possibly with light separators).
_SSN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"),
    re.compile(r"\b\d{9}\b"),
)

# Long account-number patterns. We match an "account"-style keyword
# followed by 8+ digits, with optional filler words (is/are/:/-/#) in
# between. 8+ digits is the smallest realistic account-number length
# (a typical US bank account is 8-17 digits). 7-digit and shorter
# numbers are NOT redacted because they collide with phone numbers,
# quantities, dollar amounts, etc.
_LABEL_NUMBER_PATTERN = re.compile(
    r"(?i)(?:account|acct|routing)(?:\s+(?:number|no\.?|#))?"
    r"[\s\-\:#]*(?:is\s+|are\s+)?"
    r"\d[\d\-\s]{6,}\d"
)
# A bare 10+ digit run (no label) is also redacted as a likely
# longer identifier (full account number, transaction ID, etc.).
_PURE_LONG_DIGIT_PATTERN = re.compile(r"\b\d{10,}\b")


def redact_for_logging(text: str) -> str:
    """Return *text* with SSN-shaped and account-number patterns masked.

    The replacement is the static string ``"[REDACTED]"`` so downstream
    log readers can grep for it. Both dashed SSNs and bare 9-digit runs
    are caught, plus any "account"-prefixed numeric string and any bare
    10+ digit run.

    The function is pure and side-effect free; it never raises on
    unusual input (returns an empty string on non-string input).
    """
    if not isinstance(text, str):
        return ""

    redacted = text
    for pattern in _SSN_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = _LABEL_NUMBER_PATTERN.sub("[REDACTED]", redacted)
    redacted = _PURE_LONG_DIGIT_PATTERN.sub("[REDACTED]", redacted)
    return redacted


__all__ = ["redact_for_logging"]
