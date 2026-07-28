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

__all__ = [
    "normalize_income",
    "classify_income_ambiguity",
    "income_clarification_question",
    "AMBIGUITY_HOURLY_NO_HOURS",
    "AMBIGUITY_DAILY_NO_DAYS",
    "AMBIGUITY_PERIOD_UNCLEAR_HIGH",
    "AMBIGUITY_PERIOD_UNCLEAR_LOW",
]


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

# W70: a money-shaped number specifically adjacent to an UNAMBIGUOUS
# income-bearing word ("income is 300", "I make 300", "I earn 300").
# When a message has MULTIPLE money-shaped numbers, _extract_money_number's
# plain "first match wins" behavior can grab the wrong one -- found live
# 2026-07-28: normalize_income("500 household members, income is 300")
# returned 500.0 (the household count, positionally first) instead of
# 300.0 (the number actually adjacent to "income"). This pattern lets
# _extract_money_number prefer the income-adjacent number over a merely
# earlier one when both are present and differ.
#
# Deliberately EXCLUDES "pay"/"pays"/"paid": that verb is ambiguous
# between earning ("they pay me 1800") and an expense ("I pay 400 for
# daycare"), and preferring it here risks grabbing a deduction amount
# instead of income -- the same class of wrong-verdict bug this fix is
# meant to close, not open. Only words that mean income in every
# ordinary reading are included.
_INCOME_ADJACENT_MONEY_PATTERN = re.compile(
    r"\b(?:income|make|makes|earn|earns|earned|salary|salaries|wages?)\b"
    r"[^.\n]{0,20}?\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)",
    re.IGNORECASE,
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

# "a year", "per year", "yearly", "annually", "/yr", "annual salary".
# KB/02 ("Annual / post-tax / 'it varies'") is explicit: divide by 12.
# Without this the screen read "$42,000 a year" as $42,000 A MONTH and
# falsely denied an eligible household -- the worst failure this tool has.
_ANNUAL_HINT_PATTERN = re.compile(
    r"(?:per|a|each)\s*year|/year|/yr\b|\byr\b|\byearly\b|\bannually\b|\bannual\b|\bp\.?a\.?\b",
    re.IGNORECASE,
)

# "a day", "per day", "daily", "/day". Unlike weekly/annual there is NO
# published day->month conversion: it depends entirely on days worked, so
# we refuse to guess and ask, exactly like the hourly-without-hours path.
_DAILY_HINT_PATTERN = re.compile(
    r"(?:per|a|each)\s*day|/day|\bdaily\b",
    re.IGNORECASE,
)

# "5 days a week", "3 days per week", "6 days/wk"
_DAYS_PER_WEEK_PATTERN = re.compile(
    r"([\d.]+)\s*days?\s*(?:/|a\s+|per\s+|each\s+)\s*(?:week|wk)\b",
    re.IGNORECASE,
)

# ANY stated quantity of hours/days, in any shape -- "40 hours a week",
# "40hrs", "40 hrs". Used only to decide whether the message is missing
# the quantity outright, which is a lower bar than being able to parse it.
_HOURS_QUANTITY_PATTERN = re.compile(r"\d\s*(?:hours?|hrs?)\b", re.IGNORECASE)
_DAYS_QUANTITY_PATTERN = re.compile(r"\d\s*days?\b", re.IGNORECASE)

# k-shorthand: "$2.5k", "$2K", "2k", "1.2k"
_K_SCALE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE)

# m-shorthand: "$2m", "1.5M"
_M_SCALE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*m\b", re.IGNORECASE)

# W71: "N hundred" -- a very common casual way to state a monthly income
# ("about 22 hundred a month", "15 hundred"). Found live via the
# simulator's new LLM-driven improv persona (2026-07-28): "22 hundred a
# month" fell through to the plain-number branch, which grabbed the bare
# leading "22" and silently dropped the "hundred" multiplier entirely --
# $22/month instead of the stated $2,200/month. Mirrors the k-shorthand
# pattern above (multiply by a scale word) rather than the m-shorthand's
# single-letter suffix, since "hundred" is spelled out, not abbreviated.
_HUNDRED_SCALE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*hundred\b", re.IGNORECASE)


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
    # 1b. Daily phrasing.
    #     "$70 a day, 5 days a week" is convertible; "$70 a day" alone is
    #     not -- days worked per month is unknown and guessing 7 days a
    #     week (or 5) would silently invent income. Same refusal contract
    #     as hourly-without-hours above.
    # ------------------------------------------------------------------
    if _DAILY_HINT_PATTERN.search(lowered):
        daily_value = _amount_with_scale(lowered)
        days_match = _DAYS_PER_WEEK_PATTERN.search(lowered)
        if daily_value is not None and days_match:
            try:
                days_per_week = float(days_match.group(1))
            except ValueError:
                return None
            if 0 < days_per_week <= 7:
                return daily_value * days_per_week * _WEEKS_PER_MONTH
        return None

    # ------------------------------------------------------------------
    # 1c. Annual phrasing -> divide by 12 (KB/02, "Annual ... divide by 12").
    #     Checked BEFORE the k-shorthand branch so "70k a year" is
    #     70,000/12 rather than $70,000 a month.
    # ------------------------------------------------------------------
    if _ANNUAL_HINT_PATTERN.search(lowered):
        annual_value = _amount_with_scale(lowered)
        if annual_value is not None:
            return annual_value / 12.0
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
    # 4b. "N hundred" ("22 hundred a month", "15 hundred"). Same shape as
    #     the k-shorthand branch above -- a scale word after a leading
    #     number -- but spelled out rather than abbreviated, so it needs
    #     its own pattern rather than reusing _K_SCALE_PATTERN.
    # ------------------------------------------------------------------
    hundred_match = _HUNDRED_SCALE_PATTERN.search(lowered)
    if hundred_match:
        try:
            base = float(hundred_match.group(1))
        except ValueError:
            base = None
        if base is not None and base >= 0:
            return base * 100.0

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


def _amount_with_scale(text: str) -> Optional[float]:
    """Return the first money amount in *text*, honouring k/m shorthand.

    ``_extract_money_number`` alone reads "70k" as ``70``; the period
    branches (annual, daily) need "70k a year" to mean 70,000. Plain
    amounts fall through to ``_extract_money_number`` unchanged.
    """
    k_match = _K_SCALE_PATTERN.search(text)
    if k_match:
        try:
            base = float(k_match.group(1))
        except ValueError:
            base = None
        if base is not None and base >= 0:
            return base * 1000.0
    m_match = _M_SCALE_PATTERN.search(text)
    if m_match:
        try:
            base = float(m_match.group(1))
        except ValueError:
            base = None
        if base is not None and base >= 0:
            return base * 1_000_000.0
    return _extract_money_number(text)


# ---------------------------------------------------------------------------
# Ambiguity classification -- WHY a value could not be pinned down
# ---------------------------------------------------------------------------
#
# ``normalize_income`` returns a bare ``None`` for anything it refuses to
# guess at, which is the right safety contract but leaves the caller with
# only a generic "please repeat that" follow-up. These reason codes let the
# agent ask the SPECIFIC question that resolves the ambiguity, which is the
# difference between a screen a real person can finish and one they abandon.

AMBIGUITY_HOURLY_NO_HOURS = "hourly_no_hours"
AMBIGUITY_DAILY_NO_DAYS = "daily_no_days"
AMBIGUITY_PERIOD_UNCLEAR_HIGH = "period_unclear_high"
AMBIGUITY_PERIOD_UNCLEAR_LOW = "period_unclear_low"

# A bare number below this is far more likely a daily/weekly figure (or an
# hourly rate) than a real monthly income, so we ask instead of assuming.
# Zero is exempt: "0" is a legitimate, common answer meaning no income.
_IMPLAUSIBLY_LOW_MONTHLY = 100.0


def _implausibly_high_monthly() -> float:
    """The ceiling above which a bare number is probably an annual figure.

    Anchored to the calculator's own most generous monthly band (200% FPL
    at the largest household size) rather than a hand-picked constant, so
    the threshold cannot drift away from the published tables. Imported
    lazily to keep this module free of import-time coupling.
    """
    try:
        from ..calculator import income_limits, max_household_size

        return float(income_limits(max_household_size())["expanded"])
    except Exception:  # pragma: no cover - defensive; module must never raise
        return 20_000.0


def classify_income_ambiguity(
    text: object, *, value: Optional[float] = None
) -> Optional[str]:
    """Return WHY *text* is an ambiguous income answer, or ``None``.

    ``None`` means "not ambiguous" -- either it parsed cleanly or it was
    plain nonsense that deserves the ordinary re-ask. Only string input can
    be ambiguous: a number arriving through the ``update_state`` tool call
    has already been committed to as a monthly figure by the caller.

    ``value`` is the figure about to be written to state, when the caller
    has one. It matters because this function's job is to catch a *guess*,
    not to overrule a better reading of the message: "$15/hr 40hrs" states
    the hours in a shape the regexes here miss, and "2 of us, $1000/mo"
    leads with a household size rather than the income. In both cases an
    extractor upstream understood the message better than a naive re-parse
    would, so second-guessing it would throw away a correct answer.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    lowered = raw.casefold()

    # A rate with no quantity of time is unusable -- but only when the
    # message genuinely lacks the quantity, not when it merely phrased it
    # in a shape the parser above does not recognise.
    if _HOURLY_HINT_PATTERN.search(lowered) and not _HOURS_QUANTITY_PATTERN.search(lowered):
        return AMBIGUITY_HOURLY_NO_HOURS
    if _DAILY_HINT_PATTERN.search(lowered) and not _DAYS_QUANTITY_PATTERN.search(lowered):
        return AMBIGUITY_DAILY_NO_DAYS

    # A value that parsed, but whose PERIOD was never stated and whose
    # magnitude makes "per month" an implausible reading.
    if _explicit_period_stated(lowered):
        return None
    naive = normalize_income(raw)
    if naive is None or naive == 0:
        return None
    # Only question the figure when it IS the naive reading of the message.
    # A different value means something upstream parsed more than the first
    # number it saw, and that reading is the one to trust.
    if value is not None and abs(value - naive) > 0.01:
        return None
    if naive > _implausibly_high_monthly():
        return AMBIGUITY_PERIOD_UNCLEAR_HIGH
    if naive < _IMPLAUSIBLY_LOW_MONTHLY:
        return AMBIGUITY_PERIOD_UNCLEAR_LOW
    return None


def _explicit_period_stated(lowered: str) -> bool:
    """True when the user named a period, so we must not second-guess them."""
    return bool(
        _MONTHLY_HINT_PATTERN.search(lowered)
        or _WEEKLY_HINT_PATTERN.search(lowered)
        or _ANNUAL_HINT_PATTERN.search(lowered)
        or _DAILY_HINT_PATTERN.search(lowered)
        or _HOURLY_HINT_PATTERN.search(lowered)
    )


def income_clarification_question(
    reason: str, lang: str = "en", *, value: Optional[float] = None
) -> str:
    """Return the follow-up question that resolves *reason*.

    Code-templated, never model-written: a clarifying question that
    invented a number or a period would defeat the point of asking.
    """
    amount = ""
    if value is not None:
        amount = f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"

    if lang == "es":
        questions = {
            AMBIGUITY_HOURLY_NO_HOURS: (
                "Entendido. Para convertir un salario por hora a un total mensual, "
                "¿cuántas horas trabajas por semana normalmente?"
            ),
            AMBIGUITY_DAILY_NO_DAYS: (
                "Entendido. Para convertir un pago diario a un total mensual, "
                "¿cuántos días por semana trabajas normalmente?"
            ),
            AMBIGUITY_PERIOD_UNCLEAR_HIGH: (
                f"Solo para confirmar: {amount} ¿es por año o por mes? "
                "Necesito el total mensual bruto del hogar."
            ).strip(),
            AMBIGUITY_PERIOD_UNCLEAR_LOW: (
                f"Solo para confirmar: {amount} ¿es por mes, por semana o por día? "
                "Necesito el total mensual bruto del hogar."
            ).strip(),
        }
    else:
        questions = {
            AMBIGUITY_HOURLY_NO_HOURS: (
                "Got it. To turn an hourly rate into a monthly total, "
                "about how many hours a week do you usually work?"
            ),
            AMBIGUITY_DAILY_NO_DAYS: (
                "Got it. To turn a daily rate into a monthly total, "
                "about how many days a week do you usually work?"
            ),
            AMBIGUITY_PERIOD_UNCLEAR_HIGH: (
                f"Just to be sure I have this right — is {amount} per year or per month? "
                "I need the household's gross monthly total."
            ).strip(),
            AMBIGUITY_PERIOD_UNCLEAR_LOW: (
                f"Just to be sure I have this right — is {amount} per month, per week, or per day? "
                "I need the household's gross monthly total."
            ).strip(),
        }
    return questions.get(reason, "")


def _extract_money_number(text: str) -> Optional[float]:
    """Return the money-shaped number in *text* most likely to be income,
    or ``None``.

    Used for plain monthly and weekly phrasings. We don't reach here
    when the input contains a k- or m-shorthand (those are handled
    by their own branches) so we don't have to worry about a stray
    ``"2.5k"`` being misread as ``2.5``.

    W70: when a number is directly adjacent to an income-bearing word
    ("income is 300", "I make 300"), that number wins over a merely
    earlier one in the text -- otherwise a message like "500 household
    members, income is 300" would silently resolve to 500 (the
    positionally-first number) instead of the actually-stated 300.
    """
    # Strip the k- and m-shorthand patterns so _MONEY_NUMBER_PATTERN
    # doesn't accidentally bite into "2.5k" and return 2.5.
    scrubbed = _K_SCALE_PATTERN.sub(" ", text)
    scrubbed = _M_SCALE_PATTERN.sub(" ", scrubbed)

    income_adjacent = _INCOME_ADJACENT_MONEY_PATTERN.search(scrubbed)
    if income_adjacent:
        return _to_float(income_adjacent.group(1))

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
