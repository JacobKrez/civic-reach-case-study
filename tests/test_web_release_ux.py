"""Reviewer-path UI contracts that do not require a physical device."""

from fastapi.testclient import TestClient

import src.web as web


def test_crisis_focus_is_not_stolen_by_submit_finally():
    with TestClient(web.app) as client:
        html = client.get("/").text
    assert "let responseWasCrisis = false" in html
    assert "responseWasCrisis = crisis" in html
    assert "if(!responseWasCrisis) input.focus()" in html


def test_accessibility_landmarks_and_live_regions_are_present():
    with TestClient(web.app) as client:
        html = client.get("/").text
    for contract in (
        'href="#main-content"',
        'role="main"',
        'role="alert"',
        'aria-live="assertive"',
        'aria-live="polite"',
        'aria-modal="true"',
        "resetDialog.showModal()",
        "resetDialog.addEventListener('cancel'",
        "target.focus()",
        "overflow-x:hidden",
        "font-size:16px",
    ):
        assert contract in html


def test_spanish_page_declares_language_and_translated_reset_dialog():
    with TestClient(web.app) as client:
        html = client.get("/?lang=es").text
    assert '<html lang="es">' in html
    assert "¿Iniciar una nueva evaluación?" in html
    assert "Conservar esta evaluación" in html


def test_crisis_transport_has_no_toast_events():
    web.clear_sessions()
    with TestClient(web.app) as client:
        response = client.post(
            "/chat",
            json={"session_id": "release-crisis", "message": "I want to kill myself"},
        )
    assert response.status_code == 200
    assert response.headers["X-FoodPath-Layer"] == "crisis"
    assert response.json()["events"] == []
    assert all(token in response.json()["reply"] for token in ("911", "988", "211"))
    web.clear_sessions()
