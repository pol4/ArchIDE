"""Model store: canonical serialization and persistence of ArchIDE graphs.

Canonical form is deterministic (sorted nodes/edges, sorted keys) so the same
model always yields the same hash — the basis for versioning (ROADMAP Ф2).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from .constraints import normalize as normalize_constraints


def normalize(model: dict) -> dict:
    nodes = []
    for node in model.get("nodes", []) or []:
        if isinstance(node, dict) and node.get("id"):
            nodes.append(dict(node))
    nodes.sort(key=lambda n: str(n["id"]))

    edges = []
    for edge in model.get("edges", []) or []:
        if isinstance(edge, dict):
            edges.append(dict(edge))
    edges.sort(key=lambda e: (str(e.get("type", "")), str(e.get("source", "")), str(e.get("target", ""))))

    out = {"nodes": nodes, "edges": edges}
    constraints = model.get("constraints")
    if constraints:
        out["constraints"] = normalize_constraints(constraints)
    return out


def canonical(model: dict) -> str:
    return json.dumps(normalize(model), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def model_hash(model: dict) -> str:
    return hashlib.sha256(canonical(model).encode("utf-8")).hexdigest()[:16]


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(name or "model")).strip("-") or "model"


def save_model(model: dict, models_dir, name: str) -> tuple[pathlib.Path, str]:
    directory = pathlib.Path(models_dir)
    directory.mkdir(parents=True, exist_ok=True)
    data = normalize(model)
    digest = model_hash(data)
    path = directory / f"{_safe_name(name)}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path, digest


def list_models(models_dir) -> list[dict]:
    directory = pathlib.Path(models_dir)
    out: list[dict] = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                model = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({
                "name": path.stem,
                "hash": model_hash(model),
                "nodes": len(model.get("nodes", [])),
                "edges": len(model.get("edges", [])),
            })
    return out


def load_model(models_dir, name: str) -> dict | None:
    path = pathlib.Path(models_dir) / f"{_safe_name(name)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
