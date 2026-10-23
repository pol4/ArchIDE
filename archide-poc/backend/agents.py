"""Agent registry (Ф1.1).

Agents are described by a **plugin manifest** at `data/agents.json`, merged with
built-in defaults. Each entry:

    { "id", "name", "kind": "builtin"|"cli", "image", "description", "limits": {...} }

`kind` selects the container entrypoint; `limits` overrides the global sandbox
limits (Ф1.2) for that agent.
"""
from __future__ import annotations

import json

from . import config as config_mod

DEFAULT_AGENTS: list[dict] = [
    {
        "id": "builtin",
        "name": "Built-in Reconstructor",
        "kind": "builtin",
        "image": "archide-agent:latest",
        "description": "Stdlib agent: reconstructs the graph via an OpenAI-compatible LLM through the gateway.",
        "limits": {},
    },
    {
        "id": "kilocode",
        "name": "Kilo Code CLI",
        "kind": "cli",
        "image": "archide-kilo:latest",
        "description": "Runs the Kilo Code CLI in the sandbox; reaches the LLM through the ArchIDE gateway.",
        "limits": {},
    },
]


def _read_manifest() -> list[dict]:
    path = config_mod.AGENTS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        return [a for a in data.get("agents", []) if isinstance(a, dict)]
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    return []


def ensure_manifest() -> None:
    """Create the manifest with defaults if it does not exist yet."""
    path = config_mod.AGENTS_PATH
    if path.exists():
        return
    config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"agents": DEFAULT_AGENTS}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_registry() -> list[dict]:
    merged: dict[str, dict] = {a["id"]: dict(a) for a in DEFAULT_AGENTS}
    for entry in _read_manifest():
        agent_id = entry.get("id")
        if not agent_id:
            continue
        item = dict(merged.get(agent_id, {}))
        item.update(entry)
        item.setdefault("limits", {})
        merged[agent_id] = item
    return list(merged.values())


def list_agents() -> list[dict]:
    return load_registry()


def effective_limits(agent: dict, cfg: dict) -> dict:
    """Merge global sandbox limits with per-agent overrides (Ф1.2)."""
    base = (cfg.get("agent", {}) or {}).get("limits", {}) or {}
    return {**base, **(agent.get("limits") or {})}


def list_agents_with_limits(cfg: dict) -> list[dict]:
    out = []
    for agent in list_agents():
        item = dict(agent)
        item["limits"] = effective_limits(agent, cfg)
        out.append(item)
    return out


def get_agent(agent_id: str) -> dict | None:
    for agent in load_registry():
        if agent.get("id") == agent_id:
            return agent
    return None
