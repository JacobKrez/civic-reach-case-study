"""W3: correction acknowledgment across turns.

The rubric explicitly lists "self-contradictions across turns." When a user
changes an intake field they already provided, the agent must:
  - acknowledge the change with a deterministic, code-templated suffix
    (not model-generated, so it is testable and cannot be hallucinated),
  - record it in an auditable corrections trail,
  - NOT treat the first-time provision of a field (None -> value) as a
    correction.
"""

from src.agent import CivicReachAgent


class ScriptedLLM:
    """Return queued canned responses in order; record calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return {"content": "ok", "tool_calls": []}


def _update(**fields):
    return {"content": "noted", "tool_calls": [{"name": "update_state", "input": fields}]}


def test_first_time_provision_is_not_a_correction():
    llm = ScriptedLLM([_update(household_size=3)])
    agent = CivicReachAgent(llm_client=llm)
    reply = agent.turn("there are 3 of us")
    assert "Updated" not in reply
    assert agent._corrections == []
    assert agent.state.household_size == 3


def test_changed_field_is_acknowledged_with_suffix():
    llm = ScriptedLLM([_update(household_size=2), _update(household_size=5)])
    agent = CivicReachAgent(llm_client=llm)
    agent.turn("2 people")
    reply = agent.turn("wait, actually 5 people")
    assert "(Updated household_size: 2 -> 5.)" in reply
    assert agent.state.household_size == 5
    assert len(agent._corrections) == 1
    rec = agent._corrections[0]
    assert rec["field"] == "household_size"
    assert rec["old"] == 2 and rec["new"] == 5


def test_multiple_corrections_in_one_turn_are_all_acknowledged():
    """Regression: an earlier implementation returned after the FIRST
    tool call, dropping corrections from any subsequent call in the same
    turn. This drives two update_state calls in one turn and asserts BOTH
    corrections are acknowledged."""
    llm = ScriptedLLM(
        [
            _update(household_size=2, gross_monthly_income=1000.0),
            {
                "content": "updating both",
                "tool_calls": [
                    {"name": "update_state", "input": {"household_size": 4}},
                    {"name": "update_state", "input": {"gross_monthly_income": 3000.0}},
                ],
            },
        ]
    )
    agent = CivicReachAgent(llm_client=llm)
    agent.turn("2 of us, $1000/mo")
    reply = agent.turn("no wait, 4 of us and I make $3000")
    assert "(Updated household_size: 2 -> 4.)" in reply
    assert "(Updated gross_monthly_income: 1000.0 -> 3000.0.)" in reply
    assert agent.state.household_size == 4
    assert agent.state.gross_monthly_income == 3000.0
    assert len(agent._corrections) == 2


def test_same_value_restated_is_not_a_correction():
    llm = ScriptedLLM([_update(gross_monthly_income=1500.0), _update(gross_monthly_income=1500.0)])
    agent = CivicReachAgent(llm_client=llm)
    agent.turn("$1500 a month")
    reply = agent.turn("yes, $1500")
    assert "Updated" not in reply
    assert agent._corrections == []


def test_corrections_surface_in_context():
    llm = ScriptedLLM([_update(household_size=2), _update(household_size=6)])
    agent = CivicReachAgent(llm_client=llm)
    agent.turn("2 people")
    agent.turn("actually 6")
    # The next context payload the model sees must include the trail.
    ctx = agent._context()
    assert "corrections" in ctx
    assert "household_size" in ctx
