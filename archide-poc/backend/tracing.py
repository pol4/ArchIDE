"""OTLP traces (Ф12.7, optional).

The platform collects traces through OTLP (`platform-tempo.pf-observability…`).
This module emits spans in the **OTLP/HTTP + JSON** encoding, which needs nothing
but the standard library — no gRPC, no protobuf.

Tracing is **off unless configured**: the endpoint comes from
`tracing.endpoint` / `ARCHIDE_OTLP_ENDPOINT`, so the standalone version stays
silent and no accidental traffic leaves the workstation.

Spans are emitted at the boundaries that matter for an architect: a run (with its
model, status and size) and each LLM call (outcome and duration).
"""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request

_SERVICE_DEFAULT = "archide"


def _settings(cfg: dict | None) -> dict:
    if cfg is None:
        # вызов без конфига — берём сохранённый (иначе трейсы молча не уходят)
        try:
            from . import config as config_mod
            cfg = config_mod.load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    section = dict((cfg or {}).get("tracing") or {})
    env_endpoint = os.environ.get("ARCHIDE_OTLP_ENDPOINT")
    if env_endpoint:
        section["endpoint"] = env_endpoint
        section["enabled"] = True
    section.setdefault("enabled", False)
    section.setdefault("service", _SERVICE_DEFAULT)
    return section


def enabled(cfg: dict | None = None) -> bool:
    section = _settings(cfg)
    return bool(section.get("enabled")) and bool(section.get("endpoint"))


def _hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def _attrs(values: dict) -> list[dict]:
    out = []
    for key, value in (values or {}).items():
        if isinstance(value, bool):
            item = {"boolValue": value}
        elif isinstance(value, int):
            item = {"intValue": str(value)}
        elif isinstance(value, float):
            item = {"doubleValue": float(value)}
        else:
            item = {"stringValue": str(value)}
        out.append({"key": str(key), "value": item})
    return out


def emit(name: str, attributes: dict, start: float, end: float,
         resource_attrs: dict | None = None, cfg: dict | None = None) -> None:
    """Send one span (a no-op when tracing is disabled)."""
    if not enabled(cfg):
        return
    section = _settings(cfg)
    payload = {
        "resourceSpans": [{
            "resource": {"attributes": _attrs({"service.name": section.get("service", _SERVICE_DEFAULT)})},
            "scopeSpans": [{
                "scope": {"name": "archide"},
                "spans": [{
                    "traceId": _hex(16),
                    "spanId": _hex(8),
                    "name": name,
                    "kind": 1,
                    "startTimeUnixNano": str(int(float(start) * 1_000_000_000)),
                    "endTimeUnixNano": str(int(float(end) * 1_000_000_000)),
                    "attributes": _attrs({**(resource_attrs or {}), **attributes}),
                }],
            }],
        }],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = str(section["endpoint"]).rstrip("/") + "/v1/traces"
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5):
            pass
    except (urllib.error.URLError, OSError, ValueError):
        # tracing must never break the work it observes
        pass


def flush() -> None:
    """Spans are sent eagerly; kept for symmetry with batching exporters."""
    return None
