from fastapi.testclient import TestClient
import pytest

import src.web as web
from src.agent import CivicReachAgent


class IntakeLLM:
    """Deterministic tool-calling LLM for web transport/session tests."""

    def chat(self, messages, system=None, tools=None):
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        raw = user_messages[-2] if len(user_messages) >= 2 else user_messages[0]
        value = raw.lower()
        if "household" in value:
            update = {"household_size": int(value.split()[-1])}
            reply = "Household size saved. What is your gross monthly income?"
        elif "income" in value:
            update = {"gross_monthly_income": float(value.split()[-1])}
            reply = "Income saved. Is anyone age 60+ or disabled?"
        else:
            update = {}
            reply = "What is your household size?"
        return {"content": reply, "tool_calls": [{"name": "update_state", "input": update}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    monkeypatch.setattr(web, "agent_factory", lambda: CivicReachAgent(llm_client=IntakeLLM()))
    web.clear_sessions()
    with TestClient(web.app) as test_client:
        yield test_client
    web.clear_sessions()


def test_root_returns_self_contained_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Live state inspector" in response.text
    assert "<script" in response.text


def test_root_includes_mobile_and_high_stakes_message_treatments(client):
    html = client.get("/").text
    assert "font-size:16px" in html
    assert "100dvh" in html
    assert "overflow-x:hidden" in html
    assert "crisis-response" in html
    assert "d.setAttribute('role','alert')" in html
    assert "citation-suffix" in html
    assert "#handoff-summary" in html


def test_chat_round_trips_state_within_session(client):
    first = client.post("/chat", json={"session_id": "same", "message": "household 4"})
    assert first.status_code == 200
    assert first.json()["state"]["household_size"] == 4
    assert "gross_monthly_income" in first.json()["missing_fields"]

    second = client.post("/chat", json={"session_id": "same", "message": "income 2500"})
    body = second.json()
    assert second.status_code == 200
    assert body["state"]["household_size"] == 4
    assert body["state"]["gross_monthly_income"] == 2500.0
    assert body["missing_fields"] == ["has_elderly_or_disabled_member"]
    assert set(body) == {"reply", "state", "missing_fields", "eligibility_result", "citations"}


def test_separate_sessions_do_not_share_state(client):
    client.post("/chat", json={"session_id": "one", "message": "household 3"})
    other = client.post("/chat", json={"session_id": "two", "message": "income 1200"}).json()
    assert other["state"]["household_size"] is None
    assert other["state"]["gross_monthly_income"] == 1200.0


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_structured_citation_panel_chrome(client):
    """W34: page chrome carries the citation-row CSS hooks used by the
    grounding inspector, so a reviewer can see snippet + score and a
    distinct 'computed limit' entry rather than just filenames."""
    html = client.get("/").text
    assert "citation-row" in html
    assert "citation-row--structured" in html
    assert "citation-kind-badge" in html
    assert "citation-score" in html
    assert "citation-snippet" in html
    assert "Computed limit (source of truth)" in html
    assert "renderCitation" in html


class GroundedLLM:
    """Completes intake in one turn so the inspector has citations to render."""

    def __init__(self):
        self._done = False

    def chat(self, messages, system=None, tools=None):
        if not self._done:
            self._done = True
            return {
                "content": "Here is your screening result.",
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
        return {"content": "Anything else?", "tool_calls": []}


def test_structured_limit_row_appears_with_kind_and_snippet(monkeypatch):
    """W34: once intake completes, the inspector exposes W33's structured
    limit-row block as a distinct, clearly-labelled 'computed limit'
    entry (not just a KB doc hit)."""
    monkeypatch.setenv("GUARDRAIL_CLASSIFIER", "off")
    monkeypatch.setattr(web, "agent_factory", lambda: CivicReachAgent(llm_client=GroundedLLM()))
    web.clear_sessions()
    with TestClient(web.app) as client:
        body = client.post(
            "/chat",
            json={"session_id": "grounded", "message": "2 people, $1,500/month, no elderly/disabled"},
        ).json()
    web.clear_sessions()
    assert body["eligibility_result"] is not None
    citations = body["citations"]
    assert citations, "expected citations after the result turn"
    structured = [c for c in citations if c.get("kind") == "structured"]
    assert len(structured) == 1, "exactly one structured limit-row block is expected"
    row = structured[0]
    assert row["doc"] == "calculator.income_limits()"
    assert "snippet" in row and "Household size 2" in row["snippet"]
    # And every citation (structured + KB hits) exposes a score so the
    # inspector can render a relevance number next to each entry.
    for c in citations:
        assert isinstance(c.get("score"), (int, float))
