"""Normalize fuzzy income phrasings into a monthly gross income float.

The eligibility calculator (C2) only accepts a single numeric
``gross_monthly_income`` float. In real conversation, users express
income in many different shapes:

    "I make about $2,500/month"
    "around $2.5k"
    "$600 a week"
    "$15/hr, 40 hours a week"
    "I get $2,100 monthly before taxes"

If the agent pipes a ``"$600/week"`` string straight into the calculator
the result is roughly 1/13th of the user's actual monthly income and
they get an incorrect eligibility decision. The job of this module is
to deterministically coerce those phrasings into a single monthly float
and to give up (return ``None``) when the input is too ambiguous to
parse without guessing.

Design constraints:

- Deterministic. No LLM calls, no I/O. Pure string-to-float.
- Best-effort, never raises. ``None`` on truly unparseable input.
- Conservative: when in doubt, return ``None`` rather than guess wildly.
- No state, no globals, no caching. The function is cheap to call.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["normalize_income"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Approximate weeks per month for converting weekly income to monthly.
# 52 / 12 = 4.3333...; a year is 52 weeks, a month is 1/12 of that.
_WEEKS_PER_MONTH = 52 / 12  # 4.3333...

# A "money-looking" number. Optionally $-prefixed, digits with optional
# thousands separators, optional decimal portion. We deliberately do NOT
# capture the trailing scale letter (k/m) here; that's handled separately.
_MONEY_NUMBER_PATTERN = re.compile(
    r"""
    \$?                       # optional dollar sign
    (?:\d{1,3}(?:,\d{3})+|    # 1,234,567 with thousands separators
       \d+(?:\.\d+)?)         # or 1234, 1234.56, 0.5
    """,
    re.VERBOSE,
)


def _to_float(token: str) -> Optional[float]:
    """Convert a matched money token like ``"$2,500"`` or ``"2.5"`` to a float.

    Returns ``None`` if the token cannot be parsed as a non-negative number.
    """
    cleaned = token.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


# ---------------------------------------------------------------------------
# Phrase detectors
# ---------------------------------------------------------------------------
#
# Each detector returns (value, scale) where scale is one of:
#   "monthly"  -> already a monthly figure, return as-is
#   "k"        -> value is in thousands, multiply by 1000 then treat as monthly
#   "weekly"   -> value is weekly, multiply by 52/12
#   "hourly_with_hours" -> value is hourly, need separate hours-per-week
#   "hourly_unknown"    -> value is hourly but hours are missing
# Returns None if the phrasing does not match that family.
#
# The detectors are evaluated in priority order inside normalize_income().
# ---------------------------------------------------------------------------

# "about $2,500", "around $2.5k", "I make $2,500", "$2,500/month", etc.
# We look for a money token optionally followed by k/m scale and a
# monthly indicator (or no period indicator at all, which defaults to monthly).
_MONTHLY_HINT_PATTERN = re.compile(
    r"(?:per|a|each)?\s*month(?:ly)?|/month|monthly",
    re.IGNORECASE,
)

# "weekly", "a week", "/wk", "per week", "per wk"
_WEEKLY_HINT_PATTERN = re.compile(
    r"(?:per|a|each)\s*week(?:ly)?|/week|/wk\b|\bwk\b",
    re.IGNORECASE,
)

# "hourly" (standalone), "an hour", "/hr", "per hour", "/h", bare "hourly"
_HOURLY_HINT_PATTERN = re.compile(
    r"(?:per|an|a|each)\s*hour(?:ly)?|/hour|/hr\b|/h\b|\bhr\b|\bhourly\b",
    re.IGNORECASE,
)

# k-shorthand: "$2.5k", "$2K", "2k", "1.2k"
_K_SCALE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE)

# m-shorthand: "$2m", "1.5M"
_M_SCALE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*m\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_income(text: str) -> Optional[float]:
    """Return a monthly gross income float for *text*, or ``None`` if unparseable.

    The function is total: it accepts any string (including ``""``, ``None``,
    whitespace, or nonsense) and never raises. When the input is too
    ambiguous to parse without guessing wildly (e.g. an hourly rate with
    no hours-per-week, or a string with no money-shaped tokens at all),
    it returns ``None`` so the caller can decide what to do.

    Supported phrasings:

    - Plain numbers (treated as monthly): ``"$2,500"``, ``"2500"``,
      ``"$2,500.00"``, ``"about $2,500"``, ``"I make $2,500/month"``.
    - k-shorthand: ``"about $2.5k"``, ``"$2K"``, ``"1.2k"`` all become
      ``2500.0`` / ``2000.0`` / ``1200.0``.
    - Weekly: ``"$600/week"``, ``"$600 a week"``, ``"600 per week"`` all
      become ``600 * 52/12 = 2600.0``.
    - Hourly with hours: ``"$15/hr, 40 hours a week"`` becomes
      ``15 * 40 * 52/12 = 2600.0``. The hours token may appear before
      or after the hourly rate.
    - Hourly without hours: ``"$15/hr"`` alone returns ``None`` —
      we won't guess the user's workweek.

    Unparseable input (``"asdf"``, ``""``, ``None``) returns ``None``.
    """
    if isinstance(text, bool):
        # bool is a subclass of int — guard against True/False being
        # accepted as a numeric income by mistake.
        return None
    if isinstance(text, (int, float)):
        value = float(text)
        if value < 0:
            return None
        return value

    if not isinstance(text, str):
        return None

    raw = text.strip()
    if not raw:
        return None

    # Work on a lowercased, normalized form for matching but keep the
    # original around for token extraction.
    lowered = raw.casefold()
    # Curly quotes and a couple of common Unicode variants -> ASCII.
    lowered = (
        lowered.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )

    # ------------------------------------------------------------------
    # 1. Hourly with hours-per-week.
    #    We need both an hourly rate AND a separate hours-per-week token.
    # ------------------------------------------------------------------
    if _HOURLY_HINT_PATTERN.search(lowered):
        hourly_rate = _extract_hours_or_rate(lowered, kind="rate")
        hours_per_week = _extract_hours_or_rate(lowered, kind="hours")
        if hourly_rate is not None and hours_per_week is not None:
            return hourly_rate * hours_per_week * _WEEKS_PER_MONTH
        # Hourly declared but no hours-per-week -> genuinely ambiguous.
        # Refuse to guess.
        return None

    # ------------------------------------------------------------------
    # 2. Weekly phrasing.
    # ------------------------------------------------------------------
    if _WEEKLY_HINT_PATTERN.search(lowered):
        weekly_value = _extract_money_number(lowered)
        if weekly_value is not None:
            return weekly_value * _WEEKS_PER_MONTH
        return None

    # ------------------------------------------------------------------
    # 3. k-shorthand ("$2.5k", "2k", "about $1.2k").
    #    Treated as monthly. We grab the FIRST k-shorthand match.
    # ------------------------------------------------------------------
    k_match = _K_SCALE_PATTERN.search(lowered)
    if k_match:
        try:
            base = float(k_match.group(1))
        except ValueError:
            base = None
        if base is not None and base >= 0:
            # If a numeric token appeared right before the k, strip it
            # so _extract_money_number doesn't double-count (it already
            # wouldn't match "$2.5k" as a $2 + 0.5, but be safe).
            return base * 1000.0

    # ------------------------------------------------------------------
    # 4. m-shorthand ("$1.5m" -> 1,500,000). Rare but cheap to support.
    # ------------------------------------------------------------------
    m_match = _M_SCALE_PATTERN.search(lowered)
    if m_match:
        try:
            base = float(m_match.group(1))
        except ValueError:
            base = None
        if base is not None and base >= 0:
            return base * 1_000_000.0

    # ------------------------------------------------------------------
    # 5. Plain monthly figure.
    #    Allow words like "about", "around", "I make", "/month", etc.
    # ------------------------------------------------------------------
    value = _extract_money_number(lowered)
    if value is not None:
        return value

    # ------------------------------------------------------------------
    # 6. Truly unparseable.
    # ------------------------------------------------------------------
    return None


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_money_number(text: str) -> Optional[float]:
    """Return the first money-shaped number in *text*, or ``None``.

    Used for plain monthly and weekly phrasings. We don't reach here
    when the input contains a k- or m-shorthand (those are handled
    by their own branches) so we don't have to worry about a stray
    ``"2.5k"`` being misread as ``2.5``.
    """
    # Strip the k- and m-shorthand patterns so _MONEY_NUMBER_PATTERN
    # doesn't accidentally bite into "2.5k" and return 2.5.
    scrubbed = _K_SCALE_PATTERN.sub(" ", text)
    scrubbed = _M_SCALE_PATTERN.sub(" ", scrubbed)

    match = _MONEY_NUMBER_PATTERN.search(scrubbed)
    if not match:
        return None
    return _to_float(match.group(0))


def _extract_hours_or_rate(text: str, kind: str) -> Optional[float]:
    """Extract an hourly rate or a hours-per-week value from *text*.

    ``kind="rate"`` -> find the number adjacent to ``/hr`` / ``per hour``
    / ``hourly`` / ``an hour``.

    ``kind="hours"`` -> find the number adjacent to ``hours a week`` /
    ``hours per week`` / ``hrs/wk`` / ``hrs a week``.

    The two may appear in either order in the user's input. We return
    ``None`` for whichever side is missing so the caller can refuse to
    guess.
    """
    if kind == "rate":
        # Try several phrasings, in order of specificity. The
        # ``(an|a)?`` connector handles the common "an hour" / "a
        # hour" phrasing between the digits and the unit, and the
        # ``hour(?:ly)?`` form catches both "hour" and "hourly"
        # phrasings (e.g. "$25 an hour", "$15 hourly").
        patterns = (
            r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*hr\b",
            r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*(?:an\s+|a\s+)?hour(?:ly)?\b",
            r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*h\b",
            r"([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*hr\b",
            r"([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*(?:an\s+|a\s+)?hour(?:ly)?\b",
            r"([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*h\b",
            r"([\d,]+(?:\.\d+)?)\s*(?:/|per\s+)?\s*hourly\b",
        )
    else:  # kind == "hours"
        patterns = (
            # "40 hours a week", "40 hours per week", "40 hours each week"
            r"([\d,]+(?:\.\d+)?)\s*hours?\s*(?:a|per|each)\s*week\b",
            # "40 hours/week", "40 hours/wk"
            r"([\d,]+(?:\.\d+)?)\s*hours?\s*(?:/|per\s+)?\s*week\b",
            # "40 hrs/wk", "40 hrs a week", "40 hours/wk"
            r"([\d,]+(?:\.\d+)?)\s*hrs?\s*(?:a|per|each)\s*wk\b",
            r"([\d,]+(?:\.\d+)?)\s*hrs?\s*(?:/|per\s+)?\s*wk\b",
            r"([\d,]+(?:\.\d+)?)\s*hours?\s*(?:/|per\s+)?\s*wk\b",
        )

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if value >= 0:
                return value
    return None
