"""Asynchronous run manager.

Each reconstruction is a background run with a live event feed (agent activity /
reasoning / logs) and can be cancelled by the user.
"""
from __future__ import annotations

import pathlib
import secrets
import subprocess
import threading
import time

from .graph_schema import validate_graph
from .runtime import CancelledError
from .runner import reconstruct

_runs: dict[str, "Run"] = {}
_lock = threading.Lock()
_MAX_EVENTS = 2000


class Run:
    def __init__(self, run_id: str):
        self.id = run_id
        self.status = "running"  # running | done | invalid | error | cancelled
        self.events: list[dict] = []
        self.graph: dict | None = None
        self.validation: dict | None = None
        self.error: str | None = None
        self.meta: dict | None = None
        self.container: str | None = None
        self.cancel = threading.Event()
        self._lock = threading.Lock()

    def add_event(self, kind: str, text: str) -> None:
        text = str(text)
        with self._lock:
            self.events.append({"ts": time.time(), "type": kind, "text": text[:8000]})
            if len(self.events) > _MAX_EVENTS:
                del self.events[: len(self.events) - _MAX_EVENTS]

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "container": self.container,
                "events": self.events[since:],
                "event_count": len(self.events),
                "graph": self.graph if self.status in ("done", "invalid") else None,
                "validation": self.validation,
                "error": self.error,
                "meta": self.meta,
            }


def start(repo: str, agent_id: str, cfg: dict) -> str:
    run = Run(secrets.token_hex(8))
    with _lock:
        _runs[run.id] = run
    threading.Thread(target=_worker, args=(run, repo, agent_id, cfg), daemon=True).start()
    return run.id


def _worker(run: Run, repo: str, agent_id: str, cfg: dict) -> None:
    try:
        graph, meta = reconstruct(repo, agent_id, cfg, run=run)
        run.meta = meta
        run.graph = graph
        run.validation = validate_graph(graph)
        run.status = "done" if run.validation["ok"] else "invalid"

        # persist the model canonically (model-as-storage, ROADMAP Ф2.1)
        try:
            from . import config as config_mod
            from . import model_store
            name = pathlib.Path(str(repo).rstrip("/")).name or "model"
            path, digest = model_store.save_model(graph, config_mod.MODELS_DIR, name)
            meta["model_path"] = str(path)
            meta["model_hash"] = digest
            run.add_event("status", f"model saved: {path.name} ({digest})")
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            run.add_event("log", f"model not saved: {exc}")

        if run.status == "invalid":
            run.add_event("error", "graph failed validation: " + "; ".join(run.validation["errors"][:5]))
    except CancelledError:
        run.status = "cancelled"
        run.add_event("error", "run cancelled by user")
    except Exception as exc:  # noqa: BLE001 - surface to the UI
        run.error = str(exc)
        run.status = "error"
        run.add_event("error", str(exc))


def get(run_id: str, since: int = 0) -> dict | None:
    run = _runs.get(run_id)
    return run.snapshot(since) if run else None


def cancel(run_id: str) -> bool:
    run = _runs.get(run_id)
    if not run:
        return False
    run.cancel.set()
    if run.container:
        subprocess.run(["docker", "rm", "-f", run.container], capture_output=True, text=True)
    return True
