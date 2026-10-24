"""Maintenance: garbage collection over host persistence (Ф1.5).

Prunes the oldest run records and saved models beyond a keep-count. The Docker
named volume layout (shared/<agent>/scratch) is managed by the agent entrypoint.
"""
from __future__ import annotations

import pathlib

from . import config as config_mod


def _prune(directory: pathlib.Path, keep: int) -> int:
    if not directory.exists():
        return 0
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[max(0, int(keep)):]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def gc(keep_runs: int = 20, keep_models: int = 20) -> dict:
    return {
        "runs_removed": _prune(config_mod.RUNS_DIR, keep_runs),
        "models_removed": _prune(config_mod.MODELS_DIR, keep_models),
    }
