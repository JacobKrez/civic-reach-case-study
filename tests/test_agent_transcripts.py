"""Scripted core-loop tests; no provider API is called."""

from unittest.mock import Mock

from src.agent import CivicReachAgent
from src.guardrails.classifier import CLASSIFIER_SYSTEM_PROMPT


class FakeLLMClient:
    def __init__(self):
        self.responses = iter(
            [
                {
                    "content": "Thanks. What is your household's gross monthly income?",
                    "tool_calls": [
                        {"name": "update_state", "input": {"household_size": 3}}
                    ],
                },
                {
                    "content": "Does anyone in the household meet the elderly or disabled criteria?",
                    "tool_calls": [
                        {"name": "update_state", "input": {"gross_monthly_income": 1800}}
                    ],
                },
                {
                    "content": "Your initial screen indicates you likely qualify under the retrieved gross-income rule.",
                    "tool_calls": [
                        {
                            "name": "update_state",
                            "input": {"has_elderly_or_disabled_member": False},
                        }
                    ],
                },
                {"content": "Your initial screen indicates you likely qualify under the retrieved gross-income rule.", "tool_calls": []},
                {"content": "The completed result remains available.", "tool_calls": []},
            ]
        )
        self.calls = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        # The classifier layer runs before the agent's main LLM call.
        # Reply "normal" without consuming a queued response so the
        # scripted queue contract stays intact across the extra call.
        if system == CLASSIFIER_SYSTEM_PROMPT:
            return {"content": "normal", "tool_calls": []}
        return next(self.responses)


def test_scripted_transcript_accumulates_state_and_calculates_once(monkeypatch):
    calculator_spy = Mock(
        return_value={
            "qualifies": True,
            "test_used": "gross",
            "limit": 2887.08,
            "income_considered": 1800.0,
            "household_size": 3,
            "notes": "Retrieved-rule grounded deterministic result.",
        }
    )
    monkeypatch.setattr("src.agent.calculate_eligibility", calculator_spy)
    fake = FakeLLMClient()
    agent = CivicReachAgent(llm_client=fake)

    replies = [
        agent.turn("There are three of us."),
        agent.turn("We make $1,800 gross each month."),
        agent.turn("No, nobody is elderly or disabled."),
        agent.turn("Can you repeat the result?"),
    ]

    assert agent.state.household_size == 3
    assert agent.state.gross_monthly_income == 1800
    assert agent.state.has_elderly_or_disabled_member is False
    assert agent.state.turn_count == 4
    assert agent.state.missing_fields() == []
    assert "how many" not in replies[0].lower()
    assert "gross monthly income" not in replies[1].lower()
    assert "likely qualify" in replies[2].lower()
    calculator_spy.assert_called_once_with(
        household_size=3,
        gross_monthly_income=1800.0,
        has_elderly_or_disabled=False,
    )
    # The classifier runs first (call 0, system=CLASSIFIER_SYSTEM_PROMPT, no tools);
    # the agent's main call (call 1, system=SYSTEM_PROMPT) carries the tools.
    assert fake.calls[0]["system"] == CLASSIFIER_SYSTEM_PROMPT
    assert fake.calls[0]["tools"] is None
    assert fake.calls[1]["tools"][0]["name"] == "update_state"
    assert "missing_fields" in fake.calls[1]["messages"][-1]["content"]


def test_free_text_is_never_parsed_into_state():
    class TextOnlyLLM:
        def chat(self, messages, system=None, tools=None):
            return {
                "content": "I understood household size 9 and income $999.",
                "tool_calls": [],
            }

    agent = CivicReachAgent(llm_client=TextOnlyLLM())
    agent.turn("There are nine people and income is $999.")
    assert agent.state.household_size is None
    assert agent.state.gross_monthly_income is None


def test_work_requirement_question_gets_deterministic_dss_redirect():
    llm = Mock()
    agent = CivicReachAgent(llm_client=llm)
    reply = agent.turn("Do the ABAWD work requirements apply to me?")
    assert "does not evaluate work requirements" in reply
    assert "county DSS" in reply
    llm.chat.assert_not_called()


def test_immigration_status_question_gets_deterministic_dss_redirect():
    llm = Mock()
    agent = CivicReachAgent(llm_client=llm)
    reply = agent.turn("Can a refugee qualify based on immigration status?")
    assert "does not evaluate" in reply
    assert "immigration status" in reply
    assert "county DSS" in reply
    llm.chat.assert_not_called()
