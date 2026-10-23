"""ArchIDE PoC HTTP API + static UI host."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents
from . import config as config_mod
from . import runs

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"

app = FastAPI(title="ArchIDE PoC", version="0.3.0")


class ReconstructRequest(BaseModel):
    repo: str
    agent: str = "builtin"


@app.get("/api/agents")
def list_agents() -> dict:
    return {"agents": agents.list_agents()}


@app.get("/api/config")
def get_config() -> dict:
    return config_mod.load_config()


@app.put("/api/config")
def put_config(patch: dict) -> dict:
    return config_mod.save_config(patch)


@app.post("/api/reconstruct")
def reconstruct_endpoint(req: ReconstructRequest) -> dict:
    if not agents.get_agent(req.agent):
        raise HTTPException(status_code=400, detail=f"unknown agent: {req.agent}")
    return {"run_id": runs.start(req.repo, req.agent, config_mod.load_config())}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, since: int = 0) -> dict:
    snapshot = runs.get(run_id, since)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return snapshot


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    return {"ok": runs.cancel(run_id)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
