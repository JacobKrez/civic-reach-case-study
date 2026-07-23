#!/usr/bin/env python3
"""Regenerate the income-limit table in knowledge_base/02_income_limits.md.

The table is produced directly from ``src.calculator.income_limits`` so the
numbers the agent *cites* to the user can never drift from the numbers it
*computes* with. Run after any change to the calculator's poverty figures:

    python3 scripts/generate_kb_table.py

The script replaces only the region between the BEGIN/END GENERATED TABLE
markers; all surrounding prose is preserved. ``tests/test_kb_consistency.py``
fails if the committed table ever falls out of sync with the calculator.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow running as a plain script (python3 scripts/generate_kb_table.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calculator import income_limits, max_household_size  # noqa: E402

KB_PATH = Path(__file__).resolve().parents[1] / "knowledge_base" / "02_income_limits.md"
BEGIN = "<!-- BEGIN GENERATED TABLE"
END = "<!-- END GENERATED TABLE -->"

# Show explicit rows for sizes 1..8, then an "each additional" delta row.
_SHOWN_SIZES = range(1, 9)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def build_table() -> str:
    lines = [
        "| Household Size | Gross (130%) | Net (100%) | Expanded (200%) |",
        "|---:|---:|---:|---:|",
    ]
    for size in _SHOWN_SIZES:
        lim = income_limits(size)
        lines.append(
            f"| {size} | {_money(lim['gross'])} | {_money(lim['net'])} "
            f"| {_money(lim['expanded'])} |"
        )
    # Per-additional-person delta (size 8 -> 9 difference is constant).
    if max_household_size() >= 9:
        a, b = income_limits(8), income_limits(9)
        lines.append(
            f"| each additional | +{_money(b['gross'] - a['gross'])} "
            f"| +{_money(b['net'] - a['net'])} "
            f"| +{_money(b['expanded'] - a['expanded'])} |"
        )
    return "\n".join(lines)


def render() -> str:
    text = KB_PATH.read_text(encoding="utf-8")
    begin_idx = text.find(BEGIN)
    end_idx = text.find(END)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        raise SystemExit("generated-table markers not found or malformed in KB doc")
    # Preserve the BEGIN marker comment (which documents how to regenerate),
    # then insert the fresh table, then the END marker.
    begin_block_end = text.find("-->", begin_idx)
    if begin_block_end == -1 or begin_block_end > end_idx:
        raise SystemExit("BEGIN marker comment not closed")
    head = text[: begin_block_end + len("-->")]
    tail = text[end_idx:]
    return f"{head}\n\n{build_table()}\n\n{tail}"


def main() -> None:
    KB_PATH.write_text(render(), encoding="utf-8")
    print(f"regenerated income table in {KB_PATH}")


if __name__ == "__main__":
    main()
