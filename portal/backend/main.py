"""Heat planning portal: FastAPI serving the map data, persona catalogue and the front end.

Run locally from portal/:
    .venv/bin/uvicorn backend.main:app --reload --port 8080
"""
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data, personas

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Brooklyn Heat Portal")
app.add_middleware(GZipMiddleware, minimum_size=1024)
logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _json(payload_fn):
    try:
        return Response(payload_fn(), media_type="application/json",
                        headers={"Cache-Control": "no-cache"})
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/cells")
def cells():
    """Every grid cell, columnar: {cell_deg, columns, rows}. The browser builds the squares."""
    return _json(data.cells_json)


@app.get("/api/tracts")
def tracts():
    """Tracts the grid touches, as GeoJSON with cleaned demographics."""
    return _json(data.tracts_json)


@app.get("/api/heat-days")
def heat_days():
    return _json(data.heat_days_json)


@app.get("/api/model")
def model():
    """Model card for heat_offset_v1: held-out score, cross-validation, feature importance."""
    return _json(data.model_json)


@app.get("/api/personas")
def get_personas():
    return {"views": personas.VIEWS, "questions": personas.QUESTIONS}


@app.post("/api/ask")
async def ask(request: AskRequest):
    if not os.environ.get("HEAT_AGENT_MODEL"):
        raise HTTPException(status_code=503, detail="Heat agent is not enabled on this portal.")
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="Enter a question.")
    from . import heat_agent

    try:
        return {"reply": await heat_agent.answer(request.message.strip())}
    except Exception as error:
        logger.warning("Heat agent request failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="Heat agent could not complete the request.") from error


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
