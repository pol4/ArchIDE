"""ArchIDE LLM gateway (Ф1.3).

Agents authenticate with a short-lived **lease** token (issued per run/agent); the
gateway validates it (or the master token) and injects the **real** upstream key.
Every request is **audited** (principal, model, status) without secrets, and a
per-lease **quota** can be enforced.
"""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request

from . import config as config_mod

_leases: dict[str, dict] = {}
_audit: list[dict] = []
_AUDIT_MAX = 200


def ensure_token(cfg: dict) -> str:
    """Return the master gateway token, generating and persisting one if missing."""
    token = (cfg.get("gateway") or {}).get("token") or ""
    if not token:
        token = "archgw-" + secrets.token_hex(16)
        config_mod.save_config({"gateway": {"token": token}})
    return token


def issue_lease(cfg: dict, agent_id: str) -> dict:
    token = "archlease-" + secrets.token_hex(16)
    ttl = int((cfg.get("gateway") or {}).get("lease_ttl", 3600) or 3600)
    _leases[token] = {"agent": agent_id, "expires": time.time() + ttl, "used": 0}
    return {"token": token, "agent": agent_id, "expires": _leases[token]["expires"]}


def _lease(token: str) -> dict | None:
    lease = _leases.get(token)
    if not lease:
        return None
    if lease["expires"] < time.time():
        _leases.pop(token, None)
        return None
    return lease


def _supplied_token(headers) -> str:
    auth = headers.get("Authorization", "") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("X-OpenAI-Api-Key", "") or headers.get("X-Api-Key", "") or ""


def authorize(cfg: dict, headers) -> tuple[bool, str | None]:
    """Return (ok, principal) where principal is ``"master"`` or an agent id."""
    expected = (cfg.get("gateway") or {}).get("token") or ""
    supplied = _supplied_token(headers)
    if expected and supplied == expected:
        return True, "master"
    lease = _lease(supplied) if supplied else None
    if lease:
        return True, lease["agent"]
    return False, None


def authorized(cfg: dict, headers) -> bool:
    return authorize(cfg, headers)[0]


def consume(cfg: dict, headers) -> tuple[bool, str | None, int]:
    """Authorize + enforce the per-lease quota. Returns (ok, principal, error_status)."""
    ok, principal = authorize(cfg, headers)
    if not ok:
        return False, None, 401
    lease = _lease(_supplied_token(headers))
    if lease:
        quota = int((cfg.get("gateway") or {}).get("quota_per_lease", 0) or 0)
        if quota and lease["used"] >= quota:
            return False, principal, 429
        lease["used"] += 1
    return True, principal, 0


def record(cfg: dict, entry: dict) -> None:
    item = {"ts": round(time.time(), 3)}
    item.update(entry)
    _audit.append(item)
    del _audit[:-_AUDIT_MAX]
    try:
        config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (config_mod.DATA_DIR / "gateway-audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        pass


def info(cfg: dict) -> dict:
    gw = cfg.get("gateway") or {}
    return {
        "enabled": bool(gw.get("enabled", True)),
        "quota_per_lease": int(gw.get("quota_per_lease", 0) or 0),
        "lease_ttl": int(gw.get("lease_ttl", 3600) or 3600),
        "leases_active": len(_leases),
        "audit": len(_audit),
    }


def audit_tail(n: int = 50) -> list[dict]:
    return _audit[-n:]


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def upstream_url(cfg: dict) -> str:
    llm = cfg.get("llm", {})
    return str(llm.get("base_url", "")).rstrip("/") + "/chat/completions"


def _request(cfg: dict, body: bytes):
    llm = cfg.get("llm", {})
    request = urllib.request.Request(upstream_url(cfg), data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if llm.get("api_key"):
        request.add_header("Authorization", "Bearer " + str(llm["api_key"]))
    return _opener().open(request, timeout=float(llm.get("timeout", 180)) + 30)


def forward_chat(cfg: dict, body: bytes):
    """Return (status, content_type, body_bytes) from the real LLM."""
    try:
        with _request(cfg, body) as resp:
            return resp.status, resp.headers.get("Content-Type", "application/json"), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, "application/json", exc.read()
    except urllib.error.URLError as exc:
        payload = json.dumps({"error": {"message": f"gateway upstream failed: {exc.reason}"}})
        return 502, "application/json", payload.encode("utf-8")


def stream_chat(cfg: dict, body: bytes):
    """Yield (status, content_type, iterator_of_bytes) for streaming responses."""
    try:
        resp = _request(cfg, body)
    except urllib.error.HTTPError as exc:
        return exc.code, "application/json", iter([exc.read()])
    except urllib.error.URLError as exc:
        payload = json.dumps({"error": {"message": f"gateway upstream failed: {exc.reason}"}})
        return 502, "application/json", iter([payload.encode("utf-8")])

    content_type = resp.headers.get("Content-Type", "text/event-stream")

    def chunks():
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()

    return resp.status, content_type, chunks()


def models(cfg: dict) -> dict:
    model = cfg.get("llm", {}).get("model", "")
    return {"object": "list", "data": [{"id": model, "object": "model"}] if model else []}
