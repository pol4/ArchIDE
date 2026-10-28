"""Signed markers (Ф7.3, Ф5.3).

Unforgeable markers: a host-held HMAC key signs a payload; anyone can verify it,
only the host can mint it. Used for document chunks (content-bound) and for code
trace markers (artifact/chain/lease).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from . import config as config_mod

_FIELDS = ("doc", "chunk", "version", "user", "span", "text_hash")
TRACE_FIELDS = ("artifact", "chain", "lease")


def _key(cfg: dict) -> str:
    """Host-held HMAC key, stable across calls.

    The key is minted once and persisted; a caller whose config predates the
    first mint gets the persisted key (and its own config is updated), otherwise
    signing and verification would use different keys.
    """
    section = dict((cfg or {}).get("markers") or {}) if isinstance(cfg, dict) else {}
    key = section.get("key") or ""
    if not key:
        key = (config_mod.load_config().get("markers") or {}).get("key") or ""
    if not key:
        key = secrets.token_hex(32)
        config_mod.save_config({"markers": {"key": key}})
    if isinstance(cfg, dict):
        cfg["markers"] = {**section, "key": key}
    return key


def _payload(marker: dict, fields) -> str:
    return json.dumps({k: marker.get(k) for k in fields}, sort_keys=True, separators=(",", ":"))


def _signature(cfg: dict, marker: dict, fields) -> str:
    return hmac.new(_key(cfg).encode("utf-8"), _payload(marker, fields).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _sign(cfg: dict, payload: dict, fields) -> dict:
    signed = {k: payload.get(k) for k in fields}
    signed["sig"] = _signature(cfg, signed, fields)
    return signed


def _verify(cfg: dict, marker, fields) -> bool:
    if not isinstance(marker, dict) or "sig" not in marker:
        return False
    return hmac.compare_digest(_signature(cfg, marker, fields), str(marker.get("sig")))


def _text_hash(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def sign(cfg: dict, marker: dict, text: str | None = None) -> dict:
    payload = {k: marker.get(k) for k in _FIELDS if k != "text_hash"}
    if text is not None:
        payload["text_hash"] = _text_hash(text)
    return _sign(cfg, payload, _FIELDS)


def verify(cfg: dict, marker) -> bool:
    return _verify(cfg, marker, _FIELDS)


def verify_content(cfg: dict, marker, text) -> bool:
    """True if the marker is valid AND its content digest matches `text`."""
    if not verify(cfg, marker):
        return False
    expected = marker.get("text_hash")
    if expected is None:
        return True
    return hmac.compare_digest(str(expected), _text_hash(text))


def sign_trace(cfg: dict, payload: dict) -> dict:
    return _sign(cfg, payload, TRACE_FIELDS)


def verify_trace(cfg: dict, marker) -> bool:
    return _verify(cfg, marker, TRACE_FIELDS)
