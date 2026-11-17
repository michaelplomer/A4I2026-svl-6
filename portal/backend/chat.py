"""Chat API: the portal's bridge to the ADK agent (agent/heat_agent).

    POST /api/chat           {message, session_id?, context?}  ->  text/event-stream
    GET  /api/chat/contract  the event types and the UI tools the browser must implement

Stream events (each an SSE `event:` name with a JSON `data:` payload):

    session      {session_id, mode}            first event; send session_id back next turn
    text         {delta}                       a chunk of the assistant's reply (append it)
    tool_call    {id, name, args, ui}          the agent called a tool (ui=true: a browser tool)
    tool_result  {id, name, response}          a DATA tool's result (for a "used: get_block" chip)
    ui_action    {id, name, args}              run this in the browser (js/ui_tools.js HANDLERS)
    error        {message}
    done         {}

`context` is what the planner sees right now (current view, map layer, selected block, map
centre). It's stored in ADK session state as `ui_context`, which the agent's instruction
reads, so "this block" and "here" resolve without the planner typing coordinates.

Modes (env AGENT_MODE):
    adk   run agent/heat_agent's root_agent in-process with Gemini on Vertex AI (default)
    mock  scripted replies + UI actions, no Gemini or BigQuery: for building the UI offline
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config

log = logging.getLogger("chat")
router = APIRouter()

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = Path(os.environ.get("AGENT_DIR", REPO / "agent"))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

# Gemini on Vertex AI in the team project unless the environment says otherwise.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")

MODE = os.environ.get("AGENT_MODE", "adk").lower()
APP_NAME = "heat_portal"
USER_ID = "planner"


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context: dict[str, Any] | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------- ADK mode
_runner = None
_ui_tool_names: set[str] = set()


def _get_runner():
    global _runner, _ui_tool_names
    if _runner is None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from heat_agent.agent import root_agent
        from heat_agent.ui_tools import UI_TOOL_NAMES

        _ui_tool_names = set(UI_TOOL_NAMES)
        # In-memory sessions: a server restart forgets conversations. Swap for
        # DatabaseSessionService / VertexAiSessionService when deploying for real.
        _runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=InMemorySessionService())
    return _runner


async def _adk_stream(req: ChatRequest) -> AsyncIterator[str]:
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.genai import types

    runner = _get_runner()
    svc = runner.session_service
    session = None
    if req.session_id:
        session = await svc.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=req.session_id)
    if session is None:
        session = await svc.create_session(app_name=APP_NAME, user_id=USER_ID)
    yield _sse("session", {"session_id": session.id, "mode": "adk"})

    state_delta = {
        "ui_context": json.dumps(req.context or {}, indent=1),
        "new_york_time": datetime.now(ZoneInfo("America/New_York")).isoformat(),
    }
    streamed_text = False  # did partial chunks already carry this turn's text?
    seen_calls: set[str] = set()  # streaming repeats each function call in the final event
    said_anything = False         # any text or tool call yet this turn?
    async for ev in runner.run_async(
        user_id=USER_ID, session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=req.message)]),
        state_delta=state_delta,
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    ):
        if ev.error_message:
            # Gemini sometimes ends a turn with an empty message after its last tool call
            # ("no actionable content"). If it already answered, that's not worth alarming anyone.
            if said_anything:
                log.info("ignored trailing agent error: %s", ev.error_message)
            else:
                yield _sse("error", {"message": ev.error_message})
            continue
        for fc in ev.get_function_calls() or []:
            if fc.id in seen_calls:
                continue
            seen_calls.add(fc.id)
            said_anything = True
            ui = fc.name in _ui_tool_names
            yield _sse("tool_call", {"id": fc.id, "name": fc.name, "args": fc.args or {}, "ui": ui})
            if ui:
                yield _sse("ui_action", {"id": fc.id, "name": fc.name, "args": fc.args or {}})
        for fr in ev.get_function_responses() or []:
            if fr.name not in _ui_tool_names:
                yield _sse("tool_result", {"id": fr.id, "name": fr.name, "response": fr.response})
        if not ev.content or not ev.content.parts:
            continue
        text = "".join(p.text for p in ev.content.parts if p.text and not getattr(p, "thought", False))
        if not text or ev.author == "user":
            continue
        said_anything = True
        if ev.partial:
            streamed_text = True
            yield _sse("text", {"delta": text})
        elif not streamed_text:
            # Non-streamed final text (model returned it in one piece).
            yield _sse("text", {"delta": text})
        else:
            streamed_text = False  # the final aggregate repeats the partials; skip it
    yield _sse("done", {})


# ---------------------------------------------------------------------------- mock mode
def _mock_hot_cell() -> tuple[int, int, float]:
    """Hottest measured block in the snapshot, so mock replies point at a real cell."""
    try:
        from . import data

        d = json.loads(data.cells_json())
        cols = d["columns"]
        i, j = cols.index("cell_i"), cols.index("cell_j")
        k = cols.index("measured_offset_f") if "measured_offset_f" in cols else cols.index("offset_f")
        best = max((r for r in d["rows"] if r[k] is not None), key=lambda r: r[k])
        return best[i], best[j], best[k]
    except Exception:
        return 40678, -73945, 4.2


def _mock_top_cells(n: int) -> list[tuple[int, int, float]]:
    """The n hottest measured blocks in the snapshot, for the mock's ranked answer."""
    try:
        from . import data

        d = json.loads(data.cells_json())
        cols = d["columns"]
        i, j, k = cols.index("cell_i"), cols.index("cell_j"), cols.index("measured_offset_f")
        rows = sorted((r for r in d["rows"] if r[k] is not None), key=lambda r: r[k], reverse=True)
        return [(r[i], r[j], r[k]) for r in rows[:n]]
    except Exception:
        ci, cj, off = _mock_hot_cell()
        return [(ci, cj, off)]


async def _mock_stream(req: ChatRequest) -> AsyncIterator[str]:
    sid = req.session_id or f"mock-{uuid.uuid4().hex[:8]}"
    yield _sse("session", {"session_id": sid, "mode": "mock"})
    msg = req.message.lower()
    ci, cj, off = _mock_hot_cell()

    async def say(text: str):
        for chunk in re.findall(r"\S+\s*", text):
            await asyncio.sleep(0.02)
            yield _sse("text", {"delta": chunk})

    def call(name: str, args: dict):
        cid = f"mock-{uuid.uuid4().hex[:6]}"
        return [_sse("tool_call", {"id": cid, "name": name, "args": args, "ui": True}),
                _sse("ui_action", {"id": cid, "name": name, "args": args})]

    if any(w in msg for w in ("layer", "tree", "colour", "color")):
        for e in call("set_map_layer", {"layer_id": "trees_100m"}):
            yield e
        async for e in say("(Mock agent) I've switched the map to street trees within 100 m."):
            yield e
    elif any(w in msg for w in ("sites", "top", "recommend", "highlight", "where", "truck", "dispatch")):
        top = _mock_top_cells(5)
        locations = [{"rank": r, "cell_i": i, "cell_j": j, "label": f"Block {i},{j}",
                      "metric": "measured_offset_f", "value": round(v, 2), "unit": "°F vs campaign average",
                      "layer": "measured_offset_f", "source": "measured"}
                     for r, (i, j, v) in enumerate(top, start=1)]
        for e in call("show_ranked_locations", {"locations": locations, "label": "Mock: 5 hottest measured blocks"}):
            yield e
        lines = "\n".join(f"{loc['rank']}. Block {loc['cell_i']},{loc['cell_j']}: {loc['value']:+.1f}°F"
                          for loc in locations)
        async for e in say("(Mock agent) The five hottest measured blocks, numbered on the map:\n"
                           f"{lines}\n\nA real answer would rank by heat × residents 65+ × no-car households."):
            yield e
    else:
        cid = f"mock-{uuid.uuid4().hex[:6]}"
        yield _sse("tool_call", {"id": cid, "name": "get_block", "ui": False,
                                 "args": {"lat": (ci + 0.5) / 1000, "lon": (cj + 0.5) / 1000}})
        yield _sse("tool_result", {"id": cid, "name": "get_block",
                                   "response": {"status": "ok", "cell_i": ci, "cell_j": cj, "offset_best_f": off}})
        for e in call("show_cell_on_map", {"cell_i": ci, "cell_j": cj, "zoom": 16}):
            yield e
        async for e in say(f"(Mock agent) The hottest measured block runs **{off:+.1f}°F** above the "
                           f"campaign average. I've zoomed the map to it (cell {ci},{cj}). "
                           "Set AGENT_MODE=adk for real answers."):
            yield e
    yield _sse("done", {})


# ---------------------------------------------------------------------------- routes
async def _guarded(gen: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        async for chunk in gen:
            yield chunk
    except Exception as e:  # surface agent failures in the chat instead of a dead stream
        log.exception("chat failed")
        yield _sse("error", {"message": f"{type(e).__name__}: {e}"})
        yield _sse("done", {})


@router.post("/api/chat")
async def chat(req: ChatRequest):
    gen = _mock_stream(req) if MODE == "mock" else _adk_stream(req)
    return StreamingResponse(_guarded(gen), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/chat/contract")
def contract():
    """What a chat UI must handle: the stream's event types and the browser tools."""
    ui_tools = []
    try:
        from google.adk.tools import FunctionTool
        from heat_agent.ui_tools import UI_TOOLS

        for f in UI_TOOLS:
            decl = FunctionTool(f)._get_declaration()
            ui_tools.append(decl.model_dump(exclude_none=True, mode="json"))
    except Exception as e:  # agent package missing or ADK not installed
        ui_tools = [{"error": f"{type(e).__name__}: {e}"}]
    return {
        "mode": MODE,
        "endpoint": "POST /api/chat {message, session_id?, context?} -> text/event-stream",
        "events": ["session", "text", "tool_call", "tool_result", "ui_action", "error", "done"],
        "ui_tools": ui_tools,
    }
