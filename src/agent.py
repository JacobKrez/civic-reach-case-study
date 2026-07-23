"""Stateful, tool-driven conversation loop for the CivicReach agent."""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import ValidationError

from .calculator import calculate_eligibility
from .llm_client import LLMClient
from .retrieval import retrieve
from .state import AssessmentState

SYSTEM_PROMPT = """You are CivicReach, an empathetic North Carolina Food and
Nutrition Services (FNS) eligibility assistant. Collect only the information
needed for an initial screen. Ask exactly ONE question at a time, for the first
field listed under missing_fields. Never ask again for a field already present
in state. Record user-provided facts only by calling update_state; do not put
arithmetic or inferred values in that tool call. Never do eligibility arithmetic
yourself. Once the deterministic eligibility result is supplied, explain it
using that result and the retrieved rule text, clearly labeling it as an initial
screen rather than a final agency determination.
"""

UPDATE_STATE_TOOL = {
    "name": "update_state",
    "description": "Record explicitly supplied assessment fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "household_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "gross_monthly_income": {"type": "number", "minimum": 0},
            "has_elderly_or_disabled_member": {"type": "boolean"},
            "is_crisis": {"type": "boolean"},
            "is_out_of_scope": {"type": "boolean"},
            "is_injection_attempt": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}

_ALLOWED_UPDATES = set(UPDATE_STATE_TOOL["input_schema"]["properties"])


class CivicReachAgent:
    """Maintain assessment state and delegate extraction to structured tools."""

    def __init__(
        self,
        llm_client: LLMClient | Any | None = None,
        state: AssessmentState | None = None,
        retriever: Callable[[str], str] = retrieve,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.state = state or AssessmentState()
        self.system_prompt = SYSTEM_PROMPT
        self.tools = [UPDATE_STATE_TOOL]
        self._retrieve = retriever
        self._messages: list[dict[str, str]] = []
        self._eligibility_result: dict[str, Any] | None = None

    @property
    def eligibility_result(self) -> dict[str, Any] | None:
        return self._eligibility_result

    def _context(self) -> str:
        missing = self.state.missing_fields()
        query = "NC FNS eligibility intake questions"
        if not missing:
            track = (
                "elderly disabled household net income rule"
                if self.state.has_elderly_or_disabled_member
                else "gross income 130 percent rule"
            )
            query = f"NC FNS {track}"
        rule_text = self._retrieve(query)
        payload: dict[str, Any] = {
            "assessment_state": self.state.model_dump(),
            "missing_fields": missing,
            "retrieved_rule_text": rule_text,
        }
        if self._eligibility_result is not None:
            payload["deterministic_eligibility_result"] = self._eligibility_result
        return json.dumps(payload, default=str)

    def _apply_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            if call.get("name") != "update_state":
                continue
            raw = call.get("input") or {}
            if not isinstance(raw, dict):
                raise ValueError("update_state input must be an object")
            updates = {key: value for key, value in raw.items() if key in _ALLOWED_UPDATES}
            if not updates:
                continue
            try:
                self.state = self.state.model_copy(update=updates)
                # model_copy does not validate updates in Pydantic v2.
                self.state = AssessmentState.model_validate(self.state.model_dump())
            except ValidationError as exc:
                raise ValueError(f"Invalid update_state call: {exc}") from exc

    def _calculate_once(self) -> None:
        if self._eligibility_result is not None or not self.state.is_ready_to_calculate():
            return
        household_size = self.state.household_size
        gross_income = self.state.gross_monthly_income
        elderly_or_disabled = self.state.has_elderly_or_disabled_member
        assert household_size is not None
        assert gross_income is not None
        assert elderly_or_disabled is not None
        self._eligibility_result = calculate_eligibility(
            household_size=household_size,
            gross_monthly_income=gross_income,
            has_elderly_or_disabled=elderly_or_disabled,
        )

    def turn(self, user_message: str) -> str:
        """Process one user turn and return the LLM's assistant text."""
        self.state.turn_count += 1
        self._messages.append({"role": "user", "content": user_message})

        response = self.llm_client.chat(
            [*self._messages, {"role": "user", "content": self._context()}],
            system=self.system_prompt,
            tools=self.tools,
        )
        self._apply_tool_calls(response.get("tool_calls") or [])
        self._calculate_once()

        content = str(response.get("content") or "")
        self._messages.append({"role": "assistant", "content": content})
        return content


__all__ = ["CivicReachAgent", "SYSTEM_PROMPT", "UPDATE_STATE_TOOL"]
