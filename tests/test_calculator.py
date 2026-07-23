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
