"""Tests for the deterministic NC FNS eligibility calculator.

The values in these tests are derived directly from the HHS 2025 Federal
Poverty Guidelines via the multipliers in ``src/calculator.py`` (130%
for the gross test, 100% for the net test). If the poverty guidelines
change for a new program year, update both the calculator and these
expected values.

Reference: https://aspe.hhs.gov/2025-poverty-guidelines
"""

from __future__ import annotations

import pytest

from src.calculator import (
    _POVERTY_GUIDELINES_SOURCE_URL,
    calculate_eligibility,
)


# ---------- fixtures for expected values ----------
# (Calculated from the published 2025 HHS poverty guidelines so the
#  expected values stay auditable. If you change the base figures,
#  change these too — and add a comment explaining when/why.)
HH1_GROSS_LIMIT = round((15_650.0 * 1.30) / 12.0, 2)   # 1,695.42
HH1_NET_LIMIT = round((15_650.0 * 1.00) / 12.0, 2)      # 1,304.17
HH4_GROSS_LIMIT = round((32_150.0 * 1.30) / 12.0, 2)   # 3,482.92
HH4_NET_LIMIT = round((32_150.0 * 1.00) / 12.0, 2)      # 2,679.17


# ---------- 1. household = 1, clearly UNDER gross limit ----------
def test_single_person_under_gross_qualifies():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["test_used"] == "gross"
    assert result["household_size"] == 1
    assert result["limit"] == HH1_GROSS_LIMIT
    assert result["income_considered"] == 1000.0
    assert "gross income test" in result["notes"]


# ---------- 2. household = 1, clearly OVER gross limit ----------
def test_single_person_over_gross_fails():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=5000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is False
    assert result["test_used"] == "gross"
    assert result["limit"] == HH1_GROSS_LIMIT
    assert "does not qualify" in result["notes"]


# ---------- 3. household = 4, clearly UNDER gross limit ----------
def test_family_of_four_under_gross_qualifies():
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=2000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["test_used"] == "gross"
    assert result["limit"] == HH4_GROSS_LIMIT
    assert result["income_considered"] == 2000.0


# ---------- 4. household = 4, clearly OVER gross limit ----------
def test_family_of_four_over_gross_fails():
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=5000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is False
    assert result["test_used"] == "gross"
    assert result["limit"] == HH4_GROSS_LIMIT


# ---------- 5. elderly/disabled household is routed through NET test ----------
def test_elderly_or_disabled_uses_net_test():
    # For HH=1, $1500/mo would QUALIFY under the gross test (130% = $1,695.42)
    # but must FAIL under the net test (100% = $1,304.17). This case proves
    # the elderly/disabled flag actually selects a different test and limit.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=True,
    )
    assert result["test_used"] == "net"
    assert result["limit"] == HH1_NET_LIMIT
    assert result["qualifies"] is False
    assert "net income test" in result["notes"]

    # And the symmetric case: same household with income BELOW the net
    # limit (but still below the gross limit) should qualify under net.
    result_qualifies = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1000.0,
        has_elderly_or_disabled=True,
    )
    assert result_qualifies["test_used"] == "net"
    assert result_qualifies["qualifies"] is True


# ---------- 6. boundary case: income exactly equals the limit ----------
def test_income_exactly_at_limit_qualifies():
    # Policy decision (documented in calculator.py): income == limit
    # counts as qualifying. This matches the at-or-below reading of the
    # 130% / 100% language in 7 CFR §273.9(a).
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=HH1_GROSS_LIMIT,  # exactly 1,695.42
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["income_considered"] == result["limit"]
    assert "equals the limit" in result["notes"]

    # Boundary on the net side too.
    result_net = calculate_eligibility(
        household_size=4,
        gross_monthly_income=HH4_NET_LIMIT,  # exactly 2,679.17
        has_elderly_or_disabled=True,
    )
    assert result_net["test_used"] == "net"
    assert result_net["qualifies"] is True
    assert result_net["income_considered"] == result_net["limit"]


# ---------- bonus: invalid inputs are rejected ----------
def test_invalid_household_size_rejected():
    with pytest.raises(ValueError):
        calculate_eligibility(
            household_size=0,
            gross_monthly_income=1000.0,
            has_elderly_or_disabled=False,
        )


def test_negative_income_rejected():
    with pytest.raises(ValueError):
        calculate_eligibility(
            household_size=1,
            gross_monthly_income=-1.0,
            has_elderly_or_disabled=False,
        )


# ---------- source URL is exported (and so independently verifiable) ----------
def test_source_url_is_a_real_https_link():
    assert _POVERTY_GUIDELINES_SOURCE_URL.startswith("https://")
    assert "aspe.hhs.gov" in _POVERTY_GUIDELINES_SOURCE_URL


# ---------- three-outcome eligibility band (130-200% expanded band) ----------
def test_non_elderly_likely_eligible_outcome():
    result = calculate_eligibility(1, HH1_GROSS_LIMIT, False)
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_non_elderly_uncertain_apply_anyway_outcome():
    result = calculate_eligibility(1, 2500.0, False)
    assert result["qualifies"] is False
    assert result["outcome"] == "uncertain_apply_anyway"
    assert "allowable deductions" in result["notes"]
    assert "NC ePASS" in result["notes"]


def test_non_elderly_exactly_at_200_percent_is_uncertain():
    result = calculate_eligibility(1, 2_608.33, False)
    assert result["outcome"] == "uncertain_apply_anyway"
    assert result["qualifies"] is False
    assert result["income_considered"] == result["expanded_limit"]


def test_non_elderly_above_200_percent_is_likely_ineligible():
    result = calculate_eligibility(1, 9000.0, False)
    assert result["qualifies"] is False
    assert result["outcome"] == "likely_ineligible"


def test_elderly_disabled_likely_eligible_outcome():
    result = calculate_eligibility(1, HH1_NET_LIMIT, True)
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_elderly_disabled_uncertain_apply_anyway_outcome():
    result = calculate_eligibility(1, 2500.0, True)
    assert result["qualifies"] is False
    assert result["outcome"] == "uncertain_apply_anyway"
    assert "net income test" in result["notes"]


def test_elderly_disabled_exactly_at_200_percent_is_uncertain():
    result = calculate_eligibility(1, 2_608.33, True)
    assert result["outcome"] == "uncertain_apply_anyway"
    assert result["qualifies"] is False
    assert result["income_considered"] == result["expanded_limit"]


def test_elderly_disabled_above_200_percent_is_likely_ineligible():
    result = calculate_eligibility(1, 9000.0, True)
    assert result["qualifies"] is False
    assert result["outcome"] == "likely_ineligible"


# ===========================================================================
# W14 — real federal net-income deduction chain (7 CFR §273.9(d), FY2025)
#
# FY2025 deduction standards (48 States & DC, effective 2024-10-01):
#   standard deduction: $204 (sizes 1-3), $217 (4), $254 (5), $291 (6+)
#   earned-income deduction: 20% of gross earned income
#   medical: out-of-pocket medical above $35/mo, elderly/disabled only
#   shelter: costs above half of adjusted income, capped at $712/mo
#            unless the household has an elderly/disabled member
# Source: https://www.fns.usda.gov/snap/fy-2025-cola
# ===========================================================================

from src.calculator import compute_net_monthly_income


# ---------- regression safety: no deduction inputs → pre-W14 behavior ----------
def test_w14_no_deduction_inputs_is_regression_safe():
    """A household with none of the new fields set must produce output
    identical to the pre-W14 calculator: income_considered is the gross
    figure, no deduction breakdown is attached, and the widened 130-200%
    band applies unchanged."""
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=False,
    )
    assert result["income_considered"] == 2500.0
    assert result["deductions"] is None
    assert result["qualifies"] is False
    assert result["outcome"] == "uncertain_apply_anyway"

    # compute_net_monthly_income with zeroed inputs is a pass-through.
    chain = compute_net_monthly_income(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=False,
    )
    assert chain["net_income"] == 2500.0
    assert chain["total_deductions"] == 0.0


# ---------- earned-income deduction: 20% of earned income ----------
def test_w14_earned_income_deduction_moves_household_into_eligibility():
    # HH=1, gross $1,800 (all earned) is ABOVE the gross limit $1,695.42,
    # so pre-W14 this household landed in "uncertain_apply_anyway".
    # With the chain: standard $204 + earned 20%*$1,800=$360 →
    # net = 1800 - 204 - 360 = $1,236 ≤ net limit $1,304.17 → eligible.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        earned_monthly_income=1800.0,
    )
    assert result["deductions"]["standard"] == 204.0
    assert result["deductions"]["earned_income"] == 360.0
    assert result["deductions"]["net_income"] == 1236.0
    assert result["income_considered"] == 1236.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"
    assert "earned-income" in result["notes"]


def test_w14_earned_income_cannot_exceed_gross():
    with pytest.raises(ValueError):
        calculate_eligibility(
            household_size=1,
            gross_monthly_income=1000.0,
            has_elderly_or_disabled=False,
            earned_monthly_income=1500.0,
        )


# ---------- dependent-care deduction: actual cost, uncapped ----------
def test_w14_dependent_care_deduction():
    # HH=4, gross $3,600 (above gross limit $3,482.92), $900/mo childcare.
    # standard $217 + dependent care $900 → adjusted = 3600-217-900 = $2,483
    # ≤ net limit $2,679.17 → likely eligible after deductions.
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=3600.0,
        has_elderly_or_disabled=False,
        dependent_care_monthly=900.0,
    )
    assert result["deductions"]["dependent_care"] == 900.0
    assert result["deductions"]["net_income"] == 2483.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


# ---------- excess medical deduction: elderly/disabled only, above $35 ----------
def test_w14_medical_deduction_elderly_household():
    # HH=1 elderly, gross $1,500 (above net limit $1,304.17).
    # standard $204 + medical ($200-$35=$165) → net = 1500-204-165 = $1,131
    # ≤ $1,304.17 → qualifies under the net test.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=True,
        medical_expenses_monthly=200.0,
    )
    assert result["test_used"] == "net"
    assert result["deductions"]["medical"] == 165.0
    assert result["deductions"]["net_income"] == 1131.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_w14_medical_deduction_ignored_for_non_elderly():
    # Same figures without the elderly/disabled flag: medical must NOT
    # be deducted (7 CFR §273.9(d)(3) restricts it to elderly/disabled
    # households). Net = 1500 - 204 = $1,296 > ... still under gross
    # limit $1,695.42, so the household qualifies — but via gross test
    # with NO medical deduction applied.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=False,
        medical_expenses_monthly=200.0,
    )
    assert result["deductions"]["medical"] == 0.0
    assert result["deductions"]["net_income"] == 1296.0


def test_w14_medical_below_35_threshold_yields_no_deduction():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=True,
        medical_expenses_monthly=30.0,
    )
    assert result["deductions"]["medical"] == 0.0


# ---------- excess shelter deduction: capped vs uncapped ----------
def test_w14_shelter_deduction_capped_for_non_elderly():
    # HH=1, gross $2,500 all unearned, shelter $1,500/mo.
    # adjusted = 2500 - 204 = $2,296; half = $1,148;
    # excess shelter = 1500 - 1148 = $352 (< $712 cap, so cap not binding).
    # net = 2296 - 352 = $1,944. Still above net limit → but gross
    # $2,500 ≤ 200% ($2,608.33) → uncertain_apply_anyway.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=False,
        shelter_costs_monthly=1500.0,
    )
    assert result["deductions"]["shelter"] == 352.0
    assert result["deductions"]["net_income"] == 1944.0
    assert result["outcome"] == "uncertain_apply_anyway"


def test_w14_shelter_deduction_cap_binds_for_non_elderly():
    # Drive excess shelter above the $712 cap: HH=1, gross $4,000,
    # shelter $3,000. adjusted = 4000-204 = $3,796; half = $1,898;
    # excess = 3000-1898 = $1,102 → capped to $712.
    # net = 3796 - 712 = $3,084 > 200% band → likely_ineligible.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=4000.0,
        has_elderly_or_disabled=False,
        shelter_costs_monthly=3000.0,
    )
    assert result["deductions"]["shelter"] == 712.0
    assert result["deductions"]["net_income"] == 3084.0
    assert result["outcome"] == "likely_ineligible"


def test_w14_shelter_deduction_uncapped_for_elderly():
    # Same shelter burden with an elderly member: no $712 cap.
    # HH=1 elderly, gross $2,500, shelter $1,500.
    # adjusted = 2500-204 = $2,296; half = $1,148; excess = $352.
    # net = 2296-352 = $1,944 > net limit $1,304.17; gross ≤ 200% →
    # uncertain. Use higher shelter to prove uncapped behavior instead:
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=True,
        shelter_costs_monthly=1500.0,
    )
    assert result["deductions"]["shelter"] == 352.0

    # Uncapped proof: gross $4,000, shelter $3,000, elderly → excess
    # $1,102 NOT capped (would be $712 for non-elderly).
    result_uncapped = calculate_eligibility(
        household_size=1,
        gross_monthly_income=4000.0,
        has_elderly_or_disabled=True,
        shelter_costs_monthly=3000.0,
    )
    assert result_uncapped["deductions"]["shelter"] == 1102.0
    assert result_uncapped["deductions"]["net_income"] == 2694.0


# ---------- full chain: all four deductions together ----------
def test_w14_full_deduction_chain_elderly_household():
    # HH=2 elderly; gross $3,000 ($2,000 earned); dependent care $100;
    # medical $235 (→ $200 deductible); shelter $1,400.
    # standard $204 + earned $400 + dependent $100 + medical $200 = $904
    # adjusted = 3000 - 904 = $2,096; half = $1,048
    # shelter excess = 1400 - 1048 = $352 (uncapped, elderly)
    # net = 2096 - 352 = $1,744 ≤ net limit $1,762.50 → likely eligible.
    hh2_net = round((21_150.0 * 1.00) / 12.0, 2)  # 1,762.50
    result = calculate_eligibility(
        household_size=2,
        gross_monthly_income=3000.0,
        has_elderly_or_disabled=True,
        earned_monthly_income=2000.0,
        dependent_care_monthly=100.0,
        medical_expenses_monthly=235.0,
        shelter_costs_monthly=1400.0,
    )
    d = result["deductions"]
    assert d["standard"] == 204.0
    assert d["earned_income"] == 400.0
    assert d["dependent_care"] == 100.0
    assert d["medical"] == 200.0
    assert d["shelter"] == 352.0
    assert d["total_deductions"] == 1256.0
    assert d["net_income"] == 1744.0
    assert result["income_considered"] == 1744.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"
    assert result["limit"] == hh2_net


# ---------- standard deduction table by household size ----------
def test_w14_standard_deduction_table():
    for size, expected in [(1, 204.0), (2, 204.0), (3, 204.0),
                           (4, 217.0), (5, 254.0), (6, 291.0), (8, 291.0)]:
        chain = compute_net_monthly_income(
            household_size=size,
            gross_monthly_income=5000.0,
            has_elderly_or_disabled=False,
            earned_monthly_income=100.0,  # any input triggers the chain
        )
        assert chain["standard"] == expected, f"size {size}"


# ---------- net income never goes negative ----------
def test_w14_net_income_floored_at_zero():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=100.0,
        has_elderly_or_disabled=True,
        earned_monthly_income=100.0,
        shelter_costs_monthly=2000.0,
    )
    assert result["deductions"]["net_income"] == 0.0
    assert result["qualifies"] is True


# ---------- negative deduction inputs rejected ----------
def test_w14_negative_deduction_inputs_rejected():
    for kwargs in (
        {"earned_monthly_income": -1.0},
        {"dependent_care_monthly": -1.0},
        {"medical_expenses_monthly": -1.0},
        {"shelter_costs_monthly": -1.0},
    ):
        with pytest.raises(ValueError):
            calculate_eligibility(
                household_size=1,
                gross_monthly_income=1000.0,
                has_elderly_or_disabled=False,
                **kwargs,
            )
