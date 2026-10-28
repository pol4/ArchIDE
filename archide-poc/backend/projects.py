"""Projects: the first-class owner of a graph, its runs, chat and documents (Ф10.1).

A project ties together what used to be loose ends: the model (graph), the
repositories it came from, the reconstruction runs, the agent conversation and
the per-project view state. Stored one JSON file per project under
``data/projects/``.

Existing models are migrated automatically: the first listing creates a project
per saved model, so nothing is lost when the concept is introduced.
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from . import config as config_mod
from . import model_store

CHAT_LIMIT = 200


def _dir() -> pathlib.Path:
    directory = pathlib.Path(config_mod.DATA_DIR) / "projects"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def slug(text) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-") or "project"


def _path(project_id: str) -> pathlib.Path:
    return _dir() / f"{slug(project_id)}.json"


def _read(project_id: str) -> dict | None:
    path = _path(project_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(project: dict) -> dict:
    project["updated"] = time.time()
    _path(project["id"]).write_text(json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True),
                                    encoding="utf-8")
    return project


def _model_info(name: str | None) -> dict:
    if not name:
        return {}
    graph = model_store.load_model(config_mod.MODELS_DIR, name)
    if graph is None:
        return {"missing": True}
    return {
        "nodes": len(graph.get("nodes") or []),
        "edges": len(graph.get("edges") or []),
        "hash": model_store.model_hash(graph),
    }


def create_project(name: str, repo: str | None = None, model: str | None = None) -> dict:
    project_id = slug(name)
    existing = _read(project_id)
    if existing:
        return existing
    now = time.time()
    project = {
        "id": project_id,
        "name": name,
        "repos": [repo] if repo else [],
        "model": model,
        "runs": [],
        "chat": [],
        "docs": [],
        "view": {},
        "created": now,
        "updated": now,
    }
    return _write(project)


def get_project(project_id: str) -> dict | None:
    return _read(project_id)


def update_project(project_id: str, patch: dict) -> dict | None:
    project = _read(project_id)
    if project is None:
        return None
    for key in ("name", "model", "view"):
        if key in patch:
            project[key] = patch[key]
    if isinstance(patch.get("repos"), list):
        project["repos"] = [str(r) for r in patch["repos"] if str(r).strip()]
    if isinstance(patch.get("docs"), list):
        project["docs"] = [str(d) for d in patch["docs"]]
    return _write(project)


def attach_run(project_id: str, run_id: str, model: str | None = None,
               repo: str | None = None) -> dict | None:
    project = _read(project_id)
    if project is None:
        return None
    runs = [r for r in project.get("runs") or [] if r.get("id") != run_id]
    runs.append({"id": run_id, "model": model, "repo": repo, "at": time.time()})
    project["runs"] = runs[-50:]
    if model:
        project["model"] = model
    if repo and repo not in (project.get("repos") or []):
        project["repos"] = ([repo] + list(project.get("repos") or []))[:10]
    return _write(project)


def append_chat(project_id: str, role: str, text: str) -> dict | None:
    project = _read(project_id)
    if project is None:
        return None
    chat = project.get("chat") or []
    chat.append({"ts": time.time(), "role": str(role), "text": str(text)[:8000]})
    project["chat"] = chat[-CHAT_LIMIT:]
    return _write(project)


def set_view(project_id: str, view: dict) -> dict | None:
    return update_project(project_id, {"view": view or {}})


def for_model(model_name: str) -> dict:
    """Find the project that owns a model, or create one named after the model."""
    for project in list_projects():
        if project.get("model") == model_name:
            return project
    return create_project(model_name, model=model_name)


def list_projects() -> list[dict]:
    """All projects, migrating any saved model that has no project yet."""
    projects = []
    for path in sorted(_dir().glob("*.json")):
        project = _read(path.stem)
        if project:
            projects.append(project)
    known = {p.get("model") for p in projects if p.get("model")}
    models_dir = pathlib.Path(config_mod.MODELS_DIR)
    if models_dir.is_dir():
        for path in sorted(models_dir.glob("*.json")):
            name = path.stem
            if name in known:
                continue
            projects.append(create_project(name, model=name))
            known.add(name)
    projects.sort(key=lambda p: p.get("updated") or 0, reverse=True)
    return projects


def list_summaries() -> list[dict]:
    out = []
    for project in list_projects():
        summary = {
            "id": project["id"],
            "name": project.get("name"),
            "model": project.get("model"),
            "repos": project.get("repos") or [],
            "runs": len(project.get("runs") or []),
            "chat": len(project.get("chat") or []),
            "updated": project.get("updated"),
        }
        summary.update(_model_info(project.get("model")))
        out.append(summary)
    return out


def open_project(project_id: str) -> dict | None:
    """Everything the UI needs to switch to a project.

    A saved model whose name matches becomes a project on first open, so the
    migration also works when someone opens a model before listing projects.
    """
    project = _read(project_id)
    if project is None:
        if model_store.load_model(config_mod.MODELS_DIR, project_id) is not None:
            project = create_project(project_id, model=project_id)
        else:
            return None
    graph = model_store.load_model(config_mod.MODELS_DIR, project.get("model")) if project.get("model") else None
    return {
        "project": project,
        "graph": graph,
        "chat": project.get("chat") or [],
        "view": project.get("view") or {},
        "model": project.get("model"),
    }
