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
    "extract_two_figures",
    "resolve_two_figures_answer",
    "AMBIGUITY_HOURLY_NO_HOURS",
    "AMBIGUITY_DAILY_NO_DAYS",
    "AMBIGUITY_PERIOD_UNCLEAR_HIGH",
    "AMBIGUITY_PERIOD_UNCLEAR_LOW",
    "AMBIGUITY_TWO_FIGURES",
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
#
# ``\bweekly\b`` is a standalone alternative for the same reason the
# hourly/annual/daily patterns carry ``\bhourly\b`` / ``\byearly\b`` /
# ``\bdaily\b``: people write the bare adverb. Without it, "300 weekly"
# matched no period at all and was read as $300 A MONTH -- a quarter of
# the real figure, and silently a wrong verdict. ``biweekly`` is NOT
# matched by ``\bweekly\b`` (no word boundary before "weekly" inside it),
# which is correct: every-two-weeks is a different conversion and is
# handled as its own ambiguity rather than guessed at here.
_WEEKLY_HINT_PATTERN = re.compile(
    r"(?:per|a|each)\s*week(?:ly)?|/week|/wk\b|\bwk\b|\bweekly\b",
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
# F3 (2026-07-28): a single user message that states TWO income figures
# — typically "I was making $X, now I get $Y" (job loss, raise, second
# job, etc.). The current income could be either figure; we must ask
# which is the current monthly total instead of guessing or dropping
# the message. The probe message from §9.1: "i was making 2500 but
# now i just get unemployment, like 300 a week i think" — the
# brief's *named* ambiguous-income case.
AMBIGUITY_TWO_FIGURES = "two_figures"


# F3 detection: a past-tense income marker ("was making", "was getting",
# "used to make", "before") AND a present-tense income marker ("now",
# "currently", "get", "gets") within the same message. Conservative
# on purpose — we don't want to fire this for messages that just
# contain one figure, even if "now" appears incidentally ("now what's
# the next question").
_PAST_TENSE_INCOME_HINT = re.compile(
    r"\b(?:was\s+(?:making|getting|making|earning|bringing|doing)|"
    r"used\s+to\s+(?:make|get|earn|bring|do)|"
    r"before\b|previously|prior\b)",
    re.IGNORECASE,
)
_PRESENT_TENSE_INCOME_HINT = re.compile(
    r"\b(?:now\b|currently\b|get\s+|gets\b|getting\b|earning\b|bringing\b|"
    r"i\s+only\s+(?:get|make|earn)|"
    r"these\s+days|right\s+now)",
    re.IGNORECASE,
)

# A money-shaped token (permissive: with or without $ / commas / k
# suffix / decimal). Matches "$2500", "2500", "$2,500", "$1.5k",
# "300", "$600", etc.
_MONEY_TOKEN_PATTERN = re.compile(
    r"\$?\s*([\d,]+(?:\.\d+)?)(?:\s*k\b)?",
    re.IGNORECASE,
)


def _extract_two_figures(text: str) -> "tuple[float, float] | None":
    """If *text* names two distinct income-shaped numbers, return
    them as ``(first, second)`` in the order they appear in the
    message. Returns ``None`` if there aren't at least two distinct
    money-shaped numbers in *text*.

    Used by F3 to populate the two-figures clarification question.
    Single-figure messages and messages with one figure repeated
    ("2500 ... 2500 a month") are excluded: the second appearance
    is the same figure and doesn't represent a new income.

    The detection is permissive on purpose — false positives fall
    through to the existing single-figure branches; false negatives
    would surface the user's two figures without a question and
    silently produce the wrong verdict.
    """
    if not isinstance(text, str):
        return None
    seen: list[float] = []
    for match in _MONEY_TOKEN_PATTERN.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value <= 0:
            continue
        # Skip "100%" / "50%" / etc. — the surrounding context check
        # happens in classify_income_ambiguity; here we just collect.
        if value not in seen and (value, value) not in [(s, s) for s in seen]:
            # Actually we want distinct values: skip a repeat of an
            # already-seen value.
            if not any(abs(value - s) < 0.5 for s in seen):
                seen.append(value)
        if len(seen) >= 2:
            return (seen[0], seen[1])
    return None


def period_converted_income(text: str) -> Optional[float]:
    """Return the monthly income *text* states, but ONLY when it names a
    non-monthly pay period that requires a conversion.

    This is the deterministic reading of the user's own words, used to
    overrule a model that did the conversion arithmetic itself and got it
    wrong. Live probe, 2026-07-28: "i get 300 a week" produced a tool call
    of ``gross_monthly_income=1200`` (300 x 4) instead of 1300
    (300 x 52/12), and the agent confirmed "$1,200 per month" -- a figure
    the user never said, off by a full week's income, feeding a verdict.

    Returns ``None`` when the message states no period, states a monthly
    period, or cannot be resolved. In all those cases the model's value
    stands: this function's job is narrowly to catch mis-converted
    periods, not to second-guess every figure the model records. (A bare
    "1800", or "1800 and I pay 400 for daycare", must keep flowing
    through the model's reading -- picking a number out of a multi-number
    sentence is what the W61 role-bearing logic is for.)
    """
    if not isinstance(text, str) or not text.strip():
        return None
    lowered = text.casefold()
    converts = (
        _WEEKLY_HINT_PATTERN.search(lowered)
        or _ANNUAL_HINT_PATTERN.search(lowered)
        or _HOURLY_HINT_PATTERN.search(lowered)
        or _DAILY_HINT_PATTERN.search(lowered)
    )
    if not converts:
        return None
    # A message naming BOTH a monthly figure and another period is
    # ambiguous about which one the income is; leave it to the existing
    # ambiguity path rather than overriding on a guess.
    if _MONTHLY_HINT_PATTERN.search(lowered):
        return None
    return normalize_income(text)


def extract_two_figures(text: str) -> "tuple[float, float] | None":
    """Public wrapper over :func:`_extract_two_figures`.

    The agent needs the same two candidate figures the clarification
    question quoted back, so it can later match the user's answer
    against them. Exposed rather than duplicated so the question and
    the resolver can never disagree about what was offered.
    """
    return _extract_two_figures(text)


# Words that pick the EARLIER figure ("I was making $2,500") versus the
# LATER one ("now I just get $300 a week"). The two-figures question is
# always phrased "$X before, and $Y now", so "before"/"first"/"was" mean
# the first figure and "now"/"current"/"second" mean the second. Kept
# deliberately small: an answer that matches nothing here and names no
# number resolves NOTHING (see ``resolve_two_figures_answer``), which is
# the safe outcome -- a wrong income silently produces a wrong verdict.
_SELECT_FIRST_PATTERN = re.compile(
    r"\b(?:before|first|former|earlier|old|was|used\s+to|previous(?:ly)?|"
    r"antes|primer[oa]?|anterior)\b",
    re.IGNORECASE,
)
_SELECT_SECOND_PATTERN = re.compile(
    r"\b(?:now|current(?:ly)?|second|latter|later|new|these\s+days|"
    r"unemployment|right\s+now|today|"
    r"ahora|actual(?:mente)?|segund[oa]?|desempleo)\b",
    re.IGNORECASE,
)


def _slice_around_figure(text: str, value: float) -> Optional[str]:
    """Return the fragment of *text* that belongs to *value*.

    The fragment runs from the matched number up to (not including) the
    next money-shaped number, so the period cue the user attached to
    THAT figure travels with it: in "was making 2500 but now i just get
    unemployment, like 300 a week i think", the fragment for ``300`` is
    "300 a week i think" and normalizing it yields $1,300/month.

    Reading the period off the whole message instead is exactly the F3
    resolution bug: "a week" belonged to 300, but a whole-string parse
    applied it to 2500 and produced a fabricated $10,833/month.
    """
    if not isinstance(text, str):
        return None
    matches = list(_MONEY_TOKEN_PATTERN.finditer(text))
    for index, match in enumerate(matches):
        raw = match.group(1).replace(",", "")
        try:
            found = float(raw)
        except ValueError:
            continue
        if abs(found - value) >= 0.5:
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fragment = text[start:end].strip()
        return fragment or None
    return None


def resolve_two_figures_answer(
    context: str, answer: str
) -> Optional[float]:
    """Resolve a two-figures clarification into ONE monthly income.

    *context* is the original message that stated both figures;
    *answer* is the user's reply to "which one is current?".

    Returns the selected figure normalized to a monthly total, or
    ``None`` when the answer does not clearly select one. ``None`` is
    the important case: the caller MUST then leave income unset and let
    the message be handled as whatever it actually was. Live-probed
    2026-07-28: without this, "my mom is 68 does that matter" resolved
    the pending income question to a fabricated $10,833/month and the
    agent issued a confident false denial.

    Resolution order:
      1. The answer names one of the two figures -> that one.
      2. The answer names a fresh, fully-specified income -> that value
         (the user restated rather than chose).
      3. The answer uses a selection word ("the now one", "before") ->
         the corresponding figure.
      4. Anything else -> ``None``.
    """
    figures = _extract_two_figures(context or "")
    if figures is None:
        return None
    first, second = figures
    text = (answer or "").strip()
    if not text:
        return None

    def _monthly(value: float) -> Optional[float]:
        fragment = _slice_around_figure(context, value)
        if fragment:
            resolved = normalize_income(fragment)
            if resolved is not None:
                return resolved
        # No period cue travelled with the figure; a bare number in an
        # income answer is read as monthly, the same reading
        # ``normalize_income`` gives a bare figure on its own.
        return float(value)

    # 1. The answer restates one of the two figures ("the 300 one").
    named = [
        float(match.group(1).replace(",", ""))
        for match in _MONEY_TOKEN_PATTERN.finditer(text)
        if match.group(1).strip(",.")
    ]
    for value in named:
        if abs(value - second) < 0.5:
            return _monthly(second)
        if abs(value - first) < 0.5:
            return _monthly(first)

    # 2. The answer states a fresh figure with its own period
    #    ("it's about $1,300 a month now"). Trust the explicit answer --
    #    but ONLY when it is unmistakably about money. A bare number
    #    that matches neither figure is far more likely to belong to
    #    another field than to be a restated income: "my mom is 68 does
    #    that matter" is an age, and reading it as $68/month is the
    #    live-probed false-denial bug this guard exists to stop.
    if named and ("$" in text or _explicit_period_stated(text.casefold())):
        restated = normalize_income(text)
        if restated is not None:
            return restated

    # 3. A selection word, with no number at all.
    picks_second = _SELECT_SECOND_PATTERN.search(text)
    picks_first = _SELECT_FIRST_PATTERN.search(text)
    if picks_second and not picks_first:
        return _monthly(second)
    if picks_first and not picks_second:
        return _monthly(first)

    # 4. Ambiguous or unrelated -- resolve nothing.
    return None


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

    # F3: a two-figures case (past income + current income in the
    # same message) MUST be checked BEFORE the hourly/daily/period
    # branches. The pre-F3 early-out at the bottom of this function
    # was triggered by "a week" matching the weekly hint, so the
    # function returned None for "i was making 2500 but now i just
    # get unemployment, like 300 a week i think" — silently dropping
    # the income update. We want this branch to fire FIRST when both
    # a past-tense and present-tense income hint appear in the same
    # message AND there are at least two distinct money figures.
    if (
        _PAST_TENSE_INCOME_HINT.search(lowered)
        and _PRESENT_TENSE_INCOME_HINT.search(lowered)
        and _extract_two_figures(lowered) is not None
    ):
        return AMBIGUITY_TWO_FIGURES

    # A rate with no quantity of time is unusable -- but only when the
    # message genuinely lacks the quantity, not when it merely phrased it
    # in a shape the parser above does not recognise.
    if _HOURLY_HINT_PATTERN.search(lowered) and not _HOURS_QUANTITY_PATTERN.search(lowered):
        return AMBIGUITY_HOURLY_NO_HOURS
    if _DAILY_HINT_PATTERN.search(lowered) and not _DAYS_QUANTITY_PATTERN.search(lowered):
        return AMBIGUITY_DAILY_NO_DAYS

    # A value that parsed, but whose PERIOD was never stated and whose
    # magnitude makes "per month" an implausible reading.
    # (Pre-F3 bug: this early-out was firing for two-figures messages
    # because the SECOND figure carried a period hint; the function
    # bailed thinking the period was stated, even though the FIRST
    # figure had no period. The two-figures check above catches this
    # case before we reach this line.)
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
    reason: str, lang: str = "en", *, value: Optional[float] = None,
    text: Optional[str] = None,
) -> str:
    """Return the follow-up question that resolves *reason*.

    Code-templated, never model-written: a clarifying question that
    invented a number or a period would defeat the point of asking.

    *value* is the figure the caller has committed-or-about-to-commit
    to state. When None (the agent pipeline's normal case for an
    ambiguous message), the function looks at *text* for a money-shaped
    token so the user's own figure still appears quoted back. *text*
    is also the source for two-figures extraction in F3.
    """
    amount = ""
    if value is not None:
        amount = f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"

    # F3: extract two distinct money figures from the raw text. Used
    # when reason is two_figures; falls through to the existing
    # single-figure wording otherwise.
    two_figures = _extract_two_figures(text) if text else None
    if two_figures is not None:
        f1, f2 = two_figures
        a1 = f"${f1:,.0f}" if float(f1).is_integer() else f"${f1:,.2f}"
        a2 = f"${f2:,.0f}" if float(f2).is_integer() else f"${f2:,.2f}"
        two_amount_en = f"{a1} before, and {a2} now"
        two_amount_es = f"{a1} antes, y {a2} ahora"

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
        if reason == AMBIGUITY_TWO_FIGURES:
            if two_figures is not None:
                questions[AMBIGUITY_TWO_FIGURES] = (
                    f"Parece que mencionaste dos cifras diferentes — {two_amount_es}. "
                    "¿Cuál es tu ingreso mensual bruto actual?"
                )
            else:
                # Fallback when text wasn't supplied: still surface the
                # two-figures framing without inventing amounts.
                questions[AMBIGUITY_TWO_FIGURES] = (
                    "Parece que mencionaste dos cifras de ingreso diferentes. "
                    "¿Cuál es tu ingreso mensual bruto actual? "
                    "Si me dices las dos, te ayudo a calcular el total."
                )
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
        if reason == AMBIGUITY_TWO_FIGURES:
            if two_figures is not None:
                questions[AMBIGUITY_TWO_FIGURES] = (
                    f"Sounds like two different figures — {two_amount_en}. "
                    "Which one is your current gross monthly income?"
                )
            else:
                questions[AMBIGUITY_TWO_FIGURES] = (
                    "Sounds like two different income figures. "
                    "Which one is your current gross monthly income? "
                    "If you give me both, I can work out the total."
                )
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
