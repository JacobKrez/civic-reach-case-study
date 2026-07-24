"""W2: the KB income table must match the calculator's own numbers.

The table in knowledge_base/02_income_limits.md is generated from
src.calculator.income_limits (scripts/generate_kb_table.py). This test parses
the committed table and asserts every row equals the calculator, so the
agent's cited numbers can never silently drift from the numbers it computes
with. If it fails, run: python3 scripts/generate_kb_table.py
"""

import re
from pathlib import Path

from src.calculator import calculate_eligibility, income_limits

KB_DIR = Path(__file__).resolve().parents[1] / "knowledge_base"
KB_PATH = KB_DIR / "02_income_limits.md"
DEDUCTIONS_KB_PATH = KB_DIR / "03_deductions_and_household.md"

_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*\$([\d,]+\.\d{2})\s*\|\s*\$([\d,]+\.\d{2})\s*\|\s*\$([\d,]+\.\d{2})\s*\|$"
)


def _money_to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_table_rows() -> dict[int, dict[str, float]]:
    rows: dict[int, dict[str, float]] = {}
    for line in KB_PATH.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        size = int(m.group(1))
        rows[size] = {
            "gross": _money_to_float(m.group(2)),
            "net": _money_to_float(m.group(3)),
            "expanded": _money_to_float(m.group(4)),
        }
    return rows


def test_table_has_rows():
    rows = _parse_table_rows()
    assert len(rows) >= 8, f"expected at least 8 sized rows, parsed {len(rows)}"


def test_every_table_row_matches_calculator():
    rows = _parse_table_rows()
    for size, cited in rows.items():
        computed = income_limits(size)
        for band in ("gross", "net", "expanded"):
            assert cited[band] == computed[band], (
                f"KB table row household_size={size} {band}={cited[band]} "
                f"but calculator computes {computed[band]}. "
                f"Run: python3 scripts/generate_kb_table.py"
            )


def test_generator_is_idempotent():
    """Running the generator again must not change the committed file."""
    before = KB_PATH.read_text(encoding="utf-8")
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "scripts/generate_kb_table.py"],
        cwd=str(KB_PATH.resolve().parents[1]),
        check=True,
        capture_output=True,
    )
    after = KB_PATH.read_text(encoding="utf-8")
    assert before == after, "generator output changed the committed file — commit the regenerated table"


def test_worked_example_matches_calculator():
    """The FY2026 $1,800-wages example must cite the calculator's result."""
    result = calculate_eligibility(
        household_size=1,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
        earned_monthly_income=1800.0,
    )
    assert result["deductions"] is not None
    assert result["deductions"]["net_income"] == 1231.0
    assert income_limits(1)["net"] == 1305.0

    doc = DEDUCTIONS_KB_PATH.read_text(encoding="utf-8")
    assert "net monthly income of **$1,231**" in doc
    assert "FY2026 **$1,305 net limit**" in doc
    assert "209" in doc
    assert "744" in doc
    assert "198.99" in doc
    assert "child support" not in doc.lower()
