"""Built-in agent sandbox entrypoint (runs inside the container).

Fetches the repo tarball, prompt and constraints from the ArchIDE sandbox
endpoints, reconstructs the graph (calling the LLM through the gateway) and
uploads the result. No host paths are mounted.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "venv", ".venv", "__pycache__",
    ".idea", ".vscode", "target", "vendor", ".mypy_cache", ".pytest_cache",
}
TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".c", ".h", ".cpp", ".hpp", ".kt", ".swift", ".scala", ".sql",
    ".md", ".yaml", ".yml", ".json", ".toml", ".proto", ".sh", ".dockerfile",
}


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str) -> bytes:
    with _opener().open(url, timeout=180) as resp:
        return resp.read()


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    with _opener().open(request, timeout=180) as resp:
        resp.read()


def _post_event(url: str, kind: str, text: str) -> None:
    try:
        _post(url, {"type": kind, "text": str(text)})
    except Exception:  # noqa: BLE001 - activity is best-effort
        pass


def collect(root: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_EXT and path.name.lower() != "dockerfile":
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files.append((str(path.relative_to(root)), content))
    return files


def main() -> int:
    base = os.environ["ARCHIDE_SANDBOX_BASE"].rstrip("/")
    job = os.environ["ARCHIDE_JOB_ID"]
    token = os.environ["ARCHIDE_JOB_TOKEN"]
    result_url = f"{base}/sandbox/result/{job}?token={token}"

    repo_dir = Path(os.environ.get("ARCHIDE_WORK", "/work")) / "repo"
    shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    data = _get(f"{base}/sandbox/repo/{job}?token={token}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        try:
            tar.extractall(repo_dir, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(repo_dir)

    meta = json.loads(_get(f"{base}/sandbox/meta/{job}?token={token}"))
    files = collect(repo_dir)
    print(f"collected {len(files)} files", file=sys.stderr, flush=True)

    llm = {
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", ""),
        "mode": os.environ.get("LLM_MODE", "live"),
        "timeout": float(os.environ.get("LLM_TIMEOUT", "180")),
        "max_repairs": int(os.environ.get("ARCHIDE_MAX_REPAIRS", "3")),
        "validate_url": os.environ.get("ARCHIDE_VALIDATE_URL", ""),
    }

    try:
        from agent.core import run

        event_url = f"{base}/sandbox/event/{job}?token={token}"
        emit = lambda kind, text: _post_event(event_url, kind, text)
        _prepare_storage(emit)
        graph = run(meta["prompt"], meta["repo_name"], files, llm, on_event=emit)
        _save_artifact(graph, meta.get("repo_name", "repo"), emit)
        _post(result_url, {"graph": graph})
        print("ok", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - report to the host
        _post(result_url, {"error": str(exc)})
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1


def _prepare_storage(emit) -> None:
    """Create the shared-volume layout (mounted at ARCHIDE_DATA)."""
    keys = ("ARCHIDE_DATA", "ARCHIDE_AGENT_DIR", "ARCHIDE_SHARED_DIR", "ARCHIDE_SCRATCH_DIR")
    for key in keys:
        if not os.environ.get(key):
            return
    try:
        for key in keys:
            Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
        emit("status", f"shared storage ready at {os.environ['ARCHIDE_DATA']}")
    except OSError as exc:
        emit("log", f"shared storage unavailable: {exc}")


def _save_artifact(graph: dict, repo_name: str, emit) -> None:
    agent_dir = os.environ.get("ARCHIDE_AGENT_DIR")
    if not agent_dir:
        return
    try:
        out = Path(agent_dir) / "graphs"
        out.mkdir(parents=True, exist_ok=True)
        target = out / (re.sub(r"[^a-zA-Z0-9._-]+", "-", repo_name) + ".json")
        target.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
        emit("log", f"saved graph to shared storage: {target}")
    except OSError as exc:
        emit("log", f"could not save graph to shared storage: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
