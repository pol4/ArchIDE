"""Dialogue store (Ф6.1) — what the architect and the agent said, and what it led to.

Ф6 (experience and self-adaptation) needs the raw material first: every turn of the
conversation, kept per architect and per project, together with its **provenance** —
which model/run/selection/view it was about and what the turn actually produced
(an applied edit, a validation, a synthesis).

The store is append-only JSONL (``data/users/<user>/dialogue.jsonl``): it survives
restarts, it is trivial to audit, and a later reflection step (Ф6.5) can replay it
without any schema migration.
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from . import config as config_mod

ROLES = ("user", "agent", "system")
MAX_TEXT = 8000
MAX_TURNS = 5000


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-") or "default"


def _path(user: str) -> pathlib.Path:
    directory = pathlib.Path(config_mod.DATA_DIR) / "users" / _safe(user)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "dialogue.jsonl"


def _clean_provenance(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)) and str(value) != "":
            out[str(key)] = value
        elif isinstance(value, list):
            out[str(key)] = [str(v) for v in value[:20]]
    return out


def record(user: str, role: str, text: str, project: str | None = None,
           provenance: dict | None = None, kind: str | None = None) -> dict:
    """Append one turn and return it (the normalised record)."""
    body = str(text or "").strip()
    if not body:
        raise ValueError("a turn needs text")
    turn = {
        "ts": time.time(),
        "role": role if role in ROLES else "system",
        "text": body[:MAX_TEXT],
        "project": str(project) if project else None,
        "kind": str(kind) if kind else None,
        "provenance": _clean_provenance(provenance),
    }
    path = _path(user)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(turn, ensure_ascii=False) + "\n")
    _trim(path)
    return turn


def _trim(path: pathlib.Path) -> None:
    """Keep the file bounded without rewriting it on every turn."""
    try:
        if path.stat().st_size < 4_000_000:
            return
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_TURNS:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _read_all(user: str) -> list[dict]:
    path = _path(user)
    if not path.is_file():
        return []
    turns: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                turns.append(item)
    except OSError:
        return []
    return turns


def history(user: str, project: str | None = None, limit: int = 50) -> list[dict]:
    """The newest ``limit`` turns, oldest first (ready to display)."""
    turns = _read_all(user)
    if project:
        turns = [t for t in turns if (t.get("project") or None) == str(project)]
    count = max(1, int(limit or 50))
    return turns[-count:]


def summary(user: str) -> list[dict]:
    """The dialogues of an architect, grouped by project (newest activity first)."""
    grouped: dict[str, dict] = {}
    for turn in _read_all(user):
        key = turn.get("project") or ""
        item = grouped.setdefault(key, {"project": turn.get("project"), "turns": 0,
                                        "last": 0.0, "first_text": turn.get("text", "")[:120],
                                        "last_text": ""})
        item["turns"] += 1
        item["last"] = max(item["last"], float(turn.get("ts") or 0))
        item["last_text"] = str(turn.get("text") or "")[:120]
    out = list(grouped.values())
    out.sort(key=lambda d: d["last"], reverse=True)
    return out
