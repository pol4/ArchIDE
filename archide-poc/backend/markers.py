"""Signed trace markers (Ф7.3).

Unforgeable markers for document chunks — the same idea as graph-element markers
(§11): a host-held HMAC key signs a payload; anyone can verify it, only the host
can mint it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from . import config as config_mod

_FIELDS = ("doc", "chunk", "version", "user", "span", "text_hash")


def _key(cfg: dict) -> str:
    key = (cfg.get("markers") or {}).get("key") or ""
    if not key:
        key = secrets.token_hex(32)
        config_mod.save_config({"markers": {"key": key}})
    return key


def _payload(marker: dict) -> str:
    return json.dumps({k: marker.get(k) for k in _FIELDS}, sort_keys=True, separators=(",", ":"))


def _signature(cfg: dict, marker: dict) -> str:
    return hmac.new(_key(cfg).encode("utf-8"), _payload(marker).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _text_hash(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def sign(cfg: dict, marker: dict, text: str | None = None) -> dict:
    signed = {k: marker.get(k) for k in _FIELDS if k != "text_hash"}
    if text is not None:
        signed["text_hash"] = _text_hash(text)
    signed["sig"] = _signature(cfg, signed)
    return signed


def verify(cfg: dict, marker) -> bool:
    if not isinstance(marker, dict) or "sig" not in marker:
        return False
    return hmac.compare_digest(_signature(cfg, marker), str(marker.get("sig")))


def verify_content(cfg: dict, marker, text) -> bool:
    """True if the marker is valid AND its content digest matches `text`."""
    if not verify(cfg, marker):
        return False
    expected = marker.get("text_hash")
    if expected is None:
        return True
    return hmac.compare_digest(str(expected), _text_hash(text))
