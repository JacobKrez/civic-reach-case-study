"""Single-process web UI for the CivicReach eligibility assistant.

Sessions intentionally live only in this process. Restarting the server clears
all conversations; this demo does not persist household information.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .agent import CivicReachAgent

app = FastAPI(title="CivicReach", version="1.0.0")

# Public injection seam for tests and alternate deployments.
agent_factory: Callable[[], CivicReachAgent] = CivicReachAgent
_sessions: dict[str, CivicReachAgent] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
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


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CivicReach — NC FNS Screen</title><style>
:root{--navy:#102a43;--blue:#2563eb;--pale:#f4f7fb;--line:#d9e2ec;--ink:#243b53;--muted:#627d98;--green:#15803d}*{box-sizing:border-box}
body{margin:0;background:var(--pale);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}.top{background:var(--navy);color:white;padding:16px 24px}.top b{font-size:21px}.top span{opacity:.75;margin-left:12px}.layout{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.8fr);gap:18px;max-width:1200px;margin:20px auto;padding:0 18px;height:calc(100vh - 105px)}.card{background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 14px #102a4312}.chat{display:flex;flex-direction:column;min-height:0}.messages{padding:20px;overflow:auto;flex:1}.bubble{max-width:82%;padding:11px 14px;border-radius:14px;margin:0 0 12px;white-space:pre-wrap}.assistant{background:#eaf2ff;border-bottom-left-radius:3px}.user{background:var(--navy);color:white;margin-left:auto;border-bottom-right-radius:3px}.composer{display:flex;gap:9px;padding:14px;border-top:1px solid var(--line)}input{flex:1;padding:12px;border:1px solid #bcccdc;border-radius:8px;font:inherit}button{border:0;border-radius:8px;background:var(--blue);color:white;padding:0 20px;font-weight:650;cursor:pointer}button:disabled{opacity:.55}.panel{padding:18px;overflow:auto}.panel h2{margin:0 0 4px;font-size:17px}.hint{color:var(--muted);font-size:12px;margin-bottom:18px}.section{border-top:1px solid var(--line);padding-top:14px;margin-top:14px}.section h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 9px}pre{background:#f6f8fa;border-radius:7px;padding:10px;overflow:auto;font-size:12px}.tag{display:inline-block;background:#fff3cd;padding:3px 7px;border-radius:10px;margin:2px;font-size:12px}.good{color:var(--green)}.error{color:#b91c1c}@media(max-width:760px){.layout{grid-template-columns:1fr;height:auto}.chat{height:65vh}.panel{min-height:300px}.top span{display:block;margin:2px 0 0}}
</style></head><body><header class="top"><b>CivicReach</b><span>North Carolina FNS initial eligibility screen</span></header><main class="layout"><section class="card chat"><div id="messages" class="messages"><div class="bubble assistant">Hi — I can help with an initial NC Food and Nutrition Services eligibility screen. What would you like to know?</div></div><form id="form" class="composer"><input id="message" autocomplete="off" maxlength="10000" aria-label="Message" placeholder="Type your answer…" required><button id="send">Send</button></form></section><aside class="card panel"><h2>Live state inspector</h2><div class="hint">Session-only • nothing is persisted</div><div class="section"><h3>Missing fields</h3><div id="missing"><span class="tag">household_size</span><span class="tag">gross_monthly_income</span><span class="tag">has_elderly_or_disabled_member</span></div></div><div class="section"><h3>Typed assessment state</h3><pre id="state">{}</pre></div><div class="section"><h3>Calculator result</h3><pre id="result">Not calculated yet</pre></div><div class="section"><h3>Citations</h3><div id="citations" class="hint">No sources retrieved yet</div></div></aside></main><script>
const sid=(crypto.randomUUID?crypto.randomUUID():Date.now()+Math.random().toString(36));const form=document.querySelector('#form'),input=document.querySelector('#message'),messages=document.querySelector('#messages'),send=document.querySelector('#send');
function bubble(text,kind){const d=document.createElement('div');d.className='bubble '+kind;d.textContent=text;messages.appendChild(d);messages.scrollTop=messages.scrollHeight}
function render(data){document.querySelector('#state').textContent=JSON.stringify(data.state,null,2);document.querySelector('#result').textContent=data.eligibility_result?JSON.stringify(data.eligibility_result,null,2):'Not calculated yet';const m=document.querySelector('#missing');m.replaceChildren(...(data.missing_fields.length?data.missing_fields.map(x=>{const s=document.createElement('span');s.className='tag';s.textContent=x;return s}):[Object.assign(document.createElement('span'),{className:'good',textContent:'Complete'})]));const c=document.querySelector('#citations');c.replaceChildren(...(data.citations.length?data.citations.map(x=>{const p=document.createElement('p');p.textContent=x.doc+(x.snippet?' — '+x.snippet:'');return p}):[document.createTextNode('No sources retrieved yet')]))}
form.addEventListener('submit',async e=>{e.preventDefault();const message=input.value.trim();if(!message)return;bubble(message,'user');input.value='';send.disabled=true;try{const r=await fetch('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message,session_id:sid})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Request failed');bubble(data.reply,'assistant');render(data)}catch(err){bubble('Sorry, the assistant is unavailable: '+err.message,'assistant error')}finally{send.disabled=false;input.focus()}});
</script></body></html>'''


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)
