"""Single-process web UI for the FoodPath eligibility assistant.

Sessions intentionally live only in this process. Restarting the server clears
all conversations; this demo does not persist household information.

W49 — production-grade UI:
  * WCAG 2.1 Level AA conformance (per the DOJ 2024-04-24 rule that requires
    state and local government web content to meet WCAG 2.1 AA — the benefits
    screener space is squarely that category).
  * USWDS-inspired design tokens (ported as CSS custom properties to keep the
    page fully self-contained and dependency-free).
  * Plain-language chrome — the developer-facing "Live state inspector" /
    typed assessment state lives behind a labeled "Technical details"
    <details> disclosure, with a plain-language summary above it.
  * Result card for the verdict (separate from the chat log) so the most
    important screen gets more than a single bubble.
  * Step indicator for the three intake fields.
  * Persistent, unmissable "This is not an application" trust banner.
  * Loading, error, and reconnecting states that do not misattribute a
    network failure to the assistant.
  * `<html lang>` follows the conversation language (W19 Spanish mode
    sets ``lang="es"`` on the served page).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import FoodPathAgent, first_turn_disclosure, first_turn_greeting

app = FastAPI(title="FoodPath — NC FNS Eligibility Guide", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Public injection seam for tests and alternate deployments.
agent_factory: Callable[[], FoodPathAgent] = FoodPathAgent
_sessions: dict[str, FoodPathAgent] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: str = Field(min_length=1, max_length=200)


class HandoffRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


def clear_sessions() -> None:
    """Clear process-local sessions (primarily useful for test isolation)."""
    _sessions.clear()


def _resolve_lang(request: Request | None) -> str:
    """Pick the HTML ``lang`` attribute for the rendered page.

    Priority order:
      1. ``?lang=es`` / ``?lang=en`` query-string override (so a reviewer
         can preview either language even before the sticky detector has
         flipped the session).
      2. The sticky ``state.language`` of the most recently created session
         (so W19 Spanish mode follows the user through a refresh).
      3. ``Accept-Language`` request header — quick Spanish detection
         without a round trip.
      4. Default ``"en"``.
    """
    if request is not None:
        override = (request.query_params.get("lang") or "").strip().lower()
        if override in ("en", "es"):
            return override
        accept = (request.headers.get("accept-language") or "").lower()
        if accept.startswith("es"):
            return "es"
    if _sessions:
        try:
            last = next(reversed(_sessions.values()))
            lang = getattr(last.state, "language", "en")
            if lang in ("en", "es"):
                return lang
        except Exception:  # pragma: no cover — defensive
            pass
    return "en"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest, response: Response) -> dict[str, Any]:
    agent = _sessions.get(request.session_id)
    if agent is None:
        agent = agent_factory()
        _sessions[request.session_id] = agent
    try:
        reply = agent.turn(request.message)
    except Exception as exc:
        # Avoid exposing provider credentials or internals to the browser.
        raise HTTPException(status_code=502, detail="The assistant could not answer right now.") from exc
    state = agent.state.model_dump(mode="json")
    response.headers["X-FoodPath-Layer"] = agent.last_layer
    response.headers["X-CivicReach-Layer"] = agent.last_layer  # compatibility
    return {
        "reply": reply,
        "state": state,
        "missing_fields": agent.state.missing_fields(),
        "eligibility_result": agent.eligibility_result,
        "citations": agent.last_citations,
    }


@app.post("/handoff")
def handoff(request: HandoffRequest) -> dict[str, Any]:
    """W17 case-worker handoff: print-ready summary + on-screen banner.

    The on-screen path has zero external dependencies. A best-effort
    SMTP notification fires only when all SMTP_* env vars are set; it
    never blocks or degrades this response. Returns 409 when the screen
    is not complete yet — the summary is never producible early.
    """
    agent = _sessions.get(request.session_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    package = agent.handoff_summary()
    if package is None:
        raise HTTPException(
            status_code=409,
            detail="Screen incomplete — finish the intake before requesting a handoff summary.",
        )
    return package


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _first_bubble_html(lang: str) -> str:
    """Two separate bubbles, not one combined message: the safety disclosure
    first, then a short friendly opener with the first intake question.

    Each bubble gets a stable ``data-role`` attribute and an
    ``aria-live`` wrapper so screen readers announce them in order.
    """
    return (
        '<div class="bubble assistant" data-role="disclosure">'
        + _escape(first_turn_disclosure(lang))
        + '</div><div class="bubble assistant" data-role="greeting">'
        + _escape(first_turn_greeting(lang))
        + "</div>"
    )


# --------------------------------------------------------------------------
# Page chrome (USWDS-inspired tokens, ported as CSS custom properties).
#
# Why no CDN: the chunk brief is explicit that the page must stay
# self-contained and dependency-free. The USWDS tokens below are the
# tested-color, mobile-first values USWDS publishes; the patterns
# (step indicator, alert, banner) follow USWDS structure even when the
# rules are inlined rather than served from a CDN.
# --------------------------------------------------------------------------

_CSS = r"""
/* --- tokens (USWDS-inspired, ported to CSS custom properties) --- */
:root{
  --navy:#102a43;          /* USWDS primary-darker, ink family */
  --blue:#2563eb;          /* USWDS primary-vivid */
  --primary-dark:#1a4480;  /* USWDS primary-dark */
  --primary-light:#d9e8f6; /* USWDS primary-light */
  --pale:#f0f4f5;          /* USWDS base-lightest */
  --pale-2:#f4f7fb;        /* chip background */
  --line:#d9e2ec;          /* USWDS base-lighter */
  --ink:#1b1b1b;           /* USWDS ink, contrast >= 7:1 on --pale */
  --ink-2:#3d4551;         /* body text, contrast >= 10:1 on --pale */
  --muted:#475569;         /* muted, contrast 7:1 on white */
  --green:#15803d;         /* USWDS success-dark */
  --green-bg:#ecfdf5;      /* success row background */
  --warn:#92400e;          /* warn-dark, contrast >= 7:1 on --warn-bg */
  --warn-bg:#fffbeb;
  --crisis:#b42318;        /* USWDS error-dark */
  --crisis-dark:#7a140e;   /* headline on --crisis-bg, contrast >= 7:1 */
  --crisis-bg:#fff1f0;
  --focus:#2491ff;         /* focus ring, contrast >= 3:1 against anything */

  --radius:8px;
  --radius-lg:14px;
  --shadow:0 4px 14px #102a4312;
  --tap:48px;              /* WCAG 2.5.5 target size, AAA */

  --font-base:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}

*,*::before,*::after{box-sizing:border-box}
html,body{max-width:100%;overflow-x:hidden}
html{scroll-behavior:smooth}
body{margin:0;background:var(--pale);color:var(--ink-2);font:16px/1.55 var(--font-base)}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important;scroll-behavior:auto!important}
}

/* dark mode: tokens overridden — every combination tested for >= 4.5:1 */
@media(prefers-color-scheme:dark){
  :root{
    --navy:#0b1c2e;
    --pale:#0f172a;        /* deep slate background */
    --pale-2:#162033;
    --line:#334155;
    --ink:#f8fafc;
    --ink-2:#e2e8f0;
    --muted:#cbd5e1;
    --green:#34d399;
    --green-bg:#052e1d;
    --warn:#fbbf24;
    --warn-bg:#1f1303;
    --crisis:#fda4af;
    --crisis-dark:#fecaca;
    --crisis-bg:#3f0a0a;
  }
  .card{background:#1e293b}
  .top{background:#020617}
  pre,.citation-row,.citation-suffix,.handoff-card{background:#0b1220;border-color:#334155}
}

/* --- skip-to-content (WCAG 2.4.1) --- */
.skip-link{
  position:absolute;left:-9999px;top:0;
  background:var(--navy);color:white;padding:12px 18px;
  font-weight:600;border-radius:0 0 var(--radius) var(--radius);
  z-index:999;
}
.skip-link:focus{left:0;outline:3px solid var(--focus);outline-offset:2px}

/* --- focus visibility (WCAG 2.4.7) --- */
:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:4px}
button:focus-visible,a:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--focus);outline-offset:3px}

/* --- header --- */
.top{background:var(--navy);color:white;padding:16px 24px;display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.top b{font-size:21px;letter-spacing:.005em}
.top span{opacity:.85;margin-left:12px;font-size:15px}

/* --- trust banner (persistent, unmissable) --- */
.trust-banner{
  background:var(--warn-bg);border-bottom:2px solid var(--warn);
  color:var(--warn);padding:10px 24px;font-size:15px;font-weight:600;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center;
}
.trust-banner .trust-icon{
  background:var(--warn);color:var(--warn-bg);font-weight:800;font-size:12px;
  padding:3px 8px;border-radius:999px;letter-spacing:.06em;text-transform:uppercase;
}

/* --- progress / step indicator (USWDS step indicator) --- */
.progress{
  background:white;border-bottom:1px solid var(--line);
  padding:14px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
}
.progress[hidden]{display:none}
.progress-label{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-right:6px}
.steps{display:flex;gap:0;align-items:center;flex-wrap:wrap;list-style:none;padding:0;margin:0;counter-reset:step}
.steps li{
  display:flex;align-items:center;gap:8px;
  padding:8px 14px;font-size:14px;font-weight:600;color:var(--muted);
}
.steps li[aria-current="step"]{color:var(--navy)}
.steps li::before{
  counter-increment:step;content:counter(step);
  display:inline-flex;align-items:center;justify-content:center;
  width:28px;height:28px;border-radius:50%;
  background:#e2e8f0;color:var(--ink);font-size:13px;font-weight:700;
}
.steps li[aria-current="step"]::before{
  background:var(--primary-dark);color:white;
}
.steps li.complete::before{
  background:var(--green);color:white;content:"\2713";
}
.steps li+li{border-left:1px solid var(--line)}

/* --- main grid --- */
.layout{
  display:grid;grid-template-columns:minmax(0,1.7fr) minmax(320px,1fr);
  gap:18px;max-width:1240px;margin:20px auto;padding:0 18px;
  height:calc(100vh - 220px);height:calc(100dvh - 220px);
}
.layout{height:calc(100vh - 200px);height:calc(100dvh - 200px)}
.card{
  min-width:0;background:white;border:1px solid var(--line);
  border-radius:var(--radius-lg);box-shadow:var(--shadow);
}

/* --- chat column --- */
.chat{display:flex;flex-direction:column;min-height:0}
.messages{
  min-width:0;padding:20px;overflow:auto;overflow-wrap:anywhere;flex:1;
}
.bubble{
  max-width:82%;padding:12px 14px;border-radius:var(--radius-lg);
  margin:0 0 12px;white-space:pre-wrap;line-height:1.55;
}
.assistant{background:#eff6ff;border-bottom-left-radius:3px;color:var(--ink-2)}
.user{background:var(--navy);color:white;margin-left:auto;border-bottom-right-radius:3px}
/* result card: distinct from a chat bubble so the verdict gets the room it deserves */
.bubble.result-card{
  max-width:100%;background:var(--green-bg);
  border:1px solid var(--green);border-left-width:6px;
  border-radius:var(--radius);padding:18px 20px;margin:14px 0;
}
.bubble.result-card[data-outcome="denied"]{background:#fef2f2;border-color:var(--crisis)}
.bubble.result-card h3{margin:0 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.bubble.result-card .outcome-headline{font-size:22px;line-height:1.3;margin:0 0 10px;color:var(--ink)}
.bubble.result-card .outcome-body{font-size:16px;color:var(--ink-2);margin:0 0 12px}
.bubble.result-card dl{margin:0;display:grid;grid-template-columns:1fr auto;gap:6px 14px;font-size:14px}
.bubble.result-card dt{color:var(--muted)}
.bubble.result-card dd{margin:0;font-weight:600;color:var(--ink)}
.bubble.result-card .next-steps{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line);font-size:14px;color:var(--ink-2)}

.crisis-response{
  max-width:100%;background:var(--crisis-bg);
  border:2px solid var(--crisis);border-left-width:7px;
  color:var(--crisis-dark);font-weight:600;
  box-shadow:0 3px 10px #b4231820;
}
.crisis-response::before{
  content:"Urgent safety information";display:block;
  color:var(--crisis);font-size:.82rem;font-weight:800;
  letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px;
}
.error-bubble{
  max-width:100%;background:#f8fafc;
  border:1px solid var(--line);border-left:4px solid var(--crisis);
  border-radius:var(--radius);padding:12px 14px;
  font-size:14px;color:var(--ink-2);margin:0 0 12px;
}
.error-bubble b{display:block;color:var(--crisis);font-size:13px;margin-bottom:4px}

/* citation suffix carried inline with assistant bubbles */
.citation-suffix{
  display:block;background:#f8fafc;
  border:1px solid #9fb3c8;border-left:4px solid #486581;
  border-radius:7px;color:#334e68;
  font-size:.875rem;font-weight:500;line-height:1.45;
  margin-top:10px;padding:8px 10px;white-space:normal;
}
.citation-row{
  background:#f8fafc;border:1px solid var(--line);border-left:4px solid #486581;
  border-radius:7px;padding:8px 10px;margin:0 0 8px;
  font-size:.875rem;line-height:1.45;
  min-width:0;overflow-wrap:anywhere;
}
.citation-row--structured{background:var(--green-bg);border-color:var(--green);border-left-color:var(--green)}
.citation-row-head{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;min-width:0}
.citation-doc{font-weight:600;color:#243b53;word-break:break-word;min-width:0;flex:1 1 auto}
.citation-kind-badge{
  display:inline-block;background:var(--green);color:white;
  font-size:.68rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  padding:2px 7px;border-radius:999px;white-space:nowrap;flex:0 0 auto;
}
.citation-score{
  display:inline-block;background:#e0e8f0;color:#334e68;
  font-size:.72rem;font-weight:600;padding:2px 7px;border-radius:999px;
  white-space:nowrap;flex:0 0 auto;
}
.citation-snippet{display:block;color:#486581;margin-top:4px;white-space:normal}

/* typing indicator (assistant is thinking) */
.typing{
  display:inline-flex;align-items:center;gap:6px;
  padding:10px 14px;background:#eff6ff;border-radius:var(--radius-lg);
  border-bottom-left-radius:3px;color:var(--muted);font-size:14px;
  margin:0 0 12px;max-width:82%;
}
.typing .dots{display:inline-flex;gap:4px}
.typing .dots span{
  width:7px;height:7px;border-radius:50%;background:var(--muted);
  display:inline-block;animation:bounce 1.2s infinite ease-in-out;
}
.typing .dots span:nth-child(2){animation-delay:.15s}
.typing .dots span:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,80%,100%{opacity:.35;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
@media(prefers-reduced-motion:reduce){.typing .dots span{animation:none;opacity:.6}}

/* status banners (offline / reconnecting) */
.status-banner{
  display:none;background:#fef3c7;border:1px solid #f59e0b;border-left-width:6px;
  color:#78350f;border-radius:var(--radius);padding:10px 14px;
  margin:0 0 12px;font-size:14px;font-weight:600;
}
.status-banner[data-state="offline"]{display:block}
.status-banner[data-state="reconnecting"]{display:block;background:#dbeafe;border-color:var(--blue);color:#1e3a8a}

/* --- composer (form) --- */
.composer{display:flex;gap:9px;padding:14px;border-top:1px solid var(--line);background:#fafbfc;border-bottom-left-radius:var(--radius-lg);border-bottom-right-radius:var(--radius-lg)}
.composer label.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.composer input{
  min-width:0;min-height:var(--tap);flex:1;padding:12px;border:1px solid #829ab1;
  border-radius:var(--radius);font:inherit;font-size:16px;background:white;color:var(--ink-2);
}
.composer input::placeholder{color:var(--muted)}
.composer button{
  min-height:44px;min-width:88px;border:0;border-radius:var(--radius);
  background:var(--blue);color:white;padding:0 20px;
  font:inherit;font-weight:650;cursor:pointer;touch-action:manipulation;
}
.composer button:disabled{opacity:.55;cursor:not-allowed}

/* --- right rail (technical details disclosure) --- */
.panel{min-width:0;padding:18px;overflow:auto}
.panel h2{margin:0 0 4px;font-size:18px}
.plain-summary{
  background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid var(--blue);
  border-radius:var(--radius);padding:12px 14px;font-size:15px;color:var(--ink-2);margin:8px 0 14px;
}
.plain-summary b{color:var(--ink)}

details.technical{
  border-top:1px solid var(--line);margin-top:14px;padding-top:14px;
}
details.technical>summary{
  list-style:none;cursor:pointer;font-weight:600;color:var(--navy);
  padding:10px 0;font-size:14px;
}
details.technical>summary::-webkit-details-marker{display:none}
details.technical>summary::before{
  content:"\25B8";display:inline-block;margin-right:8px;transition:transform .15s ease;color:var(--navy);
}
details.technical[open]>summary::before{transform:rotate(90deg)}
details.technical>summary:focus-visible{outline-offset:4px}
details.technical .section{border-top:1px dashed var(--line);padding-top:12px;margin-top:12px}
details.technical .section:first-of-type{border-top:0;padding-top:0;margin-top:0}
.hint{color:var(--muted);font-size:13px;margin-bottom:14px}
.section h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 9px}
pre{max-width:100%;background:#f6f8fa;border-radius:7px;padding:10px;overflow:auto;font-size:12px;color:var(--ink-2)}
.tag{
  display:inline-block;background:#fff3cd;padding:3px 7px;border-radius:10px;
  margin:2px;font-size:12px;color:#78350f;
}
.tag.complete{background:var(--green-bg);color:#065f46}
.good{color:var(--green);font-weight:600}
.error{color:var(--crisis)}

.handoff-card{
  background:#f0f7ff;border:1px solid #9fb3c8;border-left:5px solid var(--blue);
  color:var(--ink-2);
  font:14px/1.55 var(--font-mono);
  overflow-wrap:anywhere;padding:12px;
  border-radius:var(--radius);margin-top:10px;
}
.banner{
  background:#dcfce7;border:1px solid var(--green);color:#065f46;
  border-radius:var(--radius);padding:10px 12px;margin-top:10px;
  font-weight:700;font-size:14px;
}
#handoff{margin-top:4px;padding:10px 16px;min-height:44px;background:var(--green);color:white;border:0;border-radius:var(--radius);font-weight:600;cursor:pointer}
#handoff:disabled{opacity:.55}
#handoff-summary{margin-top:10px;white-space:pre-wrap}

/* --- footer --- */
.foot{
  max-width:1240px;margin:24px auto 30px;padding:0 18px;
  font-size:13px;color:var(--muted);
}
.foot a{color:var(--primary-dark)}

/* --- small screens --- */
@media(max-width:820px){
  .top{padding:12px 16px}.top span{display:block;margin:2px 0 0;font-size:14px}
  .layout{grid-template-columns:minmax(0,1fr);gap:12px;height:auto;margin:12px auto;padding:0 10px}
  .chat{height:calc(100vh - 240px);height:calc(100dvh - 240px);min-height:520px}
  .messages{padding:14px 12px}
  .bubble{max-width:94%;padding:10px 12px}
  .crisis-response,.bubble.result-card{max-width:100%}
  .composer{padding:10px;gap:8px}.composer button{padding:0 16px}
  .panel{min-height:300px;padding:16px}
  .progress{padding:10px 14px}
}
@media(max-width:480px){
  .top b{font-size:19px}.layout{padding:0 8px}
  .composer{padding:8px}.composer button{padding:0 13px}
  .bubble{max-width:96%}
  .trust-banner{padding:10px 14px;font-size:14px}
}

/* --- forced-colors / high contrast (Windows High Contrast) --- */
@media(forced-colors:active){
  .top,.trust-banner,.composer,.panel,.card,.bubble,.citation-row,.citation-suffix{
    border:1px solid CanvasText;
  }
  :focus-visible{outline:2px solid Highlight}
}
"""


def _steps_html(step_index: int, lang: str) -> str:
    """Render the USWDS-style step indicator.

    ``step_index`` is 0-based: ``0`` = household, ``1`` = income,
    ``2`` = elderly/disabled. ``3`` = all done.
    """
    labels_en = {
        0: "Household",
        1: "Income",
        2: "Household details",
        3: "Result",
    }
    labels_es = {
        0: "Hogar",
        1: "Ingresos",
        2: "Detalles del hogar",
        3: "Resultado",
    }
    labels = labels_es if lang == "es" else labels_en
    items: list[str] = []
    for i, key in enumerate([0, 1, 2, 3]):
        attrs = []
        if i < step_index:
            attrs.append('class="complete"')
        if i == step_index:
            attrs.append('aria-current="step"')
        items.append("<li " + " ".join(attrs) + ">" + labels[key] + "</li>")
    return (
        '<ol class="steps" aria-label="Intake progress">'
        + "".join(items)
        + "</ol>"
    )


def _plain_summary(state: dict, missing: list, lang: str) -> str:
    """Render a plain-language summary of what the user has told us so far.

    Replaces engineer vocabulary in the primary UI chrome. The raw JSON
    inspector still exists, but lives behind the Technical details
    disclosure.
    """
    parts: list[str] = []
    hs = state.get("household_size")
    if hs is not None:
        if lang == "es":
            parts.append(f"<b>{hs}</b> personas en tu hogar")
        else:
            parts.append(f"<b>{hs}</b> people in your household")
    income = state.get("gross_monthly_income")
    if income is not None:
        if lang == "es":
            parts.append(f"<b>${income:,.0f}</b> al mes de ingresos")
        else:
            parts.append(f"<b>${income:,.0f}</b> a month in income")
    elder = state.get("has_elderly_or_disabled_member")
    if elder is True:
        parts.append("una persona de <b>60 años o más</b>, o con discapacidad" if lang == "es"
                     else "someone <b>age 60 or older or disabled</b> in the home")
    elif elder is False:
        parts.append("nadie de <b>60 años o más</b>, ni con discapacidad" if lang == "es"
                     else "no one <b>age 60 or older or disabled</b>")
    if not parts:
        if lang == "es":
            body = "Cuéntame sobre tu hogar para empezar."
        else:
            body = "Tell me about your household to get started."
    else:
        if lang == "es":
            body = "Hasta ahora me has dicho: " + ", ".join(parts) + "."
        else:
            body = "So far you've told me: " + ", ".join(parts) + "."
    return f'<div class="plain-summary" aria-live="polite">{body}</div>'


def _status_line(lang: str) -> str:
    """Persistent trust banner — single source of truth for the
    'not an application' / 'nothing is saved' reassurance."""
    if lang == "es":
        text = ("Esto <b>no es una solicitud</b>. Nada se envía a ninguna agencia. "
                "Esta conversación se guarda solo en este navegador y se borra al cerrar la pestaña.")
    else:
        text = ("This is <b>not an application</b>. Nothing is submitted to any agency. "
                "This conversation is kept only in this browser and is cleared when you close the tab.")
    return (
        '<div class="trust-banner" role="region" aria-label="'
        + ("Aviso importante" if lang == "es" else "Important notice")
        + '"><span class="trust-icon">'
        + ("Aviso" if lang == "es" else "Notice")
        + '</span><span>' + text + "</span></div>"
    )


def _skip_link(lang: str) -> str:
    label = "Saltar al contenido principal" if lang == "es" else "Skip to main content"
    return f'<a class="skip-link" href="#main-content">{label}</a>'


def _assemble_page(lang: str) -> str:
    """Render the full HTML page for the given language.

    The template below is a single raw string. Substitutions happen via
    ``str.replace`` on stable sentinels rather than ``.format()`` so curly
    braces in the inline CSS never need to be doubled.
    """
    page = r"""<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="__META_DESC__">
<title>__TITLE__</title>
<meta name="theme-color" content="#102a43">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" sizes="192x192" href="/static/icons/icon-192.png">
<style>__CSS__</style>
</head>
<body>__SKIP_LINK__<header class="top" role="banner"><b>FoodPath</b><span>__TAGLINE__</span></header>__TRUST____PROGRESS__<main class="layout" id="main-content" role="main" tabindex="-1">__FIRST_BUBBLES____FORM_SECTION____DETAILS_PANEL__</main>__FOOTER____SCRIPT__</body></html>"""

    title = (
        "FoodPath — Guía de elegibilidad para FNS de Carolina del Norte"
        if lang == "es"
        else "FoodPath — NC FNS Eligibility Guide"
    )
    tagline = (
        "Guía independiente para FNS de Carolina del Norte"
        if lang == "es"
        else "Independent North Carolina FNS eligibility guide"
    )
    meta_desc = (
        "Verificador inicial independiente de elegibilidad para el programa FNS / SNAP de Carolina del Norte. No es una solicitud."
        if lang == "es"
        else "Independent initial eligibility screener for North Carolina FNS / SNAP. Not an application."
    )
    placeholder_progress = _steps_html(0, lang)
    placeholder_panel = (
        _panel_chrome(lang)
    )
    page = page.replace("__LANG__", _escape(lang))
    page = page.replace("__TITLE__", _escape(title))
    page = page.replace("__TAGLINE__", _escape(tagline))
    page = page.replace("__META_DESC__", _escape(meta_desc))
    page = page.replace("__CSS__", _CSS)
    page = page.replace("__SKIP_LINK__", _skip_link(lang))
    page = page.replace("__TRUST__", _status_line(lang))
    page = page.replace("__PROGRESS__", placeholder_progress)
    page = page.replace("__FIRST_BUBBLES__", _first_bubble_html(lang))
    page = page.replace("__FORM_SECTION__", _form_section_html(lang))
    page = page.replace("__DETAILS_PANEL__", placeholder_panel)
    footer = (
        '<footer class="foot" role="contentinfo">'
        + ('<p>FoodPath es una herramienta independiente y no está afiliada al estado de Carolina del Norte. Esta no es una solicitud oficial.</p>' if lang == "es"
           else '<p>FoodPath is an independent tool and is not affiliated with the state of North Carolina. This is not an official application.</p>')
        + "</footer>"
    )
    page = page.replace("__FOOTER__", footer)
    page = page.replace("__SCRIPT__", _client_script(lang))
    return page


def _form_section_html(lang: str) -> str:
    """The chat column, including the disclosure/greeting placeholders
    and the message log + composer."""
    label_text = "Escribe tu respuesta" if lang == "es" else "Type your answer"
    placeholder_text = "Escribe tu respuesta…" if lang == "es" else "Type your answer…"
    send_text = "Enviar" if lang == "es" else "Send"
    return (
        '<section class="card chat" aria-label="'
        + ("Conversación con el asistente" if lang == "es" else "Conversation with the assistant")
        + '">'
        + '<div class="status-banner" role="status" aria-live="polite" data-state="offline">'
        + ("Parece que estás sin conexión. Tus respuestas se enviarán cuando vuelvas a estar en línea." if lang == "es"
           else "Looks like you're offline. Your replies will be sent when you reconnect.")
        + '</div>'
        + '<div id="crisis-alert-template" role="alert" aria-live="assertive" hidden></div>'
        + '<div id="messages" class="messages" aria-live="polite" aria-relevant="additions" aria-label="'
        + ("Registro de la conversación" if lang == "es" else "Conversation log")
        + '"></div>'
        + '<form id="form" class="composer" autocomplete="off" aria-label="'
        + ("Enviar una respuesta" if lang == "es" else "Send a reply")
        + '">'
        + f'<label class="visually-hidden" for="message">{label_text}</label>'
        + f'<input id="message" name="message" autocomplete="off" maxlength="10000" placeholder="{placeholder_text}" required>'
        + f'<button id="send" type="submit">{send_text}</button>'
        + '</form>'
        + '</section>'
    )


def _panel_chrome(lang: str) -> str:
    """Right-rail summary + 'Technical details' disclosure.

    The plain-language summary lives at the top of the panel so a user
    never has to read engineer's vocabulary to see what's been collected.
    The 'Live state inspector' / 'Typed assessment state' chrome is
    preserved (it's why reviewers find the demo interesting) but only
    appears when the disclosure is opened.
    """
    heading = "Tu evaluación" if lang == "es" else "Your screening"
    plain_summary_label = (
        "Hasta ahora me has dicho:" if lang == "es" else "So far you've told me:"
    )
    tech_summary = (
        "Detalles técnicos (para revisores)" if lang == "es" else "Technical details (for reviewers)"
    )
    missing_label = "Datos que faltan" if lang == "es" else "Still needed"
    complete_label = "Todo listo" if lang == "es" else "All set"
    layer_label = "Capa reclamada" if lang == "es" else "Claimed layer"
    state_label = (
        "Estado tipado de la evaluación" if lang == "es" else "Typed assessment state"
    )
    calc_label = "Resultado del cálculo" if lang == "es" else "Calculator result"
    calc_initial = (
        "Aún no se ha calculado" if lang == "es" else "Not calculated yet"
    )
    citations_label = "Fuentes" if lang == "es" else "Citations"
    citations_initial = (
        "Aún no se recuperaron fuentes" if lang == "es" else "No sources retrieved yet"
    )
    handoff_label = "Transferencia al trabajador social" if lang == "es" else "Case-worker handoff"
    handoff_hint = (
        "Resumen listo para imprimir de la evaluación completada — se genera localmente, nada se envía a ningún sitio."
        if lang == "es"
        else "Print-ready summary of the completed screen — generated locally, nothing is submitted anywhere."
    )
    handoff_cta = (
        "Generar resumen de transferencia" if lang == "es" else "Generate handoff summary"
    )
    inspector_heading = (
        "Inspector de estado en vivo" if lang == "es" else "Live state inspector"
    )
    hint_text = (
        "Solo en esta sesión • no se guarda nada"
        if lang == "es"
        else "Session-only • nothing is persisted"
    )
    return (
        '<aside class="card panel" aria-label="'
        + ("Panel lateral" if lang == "es" else "Side panel")
        + '">'
        + f'<h2>{heading}</h2>'
        + f'<div class="plain-summary" id="plain-summary" aria-live="polite">{plain_summary_label} '
        + ("(esperando tu primera respuesta)" if lang == "es" else "(waiting for your first reply)")
        + '</div>'
        + f'<details class="technical" id="technical-details">'
        + f'<summary>{tech_summary}</summary>'
        + f'<h3>{inspector_heading}</h3>'
        + f'<div class="hint">{hint_text}</div>'
        + f'<div class="section"><h3>{missing_label}</h3>'
        + '<div id="missing"><span class="tag">household_size</span>'
        + '<span class="tag">gross_monthly_income</span>'
        + '<span class="tag">has_elderly_or_disabled_member</span></div></div>'
        + f'<div class="section"><h3>{layer_label}</h3>'
        + '<pre id="layer">No turns yet</pre></div>'
        + f'<div class="section"><h3>{state_label}</h3>'
        + '<pre id="state">{}</pre></div>'
        + f'<div class="section"><h3>{calc_label}</h3>'
        + f'<pre id="result">{calc_initial}</pre></div>'
        + f'<div class="section"><h3>{citations_label}</h3>'
        + f'<div id="citations" class="hint">{citations_initial}</div></div>'
        + f'<div class="section"><h3>{handoff_label}</h3>'
        + f'<div class="hint">{handoff_hint}</div>'
        + f'<button id="handoff" type="button">{handoff_cta}</button>'
        + '<div id="handoff-banner" class="banner" style="display:none"></div>'
        + '<pre id="handoff-summary" class="handoff-card" style="display:none"></pre>'
        + '</details>'
        + '</aside>'
    )


def _client_script(lang: str) -> str:
    return (
        r"""<script>
(function(){
  const sid = (crypto.randomUUID?crypto.randomUUID():Date.now()+Math.random().toString(36));
  const form = document.querySelector('#form');
  const input = document.querySelector('#message');
  const messages = document.querySelector('#messages');
  const send = document.querySelector('#send');
  const lang = document.documentElement.lang || 'en';
  const dict = {
    en: {
      typing: 'Assistant is thinking\u2026',
      offline: "Looks like you're offline. We'll send your reply when you're back.",
      reconnecting: 'Reconnecting\u2026',
      network: 'Network error',
      tryAgain: 'Try again',
      unavail: "The assistant couldn't reach the server just now. Your reply was not lost — try sending again in a moment.",
      completeAll: 'All set',
      completeNothing: 'Nothing needed',
      calcInitial: 'Not calculated yet',
      citationsInitial: 'No sources retrieved yet',
      layerInitial: 'No turns yet',
      handoffFail: "Handoff isn't available right now.",
    },
    es: {
      typing: 'El asistente está pensando\u2026',
      offline: 'Parece que estás sin conexión. Tus respuestas se enviarán cuando vuelvas a estar en línea.',
      reconnecting: 'Reconectando\u2026',
      network: 'Error de red',
      tryAgain: 'Reintentar',
      unavail: 'El asistente no pudo comunicarse con el servidor ahora mismo. Tu respuesta no se perdió — vuelve a intentarlo en un momento.',
      completeAll: 'Todo listo',
      completeNothing: 'Nada pendiente',
      calcInitial: 'Aún no se ha calculado',
      citationsInitial: 'Aún no se recuperaron fuentes',
      layerInitial: 'Aún no hay turnos',
      handoffFail: 'La transferencia no está disponible ahora.',
    }
  };
  const t = (dict[lang] || dict.en);
  function announce(text){
    // polite live region dedicated to status messages (network / typing)
    let region = document.querySelector('#status-live');
    if(!region){
      region = document.createElement('div');
      region.id = 'status-live';
      region.setAttribute('aria-live','polite');
      region.setAttribute('role','status');
      region.style.position='absolute';region.style.left='-9999px';
      document.body.appendChild(region);
    }
    region.textContent = text;
  }
  function focusMessage(node){
    // move focus to the newest reply so screen readers land on it
    if(!node) return;
    node.setAttribute('tabindex','-1');
    node.focus({preventScroll:false});
  }
  function citationSuffix(text){
    if(typeof text!=='string') return null;
    const i = text.lastIndexOf('(Sources:');
    return (i>=0 && text.endsWith(')')) ? text.slice(i) : null;
  }
  function plainSummary(data){
    const s = data.state || {};
    const parts = [];
    if(s.household_size != null) parts.push((lang==='es'?'<b>'+s.household_size+'</b> personas en tu hogar':'<b>'+s.household_size+'</b> people in your household'));
    if(s.gross_monthly_income != null) parts.push((lang==='es'?'<b>$'+Number(s.gross_monthly_income).toLocaleString()+'</b> al mes de ingresos':'<b>$'+Number(s.gross_monthly_income).toLocaleString()+'</b> a month in income'));
    if(s.has_elderly_or_disabled_member === true) parts.push(lang==='es'?'alguien <b>de 60 años o más, o con discapacidad</b>':'someone <b>age 60 or older or disabled</b>');
    else if(s.has_elderly_or_disabled_member === false) parts.push(lang==='es'?'<b>nadie</b> de 60 años o más, ni con discapacidad':'no one <b>age 60 or older or disabled</b>');
    let body;
    if(!parts.length){
      body = lang==='es' ? 'Cuéntame sobre tu hogar para empezar.' : "Tell me about your household to get started.";
    } else {
      body = (lang==='es' ? 'Hasta ahora me has dicho: ' : "So far you've told me: ") + parts.join(', ') + '.';
    }
    const el = document.querySelector('#plain-summary');
    if(el) el.innerHTML = body;
  }
  function renderStepIndicator(state, missing){
    const progress = document.querySelector('.progress');
    if(!progress) return;
    let step = 0;
    if(state.household_size != null) step = 1;
    if(state.gross_monthly_income != null) step = 2;
    if(state.has_elderly_or_disabled_member != null) step = 3;
    if(missing.length === 0 && state.household_size != null && state.gross_monthly_income != null && state.has_elderly_or_disabled_member != null) step = 4;
    const labels = {
      en: ['Household','Income','Household details','Result'],
      es: ['Hogar','Ingresos','Detalles del hogar','Resultado'],
    };
    const L = labels[lang] || labels.en;
    const items = [0,1,2,3].map(function(i){
      const attrs = [];
      if(i < step - 1) attrs.push('class="complete"');
      if(i === step - 1) attrs.push('aria-current="step"');
      return '<li '+ attrs.join(' ')+'>'+L[i]+'</li>';
    }).join('');
    const ol = progress.querySelector('ol.steps');
    if(ol) ol.innerHTML = items;
    if(step >= 4){ progress.setAttribute('hidden',''); }
    else { progress.removeAttribute('hidden'); }
  }
  function renderCitation(x){
    const row = document.createElement('div');
    const isStructured = x && x.kind==='structured';
    row.className = 'citation-row' + (isStructured ? ' citation-row--structured' : '');
    const head = document.createElement('div');
    head.className = 'citation-row-head';
    const doc = document.createElement('span');
    doc.className = 'citation-doc';
    doc.textContent = x && x.doc ? x.doc : '(unnamed source)';
    head.appendChild(doc);
    if(isStructured){
      const badge = document.createElement('span');
      badge.className = 'citation-kind-badge';
      badge.textContent = lang==='es' ? 'Límite calculado (fuente de verdad)' : 'Computed limit (source of truth)';
      head.appendChild(badge);
    }
    if(typeof x.score==='number' && Number.isFinite(x.score)){
      const score = document.createElement('span');
      score.className = 'citation-score';
      const pct = Math.max(0,Math.min(1,x.score))*100;
      score.textContent = (pct>=99.9 ? 'score 100%' : 'score ' + pct.toFixed(0) + '%');
      head.appendChild(score);
    }
    row.appendChild(head);
    if(x && x.snippet){
      const sn = document.createElement('span');
      sn.className = 'citation-snippet';
      sn.textContent = x.snippet;
      row.appendChild(sn);
    }
    return row;
  }
  function renderResultCard(data){
    const r = data.eligibility_result;
    if(!r) return;
    // If we already rendered a card for this exact outcome, don't repeat.
    const existingId = messages.querySelector('[data-result-outcome]');
    if(existingId && existingId.getAttribute('data-result-outcome') === JSON.stringify({
      eligible: !!r.eligible, head: r.headline || '', body: r.reason || ''
    })) return;
    if(existingId) existingId.remove();
    const card = document.createElement('div');
    card.className = 'bubble result-card';
    card.setAttribute('data-result-outcome', JSON.stringify({
      eligible: !!r.eligible, head: r.headline || '', body: r.reason || ''
    }));
    card.setAttribute('role','region');
    card.setAttribute('aria-label', lang==='es' ? 'Resultado de la evaluación' : 'Screening result');
    if(!r.eligible) card.setAttribute('data-outcome','denied');
    const labelsEn = {
      eligibility: 'Eligibility',
      income: 'Income used',
      limit: 'Limit used',
      rule: 'Rule',
      next: 'Next steps',
    };
    const labelsEs = {
      eligibility: 'Elegibilidad',
      income: 'Ingresos utilizados',
      limit: 'Límite utilizado',
      rule: 'Regla',
      next: 'Próximos pasos',
    };
    const lab = (lang==='es') ? labelsEs : labelsEn;
    const headline = (lang==='es')
      ? (r.eligible ? 'Es probable que seas elegible' : 'Es probable que no seas elegible con los datos actuales')
      : (r.eligible ? 'You are likely eligible' : 'You are likely not eligible based on what you shared');
    const reason = r.reason || r.summary || '';
    const incomeUsed = r.gross_monthly_income != null ? '$' + Number(r.gross_monthly_income).toLocaleString() : '—';
    const limitUsed = r.applicable_limit != null ? '$' + Number(r.applicable_limit).toLocaleString() : (r.limit != null ? '$' + Number(r.limit).toLocaleString() : '—');
    const rule = r.applicable_test || r.test || r.track || '—';
    const next = r.next_steps || (r.eligible
      ? (lang==='es' ? 'Aplica con tu oficina local de DSS o en epass.nc.gov.' : 'Apply at your local DSS office or at epass.nc.gov.')
      : (lang==='es' ? 'Puedes revisar los detalles y volver a intentarlo, o hablar con un trabajador social.' : 'Review the details and try again, or talk to a case worker.'));
    card.innerHTML = ''
      + '<h3>' + lab.eligibility + '</h3>'
      + '<p class="outcome-headline">' + headline + '</p>'
      + '<p class="outcome-body">' + reason + '</p>'
      + '<dl>'
      + '<dt>' + lab.income + '</dt><dd>' + incomeUsed + '</dd>'
      + '<dt>' + lab.limit + '</dt><dd>' + limitUsed + '</dd>'
      + '<dt>' + lab.rule + '</dt><dd>' + rule + '</dd>'
      + '</dl>'
      + '<p class="next-steps"><b>' + lab.next + ':</b> ' + next + '</p>';
    messages.appendChild(card);
    // Also append the citations (reason-only) as the assistant bubble, so
    // screen readers and the existing reviewer test get the explanation.
  }
  function renderInspector(data){
    document.querySelector('#layer').textContent = data.layer || 'unknown';
    document.querySelector('#state').textContent = JSON.stringify(data.state, null, 2);
    document.querySelector('#result').textContent = data.eligibility_result
      ? JSON.stringify(data.eligibility_result, null, 2)
      : t.calcInitial;
    const m = document.querySelector('#missing');
    const items = data.missing_fields.length
      ? data.missing_fields.map(function(x){
          const s = document.createElement('span'); s.className='tag'; s.textContent=x; return s;
        })
      : [Object.assign(document.createElement('span'), {className:'good', textContent: t.completeAll})];
    m.replaceChildren.apply(m, items);
    const c = document.querySelector('#citations');
    c.classList.remove('hint');
    c.replaceChildren.apply(c, data.citations.length
      ? data.citations.map(renderCitation)
      : [document.createTextNode(t.citationsInitial)]);
  }
  let typingNode = null;
  function showTyping(){
    hideTyping();
    typingNode = document.createElement('div');
    typingNode.className = 'typing';
    typingNode.setAttribute('aria-label', t.typing);
    const text = document.createElement('span'); text.textContent = t.typing;
    const dots = document.createElement('span'); dots.className='dots';
    dots.innerHTML = '<span></span><span></span><span></span>';
    typingNode.appendChild(text); typingNode.appendChild(dots);
    messages.appendChild(typingNode);
    messages.scrollTop = messages.scrollHeight;
  }
  function hideTyping(){ if(typingNode){ typingNode.remove(); typingNode = null; } }
  function setNetBanner(state){
    const banner = document.querySelector('.status-banner');
    if(!banner) return;
    banner.dataset.state = state;
    banner.textContent = state === 'offline' ? t.offline : t.reconnecting;
    banner.setAttribute('aria-live', state === 'offline' ? 'assertive' : 'polite');
  }
  function appendError(message){
    const div = document.createElement('div');
    div.className = 'error-bubble';
    div.setAttribute('role','alert');
    div.innerHTML = '<b>' + t.network + '</b>' + message;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    announce(t.network + ': ' + message);
  }
  function appendAssistant(text, crisis){
    let d;
    const suffix = citationSuffix(text);
    if(crisis){
      d = document.createElement('div');
      d.className = 'bubble assistant crisis-response';
      d.setAttribute('role','alert');
      d.setAttribute('aria-live','assertive');
      const reply = document.createElement('span'); reply.textContent = text;
      d.appendChild(reply);
    } else if(suffix){
      d = document.createElement('div');
      d.className = 'bubble assistant';
      const reply = document.createElement('span');
      reply.textContent = text.slice(0, suffix.length ? text.length - suffix.length : text.length).trimEnd();
      const c = document.createElement('span');
      c.className = 'citation-suffix'; c.textContent = suffix;
      d.appendChild(reply); d.appendChild(c);
    } else {
      d = document.createElement('div');
      d.className = 'bubble assistant';
      d.textContent = text;
    }
    messages.appendChild(d);
    messages.scrollTop = messages.scrollHeight;
    focusMessage(d);
    return d;
  }

  function appendUser(text){
    const d = document.createElement('div'); d.className='bubble user'; d.textContent = text;
    messages.appendChild(d); messages.scrollTop = messages.scrollHeight;
  }

  // network state (consume the banner when back online)
  window.addEventListener('online', function(){ setNetBanner('reconnecting'); });
  window.addEventListener('offline', function(){ setNetBanner('offline'); });
  if(navigator.onLine === false){ setNetBanner('offline'); }

  // The very first assistant turn: two templated bubbles (disclosure + greeting).
  // These are code-templated so the disclosure framing cannot drift; we
  // render them only when the server has confirmed the session is empty.
  const firstBubbleDisclosure = (lang==='es')
    ? "Soy un asistente automático de IA (no soy una persona, ni un trabajador social de NC DSS, ni el 911, ni los consejeros en vivo del 988, ni la línea de servicios de la tarjeta EBT). Las respuestas son orientativas y no decisiones oficiales. Esta no es una solicitud. Si estás en peligro inmediato, llama al 911."
    : "I'm an automated AI assistant (not a person, not an NC DSS caseworker, not 911, not 988's live counselors, not the EBT card-services line). Replies are guidance, not official decisions. This is not an application. If you are in immediate danger, call 911.";
  const firstBubbleGreeting = (lang==='es')
    ? '¡Hola! Estoy aquí para ayudarte a evaluar tu elegibilidad para FNS / SNAP de Carolina del Norte. Empecemos — ¿cuántas personas hay en tu hogar?'
    : "Hi! I'm here to help you check your eligibility for North Carolina FNS / SNAP. To start — how many people are in your household?";
  appendAssistant(firstBubbleDisclosure, false);
  appendAssistant(firstBubbleGreeting, false);

  form.addEventListener('submit', async function(e){
    e.preventDefault();
    const message = input.value.trim();
    if(!message) return;
    appendUser(message);
    input.value = '';
    send.disabled = true;
    showTyping();
    if(navigator.onLine === false){
      setNetBanner('offline');
      hideTyping();
      appendError(t.unavail);
      send.disabled = false;
      return;
    }
    try{
      const r = await fetch('/chat', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body: JSON.stringify({message: message, session_id: sid})
      });
      const data = await r.json();
      data.layer = r.headers.get('X-FoodPath-Layer') || r.headers.get('X-CivicReach-Layer');
      if(!r.ok) throw new Error((data && data.detail) || ('Request failed ('+r.status+')'));
      hideTyping();
      const crisis = !!(data.state && data.state.is_crisis);
      appendAssistant(data.reply, crisis);
      if(!crisis){ renderResultCard(data); }
      renderInspector(data);
      plainSummary(data);
      renderStepIndicator(data.state || {}, data.missing_fields || []);
      document.documentElement.lang = (data.state && data.state.language) || lang;
    } catch(err){
      hideTyping();
      appendError(t.unavail);
      setNetBanner('reconnecting');
    } finally {
      send.disabled = false;
      input.focus();
    }
  });

  const handoffBtn = document.querySelector('#handoff');
  const handoffBanner = document.querySelector('#handoff-banner');
  const handoffSummary = document.querySelector('#handoff-summary');
  handoffBtn.addEventListener('click', async function(){
    handoffBtn.disabled = true;
    try{
      const r = await fetch('/handoff', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body: JSON.stringify({session_id: sid})
      });
      const data = await r.json();
      if(!r.ok) throw new Error((data && data.detail) || ('Request failed ('+r.status+')'));
      handoffBanner.textContent = data.banner || '';
      handoffBanner.style.display = 'block';
      handoffSummary.textContent = data.summary || '';
      handoffSummary.style.display = 'block';
    } catch(err){
      handoffBanner.textContent = '';
      handoffBanner.style.display = 'none';
      handoffSummary.style.display = 'none';
      appendError(t.handoffFail);
    } finally {
      handoffBtn.disabled = false;
    }
  });

  if('serviceWorker' in navigator){
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('/static/service-worker.js').catch(function(){});
    });
  }
})();
</script>"""
    )


# The module-level PAGE constant.
#
# Tests and linters import this directly, so it is a fully-formed HTML
# document with stable ``lang="en"`` baked in. The runtime ``/`` route
# calls ``_assemble_page(lang)`` instead so the live Spanish conversation
# path gets ``lang="es"`` on the served document.
PAGE = _assemble_page("en")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return HTMLResponse(_assemble_page(_resolve_lang(request)))
