"""Canvas layouts (UI) — where the architect placed the elements.

The architecture model is the graph; a layout is only how it is drawn. Keeping the
two apart means dragging a box never rewrites the architecture, an auto-grouping
can be re-applied, and a hand-made arrangement can be saved next to it.

Stored per model in ``data/layouts/<model>.json``::

    {"mode": "manual" | "auto", "layout": "c4" | "grid" | ..., "positions": {id: {x, y}}}
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from . import config as config_mod

MODES = ("auto", "manual")


def _dir() -> pathlib.Path:
    directory = pathlib.Path(config_mod.DATA_DIR) / "layouts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "")).strip("-") or "model"


def _path(model: str) -> pathlib.Path:
    return _dir() / f"{_safe(model)}.json"


def _clean_positions(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("positions must be an object")
    clean: dict[str, dict] = {}
    for key, value in raw.items():
        if (not isinstance(value, dict) or not isinstance(value.get("x"), (int, float))
                or not isinstance(value.get("y"), (int, float))):
            raise ValueError(f"position for `{key}` must be {{x, y}} with numbers")
        clean[str(key)] = {"x": float(value["x"]), "y": float(value["y"])}
    return clean


def load(model: str) -> dict:
    """The saved layout for a model, or an empty one (never fails)."""
    empty = {"mode": "", "layout": "", "positions": {}, "updated": None}
    path = _path(model)
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    try:
        positions = _clean_positions(data.get("positions") or {})
    except ValueError:
        positions = {}
    return {"mode": str(data.get("mode") or ""), "layout": str(data.get("layout") or ""),
            "positions": positions, "updated": data.get("updated")}


def save(model: str, patch: dict) -> dict:
    mode = str(patch.get("mode") or "manual")
    if mode not in MODES:
        raise ValueError(f"unknown layout mode `{mode}`")
    positions = _clean_positions(patch.get("positions") or {})
    data = {"mode": mode, "layout": str(patch.get("layout") or ""), "positions": positions,
            "updated": time.time()}
    _path(model).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8")
    return data


def clear(model: str) -> None:
    _path(model).unlink(missing_ok=True)
