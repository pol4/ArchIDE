"""ArchIDE LLM gateway.

A small OpenAI-compatible proxy that all sandbox agents talk to. Agents carry a
single **gateway token** (shared, owned by ArchIDE); the gateway validates it and
injects the **real** upstream key (``llm.api_key``) when calling the actual LLM
inference. The real key never reaches the agents.
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request

from . import config as config_mod


def ensure_token(cfg: dict) -> str:
    """Return the gateway token, generating and persisting one if missing."""
    token = (cfg.get("gateway") or {}).get("token") or ""
    if not token:
        token = "archgw-" + secrets.token_hex(16)
        config_mod.save_config({"gateway": {"token": token}})
    return token


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def authorized(cfg: dict, headers) -> bool:
    expected = (cfg.get("gateway") or {}).get("token") or ""
    if not expected:
        return False
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip() == expected:
        return True
    if headers.get("X-OpenAI-Api-Key", "") == expected:
        return True
    if headers.get("X-Api-Key", "") == expected:
        return True
    return False


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
