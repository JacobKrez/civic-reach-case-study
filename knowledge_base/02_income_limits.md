---
doc_id: nc-fns-income-fy2026
jurisdiction: nc
authority: USDA Food and Nutrition Service; North Carolina DHHS
source_url: https://www.usda.gov/sites/default/files/guidance-documents/fns.snap-cola-fy26memo.pdf
retrieved: 2026-07-24
effective: 2025-10-01..2026-09-30
supersedes: null
---

# FNS / SNAP Income Limits (Gross, Net, and Expanded Bands)

## Which test applies to a household
- Households **without** an elderly (age 60+) or disabled member are screened
  against the **gross** monthly income limit — **200% of the federal poverty
  guideline** for their household size in North Carolina (the NC BBCE ceiling;
  the federal floor outside NC is 130%). See "Important note for North
  Carolina" below.
- Households that **include** an elderly or disabled member are **not subject
  to the gross income test**. They are screened against the **net** monthly
  income limit — **100% of the federal poverty guideline** for the household
  size (after allowable deductions).
- A household subject to the gross test whose gross income is **above the
  130% federal floor but at or below 200% of poverty** may still qualify
  after deductions and should apply rather than self-deny. Under NC BBCE the
  200% figure is the gross screen ceiling, so the household passes the gross
  screen categorically and only the net test (if any) is pending; outside NC
  the same household sits in the "uncertain, apply anyway" band because the
  200% categorical pass does not apply.
- Households where an elderly/disabled person is a **separate household** (lives
  with others but buys and prepares food separately) are screened against a
  different gross-income standard — see kb/03 ("Separate Elderly/Disabled
  Households — FY2026 165% standard").

## Monthly income limits — FY 2026 SNAP income-eligibility standards

The base figures are the USDA FNS FY2026 SNAP income-eligibility standards
for the 48 contiguous states and DC (effective 2025-10-01 through
2026-09-30). The 130% gross and 100% net columns are the figures USDA FNS
publishes directly for SNAP; the expanded 200% column is the NC BBCE
ceiling (NCDHHS) — twice the published 100% net figure, expressed monthly.
USDA FNS re-sources these annually in the SNAP COLA memo.

<!-- BEGIN GENERATED TABLE — do not edit by hand.
     Regenerate with: python3 scripts/generate_kb_table.py
     Values come directly from src/calculator.py:income_limits() so the
     numbers the agent cites here always match the numbers it computes with. -->

| Household Size | Gross (130%) | Net (100%) | Expanded (200%) |
|---:|---:|---:|---:|
| 1 | $1,696.00 | $1,305.00 | $2,610.00 |
| 2 | $2,292.00 | $1,763.00 | $3,526.00 |
| 3 | $2,888.00 | $2,221.00 | $4,442.00 |
| 4 | $3,483.00 | $2,680.00 | $5,360.00 |
| 5 | $4,079.00 | $3,138.00 | $6,276.00 |
| 6 | $4,675.00 | $3,596.00 | $7,192.00 |
| 7 | $5,271.00 | $4,055.00 | $8,110.00 |
| 8 | $5,867.00 | $4,513.00 | $9,026.00 |
| each additional | +$596.00 | +$459.00 | +$918.00 |

<!-- END GENERATED TABLE -->

## Verification
Income limits change every federal fiscal year. Before giving a final
eligibility answer, the agent should:
1. Note which year's guidelines are in effect (FY2026, effective
   2025-10-01 through 2026-09-30).
2. Direct the user to confirm current figures on the USDA FNS eligibility
   page: https://www.fns.usda.gov/snap/eligibility-income-and-deductions
3. For NC BBCE specifically, confirm the 200% gross screen and the no-asset-
   test rule on the NC DHHS program-design page:
   https://www.ncdhhs.gov/divisions/social-services/program-design-and-policy

## Important note for North Carolina

North Carolina operates SNAP under **Broad-Based Categorical Eligibility
(BBCE)** (NCDHHS). Under BBCE:

- The **gross-income screen is 200% of the federal poverty guideline**, not
  the 130% federal floor. A household whose gross income is at or below 200%
  FPL passes the gross screen categorically; the asset test is also waived for
  most households.
- Households WITH an elderly or disabled member living in the same household
  are still screened against the 100% net test (BBCE does not change the net
  test — it only raises the gross screen ceiling and waives the asset test).
- Approximately 13% of NC SNAP households qualify via BBCE (NCDHHS, 2025
  BBCE policy summary). See the dedicated source above.

The household size counts everyone who lives together and purchases and
prepares food together, with a few exceptions covered in the household
composition document.

## What counts as gross monthly income (W43 close-the-loop KB addition)

SNAP gross monthly income is the household's **total income before any
deductions**, averaged across the household, expressed monthly. The
following are typical sources the household should include (this list
is not exhaustive; for deductions, see KB/03):

  - **Wages and salaries** (cash tips, too) — the gross amount on the
    pay stub before taxes, insurance, or retirement contributions.
  - **Self-employment income** — net profit from a business or gig work
    (gross receipts minus ordinary business expenses). Gig-economy
    platforms (rideshare, delivery, task work) are self-employment.
  - **Social Security benefits** (including SSI and SSDI) — the gross
    monthly benefit before any deductions for Medicare premiums.
  - **Unemployment compensation** — the weekly benefit multiplied by
    the number of weeks expected in the month.
  - **Child support received** for a child in the household — counted
    as income to the household that receives it.
  - **Pensions and retirement** — gross monthly amount.
  - **Workers' compensation, disability, VA benefits** — gross monthly.
  - **Rental income** (net) — receipts minus ordinary expenses.
  - **Cash gifts, lottery winnings, and other irregular income** —
    count the portion expected or received in the month.

Income that is **NOT** counted:

  - **Child support paid out** by a household member to a non-household
    child is not a household income source.
  - **Loans** (federal student loans, personal loans, lines of credit)
    are not income.
  - **Earned Income Tax Credit (EITC) refunds** are not income for the
    month received.
  - **SNAP benefits themselves** are not income for SNAP purposes.
  - **Most TANF, SSI, and certain other federal benefits** may be
    excluded by rule; confirm with DSS.

The "gross" qualifier matters: benefits based on post-tax pay (take-home
pay) are NOT the SNAP definition. The screen targets the gross monthly
figure. If the user only knows their annual or post-tax income, the
agent should ask for a pre-tax monthly figure before running the
calculator — see the "Annual / post-tax / 'it varies'" note below.

## Which month's income counts — current and expected, not past

The screen asks about the household's income **now and going forward**,
not what it earned earlier in the year. Federal rule bases eligibility on
the household's circumstances in the month of application, using the
income the household reasonably expects to receive during the
certification period (7 CFR 273.10(c) and 273.10(e)(2)(ii)(A) —
"anticipating income").

Practical consequences the agent should state plainly when asked:

  - **A job that has ended does not count going forward.** A household
    that lost a job reports what it expects to receive now — unemployment
    compensation, a remaining earner's wages, or **$0** if no income is
    coming in. It does **not** report the wages from the job it no longer
    has. Reporting the old wage is the most common way a newly
    unemployed household talks itself out of benefits it qualifies for.
  - **Last year's income is not the measure.** Annual figures (a tax
    return, "I made about $42,000 last year") are only useful as a way to
    reach a current monthly figure, and only if that income is still
    being received. See the annual-conversion note below.
  - **Steady pay is averaged; changed pay is not.** Where income is
    stable, a recent month or a recent set of pay stubs (typically the
    last 30 days) is a fair basis. Where income has just changed, the
    expected new amount is what counts, not the average of a period that
    no longer reflects the household.
  - **Irregular or seasonal income** is averaged over the period it is
    expected to cover, not annualized from a single high or low month.

If the household's income is mid-change and no figure is yet reliable,
DSS can still take the application: the caseworker resolves anticipated
income at the interview. The screen should not stall on an exact number
in that situation — record the household's best current estimate and say
plainly that the county office will confirm it.

## Annual / post-tax / "it varies" — handling non-monthly income (W43 close-the-loop KB addition)

The screen targets **gross monthly income** for the calculator. Real
households give income in many forms:

  - **Annual.** Divide by 12 to get the monthly figure. If the user
    reports "I make $42,000 a year", the monthly figure is $3,500.
  - **Post-tax / take-home.** Do NOT use the take-home figure. Ask
    the user for the gross monthly amount, or accept the hourly rate
    times hours-per-week and divide by 12 weeks per month.
  - **Hourly with hours-per-week.** Multiply rate × hours × 52 / 12 =
    monthly gross. (e.g. $18/hr × 32 hrs/week × 52 / 12 ≈ $2,496.)
  - **Biweekly.** Multiply by 26 / 12 = monthly. (e.g. $1,500 every
    two weeks → $3,250/month.)
  - **It varies.** If the user reports "it varies" or "depends on the
    month", the screen asks for a representative monthly figure. If
    the user cannot give one, the screen notes the ambiguity and
    recommends contacting DSS for the formal calculation.
  - **Self-employment / gig work.** Net profit (gross receipts minus
    ordinary expenses) is the SNAP figure. If the user does not track
    monthly net profit, the screen asks for the average monthly amount.

The agent's intake form collects a single gross-monthly-income value;
the calculator only sees that normalized monthly figure. When the user
gives the agent a value in any other form, the agent converts it
deterministically (see `src.guardrails.ambiguity.normalize_income`) and
renders the canonical monthly figure in its reply.
