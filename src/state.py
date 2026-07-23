"""Conversation state schema for the CivicReach NC FNS eligibility agent.

Pure data model — no I/O, no LLM calls. Used by the agent loop to track what
information the user has provided so far and what still needs to be collected
before a deterministic eligibility calculation can run.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AssessmentState(BaseModel):
    """State for a single eligibility-assessment conversation.

    All three intake fields (household_size, gross_monthly_income,
    has_elderly_or_disabled_member) are required for a SNAP eligibility
    decision under the NC FNS ruleset. They start as ``None`` and are filled
    in as the agent extracts them from the conversation.

    The three "is_*" boolean flags represent orthogonal safety /
    classification signals the guardrails layer sets during the conversation:
    crisis routing, out-of-scope handling, and prompt-injection detection.
    They are independent of the intake fields and do not affect
    ``missing_fields()``.

    ``turn_count`` is incremented by the agent loop on every user turn and
    is useful for debugging, retry logic, and forcing a graceful handoff
    when a conversation runs too long.
    """

    # --- intake fields (the three required for the calculator) ---
    household_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of people in the SNAP household. Required.",
    )
    gross_monthly_income: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Total gross monthly household income in USD. Required.",
    )
    has_elderly_or_disabled_member: Optional[bool] = Field(
        default=None,
        description=(
            "True if any household member is 60+ or disabled. Determines "
            "which income test (gross vs net) applies."
        ),
    )

    # --- guardrail classification flags (set by the safety layer) ---
    is_crisis: bool = Field(
        default=False,
        description="True if the user expressed an immediate safety/food crisis.",
    )
    is_out_of_scope: bool = Field(
        default=False,
        description="True if the request is outside NC FNS / SNAP scope.",
    )
    is_injection_attempt: bool = Field(
        default=False,
        description="True if a prompt-injection attempt was detected.",
    )

    # --- loop bookkeeping ---
    turn_count: int = Field(
        default=0,
        ge=0,
        description="Number of user turns taken in this conversation so far.",
    )

    # --- intake helpers ---
    def missing_fields(self) -> List[str]:
        """Return the names of intake fields that are still ``None``.

        Order is intentional and matches the priority the agent uses when
        asking follow-up questions: we ask the cheapest / least sensitive
        question first (household size), then income, then the
        elderly/disabled flag (which can affect routing).

        The three guardrail flags (``is_crisis``, ``is_out_of_scope``,
        ``is_injection_attempt``) and ``turn_count`` are not intake fields
        and are intentionally omitted — they are not something the user
        "provides" during the eligibility interview.
        """
        missing: List[str] = []
        if self.household_size is None:
            missing.append("household_size")
        if self.gross_monthly_income is None:
            missing.append("gross_monthly_income")
        if self.has_elderly_or_disabled_member is None:
            missing.append("has_elderly_or_disabled_member")
        return missing

    def is_ready_to_calculate(self) -> bool:
        """True once all three intake fields have been collected."""
        return len(self.missing_fields()) == 0
