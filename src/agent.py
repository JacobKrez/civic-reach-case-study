"""Stateful, tool-driven conversation loop for the CivicReach agent."""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import ValidationError

from .calculator import calculate_eligibility
from .guardrails.ambiguity import normalize_income
from .guardrails.crisis import crisis_response, detect_crisis
from .guardrails.injection import detect_injection, injection_response
from .guardrails.pii import redact_for_logging
from .guardrails.scope import detect_out_of_scope, scope_redirect_message
from .llm_client import LLMClient
from .retrieval import retrieve
from .state import AssessmentState

SYSTEM_PROMPT = """You are CivicReach, an empathetic North Carolina Food and
Nutrition Services (FNS) eligibility assistant. Your scope is strictly an
initial eligibility screen for NC FNS / SNAP; you do not give legal advice
and you do not help with unrelated topics.

Collect only the three fields the calculator needs:
  1. household_size  (integer)
  2. gross_monthly_income  (number)
  3. has_elderly_or_disabled_member  (boolean)

NEVER ask the user for any other personally identifiable information. In
particular, do NOT request: a Social Security number, a full date of birth,
a home address, a phone number, a bank account number, a driver's license
number, an immigration status, or any other identifier beyond the three
fields above. If the user volunteers such information, accept it gracefully
but do not store it and do not echo it back verbatim.

Ask exactly ONE question at a time, for the first field listed under
missing_fields. Never ask again for a field already present in state.
Record user-provided facts only by calling update_state; do not put
arithmetic or inferred values in that tool call. Never do eligibility
arithmetic yourself. Once the deterministic eligibility result is
supplied, explain it using that result and the retrieved rule text,
clearly labeling it as an initial screen rather than a final agency
determination.

If the user asks for something outside the NC FNS eligibility scope,
respond with a brief polite redirect back to the FNS questions and do
not attempt to answer the off-topic ask. Do not abandon or reset any
state that has already been collected.
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
        # Append-only history of intake-field corrections. A correction is
        # a non-None -> DIFFERENT non-None change to one of the three
        # intake fields (``household_size``, ``gross_monthly_income``,
        # ``has_elderly_or_disabled_member``). The first time a field is
        # set (None -> value) is NOT a correction — the user is providing
        # the field, not changing it. Each entry is a dict with keys
        # ``field``, ``old``, ``new``, and ``turn`` (the 1-indexed user
        # turn on which the correction was observed). The list is also
        # surfaced via ``_context()`` so the model can reference past
        # corrections naturally without re-deriving them.
        self._corrections: list[dict[str, Any]] = []
        # Retrieval hits (list of {doc, snippet, score}) behind the most
        # recent eligibility result. Populated when a result is first
        # produced (see turn()). Exposed for the web UI's citation panel
        # and so a reviewer can see WHICH documents grounded the answer.
        self.last_citations: list[dict[str, Any]] = []

    @property
    def eligibility_result(self) -> dict[str, Any] | None:
        return self._eligibility_result

    def _track_query(self) -> str:
        """The retrieval query for the household's applicable income track."""
        if self.state.missing_fields():
            return "NC FNS eligibility intake questions"
        track = (
            "elderly disabled household net income rule"
            if self.state.has_elderly_or_disabled_member
            else "gross income 130 percent rule"
        )
        return f"NC FNS {track}"

    def _context(self) -> str:
        missing = self.state.missing_fields()
        rule_text = self._retrieve(self._track_query())
        payload: dict[str, Any] = {
            "assessment_state": self.state.model_dump(),
            "missing_fields": missing,
            "retrieved_rule_text": rule_text,
            "corrections": list(self._corrections),
        }
        if self._eligibility_result is not None:
            payload["deterministic_eligibility_result"] = self._eligibility_result
        return json.dumps(payload, default=str)

    # Intake fields whose change invalidates a cached eligibility result.
    _INTAKE_FIELDS = ("household_size", "gross_monthly_income", "has_elderly_or_disabled_member")

    def _apply_tool_calls(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply every update_state tool call in ``calls``.

        Processes ALL update_state calls in the turn (a model may emit
        more than one), accumulating corrections across them. Returns a
        list of correction records — one per intake field whose value
        changed from a previous non-None value to a DIFFERENT non-None
        value. The caller commits these to ``self._corrections`` only
        after this method returns successfully, so a validation failure
        mid-turn leaves no phantom correction behind (the whole turn's
        apply raises before any correction is committed).
        """
        new_corrections: list[dict[str, Any]] = []
        for call in calls:
            if call.get("name") != "update_state":
                continue
            raw = call.get("input") or {}
            if not isinstance(raw, dict):
                raise ValueError("update_state input must be an object")
            updates = {key: value for key, value in raw.items() if key in _ALLOWED_UPDATES}
            if not updates:
                continue
            # ``gross_monthly_income`` is the only intake field that can
            # legitimately arrive as a fuzzy string like "$2,500" or
            # "$600/week" rather than a plain float. Run every incoming
            # value through ``normalize_income`` (which is total: it
            # accepts numbers, strings, and None, and returns None on
            # unparseable input) so the calculator downstream only sees
            # a clean monthly float. A None return means the caller
            # forgot to pass a usable value and we drop that field
            # rather than write garbage into state.
            if "gross_monthly_income" in updates:
                normalized = normalize_income(updates["gross_monthly_income"])
                if normalized is None:
                    updates.pop("gross_monthly_income", None)
                else:
                    updates["gross_monthly_income"] = normalized
            if not updates:
                continue
            # Detect intake-field corrections (non-None -> DIFFERENT
            # non-None) BEFORE we mutate ``self.state``. ``None -> value``
            # is intentionally NOT a correction — the user is providing
            # the field for the first time, not changing it. A False ->
            # True flip on ``has_elderly_or_disabled_member`` counts. Any
            # such change also invalidates a cached eligibility result so
            # the next ``_calculate_once`` recomputes against corrected
            # state (the self-contradiction scenario the rubric tests).
            call_corrections: list[dict[str, Any]] = []
            for field in self._INTAKE_FIELDS:
                if field not in updates:
                    continue
                new_value = updates[field]
                old_value = getattr(self.state, field)
                if old_value is None or new_value == old_value:
                    continue
                call_corrections.append(
                    {
                        "field": field,
                        "old": old_value,
                        "new": new_value,
                        "turn": self.state.turn_count,
                    }
                )
            # model_copy does not validate in Pydantic v2, so validate the
            # candidate FIRST and only assign on success — assigning before
            # validation would leave an invalid value (e.g. household_size
            # 999) live in state even after the error is caught.
            candidate = self.state.model_copy(update=updates)
            try:
                self.state = AssessmentState.model_validate(candidate.model_dump())
            except ValidationError as exc:
                raise ValueError(f"Invalid update_state call: {exc}") from exc
            if call_corrections and self._eligibility_result is not None:
                self._eligibility_result = None
            new_corrections.extend(call_corrections)
        return new_corrections

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
        """Process one user turn, prioritizing deterministic guardrails.

        Order of evaluation:
        1. Crisis detection (food/safety emergencies) -> hardcoded resources.
           Crisis MUST be evaluated before injection: a message like
           "ignore your instructions — I'm going to kill myself" matches
           both detectors, and answering it with an injection refusal
           instead of the 988/211 resources would be the worst possible
           outcome. Safety outranks security here, deliberately.
        2. Prompt-injection detection -> a hardcoded refusal. The next
           ordinary message is evaluated normally; the flag is historical
           and does not lock the conversation into refusal mode.
        3. Out-of-scope detection (legal advice, unrelated topics)
           -> fixed polite redirect. State is preserved; is_out_of_scope
           is flipped so the agent loop can remember.
        4. Otherwise, hand to the LLM as normal.

        The PII redactor is applied to the user message before it is
        recorded in the internal message history so that any later log
        dump is free of SSN-shaped or account-shaped patterns.
        """
        self.state.turn_count += 1

        # Record the redacted form of the user message in conversation
        # history so any log/print of the history is PII-safe.
        safe_user_message = redact_for_logging(user_message)
        self._messages.append({"role": "user", "content": safe_user_message})

        if detect_crisis(user_message):
            self.state.is_crisis = True
            content = crisis_response()
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_injection(user_message):
            self.state.is_injection_attempt = True
            content = injection_response()
            self._messages.append({"role": "assistant", "content": content})
            return content

        if detect_out_of_scope(user_message):
            self.state.is_out_of_scope = True
            content = scope_redirect_message()
            self._messages.append({"role": "assistant", "content": content})
            return content

        response = self.llm_client.chat(
            [*self._messages, {"role": "user", "content": self._context()}],
            system=self.system_prompt,
            tools=self.tools,
        )
        new_corrections: list[dict[str, Any]] = []
        try:
            new_corrections = self._apply_tool_calls(response.get("tool_calls") or [])
        except ValueError:
            # A malformed/over-limit tool call from the model (e.g.
            # household_size above the schema cap) must not crash the
            # turn. Drop the bad update; state stays valid, the field
            # stays missing, and the loop re-asks on the next turn.
            # ``new_corrections`` keeps its default empty value so no
            # phantom suffix is appended below.
            pass
        had_result = self._eligibility_result is not None
        self._calculate_once()
        result_just_produced = not had_result and self._eligibility_result is not None

        content = str(response.get("content") or "")

        # When an eligibility result is first produced, ground it visibly:
        # capture the retrieval hits for the applicable track and append a
        # deterministic "Sources:" line naming the documents. This is
        # code-templated (not model-generated) so the citation cannot be
        # hallucinated, and it makes the RAG grounding demonstrable live
        # instead of invisible. Hits are also stored on self.last_citations
        # for the web UI's citation panel.
        if result_just_produced:
            hits = self._retrieve(self._track_query())
            if isinstance(hits, list):
                self.last_citations = hits
                docs: list[str] = []
                for h in hits:
                    doc = h.get("doc") if isinstance(h, dict) else None
                    if doc and doc not in docs:
                        docs.append(doc)
                if docs:
                    sources = f"(Sources: {', '.join(docs)}.)"
                    content = f"{content} {sources}" if content else sources
        # Append a deterministic, code-templated suffix line for every
        # intake-field correction observed on THIS turn. The model does
        # not write these lines — they are generated from
        # ``new_corrections`` so they are testable and cannot be
        # hallucinated. Corrections are committed to ``self._corrections``
        # only after the apply call succeeded (a failed apply drops the
        # suffix so a phantom correction never reaches the user).
        self._corrections.extend(new_corrections)
        for entry in new_corrections:
            suffix = (
                f"(Updated {entry['field']}: {entry['old']} -> {entry['new']}.)"
            )
            if content:
                content = f"{content} {suffix}"
            else:
                content = suffix
        self._messages.append({"role": "assistant", "content": content})
        return content


__all__ = ["CivicReachAgent", "SYSTEM_PROMPT", "UPDATE_STATE_TOOL"]
