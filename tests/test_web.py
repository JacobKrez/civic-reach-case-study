from fastapi.testclient import TestClient
import pytest

import src.web as web
from src.agent import FoodPathAgent


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
    monkeypatch.setattr(web, "agent_factory", lambda: FoodPathAgent(llm_client=IntakeLLM()))
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
    assert set(body) == {
        "reply", "state", "missing_fields", "eligibility_result", "citations", "events"
    }


def test_separate_sessions_do_not_share_state(client):
    client.post("/chat", json={"session_id": "one", "message": "household 3"})
    other = client.post("/chat", json={"session_id": "two", "message": "income 1200"}).json()
    assert other["state"]["household_size"] is None
    assert other["state"]["gross_monthly_income"] == 1200.0


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "sessions": 0}


def test_health_exposes_live_session_count(client):
    client.post("/chat", json={"session_id": "counted", "message": "household 2"})
    assert client.get("/health").json() == {"status": "ok", "sessions": 1}


def test_reset_drops_agent_and_returns_unused_fresh_id(client):
    client.post("/chat", json={"session_id": "old", "message": "household 4"})
    response = client.post("/session/reset", json={"session_id": "old"})
    assert response.status_code == 200
    body = response.json()
    assert body["reset"] is True
    assert body["previous_session_found"] is True
    assert body["session_id"] != "old"
    assert client.get("/health").json()["sessions"] == 0

    new_turn = client.post(
        "/chat", json={"session_id": body["session_id"], "message": "income 1200"}
    ).json()
    assert new_turn["state"]["household_size"] is None
    assert new_turn["state"]["gross_monthly_income"] == 1200.0


def test_session_registry_evicts_least_recently_used_at_cap(client, monkeypatch):
    monkeypatch.setattr(web, "MAX_SESSIONS", 2)
    client.post("/chat", json={"session_id": "oldest", "message": "household 1"})
    client.post("/chat", json={"session_id": "newer", "message": "household 2"})
    # Touch oldest: newer is now the least-recently-used entry.
    client.post("/chat", json={"session_id": "oldest", "message": "income 1000"})
    client.post("/chat", json={"session_id": "newest", "message": "household 3"})
    assert client.get("/health").json()["sessions"] == 2
    assert "newer" not in web._sessions
    assert "oldest" in web._sessions


def test_session_registry_expires_idle_entries(client, monkeypatch):
    client.post("/chat", json={"session_id": "stale", "message": "household 1"})
    with web._sessions_lock:
        last_accessed = web._sessions["stale"].last_accessed
    # Add 1 second past TTL so the `now - last_accessed >= SESSION_TTL_SECONDS`
    # boundary test is unambiguous -- `last_accessed + SESSION_TTL_SECONDS`
    # exactly lands on the boundary and loses to float-rounding on some
    # timing paths (the bug shadowed an unrelated pre-W51 test flake).
    monkeypatch.setattr(
        web.time,
        "monotonic",
        lambda: last_accessed + web.SESSION_TTL_SECONDS + 1,
    )
    assert client.get("/health").json()["sessions"] == 0
    assert "stale" not in web._sessions


def test_chat_emits_field_and_correction_toast_events(client):
    first = client.post(
        "/chat", json={"session_id": "events", "message": "income 2200"}
    ).json()
    assert first["events"] == [
        {"type": "field_recorded", "field": "gross_monthly_income", "value": 2200.0}
    ]
    corrected = client.post(
        "/chat", json={"session_id": "events", "message": "income 1800"}
    ).json()
    assert corrected["events"] == [
        {
            "type": "correction_applied",
            "field": "gross_monthly_income",
            "old": 2200.0,
            "new": 1800.0,
        }
    ]


def test_crisis_reply_is_never_routed_to_toast_layer(client):
    response = client.post(
        "/chat", json={"session_id": "crisis", "message": "I want to kill myself"}
    )
    assert response.status_code == 200
    assert response.headers["X-FoodPath-Layer"] == "crisis"
    assert response.json()["state"]["is_crisis"] is True
    assert response.json()["events"] == []


def test_crisis_toast_suppression_is_a_hard_stop():
    events = web._toast_events_for_turn(
        {"gross_monthly_income": 2200.0},
        {"gross_monthly_income": 1800.0},
        None,
        {"eligible": True},
        crisis_reply=True,
    )
    assert events == []


def test_page_has_accessible_reset_dialog_and_bounded_toasts(client):
    html = client.get("/").text
    assert "Start a new screening" in html
    assert 'aria-modal="true"' in html
    assert "This will erase what you've told me so far" in html
    assert "resetDialog.addEventListener('cancel'" in html
    assert "resetDialog.addEventListener('keydown'" in html
    assert "target.focus()" in html
    assert 'aria-live="polite"' in html
    assert 'aria-live="assertive"' in html
    assert "MAX_TOASTS = 4" in html
    assert "mouseenter" in html and "focusin" in html
    assert "toast-close" in html
    assert "toast(t.handoffReady, 'success')" in html
    assert "if(i === step) attrs.push('aria-current=\"step\"')" in html
    # Network/server failures are transient UI notifications—not chat bubbles.
    assert "toast(t.network + ': ' + message, 'error')" in html


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
    monkeypatch.setattr(web, "agent_factory", lambda: FoodPathAgent(llm_client=GroundedLLM()))
    web.clear_sessions()
    with TestClient(web.app) as client:
        body = client.post(
            "/chat",
            json={"session_id": "grounded", "message": "2 people, $1,500/month, no elderly/disabled"},
        ).json()
    web.clear_sessions()
    assert body["eligibility_result"] is not None
    assert {"type": "verdict_ready"} in body["events"]
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
