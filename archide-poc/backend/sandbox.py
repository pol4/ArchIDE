"""Sandbox job store.

Coordinates a sandboxed agent without bind mounts:
  1. the host creates a job (repo tarball + prompt + constraints + token),
  2. the container downloads the repo, posts activity events and uploads the
     result over HTTP,
  3. the host waits for the result.

Jobs are in-memory and single-use (PoC).
"""
from __future__ import annotations

import io
import secrets
import tarfile
import threading
import time
from typing import Callable

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_TTL = 3600.0

OnEvent = Callable[[str, str], None]


def _gc() -> None:
    now = time.time()
    for job_id in [j for j, job in _jobs.items() if now - job["created"] > _TTL]:
        _jobs.pop(job_id, None)


def create_job(
    files: list[tuple[str, str]],
    prompt: str,
    constraints: dict,
    repo_name: str,
    agent_id: str,
    on_event: OnEvent | None = None,
) -> tuple[str, str]:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    job_id = secrets.token_hex(8)
    token = secrets.token_hex(16)
    with _lock:
        _gc()
        _jobs[job_id] = {
            "token": token,
            "tar": buf.getvalue(),
            "prompt": prompt,
            "constraints": constraints,
            "repo_name": repo_name,
            "agent": agent_id,
            "on_event": on_event,
            "result": None,
            "error": None,
            "created": time.time(),
            "done": threading.Event(),
        }
    return job_id, token


def _job(job_id: str, token: str) -> dict | None:
    job = _jobs.get(job_id)
    if not job or job["token"] != token:
        return None
    return job


def get_repo(job_id: str, token: str) -> bytes | None:
    job = _job(job_id, token)
    return job["tar"] if job else None


def get_meta(job_id: str, token: str) -> dict | None:
    job = _job(job_id, token)
    if not job:
        return None
    return {
        "repo_name": job["repo_name"],
        "prompt": job["prompt"],
        "constraints": job["constraints"],
        "agent": job["agent"],
    }


def put_event(job_id: str, token: str, kind: str, text: str) -> bool:
    job = _job(job_id, token)
    if not job:
        return False
    handler = job.get("on_event")
    if handler:
        handler(kind, text)
    return True


def put_result(job_id: str, token: str, graph: dict | None, error: str | None) -> bool:
    job = _job(job_id, token)
    if not job:
        return False
    job["result"] = graph
    job["error"] = error
    job["done"].set()
    return True


def is_done(job_id: str) -> bool:
    job = _jobs.get(job_id)
    return bool(job and job["done"].is_set())


def get_outcome(job_id: str) -> tuple[dict | None, str | None]:
    job = _jobs.get(job_id)
    if not job:
        return None, "unknown job"
    return job["result"], job["error"]


def wait_result(job_id: str, timeout: float) -> tuple[dict | None, str | None]:
    job = _jobs.get(job_id)
    if not job:
        return None, "unknown job"
    if not job["done"].wait(timeout):
        return None, f"agent did not return a result within {int(timeout)}s"
    return get_outcome(job_id)
