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
_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_TTL = 3600.0

OnEvent = Callable[[str, str], None]


def _tar(files: list[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


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
    job_id = secrets.token_hex(8)
    token = secrets.token_hex(16)
    with _lock:
        _gc()
        _jobs[job_id] = {
            "token": token,
            "tar": _tar(files),
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


# --- continuous agent sessions (Ф3.10) -------------------------------------
# One sandbox container serves the whole run: the repository is uploaded once
# and later requests (base pass, aspect passes, repairs) are queued into the
# same session instead of starting a fresh container per prompt.

def _gc_sessions() -> None:
    now = time.time()
    for session_id in [s for s, sess in _sessions.items() if now - sess["created"] > _TTL]:
        sess = _sessions.pop(session_id, None)
        if sess:
            with sess["cond"]:
                sess["closed"] = True
                sess["cond"].notify_all()


def create_session(files: list[tuple[str, str]], repo_name: str, agent_id: str,
                   on_event: OnEvent | None = None) -> tuple[str, str]:
    session_id = secrets.token_hex(8)
    token = secrets.token_hex(16)
    with _lock:
        _gc_sessions()
        _sessions[session_id] = {
            "token": token,
            "tar": _tar(files),
            "repo_name": repo_name,
            "agent": agent_id,
            "on_event": on_event,
            "created": time.time(),
            "queue": [],
            "requests": {},
            "closed": False,
            "cond": threading.Condition(),
        }
    return session_id, token


def _session(session_id: str, token: str) -> dict | None:
    sess = _sessions.get(session_id)
    if not sess or sess["token"] != token:
        return None
    return sess


def session_repo(session_id: str, token: str) -> bytes | None:
    sess = _session(session_id, token)
    return sess["tar"] if sess else None


def session_event(session_id: str, token: str, kind: str, text: str) -> bool:
    sess = _session(session_id, token)
    if not sess:
        return False
    handler = sess.get("on_event")
    if handler:
        handler(kind, text)
    return True


def session_submit(session_id: str, prompt: str, constraints: dict, repo_name: str | None = None) -> str | None:
    sess = _sessions.get(session_id)
    if not sess or sess["closed"]:
        return None
    request_id = secrets.token_hex(6)
    with sess["cond"]:
        sess["requests"][request_id] = {
            "prompt": prompt,
            "constraints": constraints,
            "repo_name": repo_name or sess["repo_name"],
            "result": None,
            "error": None,
            "done": threading.Event(),
        }
        sess["queue"].append(request_id)
        sess["cond"].notify_all()
    return request_id


def session_take(session_id: str, token: str, wait: float = 120.0) -> dict | None:
    """Container side: take the next queued request (or learn that it is done)."""
    sess = _session(session_id, token)
    if not sess:
        return None
    with sess["cond"]:
        deadline = time.time() + max(0.0, wait)
        while True:
            if sess["queue"]:
                request_id = sess["queue"].pop(0)
                req = sess["requests"][request_id]
                return {"rid": request_id, "prompt": req["prompt"],
                        "constraints": req["constraints"], "repo_name": req["repo_name"]}
            if sess["closed"]:
                return {"done": True}
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"empty": True}
            sess["cond"].wait(min(remaining, 5.0))


def session_put_result(session_id: str, token: str, request_id: str,
                       graph: dict | None, error: str | None) -> bool:
    sess = _session(session_id, token)
    if not sess:
        return False
    with sess["cond"]:
        req = sess["requests"].get(request_id)
        if req is None:
            return False
        req["result"], req["error"] = graph, error
        req["done"].set()
        sess["cond"].notify_all()
    return True


def session_wait(session_id: str, request_id: str, timeout: float) -> tuple[dict | None, str | None]:
    sess = _sessions.get(session_id)
    if not sess:
        return None, "unknown session"
    req = sess["requests"].get(request_id)
    if req is None:
        return None, "unknown request"
    if not req["done"].wait(timeout):
        return None, None   # still working: the caller keeps waiting
    return req["result"], req["error"]


def session_close(session_id: str, token: str) -> bool:
    sess = _session(session_id, token)
    if not sess:
        return False
    with sess["cond"]:
        sess["closed"] = True
        sess["cond"].notify_all()
    return True


def session_closed(session_id: str) -> bool:
    sess = _sessions.get(session_id)
    return bool(sess and sess["closed"])
