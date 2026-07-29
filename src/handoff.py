"""Case-worker handoff summary (W17).

Renders a print-ready, plain-English summary of a completed eligibility
screen so a DSS case worker can pick up mid-flow. Two hard rules:

1. The summary is CODE-TEMPLATED, never model-generated — same rule as
   citations. Every number and every sentence comes from the live
   ``AssessmentState`` and the deterministic calculator result, so the
   block cannot fabricate intake data.
2. The on-screen/print-ready path has ZERO external dependencies — it
   must work standalone in a review meeting with no network, no
   notification plumbing, and no third-party transport. The case study
   explicitly forbids any mechanism that submits real applications or
   contacts a real agency, so the only artifact is the local,
   dependency-free summary plus the on-screen banner that says it is
   ready to share with a case worker.

The print-ready block is the entire deliverable. There is no email
layer, no outbound-contact path, and no integration with anything
external. Anything sent to a real case worker leaves through whatever
channel the user/operator chooses — copy/paste, screen-share, a print
dialog — never from this code.
"""

from __future__ import annotations

from .branding import PRODUCT_NAME

from typing import Any

from .state import AssessmentState

_HANDOFF_BANNER = "Handoff summary generated — ready to share with a case worker"

_OUTCOME_LINES = {
    "likely_eligible": "LIKELY QUALIFIES under the applicable income test.",
    "uncertain_apply_anyway": (
        "UNCERTAIN — household may still qualify and should be encouraged "
        "to apply anyway."
    ),
    "likely_ineligible": "LIKELY DOES NOT QUALIFY under the applicable income test.",
}


def handoff_summary(
    state: AssessmentState,
    eligibility_result: dict[str, Any] | None,
    citations: list[dict[str, Any]] | None = None,
) -> str | None:
    """Render the print-ready case-worker handoff summary as plain text.

    Returns ``None`` when intake is incomplete or no deterministic
    eligibility result exists — the summary must reflect real state only
    and can never be produced before every required field is collected.
    The caller (CLI/web) decides how to present that "not ready" signal.
    """
    if not state.is_ready_to_calculate() or eligibility_result is None:
        return None

    # These three are guaranteed non-None by is_ready_to_calculate().
    household_size = state.household_size
    gross_income = state.gross_monthly_income
    elderly = state.has_elderly_or_disabled_member
    assert household_size is not None
    assert gross_income is not None
    assert elderly is not None

    lines: list[str] = []
    # The product name comes from branding.PRODUCT_NAME like every other
    # user-visible string. This line hardcoded "CIVICREACH" -- the name of
    # an organization this tool is not affiliated with and does not act
    # for. On a page a case worker reads as a record of a screening, that
    # reads as provenance, which is exactly the implied endorsement
    # branding.py exists to avoid.
    lines.append(f"{PRODUCT_NAME.upper()} — CASE-WORKER HANDOFF SUMMARY")
    lines.append("NC Food & Nutrition Services (FNS) initial eligibility screen")
    lines.append("=" * 60)
    lines.append("")
    lines.append("INTAKE (as reported by the applicant)")
    lines.append(f"  Household size:                    {household_size}")
    lines.append(f"  Gross monthly income:              ${gross_income:,.2f}")
    lines.append(
        "  Elderly (60+) or disabled member:  "
        + ("Yes" if elderly else "No")
    )

    # Optional W14 deduction fields — include only when actually
    # volunteered (any value > 0). Reflect real state; never invent.
    optional = (
        ("Earned (wage) monthly income", state.earned_monthly_income),
        ("Dependent-care costs (monthly)", state.dependent_care_monthly),
        ("Medical expenses (monthly)", state.medical_expenses_monthly),
        ("Shelter costs (monthly)", state.shelter_costs_monthly),
    )
    volunteered = [(label, v) for label, v in optional if v > 0]
    if volunteered:
        lines.append("")
        lines.append("VOLUNTEERED DEDUCTION DETAILS (applicant-provided)")
        for label, value in volunteered:
            lines.append(f"  {label}: ${value:,.2f}")

    lines.append("")
    lines.append("INITIAL SCREEN RESULT (deterministic, not a final determination)")
    outcome = str(eligibility_result.get("outcome") or "")
    lines.append(
        "  " + _OUTCOME_LINES.get(outcome, f"Outcome: {outcome or 'unknown'}.")
    )
    test_used = eligibility_result.get("test_used")
    limit = eligibility_result.get("limit")
    income_considered = eligibility_result.get("income_considered")
    if test_used and limit is not None and income_considered is not None:
        lines.append(
            f"  Income test applied: {test_used} — "
            f"income considered ${income_considered:,.2f} vs "
            f"limit ${limit:,.2f}."
        )
    notes = eligibility_result.get("notes")
    if notes:
        lines.append(f"  Calculator notes: {notes}")

    docs: list[str] = []
    for hit in citations or []:
        doc = hit.get("doc") if isinstance(hit, dict) else None
        if doc and doc not in docs:
            docs.append(doc)
    if docs:
        lines.append("")
        lines.append("RULE CITATIONS (sources retrieved during the screen)")
        for doc in docs:
            lines.append(f"  - {doc}")

    lines.append("")
    lines.append("=" * 60)
    lines.append(
        "This is an initial eligibility screen only, not an agency "
        "determination."
    )
    lines.append(
        "Generated locally; nothing was submitted to or shared with any agency."
    )
    return "\n".join(lines)


__all__ = [
    "_HANDOFF_BANNER",
    "handoff_summary",
]
