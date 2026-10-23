"""Persistent configuration (LLM endpoint, runner, last repo).

Stored as JSON on disk so settings entered in the UI survive restarts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

_BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ARCHIDE_DATA", _BASE / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
MODELS_DIR = DATA_DIR / "models"
AGENTS_PATH = DATA_DIR / "agents.json"
_lock = Lock()

DEFAULT_CONFIG = {
    "llm": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "qwen2.5-coder:7b",
        # "live" -> call the configured OpenAI-compatible endpoint
        # "mock" -> deterministic graph from the file tree (no LLM needed)
        "mode": "live",
        "timeout": 180,
    },
    "agent": {
        # "docker" -> run the agent as a network service in a container
        # "local"  -> run the agent in-process on the host (no docker)
        "runner": "docker",
        # per-agent image overrides: {agent_id: image}
        "images": {},
        # how many times the agent may refine the graph until it passes the
        # formal validation (repair loop)
        "max_repairs": 3,
        # shared persistent storage for ALL agents (Docker named volume).
        # A named volume needs no host file-sharing (unlike bind mounts).
        "volume": "archide-agent-data",
        "volume_mount": "/agent-data",
        "volume_enabled": True,
        # default sandbox limits for every agent (per-agent overrides live in
        # data/agents.json -> agent.limits)
        "limits": {
            "memory": "",
            "cpus": "",
            "pids": 256,
            "read_only": False,
            "cap_drop_all": False,
            "no_new_privileges": True,
        },
    },
    "gateway": {
        # ArchIDE LLM gateway: agents authenticate with this token; the gateway
        # injects the real upstream key (llm.api_key) when calling the LLM.
        # empty -> generated on first use and persisted
        "token": "",
        "enabled": True,
    },
    "repo": {
        "last": "",
    },
    "context": {
        # hard cap on the uploaded context size (characters)
        "max_chars": 400000,
        # compress once collected sources exceed this fraction of max_chars
        "compress_at": 0.8,
        # "extract" | "truncate" | "none"
        "strategy": "extract",
        # two-pass analysis: reconstruct from source code first, then compare
        # with the documentation and surface deviations
        "compare_docs": True,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            return _merge(DEFAULT_CONFIG, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(patch: dict) -> dict:
    with _lock:
        new = _merge(load_config(), patch or {})
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
        return new
