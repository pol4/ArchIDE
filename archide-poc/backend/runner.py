"""Repository resolution and agent execution.

Pipeline:
  repo address -> collect + compress sources -> build prompt -> run agent -> graph

Agents run in the Docker sandbox using a **network job protocol** (no bind
mounts): the host serves the repo tarball, prompt and constraints from the
sandbox endpoints, the container streams activity events back, and uploads the
resulting graph. All agents reach the LLM through the ArchIDE LLM gateway.

When a ``run`` object is provided, the pipeline emits live events and honours
cancellation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from . import agents, gateway, runtime, sandbox
from .compress import compress
from .context import collect_files
from .merge import merge_graphs
from .prompt import build_prompt
from .runtime import CancelledError

BASE = Path(__file__).resolve().parent.parent
_URL_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")
CLI_OUTPUT = "/work/archide/graph.json"


def resolve_repo(repo: str) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    repo = (repo or "").strip()
    if not repo:
        raise ValueError("repository address is empty")

    is_url = repo.startswith(_URL_PREFIXES) or repo.endswith(".git")
    if is_url:
        tmp = tempfile.TemporaryDirectory(prefix="archide-repo-")
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", repo, tmp.name],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            tmp.cleanup()
            raise RuntimeError("git clone timed out after 600s")
        if proc.returncode != 0:
            tmp.cleanup()
            raise RuntimeError(f"git clone failed: {proc.stderr.strip()[-500:]}")
        return Path(tmp.name), tmp

    path = Path(repo).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"local path not found: {repo}")
    if not path.is_dir():
        raise NotADirectoryError(f"not a directory: {repo}")
    return path, None


def _constraints(ctx_cfg: dict, files: int) -> dict:
    return {
        "max_chars": int(ctx_cfg.get("max_chars", 400000)),
        "compress_at": float(ctx_cfg.get("compress_at", 0.8)),
        "strategy": str(ctx_cfg.get("strategy", "extract")),
        "files": files,
    }


def _prepare(files, ctx_cfg: dict):
    base = _constraints(ctx_cfg, 0)
    return compress(files, base["max_chars"], base["compress_at"], base["strategy"])


def _run_pass(agent, source, repo_name, files, constraints, max_repairs, output_path, llm, runner, cfg, run, emit):
    prompt = build_prompt(repo_name, constraints, output_path, max_repairs, source)
    if runner == "local":
        return _run_local(agent, prompt, repo_name, files, llm, emit)
    return _run_docker(agent, prompt, constraints, repo_name, files, cfg, run, emit)


def reconstruct(repo: str, agent_id: str, cfg: dict, run=None) -> tuple[dict, dict]:
    emit = run.add_event if run else (lambda *_a: None)
    emit("status", f"run started for `{repo}`")

    agent = agents.get_agent(agent_id)
    if not agent:
        raise ValueError(f"unknown agent: {agent_id}")

    emit("status", "resolving repository (cloning if it is a URL)…")
    repo_path, tmp = resolve_repo(repo)
    try:
        emit("status", f"repository ready: {repo_path.name}")
        ctx_cfg = cfg.get("context", {}) or {}
        agent_cfg = cfg.get("agent", {}) or {}
        max_repairs = int(agent_cfg.get("max_repairs", 3))
        compare_docs = bool(ctx_cfg.get("compare_docs", True))
        runner = agent_cfg.get("runner", "docker")
        output_path = CLI_OUTPUT if agent["kind"] == "cli" else None

        llm = dict(cfg.get("llm", {}))
        llm["max_repairs"] = max_repairs

        # ---- pass 1: reconstruct from source code
        emit("status", "collecting source files…")
        code_files = collect_files(repo_path, include_docs=False)
        if not code_files:
            raise RuntimeError("no readable source files found in the repository")
        emit("status", f"collected {len(code_files)} source files; compressing context…")
        code_files, code_report = _prepare(code_files, ctx_cfg)
        emit(
            "status",
            f"pass 1/2 - source code: {code_report['files_out']} files, "
            f"{code_report['original_chars']}->{code_report['final_chars']} chars",
        )
        graph, meta = _run_pass(
            agent, "code", repo_path.name, code_files, _constraints(ctx_cfg, len(code_files)),
            max_repairs, output_path, llm, runner, cfg, run, emit,
        )

        # ---- pass 2: reconstruct from documentation and compare
        meta["docs_pass"] = False
        if compare_docs:
            emit("status", "collecting documentation…")
            docs_files = collect_files(repo_path, docs_only=True)
            if docs_files:
                docs_files, docs_report = _prepare(docs_files, ctx_cfg)
                emit(
                    "status",
                    f"pass 2/2 - documentation: {docs_report['files_out']} files; comparing",
                )
                docs_graph, _ = _run_pass(
                    agent, "docs", repo_path.name, docs_files, _constraints(ctx_cfg, len(docs_files)),
                    max_repairs, output_path, llm, runner, cfg, run, emit,
                )
                graph = merge_graphs(graph, docs_graph)
                deviations = sum(1 for n in graph["nodes"] if n.get("deviation"))
                emit(
                    "status",
                    f"merged code + docs: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
                    f"{deviations} node deviation(s) flagged",
                )
                meta["docs_pass"] = True
            else:
                emit("status", "no documentation found; comparison skipped")

        meta.update({
            "runner": runner,
            "agent": agent_id,
            "repo_path": str(repo_path),
            "files": len(code_files),
            "context": code_report,
        })
        return graph, meta
    finally:
        if tmp is not None:
            tmp.cleanup()


def _run_local(agent: dict, prompt: str, repo_name: str, files, llm: dict, emit) -> tuple[dict, dict]:
    if agent["kind"] != "builtin":
        raise ValueError(f"agent `{agent['id']}` requires the docker runner")
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from agent.core import run as core_run

    emit("status", "running the built-in agent in-process")
    return core_run(prompt, repo_name, files, llm, on_event=emit), {}


# --------------------------------------------------------------------------- #
# docker: sandbox job protocol + LLM gateway
# --------------------------------------------------------------------------- #

def _image(agent: dict, cfg: dict) -> str:
    overrides = (cfg.get("agent", {}) or {}).get("images", {}) or {}
    return overrides.get(agent["id"]) or agent["image"]


def _limit_args(limits: dict) -> list[list[str]]:
    """Translate sandbox limits into `docker run` flags (Ф1.2)."""
    args: list[list[str]] = []
    if limits.get("memory"):
        args.append(["--memory", str(limits["memory"])])
    if limits.get("cpus"):
        args.append(["--cpus", str(limits["cpus"])])
    if limits.get("pids"):
        args.append(["--pids-limit", str(int(limits["pids"]))])
    if limits.get("read_only"):
        args.append(["--read-only"])
        args.append(["--tmpfs", "/tmp"])
    if limits.get("cap_drop_all"):
        args.append(["--cap-drop", "ALL"])
    if limits.get("no_new_privileges"):
        args.append(["--security-opt", "no-new-privileges"])
    return args


def _run_docker(agent, prompt, constraints, repo_name, files, cfg, run, emit) -> tuple[dict, dict]:
    if not runtime.PUBLIC_BASE:
        raise RuntimeError("server public base URL is not set; start via server.py")

    gw_token = gateway.ensure_token(cfg)
    llm = cfg.get("llm", {})
    job_id, job_token = sandbox.create_job(
        files, prompt, constraints, repo_name, agent["id"],
        on_event=(run.add_event if run else None),
    )

    agent_cfg = cfg.get("agent", {}) or {}
    base = runtime.PUBLIC_BASE.rstrip("/")
    image = _image(agent, cfg)
    volume = agent_cfg.get("volume", "archide-agent-data")
    volume_mount = agent_cfg.get("volume_mount", "/agent-data")
    volume_enabled = agent_cfg.get("volume_enabled", True)

    env = {
        "ARCHIDE_SANDBOX_BASE": base,
        "ARCHIDE_JOB_ID": job_id,
        "ARCHIDE_JOB_TOKEN": job_token,
        "ARCHIDE_OUTPUT": CLI_OUTPUT,
        "ARCHIDE_DATA": volume_mount,
        "ARCHIDE_AGENT_DIR": f"{volume_mount}/{agent['id']}",
        "ARCHIDE_SHARED_DIR": f"{volume_mount}/shared",
        "ARCHIDE_MAX_REPAIRS": str(agent_cfg.get("max_repairs", 3)),
        "ARCHIDE_VALIDATE_URL": f"{base}/sandbox/validate/{job_id}?token={job_token}",
        "LLM_BASE_URL": f"{base}/llm/v1",
        "LLM_API_KEY": gw_token,
        "LLM_MODEL": str(llm.get("model", "")),
        "LLM_MODE": str(llm.get("mode", "live")),
        "LLM_TIMEOUT": str(llm.get("timeout", 180)),
        "KILO_PROVIDER": "archide",
        "KILO_MODEL": f"archide/{llm.get('model', '')}",
    }

    limits = dict(agent_cfg.get("limits", {}) or {})
    limits.update(agent.get("limits", {}) or {})

    name = "archide-sbx-" + uuid.uuid4().hex[:12]
    cmd = ["docker", "run", "-d", "--name", name,
           "--add-host", "host.docker.internal:host-gateway"]
    for flag in _limit_args(limits):
        cmd += flag
    if volume_enabled and volume:
        # a named volume: shared by all agents, no host file-sharing needed
        subprocess.run(["docker", "volume", "create", volume], capture_output=True, text=True)
        cmd += ["-v", f"{volume}:{volume_mount}"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    cmd.append(image)

    mount_note = f" (shared volume `{volume}` -> {volume_mount})" if (volume_enabled and volume) else ""
    emit("status", f"starting sandbox image `{image}`{mount_note}")
    started = subprocess.run(cmd, capture_output=True, text=True)
    if started.returncode != 0:
        raise RuntimeError(
            f"docker run failed (exit {started.returncode}):\n{started.stderr.strip()[-1500:]}\n"
            f"Hint: build the image with `docker build -t {image} "
            f"{'agents/kilo' if agent['kind'] == 'cli' else 'agent'}/`."
        )
    if run:
        run.container = name
    emit("log", f"container {name} started")

    deadline = time.time() + float(llm.get("timeout", 180)) + 120
    last_log = 0
    try:
        while True:
            if run and run.cancel.is_set():
                raise CancelledError()
            if sandbox.is_done(job_id):
                break
            if time.time() > deadline:
                raise RuntimeError("agent timed out")
            logs = _container_logs(name)
            if len(logs) > last_log:
                emit("log", logs[last_log:])
                last_log = len(logs)
            time.sleep(1.0)

        logs = _container_logs(name)
        if len(logs) > last_log:
            emit("log", logs[last_log:])
        graph, error = sandbox.get_outcome(job_id)
        if error:
            raise RuntimeError(f"agent failed: {error}\n{logs[-1500:]}")
        if not graph:
            raise RuntimeError(f"agent returned no graph\n{logs[-1500:]}")
        emit("status", "graph received from the agent")
        return graph, {"container": name, "job": job_id, "limits": limits}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def _container_logs(name: str) -> str:
    out = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return (out.stdout or "") + (out.stderr or "")
