"""Asynchronous run manager with persistence (Ф1.4).

Each reconstruction is a background run with a live event feed and can be
cancelled. Runs are persisted to ``data/runs/<id>.json`` so they survive restarts
and are auditable/reproducible.
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets
import subprocess
import threading
import time

from . import config as config_mod
from .graph_schema import validate_graph
from .runtime import CancelledError
from .runner import reconstruct

_runs: dict[str, "Run"] = {}
_lock = threading.Lock()
_MAX_EVENTS = 2000
_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")


def _runs_dir() -> pathlib.Path:
    config_mod.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return config_mod.RUNS_DIR


class Run:
    def __init__(self, run_id: str, repo: str = "", agent: str = ""):
        self.id = run_id
        self.repo = repo
        self.agent = agent
        self.status = "running"  # running | done | invalid | error | cancelled
        self.events: list[dict] = []
        self.graph: dict | None = None
        self.validation: dict | None = None
        self.error: str | None = None
        self.meta: dict | None = None
        self.container: str | None = None
        self.created = time.time()
        self.updated = self.created
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
                "repo": self.repo,
                "agent": self.agent,
                "status": self.status,
                "container": self.container,
                "created": self.created,
                "updated": self.updated,
                "events": self.events[since:],
                "event_count": len(self.events),
                "graph": self.graph if self.status in ("done", "invalid") else None,
                "validation": self.validation,
                "error": self.error,
                "meta": self.meta,
            }


def _persist(run: Run) -> None:
    try:
        run.updated = time.time()
        record = run.snapshot(0)
        record["events"] = run.events  # full history
        (_runs_dir() / f"{run.id}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def start(repo: str, agent_id: str, cfg: dict, options: dict | None = None) -> str:
    run = Run(secrets.token_hex(8), repo=repo, agent=agent_id)
    run.options = dict(options or {})
    with _lock:
        _runs[run.id] = run
    _persist(run)
    threading.Thread(target=_worker, args=(run, repo, agent_id, cfg, run.options), daemon=True).start()
    return run.id


def _worker(run: Run, repo: str, agent_id: str, cfg: dict, options: dict | None = None) -> None:
    try:
        graph, meta = reconstruct(repo, agent_id, cfg, run=run, options=options)
        run.meta = meta
        run.graph = graph
        run.validation = validate_graph(graph)

        # Persist BEFORE the status flips: a client that sees `done` must already
        # find `meta.model_path`, otherwise it loads nothing (the poll stops at
        # `done` and never re-reads the metadata).
        try:
            from . import model_store
            from . import projects as projects_mod
            from .runner import model_name_for
            model_name = model_name_for(repo)
            path, digest = model_store.save_model(graph, config_mod.MODELS_DIR, model_name)
            project_id = (options or {}).get("project")
            if project_id:
                projects_mod.attach_run(project_id, run.id, model_name, repo)
            meta["model_path"] = str(path)
            meta["model_hash"] = digest
            run.add_event("status", f"model saved: {path.name} ({digest})")
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            run.add_event("log", f"model not saved: {exc}")

        run.status = "done" if run.validation["ok"] else "invalid"

        if run.status == "invalid":
            run.add_event("error", "graph failed validation: " + "; ".join(run.validation["errors"][:5]))
    except CancelledError:
        run.status = "cancelled"
        run.add_event("error", "run cancelled by user")
    except Exception as exc:  # noqa: BLE001 - surface to the UI
        run.error = str(exc)
        run.status = "error"
        run.add_event("error", str(exc))
    finally:
        _persist(run)


def get(run_id: str, since: int = 0) -> dict | None:
    run = _runs.get(run_id)
    if run:
        return run.snapshot(since)
    record = _load_record(run_id)
    if record is None:
        return None
    events = record.get("events") or []
    return {
        "id": record.get("id", run_id),
        "repo": record.get("repo", ""),
        "agent": record.get("agent", ""),
        "status": record.get("status"),
        "container": record.get("container"),
        "created": record.get("created"),
        "updated": record.get("updated"),
        "events": events[since:],
        "event_count": len(events),
        "graph": record.get("graph"),
        "validation": record.get("validation"),
        "error": record.get("error"),
        "meta": record.get("meta"),
    }


def _load_record(run_id: str) -> dict | None:
    if not _ID_RE.match(str(run_id)):
        return None
    path = config_mod.RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_runs(limit: int = 100) -> list[dict]:
    directory = config_mod.RUNS_DIR
    out: list[dict] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": record.get("id", path.stem),
            "repo": record.get("repo", ""),
            "agent": record.get("agent", ""),
            "status": record.get("status"),
            "created": record.get("created"),
            "updated": record.get("updated"),
            "event_count": len(record.get("events") or []),
        })
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return out[:limit]


def cancel(run_id: str) -> bool:
    run = _runs.get(run_id)
    if not run:
        return False
    run.cancel.set()
    if run.container:
        subprocess.run(["docker", "rm", "-f", run.container], capture_output=True, text=True)
    return True
