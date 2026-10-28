"""Feedback collection (Ф6.2) — the signal side of the experience track.

Two kinds of signal, both attributed to **what they are about** (the run, the model,
the element, the view, the applied edit):

* **explicit** — the architect says it: 👍/👎 on an answer, a score, a comment;
* **implicit** — the system observes it: an edit transaction applied, a validation
  that fixed issues, a proposal the architect rejected.

Attribution is mandatory: a signal that does not say what it refers to cannot be
used for adaptation (Ф6.4) or reflection (Ф6.5), so it is refused rather than
silently stored. Signals live in ``data/users/<user>/feedback.jsonl`` (append-only,
auditable, replayable).
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from . import config as config_mod

EXPLICIT_SIGNALS = ("up", "down", "score")
IMPLICIT_SIGNALS = ("accepted", "rejected", "reverted", "improved", "unchanged", "failed")
KINDS = ("explicit", "implicit")
MAX_TEXT = 2000


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-") or "default"


def _path(user: str) -> pathlib.Path:
    directory = pathlib.Path(config_mod.DATA_DIR) / "users" / _safe(user)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "feedback.jsonl"


def _clean_about(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)) and str(value) != "":
            out[str(key)] = value
        elif isinstance(value, list):
            out[str(key)] = [str(v) for v in value[:20]]
    return out


def record(user: str, kind: str, signal: str, about: dict, comment: str | None = None,
           score: float | None = None) -> dict:
    kind = str(kind or "explicit")
    if kind not in KINDS:
        raise ValueError(f"unknown feedback kind `{kind}`")
    allowed = EXPLICIT_SIGNALS if kind == "explicit" else IMPLICIT_SIGNALS
    if str(signal) not in allowed:
        raise ValueError(f"unknown {kind} signal `{signal}` (allowed: {', '.join(allowed)})")
    attribution = _clean_about(about)
    if not attribution:
        raise ValueError("feedback must say what it is about (`about`)")
    item = {
        "ts": time.time(),
        "kind": kind,
        "signal": str(signal),
        "score": float(score) if isinstance(score, (int, float)) else None,
        "comment": (str(comment)[:MAX_TEXT] if comment else None),
        "about": attribution,
    }
    with _path(user).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def history(user: str, limit: int = 50) -> list[dict]:
    path = _path(user)
    if not path.is_file():
        return []
    items: list[dict] = []
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
                items.append(item)
    except OSError:
        return []
    return items[-max(1, int(limit or 50)):]


def summary(user: str) -> dict:
    """Aggregates the signals — the raw material for the profile (Ф6.3)."""
    items = history(user, 100000)
    by_signal: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_model: dict[str, int] = {}
    scores: list[float] = []
    for item in items:
        by_signal[item["signal"]] = by_signal.get(item["signal"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        model = (item.get("about") or {}).get("model")
        if model:
            by_model[str(model)] = by_model.get(str(model), 0) + 1
        if isinstance(item.get("score"), (int, float)):
            scores.append(float(item["score"]))
    return {
        "total": len(items),
        "by_signal": by_signal,
        "by_kind": by_kind,
        "by_model": by_model,
        "average_score": (sum(scores) / len(scores)) if scores else None,
        "positive": by_signal.get("up", 0) + by_signal.get("accepted", 0) + by_signal.get("improved", 0),
        "negative": by_signal.get("down", 0) + by_signal.get("rejected", 0) + by_signal.get("failed", 0),
    }
