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
RUNS_DIR = DATA_DIR / "runs"
AGENTS_PATH = DATA_DIR / "agents.json"
_lock = Lock()

DEFAULT_CONFIG = {
    "llm": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "qwen2.5-coder:7b",
        # Ф3.11: how much of the accumulated session conversation may be sent
        # (0 = unlimited). A long run trims its oldest exchanges to stay inside.
        "max_context_chars": 60000,
        # "live" -> call the configured OpenAI-compatible endpoint
        # "mock" -> deterministic graph from the file tree (no LLM needed)
        "mode": "live",
        "timeout": 180,
    },
    # code-generation handoff (Ф8.2/Ф8.2b): "llm" asks the model through the
    # gateway; "cli" runs an external coding agent as a command in a work dir
    "codegen": {
        "mode": "llm",
        "max_files": 60,
        "max_file_chars": 60000,
        "max_total_chars": 400000,
        "max_retries": 2,
    },
    # external coding-agent command (Ф8.2): placeholders {workdir}, {prompt_file}
    "codegen_cli": {
        "command": "",
        "timeout": 1800,
        "env": {},
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
        # per-lease request quota (0 = unlimited) and lease TTL in seconds
        "quota_per_lease": 0,
        "lease_ttl": 3600,
    },
    "repo": {
        "last": "",
    },
    "markers": {
        # HMAC key for signing chunk markers; generated on first use
        "key": "",
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
        # Ф8.4: a repository with only documents (ТЗ/требования) is designed,
        # not reconstructed — the base pass runs over the documentation
        "design_from_docs": True,
        # aspect-aware extraction from source files (Ф3.1)
        "extract_aspects": True,
        # sequential, agent-verified reconstruction of the remaining aspects
        # (Ф3.5): after the structural C4 pass, each aspect is reconstructed and
        # merged on its own, so the graph is extended in small checked steps.
        "aspect_passes": True,
        "aspect_list": ["data", "interface", "behavior", "process", "deployment", "access", "quality"],
        # Ф3.7: do not rebuild an aspect the graph already carries, and cap how
        # many elements one pass may add (0 = unlimited)
        # skip an aspect only when it is already covered — meant for RESUME runs
        # (a full run should reconstruct every aspect, even if heuristics found some)
        "aspect_skip_existing": False,
        "aspect_skip_min": 1,
        "aspect_max_nodes": 80,
        # Ф3.9: report elements left without relationships/evidence after a run,
        # and let the agent fill them in (evidence + relationships, safely merged)
        "reconcile_report": True,
        "reconcile_fix": True,
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
