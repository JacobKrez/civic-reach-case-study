"""Deterministic NC FNS / SNAP eligibility calculator.

This module is intentionally pure-Python: no I/O, no network, no LLM calls.
The agent's job is to extract the three intake fields from a conversation
and hand them to ``calculate_eligibility``; from there the decision is a
deterministic table lookup plus arithmetic.

## NC FNS three-track rule (sourced)

NC SNAP follows the federal SNAP income-eligibility rules (7 CFR §273.9)
with one state-level expansion: **NC operates Broad-Based Categorical
Eligibility (BBCE) at 200% of the federal poverty guideline (NCDHHS)**.
Under BBCE, most NC households are categorically eligible on the gross
screen when their gross income is at or below 200% FPL, and the asset
test is waived. Approximately 13% of NC SNAP households qualify via BBCE
(NCDHHS, 2025). See "North Carolina BBCE — 200% gross screen" below.

A household's income test therefore depends on TWO flags: whether any
member is elderly (60+) or disabled, and whether the household is
classified as a separate household (an elderly or disabled person living
with others who purchase and prepare food separately — see
``knowledge_base/03_deductions_and_household.md``):

* Households WITHOUT an elderly/disabled member are screened against the
  **gross** monthly income limit. Under NC BBCE that limit is **200% of
  the federal poverty guideline** (raised from the federal 130% floor).
* Households WITH an elderly/disabled member living in the same
  household are NOT subject to the gross test. They are screened against
  the **net** monthly income limit — **100% of the federal poverty
  guideline** for the household size, after the federal SNAP deduction
  chain (see "Net-income deduction chain" below).
* Households where the elderly/disabled member is a SEPARATE household
  (e.g. an elderly parent living with adult children who buy and cook
  separately) are screened against the **separate-household gross**
  monthly income limit — **165% of the federal poverty guideline** for
  the household size. This is the federal SNAP rule for separate
  elderly/disabled households, which NC follows. See "FY2027 165%
  standard for separate households" below.

Passing ``state_code="NC"`` activates NC BBCE. The generic calculator API
defaults to the federal 130% gross floor; the NC-only FoodPath agent passes
its jurisdiction explicitly.

## Net-income deduction chain (W31)

The net test computes the household's actual net monthly income per
7 CFR §273.9(d), using the FY2027 SNAP deduction standards published by
USDA FNS for the 48 contiguous states and DC (effective 2026-10-01 through
2027-09-30; NC is a contiguous state):

* **Standard deduction** — a flat monthly amount by household size
  (FY2027, 48 states & DC): $217 for sizes 1–3, $229 for size 4,
  $268 for size 5, $308 for sizes 6+.
* **Earned-income deduction** — 20% of gross earned income (wages or
  self-employment), when an earned-income figure is supplied.
* **Dependent-care deduction** — actual out-of-pocket dependent-care
  costs, uncapped under current federal rules, when supplied.
* **Excess medical deduction** — out-of-pocket medical expenses above
  $35/month, only for households with an elderly or disabled member.
* **Excess shelter deduction** — shelter costs (rent/mortgage, taxes,
  insurance, utilities) above half of the household's adjusted income
  after the deductions above, capped at $769/month for households
  WITHOUT an elderly/disabled member; uncapped for households WITH one.

Deduction source (USDA FNS, "SNAP FY 2027 Cost-of-Living Adjustments",
effective 2026-10-01 through 2027-09-30):
    https://www.fns.usda.gov/snap/allotment/cola
The memo's 48 States & DC table gives standard deductions $217 / $229 /
$268 / $308 and maximum excess shelter deduction $769.

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

The base figures below are the USDA FNS **FY2027 SNAP income-eligibility
standards** for the 48 contiguous states and DC. The USDA memo publishes
the monthly 100% and 130% standards as whole-dollar values, effective
October 1, 2026 through September 30, 2027. See:

    https://www.fns.usda.gov/snap/allotment/cola

The NC BBCE expanded band is represented as twice the published net
standard, rounded to cents. Keeping all three bands in this one table
ensures the calculator and generated grounding corpus use the same values.

## North Carolina BBCE — 200% gross screen (sourced)

NC DHHS operates SNAP under Broad-Based Categorical Eligibility (BBCE),
which raises the gross-income limit from the federal 130% floor to
**200% of the federal poverty guideline** and waives the asset test
for most households. About 13% of NC SNAP households qualify via BBCE
(NCDHHS, 2025 BBCE policy summary).

Primary NC source:
    https://www.ncdhhs.gov/divisions/social-services/program-design-and-policy
    (NC SNAP policy and program design — BBCE at 200% FPL, no asset test
    for most households)

Federal cross-reference:
    https://www.fns.usda.gov/snap/eligibility-income-and-deductions
    (general federal SNAP eligibility; the federal floor is 130% gross
    and 100% net — NC lifts the gross ceiling to 200% via BBCE.)

This is why the calculator's NC ``gross`` limit is **200%** of poverty
and not the federal 130% floor: under NC BBCE, a household whose gross
income is at or below 200% FPL passes the gross screen categorically,
and actual eligibility then turns on the net test (for elderly/disabled
households) or on the federal 130%/200% reference bands for the
non-elderly/disabled path.

## FY2027 165% standard for separate households (sourced)

When an elderly (60+) or disabled person lives with others but
purchases and prepares food separately, they may be classified as a
**separate SNAP household**. Federal SNAP rules (7 CFR §273.9(a))
and NC FNS both apply a **165% gross-income standard** to such
separate households, replacing the 200% BBCE ceiling and the 130%
federal floor. This standard was set by USDA FNS and is re-sourced
annually alongside the poverty guidelines. FY2027 monthly figures
(48 States & DC, NC uses this column):

    HH1 $2,152   HH5 $5,177
    HH2 $2,909   HH6 $5,934
    HH3 $3,665   HH7 $6,690
    HH4 $4,421   HH8 $7,446
    each additional person: +$757/month

Source: USDA FNS SNAP FY2027 income-eligibility tables (48 States &
DC column, separate-elderly/disabled household row). The calculator
keeps the 165% standard as a documented reference point; the
calculator itself does not currently auto-route a household to the
165% track (the agent's intake form is responsible for surfacing
the separate-household question — see kb/03 "Who Can Be Separate?").
The figures are exported as ``_SEPARATE_HOUSEHOLD_LIMITS_2027`` so
tests and consumers can cross-reference them without re-deriving.

## IMPORTANT — update policy

These figures must be re-sourced annually when USDA FNS publishes the new
SNAP COLA memo. When updating:
1. Refresh the FY income-limit and deduction constants from the memo.
2. Re-verify the source URL above still resolves.
3. Regenerate the KB table and update tests that hardcode expected values.

If the table falls behind the current program year, the calculator will be
slightly more permissive than the actual program — that is a known
trade-off of the deterministic design.
"""

from __future__ import annotations

from typing import Any, Dict

# Source for the FY2027 SNAP income standards and deductions:
# USDA FNS, "SNAP FY 2027 Cost-of-Living Adjustments" memo,
# effective 2026-10-01 through 2027-09-30.
_POVERTY_GUIDELINES_SOURCE_URL = "https://www.fns.usda.gov/snap/allotment/cola"

# USDA publishes these FY2027 monthly standards directly as operative
# whole-dollar thresholds (48 States & DC), so they are stored as data rather
# than re-derived at runtime. Audit trail: the source table derives gross at
# 130% and net at 100% from the 2025 HHS poverty guidelines, then rounds each
# monthly threshold UP to the next whole dollar. For example, HH1 arithmetic
# gives $1,695.42 gross / $1,304.17 net, while USDA publishes $1,696 / $1,305.
# The expanded NC BBCE column is twice the published net standard.
_GROSS_LIMITS_2027: Dict[int, float] = {
    1: 1729.0, 2: 2345.0, 3: 2960.0, 4: 3575.0,
    5: 4191.0, 6: 4806.0, 7: 5421.0, 8: 6037.0,
}
_NET_LIMITS_2027: Dict[int, float] = {
    1: 1330.0, 2: 1804.0, 3: 2277.0, 4: 2750.0,
    5: 3224.0, 6: 3697.0, 7: 4170.0, 8: 4644.0,
}
_GROSS_ADDITIONAL_2027: float = 616.0
_NET_ADDITIONAL_2027: float = 474.0
_MAX_HOUSEHOLD_SIZE: int = 20  # precomputed table size; larger households extrapolate.


def _build_limits() -> Dict[int, Dict[str, float]]:
    """Build the gross/net/expanded monthly limit tables.

    Returns a nested dict ``{household_size: {"gross": ..., "net": ..., "expanded": ...}}``
    so the calculator can pick the right track in one lookup. The three
    bands are:

    * ``gross``  — 130% of poverty (the federal default; used by NC BBCE
      as the *floor* of the categorical pass band, not the ceiling).
    * ``net``    — 100% of poverty (the net-income test, used for
      elderly/disabled households).
    * ``expanded`` — 200% of poverty (the NC BBCE gross screen ceiling;
      households at or below this pass the gross screen categorically
      under NC rules).

    The 165% separate-household standard for elderly/disabled separate
    households is built separately in ``_build_separate_household_limits``
    and exported as ``_SEPARATE_HOUSEHOLD_LIMITS_2027``.
    """
    limits: Dict[int, Dict[str, float]] = {}
    for size in range(1, _MAX_HOUSEHOLD_SIZE + 1):
        if size <= 8:
            gross = _GROSS_LIMITS_2027[size]
            net = _NET_LIMITS_2027[size]
        else:
            gross = _GROSS_LIMITS_2027[8] + (size - 8) * _GROSS_ADDITIONAL_2027
            net = _NET_LIMITS_2027[8] + (size - 8) * _NET_ADDITIONAL_2027
        limits[size] = {
            "gross": gross,
            "net": net,
            "expanded": round(net * 2.0, 2),  # 200% of poverty — NC BBCE ceiling
        }
    return limits


_LIMITS: Dict[int, Dict[str, float]] = _build_limits()


# ---------------------------------------------------------------------------
# W30 — FY2027 165% standard for SEPARATE elderly/disabled households
# (48 States & DC, USDA FNS SNAP FY2027 income-eligibility table).
#
# When an elderly (60+) or disabled person lives with others but buys and
# prepares food separately, federal SNAP rules (and NC FNS) apply a 165%
# gross-income standard. The figures below are the FY2027 monthly limits
# published by USDA FNS (48 States & DC column). The calculator does not
# currently auto-route a household to this track — the agent's intake form
# surfaces the separate-household question — but the table is exported so
# tests, docs, and downstream consumers can cross-reference it without
# re-deriving. See the "FY2027 165% standard for separate households"
# section in the module docstring for the primary source.
# ---------------------------------------------------------------------------
_SEPARATE_HOUSEHOLD_LIMITS_2027: Dict[int, float] = {
    1: 2195.0,
    2: 2976.0,
    3: 3757.0,
    4: 4538.0,
    5: 5319.0,
    6: 6100.0,
    7: 6881.0,
    8: 7662.0,
}
_SEPARATE_HOUSEHOLD_ADDITIONAL_PERSON: float = 781.0


def _build_separate_household_limits() -> Dict[int, float]:
    """Extend the 165% separate-household table out to ``_MAX_HOUSEHOLD_SIZE``.

    For household sizes 1-8 we use the published FY2027 figures verbatim
    (USD-rounded). For each additional person beyond 8, we add
    ``_SEPARATE_HOUSEHOLD_ADDITIONAL_PERSON`` ($781/mo for FY2027, the
    annual guideline's per-addition delta of $9,372 / 12 = $781/month).
    """
    limits: Dict[int, float] = dict(_SEPARATE_HOUSEHOLD_LIMITS_2027)
    for size in range(9, _MAX_HOUSEHOLD_SIZE + 1):
        limits[size] = round(
            _SEPARATE_HOUSEHOLD_LIMITS_2027[8]
            + (size - 8) * _SEPARATE_HOUSEHOLD_ADDITIONAL_PERSON,
            2,
        )
    return limits


_SEPARATE_HOUSEHOLD_LIMITS: Dict[int, float] = _build_separate_household_limits()


def separate_household_limits(household_size: int) -> float:
    """Public accessor for the FY2027 165% separate-household limit.

    Returns the monthly gross-income limit (USD) for a separate
    elderly/disabled household of the given size. NC follows the federal
    48-States-and-DC column.

    The figures are sourced from the FY2027 USDA FNS SNAP income-eligibility
    tables (separate-elderly/disabled household row) and re-sourced annually
    alongside the poverty guidelines.
    """
    if household_size < 1:
        raise ValueError(f"household_size must be >= 1; got {household_size}")
    if household_size in _SEPARATE_HOUSEHOLD_LIMITS:
        return _SEPARATE_HOUSEHOLD_LIMITS[household_size]
    return round(
        _SEPARATE_HOUSEHOLD_LIMITS_2027[8]
        + (household_size - 8) * _SEPARATE_HOUSEHOLD_ADDITIONAL_PERSON,
        2,
    )


# ---------------------------------------------------------------------------
# W31 — FY2027 federal SNAP deduction standards (48 States & DC).
#
# Source: USDA FNS, "SNAP FY 2027 Cost-of-Living Adjustments" memo,
# effective 2026-10-01 through 2027-09-30. The underlying table is titled
# "SUPPLEMENTAL NUTRITION ASSISTANCE PROGRAM DEDUCTIONS, OCTOBER 1, 2026,
# TO SEPTEMBER 30, 2027". NC uses the 48-States-and-DC column.
#
# These constants are re-sourced annually alongside the poverty guidelines
# (see the "IMPORTANT — update policy" section in the module docstring).
# ---------------------------------------------------------------------------

# Standard deduction by household size (48 States & DC, FY2027).
# Sizes 1-2: $217; 3: $217; 4: $229; 5: $268; 6+: $308.
_STANDARD_DEDUCTION_2027: Dict[int, float] = {
    1: 217.0,
    2: 217.0,
    3: 217.0,
    4: 229.0,
    5: 268.0,
}
_STANDARD_DEDUCTION_6_PLUS: float = 308.0

# Fraction of gross earned income deducted (20%).
_EARNED_INCOME_DEDUCTION_RATE: float = 0.20

# Medical expenses are deductible only to the extent they exceed $35/month
# (and only for households with an elderly or disabled member).
_MEDICAL_EXPENSE_THRESHOLD: float = 35.0

# Maximum excess-shelter deduction for households WITHOUT an
# elderly/disabled member (48 States & DC, FY2027). Households WITH an
# elderly/disabled member are uncapped.
_MAX_SHELTER_DEDUCTION_2027: float = 769.0

# Maximum homeless shelter deduction (48 States & DC, FY2027). This value is
# documented in the KB for operational reference; the current calculator
# intake has no homeless-shelter field and therefore does not apply it.
_MAX_HOMELESS_SHELTER_DEDUCTION_2027: float = 205.66


def _standard_deduction(household_size: int) -> float:
    """Flat standard deduction for the household size (FY2027 table)."""
    if household_size >= 6:
        return _STANDARD_DEDUCTION_6_PLUS
    return _STANDARD_DEDUCTION_2027[household_size]


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

    Implements 7 CFR §273.9(d) using the FY2027 deduction standards for
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
        after the deductions above; capped at $744 unless the household
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

    # Federal deduction chain, 7 CFR §273.9(d), FY2027 amounts.
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
        shelter_excess = min(shelter_excess, _MAX_SHELTER_DEDUCTION_2027)

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
    if household_size < 1:
        raise ValueError(f"household_size must be >= 1; got {household_size}")
    if household_size in _LIMITS:
        return _LIMITS[household_size][test]
    gross = _GROSS_LIMITS_2027[8] + (household_size - 8) * _GROSS_ADDITIONAL_2027
    net = _NET_LIMITS_2027[8] + (household_size - 8) * _NET_ADDITIONAL_2027
    return {"gross": gross, "net": net, "expanded": round(net * 2.0, 2)}[test]


def income_limits(household_size: int) -> Dict[str, float]:
    """Public accessor for the monthly income limits at a household size.

    Returns ``{"gross": ..., "net": ..., "expanded": ...}`` (130% / 100% /
    200% of the federal poverty guideline, monthly). This is the single
    source of truth other modules must read from — the knowledge-base
    income table (``knowledge_base/02_income_limits.md``) is *generated*
    from this so the agent's cited numbers can never drift from the
    numbers it actually computes with. See ``scripts/generate_kb_table.py``.
    """
    return {
        test: _limit_for(household_size, test)
        for test in ("gross", "net", "expanded")
    }


def max_household_size() -> int:
    """Largest precomputed table row (larger sizes are extrapolated)."""
    return _MAX_HOUSEHOLD_SIZE


def calculate_eligibility(
    household_size: int,
    gross_monthly_income: float,
    has_elderly_or_disabled: bool,
    earned_monthly_income: float = 0.0,
    dependent_care_monthly: float = 0.0,
    medical_expenses_monthly: float = 0.0,
    shelter_costs_monthly: float = 0.0,
    state_code: str = "FEDERAL",
    separate_elderly_disabled_household: bool = False,
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
    state_code:
        Two-letter US state code. Defaults to the federal rules so the
        calculator's generic public API uses the published 130% gross
        standard. The NC application must pass ``state_code="NC"``; that
        route uses NC's **200%** gross-screen ceiling via BBCE. The 165%
        separate-household standard is documented in
        ``separate_household_limits()`` and is surfaced only via the
        ``separate_elderly_disabled_household`` flag below.
    separate_elderly_disabled_household:
        True when the elderly/disabled member is a SEPARATE household
        (lives with others but purchases and prepares food separately).
        Routes the household to the federal 165% separate-household
        gross-income standard (FY2027 figures) instead of the regular
        100% net test. The agent's intake form is responsible for
        surfacing this question — see ``knowledge_base/03_deductions_and_household.md``.

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
      limit (NC BBCE gross ceiling) used to identify households that pass
      the gross screen categorically under NC rules.
    * ``bbce_limit`` (float) — the state-specific gross-screen ceiling
      actually applied (200% under NC BBCE; the 130% federal floor when
      ``state_code != "NC"``; the 165% separate-household standard when
      ``separate_elderly_disabled_household`` is set). Surfaced so
      downstream consumers can verify which ceiling governed.
    * ``outcome`` (str) — one of:

      - ``"likely_eligible"`` — household passes the applicable income
        test (gross ≤ state-specific ceiling for non-elderly/disabled;
        net ≤ 100% for elderly/disabled).
      - ``"likely_eligible_gross_pending_net"`` (W30) — household passes
        the NC BBCE gross screen (gross income ≤ 200% of poverty) but
        the federal net-income deduction chain was not supplied, so the
        net test cannot be evaluated. The household should be told it
        likely qualifies on the gross screen and that the net test is
        pending deductions. This outcome exists because under NC BBCE a
        household at, say, 150% of poverty is NOT "uncertain" — it
        passes the gross screen categorically, and the only thing left
        to determine is whether its computed net income falls at or
        below the 100% net limit (which requires deduction data we
        don't have).
      - ``"uncertain_apply_anyway"`` — household fails the applicable
        test but is still in the BBCE band (gross income > state ceiling
        but ≤ 200% FPL). Apply anyway; the case worker can evaluate
        deductions and the 100% net test.
      - ``"likely_ineligible"`` — gross income is above 200% of poverty,
        which is the hard outer ceiling for both NC BBCE and the federal
        SNAP gross-income screen.

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

    # Track selection (W30 three-track design):
    #   - NC non-elderly/disabled → gross test against 200% FPL (BBCE)
    #   - NC elderly/disabled same household → net test against 100% FPL
    #   - Separate elderly/disabled household → gross test against 165%
    #     FPL (FY2027 separate-household standard, federal rule NC follows)
    #   - Non-NC states → gross test against the 130% federal floor
    #     (only NC is implemented today; the flag is here so the
    #     calculator cannot silently drift from NC behavior if reused).
    nc_bbce = state_code.upper() == "NC"
    if separate_elderly_disabled_household:
        test_used = "gross"
        limit = separate_household_limits(household_size)
        bbce_limit = limit
    elif has_elderly_or_disabled:
        test_used = "net"
        limit = _limit_for(household_size, "net")
        bbce_limit = _limit_for(household_size, "expanded" if nc_bbce else "gross")
    else:
        test_used = "gross"
        # Under NC BBCE the gross screen is at 200% FPL (categorical
        # eligibility), not the federal 130% floor. The 130% federal floor
        # is still reported in income_limits() as the reference number.
        bbce_limit = _limit_for(household_size, "expanded" if nc_bbce else "gross")
        limit = bbce_limit
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

    # Narrowing the BBCE band (W14 + W30): when the real net-income chain
    # is computed and the household's NET income is at or below the 100%
    # net limit, the household is treated as likely eligible even if its
    # gross income is above the gross screen — the net test is the one
    # the deduction chain actually informs, and NC's broad-based
    # categorical eligibility waives the gross test for households that
    # pass on net. This only fires when deduction inputs were supplied;
    # without deduction data the new "gross screen pending net" outcome
    # applies.
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
        # Inside the BBCE band but the applicable test (200%/100%/165%
        # depending on track) was not passed. Without deductions we can't
        # say the household is unlikely eligible — they may yet pass the
        # net test if/when deductions are evaluated. Under NC BBCE the
        # proper framing is "passed the gross screen, net test pending"
        # (W30 new outcome) rather than "uncertain, apply anyway".
        if (
            nc_bbce
            and not deductions_used
            and not separate_elderly_disabled_household
        ):
            # NC BBCE household (an explicitly selected NC track) that is
            # inside the 200% expanded band but failed the applicable test
            # without deductions supplied. This fires for:
            #   - non-elderly NC households between the gross test limit
            #     (in practice a no-op now that the gross screen IS 200%,
            #     but kept for future state-rule changes)
            #   - elderly/disabled NC households whose net test failed
            #     because no deductions were supplied — they passed the
            #     200% BBCE gross screen and only the net test is pending.
            # Honest framing: "passed gross screen; net test pending
            # deductions." This outcome is only meaningful for NC — under
            # federal rules (or other states that don't lift the 130%
            # gross floor to 200%) the household IS still "uncertain,
            # apply anyway" because they may or may not have deductions
            # and there's no categorical pass at the gross screen.
            outcome = "likely_eligible_gross_pending_net"
        else:
            # Non-NC household, separate-household standard at 165%, or
            # deductions were supplied. The applicable test (or its net
            # counterpart) was actually evaluated and failed; tell the
            # household to apply anyway so a case worker can re-evaluate.
            outcome = "uncertain_apply_anyway"
    else:
        outcome = "likely_ineligible"

    if has_elderly_or_disabled:
        rule_note = (
            "Household includes an elderly (60+) or disabled member, so the "
            "net income test (100% of the federal poverty guideline) applies "
            "instead of the gross test."
        )
    elif separate_elderly_disabled_household:
        rule_note = (
            "Household is a separate elderly/disabled household, so the "
            "165% separate-household gross-income standard (federal rule "
            "followed by NC FNS) applies instead of the standard "
            "200%/130% NC BBCE gross screen."
        )
    else:
        if nc_bbce:
            rule_note = (
                "Household has no elderly (60+) or disabled member and "
                "is screened under North Carolina's Broad-Based Categorical "
                "Eligibility (BBCE). Under NC BBCE the gross income screen "
                "is 200% of the federal poverty guideline (NCDHHS); the "
                "federal 130% floor applies in other states."
            )
        else:
            rule_note = (
                f"Household has no elderly (60+) or disabled member, so the "
                f"federal gross income test (130% of the federal poverty "
                f"guideline) applies (state_code={state_code!r} does not "
                f"have a state-specific BBCE rule implemented)."
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
    elif outcome == "likely_eligible_gross_pending_net":
        outcome_note = (
            " The household passes North Carolina's BBCE gross-income "
            "screen (200% of the poverty guideline) but the federal net "
            "income test was not evaluated because no deduction amounts "
            "were supplied. Provide earned income, dependent care, "
            "medical expenses, or shelter costs to complete the net test."
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

    # The same finding, addressed to the person who asked.
    #
    # ``notes`` above is the technical record: it names the rule, the
    # citation, and every deduction, and the audit trail and tests read
    # it. It is also what the resident was being shown, and it is not
    # written for them -- "Household has no elderly (60+) or disabled
    # member and is screened under North Carolina's Broad-Based
    # Categorical Eligibility (BBCE). Under NC BBCE the gross income
    # screen is 200% of the federal poverty guideline (NCDHHS); the
    # federal 130% floor applies in other states." states the verdict
    # twice, speaks about the user in the third person, and spends most
    # of its words on a rule that is not theirs to care about. The
    # system prompt targets a 6th-grade reading level; this was nowhere
    # near it, and the continuous simulator's judge flagged it as the
    # single most frequent quality failure.
    #
    # Both are kept. ``notes`` stays exactly as it was, so nothing that
    # depends on it changes; ``plain_notes`` is what the reply renders.
    who = f"a household of {household_size}"
    if has_elderly_or_disabled:
        basis = (
            f"Because someone in your household is 60 or older or has a "
            f"disability, we compare your income after deductions to the "
            f"limit for {who}: ${limit:,.2f} a month."
        )
    elif separate_elderly_disabled_household:
        basis = (
            f"Because your household is counted separately as an elderly or "
            f"disabled household, the limit for {who} is ${limit:,.2f} a month."
        )
    else:
        # Names the elderly/disabled answer back even though it was a
        # "no": the user answered that question a moment ago, and a
        # verdict that never mentions it reads as though the answer went
        # nowhere. It also states which limit applies and why.
        basis = (
            f"Since no one in your household is 60 or older or has a "
            f"disability, the limit North Carolina uses for {who} is "
            f"${limit:,.2f} a month."
        )

    comparison = f"Your income is ${income_considered:,.2f}."
    if outcome == "likely_eligible":
        judgement = "That is under the limit, so you likely qualify."
    elif outcome == "likely_eligible_gross_pending_net":
        judgement = (
            "That is under the limit, so you likely qualify so far. Costs like "
            "rent, childcare, or medical bills could still change the result — "
            "tell me about them and I can factor them in."
        )
    elif outcome == "uncertain_apply_anyway":
        judgement = (
            "That is over the limit, but costs like rent, childcare, or medical "
            "bills are subtracted before the final decision, so you may still "
            "qualify. It is worth applying."
        )
    else:
        judgement = (
            "That is over the limit, so you likely do not qualify on income "
            "alone."
        )

    if income_considered == limit:
        judgement = "That is exactly at the limit, which still qualifies."

    plain_notes = f"{basis} {comparison} {judgement}"

    return {
        "qualifies": qualifies,
        "test_used": test_used,
        "limit": limit,
        "expanded_limit": expanded_limit,
        "bbce_limit": bbce_limit,
        "income_considered": income_considered,
        "household_size": household_size,
        "outcome": outcome,
        "deductions": deductions,
        "notes": notes,
        "plain_notes": plain_notes,
    }


__all__ = [
    "calculate_eligibility",
    "compute_net_monthly_income",
    "income_limits",
    "max_household_size",
    "separate_household_limits",
    "_POVERTY_GUIDELINES_SOURCE_URL",
    "_SEPARATE_HOUSEHOLD_LIMITS_2027",
]
