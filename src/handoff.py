"""Case-worker handoff summary (W17).

Renders a print-ready, plain-English summary of a completed eligibility
screen so a DSS case worker can pick up mid-flow. Two hard rules:

1. The summary is CODE-TEMPLATED, never model-generated — same rule as
   citations. Every number and every sentence comes from the live
   ``AssessmentState`` and the deterministic calculator result, so the
   block cannot fabricate intake data.
2. The on-screen/print-ready path has ZERO external dependencies — it
   must work standalone in a review meeting with no SMTP, no network,
   nothing. Email is a strictly secondary, best-effort notification
   layer: it fires only when SMTP_HOST, SMTP_USER, SMTP_PASS, and
   SMTP_TO are ALL present in the environment, and any SMTP failure is
   caught and never propagates to the caller.

The email recipient is read from SMTP_TO only — nothing is hardcoded,
and nothing resembling a real agency address appears in this file. This
is a demo of the notification MECHANISM a production handoff would use,
not a real integration.
"""

from __future__ import annotations

import logging
import os
import smtplib
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

from .state import AssessmentState

logger = logging.getLogger(__name__)

# All four must be present for the best-effort email path to attempt a
# send. Any one missing -> silently skip (one-line log note, no raise).
SMTP_ENV_VARS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_TO")

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
    lines.append("CIVICREACH — CASE-WORKER HANDOFF SUMMARY")
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


def smtp_is_configured(env: Mapping[str, str] | None = None) -> bool:
    """True only when ALL of SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_TO are set."""
    source = os.environ if env is None else env
    return all(source.get(var, "").strip() for var in SMTP_ENV_VARS)


def send_handoff_email(
    summary: str,
    env: Mapping[str, str] | None = None,
    *,
    smtp_factory: Any = None,
) -> bool:
    """Best-effort email of the handoff summary. NEVER raises.

    Skips silently (returns False, one-line log) unless all four SMTP_*
    env vars are present. Catches every SMTP exception and returns False
    so the on-screen handoff path is never degraded by the notification
    layer. ``env`` and ``smtp_factory`` are injection seams for tests;
    production callers use the real environment and smtplib.
    """
    source = os.environ if env is None else env
    if not smtp_is_configured(source):
        logger.info("SMTP not fully configured — skipping handoff email.")
        return False

    message = EmailMessage()
    message["From"] = source["SMTP_USER"].strip()
    message["To"] = source["SMTP_TO"].strip()
    message["Subject"] = "FoodPath case-worker handoff summary (demo)"
    message.set_content(summary)

    factory = smtplib.SMTP if smtp_factory is None else smtp_factory
    try:
        port = int(source.get("SMTP_PORT", "").strip() or "587")
        with factory(source["SMTP_HOST"].strip(), port, timeout=10) as server:
            try:
                server.starttls()
            except (AttributeError, smtplib.SMTPException, OSError):
                # Some local/demo relays (e.g. a debug sink on localhost)
                # do not support STARTTLS. Best-effort: proceed without it.
                logger.info("STARTTLS unavailable — sending without TLS.")
            server.login(source["SMTP_USER"].strip(), source["SMTP_PASS"].strip())
            server.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort must never propagate
        logger.info("Handoff email failed (non-fatal): %s", exc)
        return False


__all__ = [
    "SMTP_ENV_VARS",
    "_HANDOFF_BANNER",
    "handoff_summary",
    "send_handoff_email",
    "smtp_is_configured",
]
