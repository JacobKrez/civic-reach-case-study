"""Deterministic NC FNS / SNAP eligibility calculator.

This module is intentionally pure-Python: no I/O, no network, no LLM calls.
The agent's job is to extract the three intake fields from a conversation
and hand them to ``calculate_eligibility``; from there the decision is a
deterministic table lookup plus arithmetic.

## NC FNS two-track rule (sourced)

NC SNAP follows the federal SNAP income-eligibility rules (7 CFR §273.9).
A household's income test depends on whether any member is elderly (60+)
or disabled:

* Households WITHOUT an elderly/disabled member are screened against the
  **gross** monthly income limit — 130% of the federal poverty guideline
  for the household size.
* Households WITH an elderly/disabled member are NOT subject to the gross
  test. They are screened against the **net** monthly income limit — 100%
  of the federal poverty guideline for the household size (after the
  standard SNAP deduction, which is folded into the limit for the purpose
  of this initial screener).

## Source for the underlying poverty figures

The base figures used here are the **2025 Federal Poverty Guidelines**
published by the U.S. Department of Health and Human Services (HHS). USDA
FNS constructs the SNAP gross-income-eligibility table by multiplying
these guidelines by 1.30 (130%) and the net-income table by 1.00 (100%),
then expressing the result as a monthly figure. See:

    https://aspe.hhs.gov/2025-poverty-guidelines

(USDA FNS also publishes a derived SNAP-specific table at
https://www.fns.usda.gov/snap/income-eligibility-and-benefit-calculations
— both resolve to the same numbers because FNS is required by 7 CFR
§273.9(a) to use the current HHS guidelines.)

The figures below are the 2025 HHS annual guidelines. Monthly figures in
``GROSS_LIMITS_2025`` and ``NET_LIMITS_2025`` are computed at module load
time as ``round(annual * multiplier / 12, 2)`` so that the relationship
between the base data and the multipliers is auditable in this file.

## IMPORTANT — update policy

These figures must be re-sourced annually when HHS publishes new poverty
guidelines (typically late January / early February). When updating:
1. Refresh ``_ANNUAL_POVERTY_GUIDELINES_2025`` with the new HHS figures.
2. Re-verify the source URL above still resolves.
3. Update the comments and any tests that hardcode expected values.

If the table falls behind the current program year, the calculator will
be slightly more permissive than the actual program — that is a known
trade-off of the deterministic design.
"""

from __future__ import annotations

from typing import Any, Dict

# Source for the underlying figures: HHS 2025 Federal Poverty Guidelines.
# https://aspe.hhs.gov/2025-poverty-guidelines
# (USDA FNS uses these figures to derive the SNAP gross/net income
# limits per 7 CFR §273.9; the SNAP-specific page
# https://www.fns.usda.gov/snap/income-eligibility-and-benefit-calculations
# resolves to the same numbers.)
_POVERTY_GUIDELINES_SOURCE_URL = "https://aspe.hhs.gov/2025-poverty-guidelines"

# Annual federal poverty guidelines (HHS, 2025), by household size 1..8.
# For each additional person beyond 8, add $5,500.
_ANNUAL_POVERTY_GUIDELINES_2025: Dict[int, float] = {
    1: 15_650.0,
    2: 21_150.0,
    3: 26_650.0,
    4: 32_150.0,
    5: 37_650.0,
    6: 43_150.0,
    7: 48_650.0,
    8: 54_150.0,
}
_ADDITIONAL_PERSON_ANNUAL: float = 5_500.0
_MAX_HOUSEHOLD_SIZE: int = 20  # hard cap for table size; SNAP has no upper bound but this is plenty.


def _monthly(annual: float, multiplier: float) -> float:
    """Convert an annual guideline to a monthly limit at the given multiplier."""
    return round((annual * multiplier) / 12.0, 2)


def _build_limits() -> Dict[int, Dict[str, float]]:
    """Build both the gross (130%) and net (100%) monthly limit tables.

    Returns a nested dict ``{household_size: {"gross": ..., "net": ..., "expanded": ...}}``
    so the calculator can pick the right track and the expanded 200% band in
    one lookup.
    """
    limits: Dict[int, Dict[str, float]] = {}
    for size in range(1, _MAX_HOUSEHOLD_SIZE + 1):
        if size <= 8:
            annual = _ANNUAL_POVERTY_GUIDELINES_2025[size]
        else:
            annual = (
                _ANNUAL_POVERTY_GUIDELINES_2025[8]
                + (size - 8) * _ADDITIONAL_PERSON_ANNUAL
            )
        limits[size] = {
            "gross": _monthly(annual, 1.30),  # 130% of poverty — gross test
            "net": _monthly(annual, 1.00),    # 100% of poverty — net test
            "expanded": _monthly(annual, 2.00),  # 200% of poverty — apply-anyway band
        }
    return limits


_LIMITS: Dict[int, Dict[str, float]] = _build_limits()


def _limit_for(household_size: int, test: str) -> float:
    if household_size < 1 or household_size > _MAX_HOUSEHOLD_SIZE:
        raise ValueError(
            f"household_size must be between 1 and {_MAX_HOUSEHOLD_SIZE}; "
            f"got {household_size}"
        )
    return _LIMITS[household_size][test]


def income_limits(household_size: int) -> Dict[str, float]:
    """Public accessor for the monthly income limits at a household size.

    Returns ``{"gross": ..., "net": ..., "expanded": ...}`` (130% / 100% /
    200% of the federal poverty guideline, monthly). This is the single
    source of truth other modules must read from — the knowledge-base
    income table (``knowledge_base/02_income_limits.md``) is *generated*
    from this so the agent's cited numbers can never drift from the
    numbers it actually computes with. See ``scripts/generate_kb_table.py``.
    """
    if household_size < 1 or household_size > _MAX_HOUSEHOLD_SIZE:
        raise ValueError(
            f"household_size must be between 1 and {_MAX_HOUSEHOLD_SIZE}; "
            f"got {household_size}"
        )
    return dict(_LIMITS[household_size])


def max_household_size() -> int:
    """The largest household size the limit table covers."""
    return _MAX_HOUSEHOLD_SIZE


def calculate_eligibility(
    household_size: int,
    gross_monthly_income: float,
    has_elderly_or_disabled: bool,
) -> Dict[str, Any]:
    """Run the deterministic NC FNS gross/net income test.

    Parameters
    ----------
    household_size:
        Number of people in the SNAP household. Must be >= 1.
    gross_monthly_income:
        Total household gross monthly income in USD. Must be >= 0.
    has_elderly_or_disabled:
        True if any household member is 60+ or disabled. Selects which
        income test applies (net for elderly/disabled; gross otherwise).

    Returns
    -------
    dict with keys:

    * ``qualifies`` (bool) — True if the household passes the applicable test.
    * ``test_used`` (``"gross"`` or ``"net"``) — which test was applied.
    * ``limit`` (float) — the monthly income limit for that household size
      and test.
    * ``income_considered`` (float) — the income figure compared against
      ``limit``. For the gross test this is the household's gross monthly
      income. For the net test we still receive only the gross figure from
      the user, so this is the same value here — the limit itself is what
      differs (100% vs 130% of poverty). A more complete implementation
      would subtract the standard SNAP deduction here; for this initial
      screener, the limit being lower (100% vs 130%) provides a
      conservative bias toward the stricter net test.
    * ``household_size`` (int) — echoed back so callers don't have to
      thread the input through separately.
    * ``expanded_limit`` (float) — the 200% of poverty guideline monthly
      limit used to identify households that should apply anyway.
    * ``outcome`` (str) — ``"likely_eligible"`` when the applicable test
      passes, ``"uncertain_apply_anyway"`` when it fails but gross income is
      at or below 200% of poverty, or ``"likely_ineligible"`` when gross
      income is above 200% of poverty.
    * ``notes`` (str) — human-readable explanation of the result and the
      rule applied, suitable for surfacing in the user-facing summary.

    Boundary handling
    -----------------
    An income that exactly equals the limit **qualifies** (i.e. the test
    is ``income <= limit``). This matches the regulatory language in
    7 CFR §273.9(a) which sets the gross-income standard at 130 percent
    of the poverty line and treats households at-or-below that line as
    eligible for the gross test; the same convention is applied to the
    net test for consistency.
    """
    if household_size < 1:
        raise ValueError("household_size must be >= 1")
    if gross_monthly_income < 0:
        raise ValueError("gross_monthly_income must be >= 0")

    test_used = "net" if has_elderly_or_disabled else "gross"
    limit = _limit_for(household_size, test_used)
    expanded_limit = _limit_for(household_size, "expanded")
    qualifies = gross_monthly_income <= limit

    if qualifies:
        outcome = "likely_eligible"
    elif gross_monthly_income <= expanded_limit:
        outcome = "uncertain_apply_anyway"
    else:
        outcome = "likely_ineligible"

    if has_elderly_or_disabled:
        rule_note = (
            "Household includes an elderly (60+) or disabled member, so the "
            "net income test (100% of the federal poverty guideline) applies "
            "instead of the gross test."
        )
    else:
        rule_note = (
            "Household has no elderly (60+) or disabled member, so the gross "
            "income test (130% of the federal poverty guideline) applies."
        )

    boundary_note = ""
    if gross_monthly_income == limit:
        boundary_note = " Income equals the limit exactly and qualifies."

    if outcome == "uncertain_apply_anyway":
        outcome_note = (
            " The household fails the initial applicable income test but may "
            "still qualify after allowable deductions because gross income is "
            "at or below 200% of the poverty guideline. Apply anyway or use "
            "the NC ePASS portal for a full determination."
        )
    elif outcome == "likely_eligible":
        outcome_note = " The household passes the initial applicable income test."
    else:
        outcome_note = (
            " Gross income is above 200% of the poverty guideline, so the "
            "household is unlikely to qualify based on income."
        )

    notes = (
        f"{rule_note} Monthly income ${gross_monthly_income:,.2f} vs "
        f"{test_used} limit ${limit:,.2f} for household of {household_size}."
        f"{boundary_note} Result: "
        f"{'likely qualifies' if qualifies else 'likely does not qualify'}."
        f"{outcome_note}"
    )

    return {
        "qualifies": qualifies,
        "test_used": test_used,
        "limit": limit,
        "expanded_limit": expanded_limit,
        "income_considered": gross_monthly_income,
        "household_size": household_size,
        "outcome": outcome,
        "notes": notes,
    }


__all__ = ["calculate_eligibility", "income_limits", "max_household_size", "_POVERTY_GUIDELINES_SOURCE_URL"]
