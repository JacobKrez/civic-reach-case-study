"""PII redaction helpers for logging and printing raw user input.

The agent must never log or print personally identifiable information
verbatim. This module provides a single function, ``redact_for_logging``,
that masks:

- SSN-shaped 9-digit patterns
- long numeric account identifiers (labeled, or bare 10+ digit runs)
- US phone numbers in common formats
- street-address-shaped patterns (number + street name + common suffix)
- email addresses

The detector is intentionally conservative: it errs on the side of
redacting when in doubt, because the only consumer of the redacted text
is logging / print statements where false positives cost nothing but
false negatives leak PII.

Deliberately NOT covered: free-form personal-name detection. That needs
NER/ML to be even remotely accurate and has a high false-positive rate
on ordinary English words — out of scope for a pattern-based guardrail.
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
    r"[\s\-:#]*(?:is\s+|are\s+)?"
    r"\d[\d\-\s]{6,}\d"
)
# A bare 10+ digit run (no label) is also redacted as a likely
# longer identifier (full account number, transaction ID, etc.).
_PURE_LONG_DIGIT_PATTERN = re.compile(r"\b\d{10,}\b")

# Email addresses. Standard pragmatic shape: local part, @, domain,
# dotted TLD. Matches anywhere in the text (no word-boundary anchoring
# needed because the shape is self-delimiting).
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# US phone numbers in common formats. All require a real area code
# (first digit 2-9) and an explicit separator or country code, which is
# what keeps this pattern from colliding with in-scope numbers:
#   * "(555) 123-4567", "(555)123-4567", "(555) 123 4567"
#   * "555-123-4567", "555 123 4567", "555.123.4567"
#   * "+1 555 123 4567", "1-555-123-4567", "1 (555) 123-4567"
#   * local 7-digit: "555-1234" (no area code, dash separator)
# A bare 10-digit run with NO separators ("5551234567") is deliberately
# not matched here — it is already caught as a 9+ digit identifier by
# the SSN/long-number rules above.
_PHONE_PATTERN = re.compile(
    r"(?:"
    # +1 / 1 prefix followed by parenthesized or separated area code
    r"(?<!\d)(?:\+?1[\s.\-])?\([2-9]\d{2}\)[\s.\-]?\d{3}[\s.\-]\d{4}(?!\d)"
    r"|(?<![\d+])(?:\+?1[\s.\-])[2-9]\d{2}[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
    # area code + separator + 3 + separator + 4 (separators may differ)
    r"|(?<!\d)[2-9]\d{2}[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
    # local 7-digit: ###-####
    r"|(?<!\d)\d{3}-\d{4}(?!\d)"
    r")"
)

# Street-address-shaped patterns: house number + street name + common
# suffix. The suffix requires a preceding letter boundary so partial
# words are not consumed, and the trailing period is a lookahead (not
# consumed) so a sentence following "St." is left intact.
_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr"
    r"|Lane|Ln|Way|Court|Ct|Place|Pl|Terrace|Circle|Cir)"
)
_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}[A-Za-z]?\s+[A-Za-z0-9.'\-]+(?:\s+[A-Za-z0-9.'\-]+)?\s+"
    + _STREET_SUFFIX
    + r"(?=\.?(?:\s|$))",
    re.IGNORECASE,
)


def redact_for_logging(text: str) -> str:
    """Return *text* with PII-shaped patterns masked.

    The replacement is the static string ``"[REDACTED]"`` so downstream
    log readers can grep for it. Masked: dashed/spaced/bare SSNs,
    "account"-prefixed numeric strings, bare 10+ digit runs, email
    addresses, US phone numbers in common formats, and
    street-address-shaped patterns (house number + street + suffix).

    The function is pure and side-effect free; it never raises on
    unusual input (returns an empty string on non-string input).
    """
    if not isinstance(text, str):
        return ""

    redacted = text
    # Order matters: phone numbers must be masked before the bare
    # 9-digit SSN rule, or a dashed phone's digit groups could be
    # partially consumed out of context.
    redacted = _EMAIL_PATTERN.sub("[REDACTED]", redacted)
    redacted = _PHONE_PATTERN.sub("[REDACTED]", redacted)
    redacted = _ADDRESS_PATTERN.sub("[REDACTED]", redacted)
    for pattern in _SSN_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = _LABEL_NUMBER_PATTERN.sub("[REDACTED]", redacted)
    redacted = _PURE_LONG_DIGIT_PATTERN.sub("[REDACTED]", redacted)
    return redacted


__all__ = ["redact_for_logging"]
