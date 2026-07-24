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
  of the federal poverty guideline for the household size, after the
  federal SNAP deduction chain (see "Net-income deduction chain" below).

## Net-income deduction chain (W14)

The net test now computes the household's actual net monthly income per
7 CFR §273.9(d), using the FY2025 SNAP deduction standards published by
USDA FNS for the 48 contiguous states (effective 2024-10-01 through
2025-09-30; NC is a contiguous state):

* **Standard deduction** — a flat monthly amount by household size
  (FY2025, 48 states & DC): $204 for sizes 1–3, $217 for size 4,
  $254 for size 5, $291 for sizes 6+.
* **Earned-income deduction** — 20% of gross earned income (wages or
  self-employment), when an earned-income figure is supplied.
* **Dependent-care deduction** — actual out-of-pocket dependent-care
  costs, uncapped under current federal rules, when supplied.
* **Excess medical deduction** — out-of-pocket medical expenses above
  $35/month, only for households with an elderly or disabled member.
* **Excess shelter deduction** — shelter costs (rent/mortgage, taxes,
  insurance, utilities) above half of the household's adjusted income
  after the deductions above, capped at $712/month for households
  WITHOUT an elderly/disabled member; uncapped for households WITH one.

Deduction source (FY2025 COLA memo, "SNAP FY 2025 Cost-of-Living
Adjustments", effective 2024-10-01 through 2025-09-30):
    https://www.fns.usda.gov/snap/fy-2025-cola
Figures verified against the USDA memo table "SNAP DEDUCTIONS October 1,
2024, to September 30, 2025" (48 States & DC column): standard deduction
$204 / $217 / $254 / $291 for sizes 1–2 / 3 / 4 / 5 / 6+; maximum excess
shelter deduction $712.

These deduction inputs are OPTIONAL. A household that supplies none of
them is computed exactly as before this change (gross income compared
against the 130%/100% limits, with the widened 130–200% "apply anyway"
band). When deduction inputs ARE supplied, the net test compares the
computed net income against the 100% limit, and the "apply anyway" band
narrows accordingly: a household whose computed net income is at or
below the 100% limit is told it likely qualifies after deductions, and
only households above the net limit but at or below 200% gross remain in
the uncertain band.

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


# ---------------------------------------------------------------------------
# W14 — FY2025 federal SNAP deduction standards (48 States & DC).
#
# Source: USDA FNS, "SNAP FY 2025 Cost-of-Living Adjustments" memo,
# effective 2024-10-01 through 2025-09-30. The underlying table is titled
# "SUPPLEMENTAL NUTRITION ASSISTANCE PROGRAM DEDUCTIONS, OCTOBER 1, 2024,
# TO SEPTEMBER 30, 2025". NC uses the 48-States-and-DC column.
#
# These constants are re-sourced annually alongside the poverty guidelines
# (see the "IMPORTANT — update policy" section in the module docstring).
# ---------------------------------------------------------------------------

# Standard deduction by household size (48 States & DC, FY2025).
# Sizes 1-2: $204; 3: $204; 4: $217; 5: $254; 6+: $291.
_STANDARD_DEDUCTION_2025: Dict[int, float] = {
    1: 204.0,
    2: 204.0,
    3: 204.0,
    4: 217.0,
    5: 254.0,
}
_STANDARD_DEDUCTION_6_PLUS: float = 291.0

# Fraction of gross earned income deducted (20%).
_EARNED_INCOME_DEDUCTION_RATE: float = 0.20

# Medical expenses are deductible only to the extent they exceed $35/month
# (and only for households with an elderly or disabled member).
_MEDICAL_EXPENSE_THRESHOLD: float = 35.0

# Maximum excess-shelter deduction for households WITHOUT an
# elderly/disabled member (48 States & DC, FY2025). Households WITH an
# elderly/disabled member are uncapped.
_MAX_SHELTER_DEDUCTION_2025: float = 712.0


def _standard_deduction(household_size: int) -> float:
    """Flat standard deduction for the household size (FY2025 table)."""
    if household_size >= 6:
        return _STANDARD_DEDUCTION_6_PLUS
    return _STANDARD_DEDUCTION_2025[household_size]


def compute_net_monthly_income(
    household_size: int,
    gross_monthly_income: float,
    has_elderly_or_disabled: bool,
    earned_monthly_income: float = 0.0,
    dependent_care_monthly: float = 0.0,
    medical_expenses_monthly: float = 0.0,
    shelter_costs_monthly: float = 0.0,
) -> Dict[str, float]:
    """Compute SNAP net monthly income via the federal deduction chain.

    Implements 7 CFR §273.9(d) using the FY2025 deduction standards for
    the 48 States & DC. All deduction inputs are monthly dollar amounts
    and default to 0.0, so a caller that supplies none of them gets the
    pre-W14 behavior (gross income unchanged by the chain — the standard
    deduction remains folded into the limit comparison, exactly as the
    original screener did).

    Parameters
    ----------
    household_size, gross_monthly_income, has_elderly_or_disabled:
        As for ``calculate_eligibility``.
    earned_monthly_income:
        Portion of gross monthly income from wages / self-employment.
        20% of this is deducted. Must not exceed gross monthly income.
    dependent_care_monthly:
        Out-of-pocket dependent-care costs (uncapped deduction).
    medical_expenses_monthly:
        Out-of-pocket medical expenses. Deductible above $35/month, and
        ONLY when the household has an elderly or disabled member.
    shelter_costs_monthly:
        Total shelter costs (rent/mortgage, taxes, insurance, utilities).
        The deductible portion is the excess over half of adjusted income
        after the deductions above; capped at $712 unless the household
        has an elderly/disabled member (then uncapped).

    Returns
    -------
    dict with keys ``net_income``, ``total_deductions``, and the
    individual deduction amounts (``standard``, ``earned_income``,
    ``dependent_care``, ``medical``, ``shelter``) for transparency.
    """
    if household_size < 1:
        raise ValueError("household_size must be >= 1")
    if gross_monthly_income < 0:
        raise ValueError("gross_monthly_income must be >= 0")
    for name, value in (
        ("earned_monthly_income", earned_monthly_income),
        ("dependent_care_monthly", dependent_care_monthly),
        ("medical_expenses_monthly", medical_expenses_monthly),
        ("shelter_costs_monthly", shelter_costs_monthly),
    ):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
    if earned_monthly_income > gross_monthly_income:
        raise ValueError(
            "earned_monthly_income cannot exceed gross_monthly_income"
        )

    # When no deduction inputs are supplied, preserve the original
    # screener behavior exactly: no deduction chain is applied.
    any_deduction_inputs = any(
        v > 0
        for v in (
            earned_monthly_income,
            dependent_care_monthly,
            medical_expenses_monthly,
            shelter_costs_monthly,
        )
    )
    if not any_deduction_inputs:
        return {
            "net_income": gross_monthly_income,
            "total_deductions": 0.0,
            "standard": 0.0,
            "earned_income": 0.0,
            "dependent_care": 0.0,
            "medical": 0.0,
            "shelter": 0.0,
        }

    # Federal deduction chain, 7 CFR §273.9(d), FY2025 amounts.
    standard = _standard_deduction(household_size)
    earned = round(_EARNED_INCOME_DEDUCTION_RATE * earned_monthly_income, 2)
    dependent_care = dependent_care_monthly
    medical = 0.0
    if has_elderly_or_disabled:
        medical = max(0.0, medical_expenses_monthly - _MEDICAL_EXPENSE_THRESHOLD)

    adjusted = gross_monthly_income - standard - earned - dependent_care - medical
    # Adjusted income is floored at 0 before the shelter calculation so
    # the "excess over half of adjusted income" test never goes negative.
    adjusted = max(0.0, adjusted)

    shelter_excess = max(0.0, shelter_costs_monthly - (adjusted / 2.0))
    if not has_elderly_or_disabled:
        shelter_excess = min(shelter_excess, _MAX_SHELTER_DEDUCTION_2025)

    net = adjusted - shelter_excess
    net = max(0.0, net)

    total = standard + earned + dependent_care + medical + shelter_excess
    return {
        "net_income": round(net, 2),
        "total_deductions": round(total, 2),
        "standard": standard,
        "earned_income": earned,
        "dependent_care": dependent_care,
        "medical": medical,
        "shelter": round(shelter_excess, 2),
    }


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
    earned_monthly_income: float = 0.0,
    dependent_care_monthly: float = 0.0,
    medical_expenses_monthly: float = 0.0,
    shelter_costs_monthly: float = 0.0,
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
    earned_monthly_income, dependent_care_monthly,
    medical_expenses_monthly, shelter_costs_monthly:
        OPTIONAL deduction inputs (monthly USD, default 0). When any of
        them is supplied, the federal net-income deduction chain is
        computed for real (see ``compute_net_monthly_income``) and the
        net test compares the computed net income against the 100% limit.
        When ALL of them are 0 (the default), the function produces
        byte-identical output to the pre-W14 screener — gross income is
        compared against the applicable limit and the widened 130–200%
        "apply anyway" band is used.

    Returns
    -------
    dict with keys:

    * ``qualifies`` (bool) — True if the household passes the applicable test.
    * ``test_used`` (``"gross"`` or ``"net"``) — which test was applied.
    * ``limit`` (float) — the monthly income limit for that household size
      and test.
    * ``income_considered`` (float) — the income figure compared against
      ``limit``. For the gross test this is gross monthly income. For the
      net test: when no deduction inputs are supplied this is also the
      gross figure (the limit being lower provides a conservative bias);
      when deduction inputs ARE supplied this is the computed net monthly
      income after the federal deduction chain.
    * ``household_size`` (int) — echoed back so callers don't have to
      thread the input through separately.
    * ``expanded_limit`` (float) — the 200% of poverty guideline monthly
      limit used to identify households that should apply anyway.
    * ``outcome`` (str) — ``"likely_eligible"`` when the applicable test
      passes, ``"uncertain_apply_anyway"`` when it fails but gross income
      is at or below 200% of poverty, or ``"likely_ineligible"`` when
      gross income is above 200% of poverty.
    * ``deductions`` (dict or None) — the deduction-chain breakdown from
      ``compute_net_monthly_income`` when deduction inputs were supplied,
      else ``None``. Lets the agent explain WHICH deductions moved the
      result.
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
    for name, value in (
        ("earned_monthly_income", earned_monthly_income),
        ("dependent_care_monthly", dependent_care_monthly),
        ("medical_expenses_monthly", medical_expenses_monthly),
        ("shelter_costs_monthly", shelter_costs_monthly),
    ):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")

    deductions_used = any(
        v > 0
        for v in (
            earned_monthly_income,
            dependent_care_monthly,
            medical_expenses_monthly,
            shelter_costs_monthly,
        )
    )
    deductions: Dict[str, float] | None = None

    test_used = "net" if has_elderly_or_disabled else "gross"
    limit = _limit_for(household_size, test_used)
    expanded_limit = _limit_for(household_size, "expanded")

    if deductions_used:
        deductions = compute_net_monthly_income(
            household_size=household_size,
            gross_monthly_income=gross_monthly_income,
            has_elderly_or_disabled=has_elderly_or_disabled,
            earned_monthly_income=earned_monthly_income,
            dependent_care_monthly=dependent_care_monthly,
            medical_expenses_monthly=medical_expenses_monthly,
            shelter_costs_monthly=shelter_costs_monthly,
        )
        income_considered = deductions["net_income"]
    else:
        income_considered = gross_monthly_income

    qualifies = income_considered <= limit

    # Narrowing the 130–200% band (W14): when the real net-income chain
    # is computed and the household's NET income is at or below the 100%
    # net limit, the household is treated as likely eligible even if its
    # gross income is above the gross limit — the net test is the one the
    # deduction chain actually informs, and NC's broad-based categorical
    # eligibility waives the gross test for households that pass on net.
    # This only fires when deduction inputs were supplied; with no
    # deduction data the widened "apply anyway" band stands unchanged.
    net_limit = _limit_for(household_size, "net")
    if (
        deductions_used
        and not qualifies
        and deductions is not None
        and deductions["net_income"] <= net_limit
        and gross_monthly_income <= expanded_limit
    ):
        qualifies = True
        outcome = "likely_eligible"
    elif qualifies:
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

    if deductions_used and deductions is not None:
        deduction_note = (
            f" Federal deductions applied: ${deductions['total_deductions']:,.2f} "
            f"(standard ${deductions['standard']:,.2f}"
            + (
                f", earned-income ${deductions['earned_income']:,.2f}"
                if deductions["earned_income"] > 0
                else ""
            )
            + (
                f", dependent-care ${deductions['dependent_care']:,.2f}"
                if deductions["dependent_care"] > 0
                else ""
            )
            + (
                f", medical ${deductions['medical']:,.2f}"
                if deductions["medical"] > 0
                else ""
            )
            + (
                f", shelter ${deductions['shelter']:,.2f}"
                if deductions["shelter"] > 0
                else ""
            )
            + f"), giving net monthly income ${income_considered:,.2f}."
        )
    else:
        deduction_note = ""

    boundary_note = ""
    if income_considered == limit:
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
        f"{deduction_note}"
        f"{boundary_note} Result: "
        f"{'likely qualifies' if qualifies else 'likely does not qualify'}."
        f"{outcome_note}"
    )

    return {
        "qualifies": qualifies,
        "test_used": test_used,
        "limit": limit,
        "expanded_limit": expanded_limit,
        "income_considered": income_considered,
        "household_size": household_size,
        "outcome": outcome,
        "deductions": deductions,
        "notes": notes,
    }


__all__ = [
    "calculate_eligibility",
    "compute_net_monthly_income",
    "income_limits",
    "max_household_size",
    "_POVERTY_GUIDELINES_SOURCE_URL",
]
