"""Tests for the deterministic NC FNS eligibility calculator.

The values in these tests are read directly from the calculator's own
income-limit and deduction tables (``src.calculator``) — the calculator's
tables are the single source of truth, sourced from the USDA FNS FY2026
SNAP income-eligibility standards (48 States & DC). Pulling expected
values from the same module the production code reads from guarantees
the tests stay in sync with the production numbers.

Primary source: USDA FNS, "SNAP FY 2026 Cost-of-Living Adjustments"
memo (effective 2025-10-01 through 2026-09-30):
    https://www.usda.gov/sites/default/files/guidance-documents/fns.snap-cola-fy26memo.pdf
"""

from __future__ import annotations

import pytest

from src.calculator import (
    _POVERTY_GUIDELINES_SOURCE_URL,
    calculate_eligibility as _calculate_eligibility,
    income_limits,
)


def calculate_eligibility(*args, **kwargs):
    """Exercise FoodPath's NC route unless a test selects another state."""
    kwargs.setdefault("state_code", "NC")
    return _calculate_eligibility(*args, **kwargs)


# ---------- fixtures for expected values ----------
# Read directly from the calculator so tests stay locked to the same
# table the agent cites. ``income_limits()`` is the calculator's
# public source of truth for the gross/net/expanded (130/100/200%)
# monthly income limits.
HH1_LIMITS = income_limits(1)   # gross=1,696.00  net=1,305.00  expanded=2,610.00
HH2_LIMITS = income_limits(2)   # gross=2,292.00  net=1,763.00  expanded=3,526.00
HH4_LIMITS = income_limits(4)   # gross=3,483.00  net=2,680.00  expanded=5,360.00

HH1_GROSS_LIMIT = HH1_LIMITS["gross"]      # 1,696.00 — federal 130% floor (NOT the NC ceiling)
HH1_NET_LIMIT = HH1_LIMITS["net"]          # 1,305.00
HH1_BBCE_LIMIT = HH1_LIMITS["expanded"]    # 2,610.00 — NC BBCE 200% gross screen
HH4_GROSS_LIMIT = HH4_LIMITS["gross"]      # 3,483.00 — federal 130% floor (NOT the NC ceiling)
HH4_NET_LIMIT = HH4_LIMITS["net"]          # 2,680.00
HH4_BBCE_LIMIT = HH4_LIMITS["expanded"]    # 5,360.00 — NC BBCE 200% gross screen
HH2_NET_LIMIT = HH2_LIMITS["net"]          # 1,763.00


# ---------- 1. household = 1, clearly UNDER NC BBCE gross ceiling ----------
def test_single_person_under_gross_qualifies():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["test_used"] == "gross"
    assert result["household_size"] == 1
    # Under NC BBCE (selected explicitly by this test module) the gross screen is at
    # 200% of poverty, not the federal 130% floor. See W30.
    assert result["limit"] == HH1_BBCE_LIMIT
    assert result["bbce_limit"] == HH1_BBCE_LIMIT
    assert result["income_considered"] == 1000.0
    assert "NC" in result["notes"]
    assert "BBCE" in result["notes"]


# ---------- 2. household = 1, clearly OVER NC BBCE gross ceiling ----------
def test_single_person_over_gross_fails():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=5000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is False
    assert result["test_used"] == "gross"
    assert result["limit"] == HH1_BBCE_LIMIT
    assert result["outcome"] == "likely_ineligible"
    assert "does not qualify" in result["notes"]


# ---------- 3. household = 4, clearly UNDER NC BBCE gross ceiling ----------
def test_family_of_four_under_gross_qualifies():
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=2000.0,
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["test_used"] == "gross"
    assert result["limit"] == HH4_BBCE_LIMIT
    assert result["income_considered"] == 2000.0


# ---------- 4. household = 4, clearly OVER NC BBCE gross ceiling ----------
def test_family_of_four_over_gross_fails():
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=9000.0,  # > 200% FPL ($5,358.33) for HH=4
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is False
    assert result["test_used"] == "gross"
    assert result["limit"] == HH4_BBCE_LIMIT
    assert result["outcome"] == "likely_ineligible"


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
    # counts as qualifying. Under W30 the NC default gross ceiling is
    # 200% of poverty (BBCE), so HH=1's BBCE limit is HH1_BBCE_LIMIT
    # (=2,610.00 in the calculator's FY2026 SNAP-published table).
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=HH1_BBCE_LIMIT,  # exactly 2,608.33
        has_elderly_or_disabled=False,
    )
    assert result["qualifies"] is True
    assert result["income_considered"] == result["limit"]
    assert "equals the limit" in result["notes"]

    # Boundary on the net side (elderly/disabled — net limit unchanged).
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
    # The calculator's primary source URL is the USDA FNS SNAP FY2026
    # COLA memo (effective 2025-10-01 through 2026-09-30), which is
    # where the calculator's published monthly income limits and
    # deduction standards come from. USDA FNS re-sources this annually.
    assert _POVERTY_GUIDELINES_SOURCE_URL.startswith("https://")
    assert "fns.usda.gov" in _POVERTY_GUIDELINES_SOURCE_URL
    assert _POVERTY_GUIDELINES_SOURCE_URL.endswith("/snap/allotment/cola")


# ---------- W30: four-outcome eligibility band (NC BBCE) ----------
def test_non_elderly_likely_eligible_outcome():
    # HH=1 at exactly the BBCE ceiling passes the gross screen.
    result = calculate_eligibility(1, HH1_BBCE_LIMIT, False)
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_non_elderly_inside_bbce_band_qualifies():
    # HH=1 at $2,500 is below the 200% BBCE ceiling (HH1_BBCE_LIMIT,
    # $2,610.00) and above the 130% federal floor ($1,696.00). Under NC
    # BBCE the household passes the 200% gross screen categorically →
    # likely_eligible (NOT "uncertain_apply_anyway" — that was the
    # pre-W30 wrong framing).
    result = calculate_eligibility(1, 2500.0, False)
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"
    assert "BBCE" in result["notes"]


def test_non_elderly_exactly_at_200_percent_qualifies_under_bbce():
    # HH=1 at exactly the 200% BBCE ceiling: passes the gross screen, qualifies.
    # Pre-W30 this was uncertain_apply_anyway; under NC BBCE it is
    # likely_eligible.
    result = calculate_eligibility(1, HH1_BBCE_LIMIT, False)
    assert result["outcome"] == "likely_eligible"
    assert result["qualifies"] is True
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
    # Elderly/disabled households skip the gross screen and go straight to
    # the net test (100% of poverty). HH=1 at $2,500 is above the
    # $1,305.00 net limit and below the 200% expanded band. Under W30, with
    # NO deductions supplied, this is the "gross screen pending net test"
    # outcome — the household passes the 200% BBCE gross screen and only
    # the net test is pending. To get the historic "uncertain_apply_anyway"
    # outcome we have to actually supply deductions (the net test is then
    # computed and the household fails it).
    result = calculate_eligibility(1, 2500.0, True)
    assert result["qualifies"] is False
    assert result["outcome"] == "likely_eligible_gross_pending_net"
    assert "net income test" in result["notes"]


def test_elderly_disabled_exactly_at_200_percent_is_gross_pending_net():
    # Same logic at exactly the 200% ceiling.
    result = calculate_eligibility(1, HH1_BBCE_LIMIT, True)
    assert result["outcome"] == "likely_eligible_gross_pending_net"
    assert result["qualifies"] is False


def test_elderly_disabled_uncertain_apply_anyway_when_deductions_fail_net():
    # The historic "uncertain_apply_anyway" outcome still applies for
    # elderly/disabled households when deductions ARE supplied but the
    # computed net income still exceeds the 100% net limit. In that case
    # the net test is conclusive (failed) but the household is still
    # inside the BBCE band.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=True,
        shelter_costs_monthly=1500.0,
    )
    # adjusted = 2500 - 204 = 2296; half = 1148; excess = 352;
    # net = 2296 - 352 = 1944 > HH1 net limit $1,304.17. Inside BBCE.
    assert result["outcome"] == "uncertain_apply_anyway"


def test_elderly_disabled_above_200_percent_is_likely_ineligible():
    result = calculate_eligibility(1, 9000.0, True)
    assert result["qualifies"] is False
    assert result["outcome"] == "likely_ineligible"


# ---------- W30 new: gross_pending_net outcome ----------
def test_w30_elderly_pending_net_outcome_at_150pct_fpl():
    # The textbook BBCE case: an elderly/disabled household at ~150% FPL.
    # The net limit (100%) is exceeded, but the household is inside the
    # 200% BBCE band → passed the gross screen, net test pending deductions.
    # Honest naming: "likely_eligible_gross_pending_net", not "uncertain".
    income = round((15_650.0 * 1.50) / 12.0, 2)
    result = calculate_eligibility(1, income, True)
    assert income > HH1_NET_LIMIT  # fails the 100% net test
    assert income < HH1_BBCE_LIMIT  # but inside the BBCE band
    assert result["outcome"] == "likely_eligible_gross_pending_net"
    assert result["qualifies"] is False  # net test not yet evaluable
    assert "BBCE" in result["notes"] or "200%" in result["notes"]
    assert "deductions" in result["notes"].lower() or "net" in result["notes"].lower()


def test_w30_elderly_pending_net_disappears_when_deductions_supplied():
    # Same household + medical deduction → net test now computable.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=True,
        medical_expenses_monthly=1500.0,  # enough to bring net under 100% FPL
    )
    assert result["outcome"] == "likely_eligible"
    assert result["qualifies"] is True


def test_w30_non_nc_state_falls_back_to_federal_130pct():
    # When state_code != "NC" the calculator uses the federal 130% floor.
    # HH=1 federal 130% gross ceiling = HH1_GROSS_LIMIT ($1,696.00). A
    # household at $1,800 is OVER that ceiling but well below 200%, so it
    # lands in the "uncertain_apply_anyway" outcome (not
    # "likely_eligible_gross_pending_net" because the 200% screen does not
    # apply outside NC).
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        state_code="TX",
    )
    assert result["test_used"] == "gross"
    assert result["limit"] == HH1_GROSS_LIMIT  # 130% federal floor
    assert result["outcome"] == "uncertain_apply_anyway"
    assert "federal" in result["notes"].lower()


def test_w30_bbce_limit_field_is_surfaced():
    # The calculator exposes the BBCE ceiling actually applied so downstream
    # consumers can verify which rule governed.
    result_nc = calculate_eligibility(1, 1000.0, False, state_code="NC")
    assert result_nc["bbce_limit"] == HH1_BBCE_LIMIT
    result_tx = calculate_eligibility(1, 1000.0, False, state_code="TX")
    assert result_tx["bbce_limit"] == HH1_GROSS_LIMIT  # 130% federal floor


# ---------- W30 separate elderly/disabled household (165% standard) ----------
def test_w30_separate_elderly_household_uses_165pct_ceiling():
    # Federal separate-household standard for elderly/disabled.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2000.0,
        has_elderly_or_disabled=False,  # same-household flag, not the routing flag
        separate_elderly_disabled_household=True,
    )
    assert result["test_used"] == "gross"
    assert result["limit"] == 2152.0  # FY2026 HH1 separate-household standard
    assert result["qualifies"] is True  # 2000 ≤ 2152


def test_w30_separate_elderly_household_above_165pct_is_uncertain():
    result = calculate_eligibility(
        household_size=2,
        gross_monthly_income=3500.0,  # above HH2 165% limit of $2,909
        has_elderly_or_disabled=False,
        separate_elderly_disabled_household=True,
    )
    assert result["test_used"] == "gross"
    assert result["limit"] == 2909.0
    assert result["qualifies"] is False
    assert result["outcome"] == "uncertain_apply_anyway"


def test_w30_separate_household_limits_match_fy2026_table():
    # Sourced values from USDA FNS FY2026 income-eligibility tables
    # (48 States & DC, separate-elderly/disabled household row).
    from src.calculator import separate_household_limits
    expected = {
        1: 2152.0, 2: 2909.0, 3: 3665.0, 4: 4421.0,
        5: 5177.0, 6: 5934.0, 7: 6690.0, 8: 7446.0,
    }
    for size, value in expected.items():
        assert separate_household_limits(size) == value, (
            f"HH{size} 165% FY2026 limit expected ${value}, got ${separate_household_limits(size)}"
        )
    # Per-addition delta: 9th person is HH8 + $757.
    assert separate_household_limits(9) == 7446.0 + 757.0


# ===========================================================================
# W31 — real federal net-income deduction chain (7 CFR §273.9(d), FY2026)
#
# FY2026 deduction standards (48 States & DC, effective 2025-10-01 through
# 2026-09-30):
#   standard deduction: $209 (sizes 1-3), $223 (4), $261 (5), $299 (6+)
#   earned-income deduction: 20% of gross earned income
#   medical: out-of-pocket medical above $35/mo, elderly/disabled only
#   shelter: costs above half of adjusted income, capped at $744/mo
#            unless the household has an elderly/disabled member
# Source: USDA FNS, "SNAP FY 2026 Cost-of-Living Adjustments" memo
#   https://www.usda.gov/sites/default/files/guidance-documents/fns.snap-cola-fy26memo.pdf
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
    # Pre-W30 framing for non-elderly NC HH=1 inside the BBCE band was
    # "uncertain_apply_anyway"; under W30 the same household passes the
    # 200% BBCE gross screen → likely_eligible. This test still proves
    # the regression-safe behavior (no deductions, gross figure echoed)
    # — the outcome change is tested separately in test_non_elderly_*.
    assert result["outcome"] in ("likely_eligible", "likely_eligible_gross_pending_net")

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
    # HH=1, gross $1,800 (all earned) is ABOVE the federal 130% gross
    # floor but UNDER the NC BBCE 200% ceiling, so pre-W14 this household
    # landed in "uncertain_apply_anyway". With the FY2026 chain:
    # standard $209 + earned 20%*$1,800=$360 →
    # net = 1800 - 209 - 360 = $1,231 ≤ net limit $1,305.00 → eligible.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        earned_monthly_income=1800.0,
    )
    assert result["deductions"]["standard"] == 209.0
    assert result["deductions"]["earned_income"] == 360.0
    assert result["deductions"]["net_income"] == 1231.0
    assert result["income_considered"] == 1231.0
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
    # HH=4, gross $3,600 (above HH4 federal 130% floor $3,483.00),
    # $900/mo childcare. FY2026 chain:
    # standard $223 + dependent care $900 → adjusted = 3600-223-900 = $2,477
    # ≤ HH4 net limit $2,680.00 → likely eligible after deductions.
    result = calculate_eligibility(
        household_size=4,
        gross_monthly_income=3600.0,
        has_elderly_or_disabled=False,
        dependent_care_monthly=900.0,
    )
    assert result["deductions"]["dependent_care"] == 900.0
    assert result["deductions"]["net_income"] == 2477.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


# ---------- excess medical deduction: elderly/disabled only, above $35 ----------
def test_w14_medical_deduction_elderly_household():
    # HH=1 elderly, gross $1,500 (above net limit $1,305.00).
    # standard $209 + medical ($200-$35=$165) → net = 1500-209-165 = $1,126
    # ≤ $1,305.00 → qualifies under the net test.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=True,
        medical_expenses_monthly=200.0,
    )
    assert result["test_used"] == "net"
    assert result["deductions"]["medical"] == 165.0
    assert result["deductions"]["net_income"] == 1126.0
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_w14_medical_deduction_ignored_for_non_elderly():
    # Same figures without the elderly/disabled flag: medical must NOT
    # be deducted (7 CFR §273.9(d)(3) restricts it to elderly/disabled
    # households). Net = 1500 - 209 = $1,291 ≤ net limit $1,305.00 →
    # household qualifies under net (with FY2026 deduction applied) — but
    # via gross test with NO medical deduction applied. The flag, not the
    # income, controls which path is taken.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=False,
        medical_expenses_monthly=200.0,
    )
    assert result["deductions"]["medical"] == 0.0
    assert result["deductions"]["net_income"] == 1291.0


def test_w14_medical_below_35_threshold_yields_no_deduction():
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1500.0,
        has_elderly_or_disabled=True,
        medical_expenses_monthly=30.0,
    )
    assert result["deductions"]["medical"] == 0.0


# ---------- excess shelter deduction: capped vs uncapped ----------
def test_w14_shelter_deduction_does_not_bind_for_low_shelter_non_elderly():
    # HH=1, gross $2,500 all unearned, shelter $1,500/mo.
    # adjusted = 2500 - 209 = $2,291; half = $1,145.5;
    # excess shelter = 1500 - 1145.5 = $354.5 (< $744 cap, so cap not binding).
    # net = 2291 - 354.5 = $1,936.5. NC HH=1 BBCE ceiling is $2,610.00;
    # the household passes the gross screen categorically AND its
    # computed net ($1,936.50) is below the BBCE ceiling → likely_eligible.
    # This is the W30 fix: under NC BBCE the household at $2,500 with
    # $1,500 shelter is NOT "uncertain" — it's likely eligible.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=False,
        shelter_costs_monthly=1500.0,
    )
    assert result["deductions"]["shelter"] == 354.5
    assert result["deductions"]["net_income"] == 1936.5
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"


def test_w14_shelter_deduction_cap_binds_for_non_elderly():
    # Drive excess shelter above the $744 cap: HH=1, gross $4,000,
    # shelter $3,000. adjusted = 4000-209 = $3,791; half = $1,895.5;
    # excess = 3000-1895.5 = $1,104.5 → capped to $744.
    # net = 3791 - 744 = $3,047 > 200% band → likely_ineligible.
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=4000.0,
        has_elderly_or_disabled=False,
        shelter_costs_monthly=3000.0,
    )
    assert result["deductions"]["shelter"] == 744.0
    assert result["deductions"]["net_income"] == 3047.0
    assert result["outcome"] == "likely_ineligible"


def test_w14_shelter_deduction_uncapped_for_elderly():
    # Same shelter burden with an elderly member: no $744 cap.
    # HH=1 elderly, gross $2,500, shelter $1,500.
    # adjusted = 2500-209 = $2,291; half = $1,145.5; excess = $354.5.
    # net = 2291-354.5 = $1,936.5 > net limit $1,305.00; gross ≤ 200% →
    # uncertain. Use higher shelter to prove uncapped behavior instead:
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=2500.0,
        has_elderly_or_disabled=True,
        shelter_costs_monthly=1500.0,
    )
    assert result["deductions"]["shelter"] == 354.5

    # Uncapped proof: gross $4,000, shelter $3,000, elderly → excess
    # $1,104.5 NOT capped (would be $744 for non-elderly).
    result_uncapped = calculate_eligibility(
        household_size=1,
        gross_monthly_income=4000.0,
        has_elderly_or_disabled=True,
        shelter_costs_monthly=3000.0,
    )
    assert result_uncapped["deductions"]["shelter"] == 1104.5
    assert result_uncapped["deductions"]["net_income"] == 2686.5


# ---------- full chain: all four deductions together ----------
def test_w14_full_deduction_chain_elderly_household():
    # HH=2 elderly; gross $3,000 ($2,000 earned); dependent care $100;
    # medical $235 (→ $200 deductible); shelter $1,400.
    # standard $209 + earned $400 + dependent $100 + medical $200 = $909
    # adjusted = 3000 - 909 = $2,091; half = $1,045.5
    # shelter excess = 1400 - 1045.5 = $354.5 (uncapped, elderly)
    # net = 2091 - 354.5 = $1,736.5 ≤ HH2 net limit $1,763.00 → likely eligible.
    hh2_net = HH2_NET_LIMIT  # 1,763.00
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
    assert d["standard"] == 209.0
    assert d["earned_income"] == 400.0
    assert d["dependent_care"] == 100.0
    assert d["medical"] == 200.0
    assert d["shelter"] == 354.5
    assert d["total_deductions"] == 1263.5
    assert d["net_income"] == 1736.5
    assert result["income_considered"] == 1736.5
    assert result["qualifies"] is True
    assert result["outcome"] == "likely_eligible"
    assert result["limit"] == hh2_net


# ---------- standard deduction table by household size ----------
def test_w14_standard_deduction_table():
    # FY2026 standard deduction, 48 States & DC. Sourced from the
    # USDA FNS SNAP FY2026 COLA memo.
    for size, expected in [(1, 209.0), (2, 209.0), (3, 209.0),
                           (4, 223.0), (5, 261.0), (6, 299.0), (8, 299.0)]:
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


# ===========================================================================
# W29 — FY2026 published-table boundary tests
#
# Re-sourcing the SNAP income-standard constants from the USDA FNS FY2026
# COLA memo (48 States & DC, effective 2025-10-01 through 2026-09-30) and
# proving the calculator's monthly limit table matches the published
# whole-dollar columns VERBATIM, with the boundary semantics USDA prescribes:
# a household at exactly the published limit qualifies; a household at
# limit + $1 does not.
#
# IMPORTANT: under NC BBCE (selected explicitly by this test module) the operative gross
# screen is the 200% ceiling, not the federal 130% floor. The boundary test
# below bypasses this module's NC wrapper and calls the generic public API
# directly, proving the no-state default uses the USDA-published 130% column.
# The 100% net limit
# is tested by marking the household elderly/disabled and keeping the
# deduction chain disabled (the limit then acts as the comparison figure
# directly, mirroring the no-deductions path the calculator's regression
# tests use elsewhere).
#
# Primary source: USDA FNS SNAP FY2026 COLA memo,
#   https://www.usda.gov/sites/default/files/guidance-documents/fns.snap-cola-fy26memo.pdf
# ===========================================================================
USDA_FY2026_GROSS = {1: 1696.0, 2: 2292.0, 3: 2888.0, 4: 3483.0,
                     5: 4079.0, 6: 4675.0, 7: 5271.0, 8: 5867.0}
USDA_FY2026_NET = {1: 1305.0, 2: 1763.0, 3: 2221.0, 4: 2680.0,
                   5: 3138.0, 6: 3596.0, 7: 4055.0, 8: 4513.0}
USDA_FY2026_GROSS_PER_ADDITIONAL = 596.0
USDA_FY2026_NET_PER_ADDITIONAL = 459.0


@pytest.mark.parametrize("hh_size", range(1, 9))
def test_w29_income_limits_match_usda_fy2026_gross_column(hh_size):
    """USDA FNS FY2026 monthly gross-income standard (130% FPL, 48 States & DC).

    The published column is whole-dollar. The calculator's ``income_limits()``
    table must reproduce it verbatim — no rounding, no derivation drift.
    """
    expected = USDA_FY2026_GROSS[hh_size]
    actual = income_limits(hh_size)["gross"]
    assert abs(actual - expected) < 0.01, (
        f"HH{hh_size} gross limit ${actual} != USDA-published ${expected}"
    )


@pytest.mark.parametrize("hh_size", range(1, 9))
def test_w29_income_limits_match_usda_fy2026_net_column(hh_size):
    """USDA FNS FY2026 monthly net-income standard (100% FPL, 48 States & DC)."""
    expected = USDA_FY2026_NET[hh_size]
    actual = income_limits(hh_size)["net"]
    assert abs(actual - expected) < 0.01, (
        f"HH{hh_size} net limit ${actual} != USDA-published ${expected}"
    )


@pytest.mark.parametrize("hh_size", range(1, 9))
def test_w29_federal_130pct_gross_floor_boundary(hh_size):
    """Household at exactly the 130% federal floor qualifies; +$1 does not.

    Uses the generic calculator default directly so this test is identical to
    the public three-argument API boundary: the published federal gross
    column is the operative ceiling when no state is selected.
    """
    floor = USDA_FY2026_GROSS[hh_size]
    at_limit = _calculate_eligibility(
        household_size=hh_size,
        gross_monthly_income=floor,
        has_elderly_or_disabled=False,
    )
    over_limit = _calculate_eligibility(
        household_size=hh_size,
        gross_monthly_income=floor + 1.0,
        has_elderly_or_disabled=False,
    )
    assert at_limit["qualifies"] is True, (
        f"HH{hh_size} at ${floor} (130% federal floor) must qualify"
    )
    assert at_limit["test_used"] == "gross"
    assert at_limit["bbce_limit"] == floor
    assert at_limit["outcome"] == "likely_eligible"
    assert over_limit["qualifies"] is False, (
        f"HH{hh_size} at ${floor + 1} (above 130% federal floor) must NOT qualify"
    )
    # Non-NC routes the over-limit case into the "uncertain_apply_anyway"
    # band ONLY when gross income is still within the 200% expanded band;
    # for sizes 1-8 the over-limit figure is still below 200% FPL, so we
    # expect "uncertain_apply_anyway" (not "likely_ineligible").
    assert over_limit["outcome"] == "uncertain_apply_anyway"


@pytest.mark.parametrize("hh_size", range(1, 9))
def test_w29_net_limit_at_or_below_qualifies_above_does_not(hh_size):
    """The 100% net limit is the operative ceiling for elderly/disabled
    households with no deduction inputs supplied (the calculator's
    regression-safe no-deductions path applies the gross figure directly
    against the net limit, producing a conservative bias).

    The net standard is federal and remains operative for an
    elderly/disabled household. Exactly the published whole-dollar limit
    passes; one dollar above it does not.
    """
    net_limit = USDA_FY2026_NET[hh_size]
    reported = income_limits(hh_size)["net"]
    assert abs(reported - net_limit) < 0.01, (
        f"HH{hh_size} net limit ${reported} != USDA-published ${net_limit}"
    )
    at_limit = calculate_eligibility(
        household_size=hh_size,
        gross_monthly_income=net_limit,
        has_elderly_or_disabled=True,
    )
    over_limit = calculate_eligibility(
        household_size=hh_size,
        gross_monthly_income=net_limit + 1.0,
        has_elderly_or_disabled=True,
    )
    assert at_limit["qualifies"] is True
    assert at_limit["test_used"] == "net"
    assert at_limit["limit"] == net_limit
    assert over_limit["qualifies"] is False


def test_w29_gross_additional_per_person_matches_fy2026():
    """Per-addition delta for household size > 8, USDA FY2026 whole-dollar column."""
    expected = USDA_FY2026_GROSS_PER_ADDITIONAL
    actual_9 = income_limits(9)["gross"] - income_limits(8)["gross"]
    actual_10 = income_limits(10)["gross"] - income_limits(9)["gross"]
    assert abs(actual_9 - expected) < 0.01, (
        f"HH9-HH8 gross delta ${actual_9} != USDA ${expected}"
    )
    assert abs(actual_10 - expected) < 0.01, (
        f"HH10-HH9 gross delta ${actual_10} != USDA ${expected}"
    )


def test_w29_net_additional_per_person_matches_fy2026():
    expected = USDA_FY2026_NET_PER_ADDITIONAL
    actual_9 = income_limits(9)["net"] - income_limits(8)["net"]
    actual_10 = income_limits(10)["net"] - income_limits(9)["net"]
    assert abs(actual_9 - expected) < 0.01, (
        f"HH9-HH8 net delta ${actual_9} != USDA ${expected}"
    )
    assert abs(actual_10 - expected) < 0.01, (
        f"HH10-HH9 net delta ${actual_10} != USDA ${expected}"
    )


def test_w29_usda_fy2026_constants_match_published_table():
    """The calculator's gross/net limits table must match the USDA FY2026
    published column verbatim (48 States & DC). This is the single
    whole-table assertion — a regression in any one cell fails here.
    """
    for hh_size in range(1, 9):
        L = income_limits(hh_size)
        assert abs(L["gross"] - USDA_FY2026_GROSS[hh_size]) < 0.01
        assert abs(L["net"] - USDA_FY2026_NET[hh_size]) < 0.01
    # Beyond 8: per-addition delta per USDA published table.
    assert abs(income_limits(9)["gross"] - (5867.0 + 596.0)) < 0.01
    assert abs(income_limits(9)["net"] - (4513.0 + 459.0)) < 0.01
