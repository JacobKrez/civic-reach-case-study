"""W4: visible citations when an eligibility result is produced.

Grounding (rubric point 2) must be demonstrable, not invisible. When the
agent produces its eligibility verdict it appends a deterministic
"(Sources: ...)" line naming the knowledge-base documents behind it, and
stores the hits on self.last_citations for the web UI.
"""

from src.agent import CivicReachAgent
from src.guardrails.classifier import CLASSIFIER_SYSTEM_PROMPT


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        # The classifier layer runs before the agent's main LLM call.
        # Reply "normal" without consuming a queued response so the
        # scripted queue contract stays intact across the extra call.
        if system == CLASSIFIER_SYSTEM_PROMPT:
            return {"content": "normal", "tool_calls": []}
        if self._responses:
            return self._responses.pop(0)
        return {"content": "ok", "tool_calls": []}


def _complete_intake_response():
    return {
        "content": "Here is your initial screening result.",
        "tool_calls": [
            {
                "name": "update_state",
                "input": {
                    "household_size": 2,
                    "gross_monthly_income": 1500.0,
                    "has_elderly_or_disabled_member": False,
                },
            }
        ],
    }


def test_result_turn_appends_sources_line_with_real_kb_doc():
    # Uses the REAL retriever (default) over the real knowledge_base/.
    agent = CivicReachAgent(llm_client=ScriptedLLM([_complete_intake_response()]))
    reply = agent.turn("2 of us, $1,500/month, nobody elderly or disabled")
    assert "(Sources:" in reply
    assert ".md" in reply  # names at least one real knowledge_base file
    assert agent.eligibility_result is not None


def test_last_citations_populated_with_doc_snippet_score():
    agent = CivicReachAgent(llm_client=ScriptedLLM([_complete_intake_response()]))
    agent.turn("2 of us, $1,500/month, no elderly or disabled")
    assert agent.last_citations, "expected citations to be captured"
    top = agent.last_citations[0]
    assert set(("doc", "snippet", "score")).issubset(top.keys())


def test_no_sources_line_before_result_is_ready():
    # A mid-intake turn (only one field) must NOT emit a sources line.
    partial = {
        "content": "How many people are in your household?",
        "tool_calls": [
            {"name": "update_state", "input": {"gross_monthly_income": 1500.0}}
        ],
    }
    agent = CivicReachAgent(llm_client=ScriptedLLM([partial]))
    reply = agent.turn("I make $1,500 a month")
    assert "(Sources:" not in reply
    assert agent.last_citations == []


def test_sources_line_appears_once_not_repeated_on_later_turns():
    agent = CivicReachAgent(
        llm_client=ScriptedLLM(
            [
                _complete_intake_response(),
                {"content": "Anything else I can help clarify?", "tool_calls": []},
            ]
        )
    )
    first = agent.turn("2 of us, $1,500/month, no elderly or disabled")
    second = agent.turn("thanks")
    assert "(Sources:" in first
    assert "(Sources:" not in second  # result already produced; no re-cite
