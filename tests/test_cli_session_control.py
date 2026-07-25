"""W50 CLI session-control parity tests."""

from io import StringIO

from src.agent import FoodPathAgent
from src.cli import _BANNER, _run_repl


class RecordingIntakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, system=None, tools=None):
        self.calls += 1
        return {
            "content": "Recorded.",
            "tool_calls": [
                {
                    "name": "update_state",
                    "input": {"household_size": self.calls},
                }
            ],
        }


def test_banner_documents_new_and_restart_controls():
    assert "new" in _BANNER.lower()
    assert "restart" in _BANNER.lower()


def test_new_command_replaces_agent_state_but_reuses_client(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    llm = RecordingIntakeLLM()
    original = FoodPathAgent(llm_client=llm, retriever=lambda _q: "")
    output = StringIO()

    result = _run_repl(
        original,
        input_stream=StringIO("household 4\nnew\nhousehold 2\nexit\n"),
        output=output,
    )

    assert result == 0
    assert original.state.household_size == 1
    assert llm.calls == 2
    text = output.getvalue()
    assert "New screening started" in text
    assert "Previous answers were erased" in text
    assert "Goodbye." in text


def test_restart_is_alias_for_new(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    output = StringIO()
    result = _run_repl(
        FoodPathAgent(llm_client=RecordingIntakeLLM(), retriever=lambda _q: ""),
        input_stream=StringIO("restart\nexit\n"),
        output=output,
    )
    assert result == 0
    assert "New screening started" in output.getvalue()
