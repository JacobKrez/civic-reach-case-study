---
doc_id: snap-deductions-household-fy2026
jurisdiction: federal
authority: USDA Food and Nutrition Service
source_url: https://www.usda.gov/sites/default/files/guidance-documents/fns.snap-cola-fy26memo.pdf
retrieved: 2026-07-24
effective: 2025-10-01..2026-09-30
supersedes: null
---

# FNS / SNAP Allowable Deductions

After gross monthly income is calculated, certain deductions are subtracted
to arrive at net monthly income for the net income test. The following
deductions are allowed under federal SNAP rules and used by NC FNS.

## Elderly or disabled — operational intake definition

For this screener, mark the household as having an **elderly or disabled**
member when at least one member is **60 or older**, or is **receiving
disability benefits / otherwise meets the SNAP disability definition in 7
CFR §273.1(b)**. This is one of the three core intake fields. It matters
because it switches the household from the ordinary gross-income screen to
the net-income test after allowable deductions. If the person lives with
others but buys and prepares food separately, use the separate-household
rule below instead.

## The net-income formula (as computed by this agent's calculator)

The calculator applies the federal deduction chain of 7 CFR §273.9(d) in
this exact order, using FY2026 amounts:

  1. Start with gross monthly income.
  2. Subtract the standard deduction for the household size.
  3. Subtract 20% of any earned income (the earned income deduction).
  4. Subtract actual out-of-pocket dependent-care costs.
  5. For elderly/disabled households only, subtract out-of-pocket medical
     expenses above $35/month.
  6. The result is the household's adjusted income. Subtract the excess
     shelter deduction: shelter costs above half of adjusted income,
     capped at $744/month for most households and uncapped for
     elderly/disabled households.
  7. The remainder is net monthly income, compared against the net income
     limit (100% of the federal poverty guideline).

Worked example: a household of 1 with $1,800/month in wages takes a $209
standard deduction plus a $360 earned income deduction (20% of $1,800).
The calculator returns net monthly income of **$1,231** ($1,800 - $209 -
$360), which is under the FY2026 **$1,305 net limit** for household size 1.
This is the deduction arithmetic; the applicable household track still
depends on the elderly/disabled and household-composition answers.

## 1. Standard Deduction
Every household gets a flat standard deduction that varies by household
size. FY2026 amounts (48 States & DC) are: household sizes **1: $209, 2:
$209, 3: $209, 4: $223, 5: $261, and 6 or more: $299** per month. USDA
updates these each October in the SNAP allotment and deduction tables.

## 2. Earned Income Deduction
20% of gross earned income (wages, self-employment) is deducted. This
recognizes work-related expenses like taxes and transportation.

## 3. Medical Expense Deduction (Elderly/Disabled Households Only)
Households containing an elderly (60+) or disabled member may deduct
out-of-pocket medical expenses above $35 per month. This includes:
- Medical and dental care
- Hospital care
- Prescription drugs
- Health insurance premiums
- Costs for dentures, hearing aids, eyeglasses, prosthetics
- Costs for service animals and attendant care

## 4. Dependent Care Deduction
Actual out-of-pocket costs for care of a child or other dependent who is
needed so a household member can work, look for work, or attend school or
training. Under current federal rules this deduction is not capped.

## 5. Excess Shelter Deduction (Shelter and Utility Costs)
The excess shelter deduction covers shelter costs (rent, mortgage, taxes,
insurance) plus utilities above half of the household's adjusted income
after the deductions above. For most households the shelter deduction is
capped at **$744/month (FY2026, 48 States & DC)**; households with an
elderly or disabled member have no cap. The maximum homeless shelter
deduction is **$198.99/month (FY2026, 48 States & DC)** when that rule
applies. The current calculator has no homeless-shelter input, so it does
not apply that amount automatically. A Standard Utility Allowance (SUA) is
used in NC for households that pay heating or cooling separately or receive
LIHEAP assistance.

---

# FNS / SNAP Household Composition Rules

## What Is a "Household" for FNS?
A SNAP/FNS household is a group of people who **live together and customarily
purchase and prepare food together**. People who live together but buy and
cook food separately may be separate households.

## Who Must Be in the Same Household?
The following people living together must be included in the same FNS
household, even if they purchase food separately:
- Spouses living together
- Parents and their children under 22 years old (including stepchildren)
- Children under 18 living with a parent who is also under 18 (the
  children must be in the same household as the parent)

## Who Can Be Separate?
Under federal rules, the following may be a separate household even if
living with others, if they purchase and prepare food separately:
- An elderly person (60+) living with others
- A disabled person living with others, in certain circumstances
- A boarder (pays for room and meals) — boarders are usually not part
  of the FNS household

## Separate Elderly/Disabled Households — FY2026 165% standard

When an elderly (60+) or disabled person lives with others but purchases
and prepares food separately, they may qualify as a **separate SNAP
household**. Federal SNAP rules (7 CFR §273.9(a)) and NC FNS apply a
**165% gross-income standard** to such separate households — replacing
both the 200% NC BBCE gross ceiling and the 130% federal floor. The
standard is re-sourced annually alongside the poverty guidelines.

Source: USDA FNS SNAP FY2026 income-eligibility tables (48 States & DC
column, separate-elderly/disabled household row), effective 2025-10-01
through 2026-09-30. The calculator exports these as
``separate_household_limits(size)``.

| Household Size | 165% Separate-Household Gross Limit (monthly) |
|---:|---:|
| 1 | $2,152 |
| 2 | $2,909 |
| 3 | $3,665 |
| 4 | $4,421 |
| 5 | $5,177 |
| 6 | $5,934 |
| 7 | $6,690 |
| 8 | $7,446 |
| each additional person | +$757 |

The agent's intake form is responsible for surfacing the
separate-household question ("Do you buy and prepare food separately
from the people you live with?") — the calculator does not auto-route
a household to the 165% track. When the user confirms separate-household
status, the agent passes ``separate_elderly_disabled_household=True``
to ``calculate_eligibility``, which then applies the 165% figures above.

## Counting People for the Household Size
- Count everyone in the household, including newborn babies.
- Do not count people who are temporarily away (less than 2 months) if
  they intend to return.
- Do not count people who are in the household only because they are
  being fostered for pay.
- Roommates who do not buy and cook together are separate households.

## Effect on Income Limits
Household size drives the income limit column. Misclassifying a household
(merging or splitting) can change eligibility significantly. When in doubt,
the agent should ask the user whether people in the home buy and prepare
food together.
