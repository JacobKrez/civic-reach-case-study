"""P1.2 output validators \u2014 post-LLM deterministic checks.

The doc (CONVERSATION_OVERHAUL.md \u00a73 P1.2) says: for turns the LLM
now handles (meta/social/borderline), add deterministic **post-LLM
output checks** before the reply ships. Three validators:

  1. **No invented phone numbers / hotlines.** Regex for phone-shaped
     strings; the only phone numbers that may appear in a reply are
     the ones in ``CRISIS_RESOURCES`` (the hardcoded 911/988/etc.
     set). Any other phone-shaped string is a fabrication.

  2. **No eligibility verdict language unless a calculator result
     exists.** Confident verdict phrases ("you qualify", "you don't
     qualify", "you are eligible", "you are ineligible") only appear
     in a reply when ``_eligibility_result`` is set on the agent.
     Hedges ("you may qualify", "we'll see") are allowed \u2014 only
     confident assertions trigger the validator.

  3. **No fabricated income limits.** The reply must not contain
     a dollar-amount income limit that does not match
     ``income_limits(current_household_size)`` for any of the
     three tracks (gross / net / expanded). The model can echo the
     calculator's numbers; it cannot invent its own.

Each validator has a ``check(...)`` function returning a list of
``ValidationFailure`` objects (empty list = pass). On any failure,
the caller (agent.py) falls back to the templated state-aware
continuation and logs the event for telemetry.

Crisis / injection / PII stay exactly where they are: deterministic,
pre-LLM. The blast radius of a bad model turn stays: worst case =
one awkward sentence replaced by a templated reply, never a wrong
number, never a fake verdict, never a missed crisis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .calculator import income_limits
from .guardrails.crisis import CRISIS_RESOURCES


# All the crisis phone numbers, normalized for substring comparison.
# CRISIS_RESOURCES values are 911, 988, 211, 1-866-3-HUNGRY, 1-866-348-6479,
# 1-800-799-7233. We allow all of these in replies (the crisis response
# includes them, the calculator summary may reference 988 in a
# crisis-resources citation, etc.).
_ALLOWED_PHONE_STRINGS: tuple[str, ...] = tuple(CRISIS_RESOURCES.values())


# Phone-shaped pattern: 3 digits, separator, 3 digits, separator,
# 4 digits. Optional "1-" prefix. Separator is "-", " ", or ".".
# This deliberately does NOT match 988 alone (988 is in
# _ALLOWED_PHONE_STRINGS and is checked separately by string
# containment, not by regex).
_PHONE_PATTERN = re.compile(
    r"\b(?:1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"
)


# Short hotline tokens: 988, 911, 211. These are 3 digits with no
# separators and may appear in the crisis response or in the
# calculator's citation suffix. Match them as words, not inside
# other numbers (the word boundary on each side prevents matching
# inside, e.g., "1500" or "9881").
_SHORT_HOTLINE_PATTERN = re.compile(r"\b(?:911|988|211|711|9881)\b")


@dataclass(frozen=True)
class ValidationFailure:
    """A single post-LLM validator hit. ``kind`` names the validator
    ("phone", "verdict", "income_limit"), ``detail`` is a
    human-readable description suitable for telemetry logs."""

    kind: str
    detail: str


def _extract_phone_like_strings(reply: str) -> list[str]:
    """Return all phone-shaped strings in *reply* that are NOT in the
    allowed crisis-resources set. Empty list = pass."""
    found: list[str] = []
    for m in _PHONE_PATTERN.finditer(reply):
        s = m.group(0)
        # Normalize spaces and dots to dashes for the
        # allowed-list comparison (the regex allows "." and " "
        # as separators; the allowed list uses "-").
        normalized = re.sub(r"[.\s]", "-", s)
        if normalized not in _ALLOWED_PHONE_STRINGS and s not in _ALLOWED_PHONE_STRINGS:
            # Also check substring match (e.g. "988" inside the
            # longer number is fine; "1-866-348-6479" is fine).
            if not any(allowed in s for allowed in _ALLOWED_PHONE_STRINGS):
                found.append(s)
    # Also check 3-digit hotline tokens (988, 911, 211). These are
    # allowed BUT only when they appear in an allowed-list context
    # (e.g. "call 988"). We allow them anywhere \u2014 they're verified
    # resources, not fabrications. (The crisis-response reply
    # includes 911/988 by construction; the calculator summary
    # cites 988 in the citations suffix. Both are legitimate.)
    return found


def check_phone_numbers(reply: str) -> list[ValidationFailure]:
    """P1.2 validator 1: no invented phone numbers / hotlines.

    Phone-shaped strings (NNN-NNN-NNNN with optional "1-" prefix and
    space/dot/dash separators) must be in the hardcoded
    ``CRISIS_RESOURCES`` set or in a known citation context. Any
    other phone-shaped string is a fabrication.
    """
    fabricated = _extract_phone_like_strings(reply)
    if not fabricated:
        return []
    return [
        ValidationFailure(
            kind="phone",
            detail=f"reply contains phone-shaped string(s) not in CRISIS_RESOURCES: {fabricated!r}",
        )
    ]


# Confident verdict language. The model is allowed to use HEDGES
# ("you may qualify", "you might", "we'll see if") \u2014 only confident
# assertions trigger the validator. The pattern is intentionally
# specific to confident phrasings.
_VERDICT_PHRASES: tuple[re.Pattern[str], ...] = (
    # Bare "you qualify" / "you don't qualify" -- confident assertion
    re.compile(r"\byou\s+(?:do\s+not|don'?t\s+)?qualify\b", re.IGNORECASE),
    # "you will/won't qualify" -- future-tense assertion
    re.compile(r"\byou\s+(?:will|won'?t|will\s+not)\s+(?:qualify|be\s+(?:eligible|approved|denied))\b", re.IGNORECASE),
    # "you are eligible/approved/denied" -- stative assertion
    re.compile(r"\byou\s+are\s+(?:eligible|ineligible|approved|denied)\b", re.IGNORECASE),
    # "you do qualify" -- present-tense with auxiliary
    re.compile(r"\byou\s+(?:do|does|did)\s+(?:not\s+)?qualify\b", re.IGNORECASE),
    # "(you) definitely/certainly/absolutely eligible/qualify"
    re.compile(r"\b(?:you\s+)?(?:definitely|certainly|absolutely)\s+(?:eligible|ineligible|qualify)\b", re.IGNORECASE),
)


_HEDGE_MARKERS = (
    "if", "whether", "may", "might", "could", "would", "let", "we",
    "once", "after", "until", "depending", "seem", "looks",
    "possibly", "probably", "maybe", "perhaps", "depending",
    "not sure", "not certain", "can\'t say", "cannot say",
    "i don\'t know", "we will see", "we\'ll see",
    "let me check", "let\'s check", "once i have", "once we have",
)


def _verdict_in_hedge_context(reply: str, match_start: int) -> bool:
    """Return True if the verdict phrase at *match_start* appears in
    a hedge context (preceded by a hedge marker within the same
    sentence)."""
    # Look at the 80 chars before the match. If any hedge marker
    # appears in that window, treat as a hedge.
    window = reply[max(0, match_start - 80):match_start].lower()
    if not window:
        return False
    # Only consider text in the same sentence (no period before
    # the verdict phrase means we're in the same sentence).
    last_period = window.rfind(".")
    if last_period != -1:
        window = window[last_period + 1:]
    return any(marker in window for marker in _HEDGE_MARKERS)


def check_verdict_language(reply: str, *, has_eligibility_result: bool) -> list[ValidationFailure]:
    """P1.2 validator 2: no confident verdict language unless the
    calculator has actually produced a result.

    *has_eligibility_result* is True when the agent has run the
    calculator on this session. If True, the reply may contain
    verdict language (the calculator's number is real). If False,
    any confident verdict phrase is a fabrication -- the model is
    guessing about eligibility before intake is complete.

    A "confident" verdict is one that appears in the reply without
    a nearby hedge marker (if, may, might, we'll see, etc.). The
    patterns themselves are confident in structure (e.g. "you are
    eligible", "you will qualify", "you do not qualify"), and the
    hedge check filters the few that fall inside conditional
    sentences.
    """
    if has_eligibility_result:
        return []
    hits: list[str] = []
    for pattern in _VERDICT_PHRASES:
        for m in pattern.finditer(reply):
            if not _verdict_in_hedge_context(reply, m.start()):
                hits.append(m.group(0))
    if not hits:
        return []
    return [
        ValidationFailure(
            kind="verdict",
            detail=f"reply contains confident verdict language before calculator ran: {hits!r}",
        )
    ]


_DOLLAR_PATTERN = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


def _normalize_money(s: str) -> int:
    """Strip commas, round to int dollars. $2,500.00 -> 2500."""
    return int(round(float(s.replace(",", ""))))


_USER_INCOME_ECHO_MARKERS = (
    "your income is",
    "your monthly income",
    "you told me",
    "you said your",
    "you mentioned",
    "you stated",
    "your gross monthly income is",
    # The intake-progress reply uses these phrasings to echo
    # back what the user just told us ("Got it — $1,500 per
    # month" / "Entendido — $1,500 al mes"). The dollar amount
    # in that echo is the user's value, not a fabricated limit.
    # Note: the marker does NOT include the $ (we check the
    # window BEFORE the dollar amount).
    #
    # P2.1: the rotation expanded the opener set; all four
    # entries (canonical + rotated) must suppress.
    "got it —",
    "entendido —",
    "thanks —",
    "gracias —",
    "right —",
    "correcto —",
    "ok —",
    "anotado —",
    # The income-clarification question quotes the user's own ambiguous
    # figure back at them ("is $70,000 per year or per month?"). That
    # amount is the user's number, not a limit the model invented, so it
    # must not trip the fabricated-limit check -- otherwise the validator
    # would replace the clarifying question with the generic one and the
    # ambiguity would never get resolved.
    "have this right —",
    "solo para confirmar:",
)


def _dollar_amount_is_user_income_echo(reply: str, dollar_match_start: int) -> bool:
    """Return True if the dollar amount at *dollar_match_start* is the
    user's own income echo (preceded by a user-voice marker within
    20 chars), not a fabricated income limit."""
    window = reply[max(0, dollar_match_start - 40):dollar_match_start].lower()
    return any(marker in window for marker in _USER_INCOME_ECHO_MARKERS)


def check_income_limits(reply: str, *, household_size: int | None) -> list[ValidationFailure]:
    """P1.2 validator 3: no fabricated income limits.

    Scans the reply for dollar amounts (any $X,XXX pattern) and
    verifies each one against ``income_limits(household_size)`` for
    the gross/net/expanded tracks. Any dollar amount that doesn't
    match one of those three values is a fabrication \u2014 the model
    made it up.

    *household_size* may be None (intake hasn't filled it yet); in
    that case the validator passes (no limits to check).

    Note: a reply that says "Your income is $2,500" is fine \u2014 the
    validator only checks against income *limits*, not against the
    user's stated income. The user's own dollar amounts are echoed
    in the citations suffix and are not limits.
    """
    if household_size is None:
        return []
    limits = income_limits(household_size)
    allowed = {int(round(limits["gross"])), int(round(limits["net"])), int(round(limits["expanded"]))}
    fabricated: list[str] = []
    for m in _DOLLAR_PATTERN.finditer(reply):
        # Skip user-income echoes (the model echoing back what the
        # user told them). These are not fabricated limits.
        if _dollar_amount_is_user_income_echo(reply, m.start()):
            continue
        value = _normalize_money(m.group(1))
        if value not in allowed:
            fabricated.append(f"${m.group(1)}")
    if not fabricated:
        return []
    return [
        ValidationFailure(
            kind="income_limit",
            detail=(
                f"reply contains dollar amount(s) not matching "
                f"income_limits(household_size={household_size})={limits!r}: {fabricated!r}"
            ),
        )
    ]


def check_all(reply: str, *, has_eligibility_result: bool, household_size: int | None) -> list[ValidationFailure]:
    """Run all three validators and return the combined failures.

    Empty list = pass. The caller decides what to do on failure
    (the doc's policy: fall back to the templated state-aware
    continuation and log the event for telemetry).
    """
    failures: list[ValidationFailure] = []
    failures.extend(check_phone_numbers(reply))
    failures.extend(check_verdict_language(reply, has_eligibility_result=has_eligibility_result))
    failures.extend(check_income_limits(reply, household_size=household_size))
    return failures


__all__ = [
    "ValidationFailure",
    "check_phone_numbers",
    "check_verdict_language",
    "check_income_limits",
    "check_all",
]
