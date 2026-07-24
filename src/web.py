"""Single-process web UI for the CivicReach eligibility assistant.

Sessions intentionally live only in this process. Restarting the server clears
all conversations; this demo does not persist household information.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import CivicReachAgent, first_turn_disclosure, first_turn_greeting

app = FastAPI(title="CivicReach", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Public injection seam for tests and alternate deployments.
agent_factory: Callable[[], CivicReachAgent] = CivicReachAgent
_sessions: dict[str, CivicReachAgent] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: str = Field(min_length=1, max_length=200)


class HandoffRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


def clear_sessions() -> None:
    """Clear process-local sessions (primarily useful for test isolation)."""
    _sessions.clear()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
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


# Two separate bubbles, not one combined message: the safety disclosure
# first, then a short friendly opener with the first intake question.
_FIRST_BUBBLE_HTML = (
    '<div class="bubble assistant">' + _escape(first_turn_disclosure("en")) + "</div>"
    '<div class="bubble assistant">' + _escape(first_turn_greeting("en")) + "</div>"
)


PAGE = (
    r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CivicReach — NC FNS Screen</title><meta name="theme-color" content="#102a43"><link rel="manifest" href="/static/manifest.json"><link rel="apple-touch-icon" sizes="192x192" href="/static/icons/icon-192.png"><style>
:root{--navy:#102a43;--blue:#2563eb;--pale:#f4f7fb;--line:#d9e2ec;--ink:#243b53;--muted:#627d98;--green:#15803d;--crisis:#b42318;--crisis-bg:#fff1f0}*{box-sizing:border-box}
html,body{max-width:100%;overflow-x:hidden}body{margin:0;background:var(--pale);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.top{background:var(--navy);color:white;padding:16px 24px}.top b{font-size:21px}.top span{opacity:.8;margin-left:12px}.layout{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.8fr);gap:18px;max-width:1200px;margin:20px auto;padding:0 18px;height:calc(100vh - 105px);height:calc(100dvh - 105px)}.card{min-width:0;background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 14px #102a4312}.chat{display:flex;flex-direction:column;min-height:0}.messages{min-width:0;padding:20px;overflow:auto;overflow-wrap:anywhere;flex:1}.bubble{max-width:82%;padding:11px 14px;border-radius:14px;margin:0 0 12px;white-space:pre-wrap}.assistant{background:#eaf2ff;border-bottom-left-radius:3px}.user{background:var(--navy);color:white;margin-left:auto;border-bottom-right-radius:3px}.crisis-response{max-width:100%;background:var(--crisis-bg);border:2px solid var(--crisis);border-left-width:7px;color:#67120c;font-weight:600;box-shadow:0 3px 10px #b4231820}.crisis-response::before{content:"Urgent safety information";display:block;color:var(--crisis);font-size:.82rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px}.citation-suffix{display:block;background:#f8fafc;border:1px solid #9fb3c8;border-left:4px solid #486581;border-radius:7px;color:#334e68;font-size:.875rem;font-weight:500;line-height:1.45;margin-top:10px;padding:8px 10px;white-space:normal}.composer{display:flex;gap:9px;padding:14px;border-top:1px solid var(--line)}input{min-width:0;min-height:48px;flex:1;padding:12px;border:1px solid #829ab1;border-radius:8px;font:inherit;font-size:16px}button{min-height:44px;border:0;border-radius:8px;background:var(--blue);color:white;padding:0 20px;font:inherit;font-weight:650;cursor:pointer;touch-action:manipulation}button:disabled{opacity:.55}.panel{min-width:0;padding:18px;overflow:auto}.panel h2{margin:0 0 4px;font-size:17px}.hint{color:var(--muted);font-size:13px;margin-bottom:18px}.section{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}.section h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 9px}pre{max-width:100%;background:#f6f8fa;border-radius:7px;padding:10px;overflow:auto;font-size:12px}.tag{display:inline-block;background:#fff3cd;padding:3px 7px;border-radius:10px;margin:2px;font-size:12px}.good{color:var(--green)}.error{color:#b91c1c}.banner{background:#dcfce7;border:1px solid var(--green);color:#12652f;border-radius:8px;padding:10px 12px;margin-top:10px;font-weight:700;font-size:14px}#handoff{margin-top:4px;padding:10px 16px}#handoff-summary{margin-top:10px;white-space:pre-wrap;background:#f0f7ff;border:1px solid #9fb3c8;border-left:5px solid var(--blue);color:#243b53;font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}@media(max-width:760px){.top{padding:12px 16px}.top span{display:block;margin:2px 0 0;font-size:14px}.layout{grid-template-columns:minmax(0,1fr);gap:12px;height:auto;margin:12px auto;padding:0 10px}.chat{height:calc(100vh - 105px);height:calc(100dvh - 105px);min-height:480px}.messages{padding:14px 12px}.bubble{max-width:92%;padding:10px 12px}.crisis-response{max-width:100%}.composer{padding:10px;gap:8px}.composer button{padding:0 16px}.panel{min-height:300px;padding:16px}}@media(max-width:390px){.top b{font-size:19px}.layout{padding:0 8px}.composer{padding:8px}.composer button{padding:0 13px}.bubble{max-width:95%}}
</style></head><body><header class="top"><b>CivicReach</b><span>North Carolina FNS initial eligibility screen</span></header><main class="layout"><section class="card chat"><div id="messages" class="messages">"""
    + _FIRST_BUBBLE_HTML
    + r"""</div><form id="form" class="composer"><input id="message" autocomplete="off" maxlength="10000" aria-label="Message" placeholder="Type your answer…" required><button id="send">Send</button></form></section><aside class="card panel"><h2>Live state inspector</h2><div class="hint">Session-only • nothing is persisted</div><div class="section"><h3>Missing fields</h3><div id="missing"><span class="tag">household_size</span><span class="tag">gross_monthly_income</span><span class="tag">has_elderly_or_disabled_member</span></div></div><div class="section"><h3>Typed assessment state</h3><pre id="state">{}</pre></div><div class="section"><h3>Calculator result</h3><pre id="result">Not calculated yet</pre></div><div class="section"><h3>Citations</h3><div id="citations" class="hint">No sources retrieved yet</div></div><div class="section"><h3>Case-worker handoff</h3><div class="hint">Print-ready summary of the completed screen — generated locally, nothing is submitted anywhere.</div><button id="handoff" type="button">Generate handoff summary</button><div id="handoff-banner" class="banner" style="display:none"></div><pre id="handoff-summary" style="display:none"></pre></div></aside></main><script>
const sid=(crypto.randomUUID?crypto.randomUUID():Date.now()+Math.random().toString(36));const form=document.querySelector('#form'),input=document.querySelector('#message'),messages=document.querySelector('#messages'),send=document.querySelector('#send');
function bubble(text,kind,options={}){const d=document.createElement('div');d.className='bubble '+kind+(options.crisis?' crisis-response':'');if(options.crisis)d.setAttribute('role','alert');const citationStart=kind.includes('assistant')?text.lastIndexOf('(Sources:'):-1;if(citationStart>=0&&text.endsWith(')')){const reply=document.createElement('span');reply.textContent=text.slice(0,citationStart).trimEnd();const citation=document.createElement('span');citation.className='citation-suffix';citation.textContent=text.slice(citationStart);d.append(reply,citation)}else{d.textContent=text}messages.appendChild(d);messages.scrollTop=messages.scrollHeight}
function render(data){document.querySelector('#state').textContent=JSON.stringify(data.state,null,2);document.querySelector('#result').textContent=data.eligibility_result?JSON.stringify(data.eligibility_result,null,2):'Not calculated yet';const m=document.querySelector('#missing');m.replaceChildren(...(data.missing_fields.length?data.missing_fields.map(x=>{const s=document.createElement('span');s.className='tag';s.textContent=x;return s}):[Object.assign(document.createElement('span'),{className:'good',textContent:'Complete'})]));const c=document.querySelector('#citations');c.replaceChildren(...(data.citations.length?data.citations.map(x=>{const p=document.createElement('p');p.textContent=x.doc+(x.snippet?' — '+x.snippet:'');return p}):[document.createTextNode('No sources retrieved yet')]))}
form.addEventListener('submit',async e=>{e.preventDefault();const message=input.value.trim();if(!message)return;bubble(message,'user');input.value='';send.disabled=true;try{const r=await fetch('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message,session_id:sid})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Request failed');bubble(data.reply,'assistant',{crisis:Boolean(data.state&&data.state.is_crisis)});render(data)}catch(err){bubble('Sorry, the assistant is unavailable: '+err.message,'assistant error')}finally{send.disabled=false;input.focus()}});
const handoffBtn=document.querySelector('#handoff'),handoffBanner=document.querySelector('#handoff-banner'),handoffSummary=document.querySelector('#handoff-summary');
handoffBtn.addEventListener('click',async()=>{handoffBtn.disabled=true;try{const r=await fetch('/handoff',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session_id:sid})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Request failed');handoffBanner.textContent=data.banner;handoffBanner.style.display='block';handoffSummary.textContent=data.summary;handoffSummary.style.display='block'}catch(err){handoffBanner.textContent='';handoffBanner.style.display='none';handoffSummary.style.display='none';bubble('Handoff unavailable: '+err.message,'assistant error')}finally{handoffBtn.disabled=false}});
if('serviceWorker' in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('/static/service-worker.js').catch(()=>{}));}
</script></body></html>"""
)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)
