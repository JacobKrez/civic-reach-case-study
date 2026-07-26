"""W55 regression: stale model income cannot land on an unrelated turn."""

import os

os.environ.setdefault("GUARDRAIL_CLASSIFIER", "off")

from src.agent import FoodPathAgent


class StaleIncomeLLM:
    def chat(self, messages, system=None, tools=None):
        import json

        context = json.loads(messages[-1]["content"])
        if "household_size" in context["missing_fields"]:
            return {
                "content": "ok",
                "tool_calls": [{"name": "update_state", "input": {"household_size": 2}}],
            }
        return {
            "content": "ok",
            "tool_calls": [
                {"name": "update_state", "input": {"gross_monthly_income": 1500}}
            ],
        }


def test_w55_stale_income_rejected_without_income_cue():
    agent = FoodPathAgent(llm_client=StaleIncomeLLM())
    agent.turn("There are 2 of us in the house.")
    agent.turn("No elderly or disabled in our household.")
    assert agent.state.gross_monthly_income is None
