Source: https://www.fns.usda.gov/snap/eligibility-income-and-deductions

# FNS / SNAP Income Limits (Gross, Net, and Expanded Bands)

## Which test applies to a household
- Households **without** an elderly (age 60+) or disabled member are screened
  against the **gross** monthly income limit — **130% of the federal poverty
  guideline** for their household size.
- Households that **include** an elderly or disabled member are **not subject
  to the gross income test**. They are screened against the **net** monthly
  income limit — **100% of the federal poverty guideline** for the household
  size (after allowable deductions).
- Under NC's expanded eligibility, a household subject to the gross test whose
  gross income is **above 130% but at or below 200% of poverty** may still
  qualify after deductions and should apply rather than self-deny. This is the
  "apply anyway" band the agent surfaces as an *uncertain* outcome.

## Monthly income limits — FY 2025 federal poverty guidelines

The base figures are the U.S. Department of Health and Human Services (HHS)
2025 federal poverty guidelines. USDA FNS derives the SNAP income tables by
multiplying those guidelines by 130% (gross), 100% (net), and — for the
expanded band — 200%, expressed monthly. North Carolina follows the federal
chart without state-level adjustment.

<!-- BEGIN GENERATED TABLE — do not edit by hand.
     Regenerate with: python3 scripts/generate_kb_table.py
     Values come directly from src/calculator.py:income_limits() so the
     numbers the agent cites here always match the numbers it computes with. -->

| Household Size | Gross (130%) | Net (100%) | Expanded (200%) |
|---:|---:|---:|---:|
| 1 | $1,695.42 | $1,304.17 | $2,608.33 |
| 2 | $2,291.25 | $1,762.50 | $3,525.00 |
| 3 | $2,887.08 | $2,220.83 | $4,441.67 |
| 4 | $3,482.92 | $2,679.17 | $5,358.33 |
| 5 | $4,078.75 | $3,137.50 | $6,275.00 |
| 6 | $4,674.58 | $3,595.83 | $7,191.67 |
| 7 | $5,270.42 | $4,054.17 | $8,108.33 |
| 8 | $5,866.25 | $4,512.50 | $9,025.00 |
| each additional | +$595.83 | +$458.33 | +$916.67 |

<!-- END GENERATED TABLE -->

## Verification
Income limits change every federal fiscal year. Before giving a final
eligibility answer, the agent should:
1. Note which year's guidelines are in effect.
2. Direct the user to confirm current figures on the USDA FNS eligibility
   page: https://www.fns.usda.gov/snap/eligibility-income-and-deductions

## Important note for North Carolina
NC FNS uses the federal gross and net income limits without state-level
adjustments to the percentages. The household size counts everyone who lives
together and purchases and prepares food together, with a few exceptions
covered in the household composition document.
