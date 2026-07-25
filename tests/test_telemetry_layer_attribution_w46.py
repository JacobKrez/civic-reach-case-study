"""W46 regression coverage for truthful telemetry layer attribution."""

import json

from src.agent import FoodPathAgent


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages, system=None, tools=None):
        return next(self.responses)


def _response(**fields):
    return {
        "content": "noted",
        "tool_calls": [{"name": "update_state", "input": fields}],
    }


def test_intake_turn_is_not_mislabeled_as_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVICREACH_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    agent = FoodPathAgent(llm_client=ScriptedLLM([_response(household_size=2)]))

    agent.turn("2 people")

    event = json.loads(next(tmp_path.glob("turns-*.jsonl")).read_text())
    assert event["layer"] == "intake"
    assert agent.last_layer == "intake"


def test_completed_screen_is_attributed_to_calculator(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVICREACH_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    agent = FoodPathAgent(llm_client=ScriptedLLM([
        _response(household_size=2),
        _response(gross_monthly_income=1800),
        _response(has_elderly_or_disabled_member=False),
    ]))

    agent.turn("2 people")
    agent.turn("1800")
    agent.turn("no")

    events = [json.loads(line) for line in next(tmp_path.glob("turns-*.jsonl")).read_text().splitlines()]
    assert events[-1]["layer"] == "calculator"