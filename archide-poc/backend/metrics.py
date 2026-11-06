"""Metrics and structured logs (Ф12.2).

The platform scrapes `/metrics` (Prometheus text format) and collects container
stdout into Loki, so the service must expose both. No client libraries: the
exposition format is a few lines of text and a counter is a dict.

Everything here is process-local and lock-protected — one process serves the UI,
the API and the in-process agent.
"""
from __future__ import annotations

import json
import threading
import time

_LOCK = threading.Lock()
_STARTED = time.time()

HELP = {
    "archide_info": ("gauge", "Build and runtime identity of this instance"),
    "archide_uptime_seconds": ("gauge", "Seconds since the process started"),
    "archide_requests_total": ("counter", "HTTP requests by method, route and status"),
    "archide_llm_calls_total": ("counter", "LLM calls by outcome"),
    "archide_llm_call_seconds": ("summary", "LLM call duration in seconds"),
    "archide_runs_total": ("counter", "Reconstruction/synthesis runs by final status"),
    "archide_runs_active": ("gauge", "Runs currently executing"),
    "archide_models": ("gauge", "Models stored on disk"),
    "archide_graph_nodes": ("gauge", "Nodes in the most recent run graph"),
    "archide_graph_edges": ("gauge", "Edges in the most recent run graph"),
    "archide_auth_failures_total": ("counter", "Rejected requests without a valid session"),
}

_counters: dict[tuple[str, tuple], float] = {}
_gauges: dict[tuple[str, tuple], float] = {}
_sum_count: dict[tuple[str, tuple], float] = {}
_sum_total: dict[tuple[str, tuple], float] = {}


def _key(name: str, labels: dict | None):
    return (name, tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items())))


def inc(name: str, labels: dict | None = None, value: float = 1) -> None:
    with _LOCK:
        key = _key(name, labels)
        _counters[key] = _counters.get(key, 0) + value


def set_gauge(name: str, value: float, labels: dict | None = None) -> None:
    with _LOCK:
        _gauges[_key(name, labels)] = value


def observe_llm(seconds: float, outcome: str = "ok") -> None:
    """One LLM call: its outcome and duration."""
    inc("archide_llm_calls_total", {"outcome": outcome})
    with _LOCK:
        key = _key("archide_llm_call_seconds", None)
        _sum_count[key] = _sum_count.get(key, 0) + 1
        _sum_total[key] = _sum_total.get(key, 0) + float(seconds)


def inc_request(method: str, route: str, status: int) -> None:
    inc("archide_requests_total", {"method": method, "route": route, "status": status})
    if int(status) == 401:
        inc("archide_auth_failures_total")


def _fmt_labels(labels: tuple) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


def render(info: dict | None = None) -> str:
    """The Prometheus text exposition."""
    lines: list[str] = []
    with _LOCK:
        counters = dict(_counters)
        gauges = dict(_gauges)
        sum_count = dict(_sum_count)
        sum_total = dict(_sum_total)

    for name in ("archide_info", "archide_uptime_seconds", "archide_requests_total",
                 "archide_llm_calls_total", "archide_llm_call_seconds", "archide_runs_total",
                 "archide_runs_active", "archide_models", "archide_graph_nodes",
                 "archide_graph_edges", "archide_auth_failures_total"):
        kind, help_text = HELP[name]
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")

    identity = {"version": "0.1.0", "runner": "?", "auth": "none", "platform": "standalone"}
    identity.update(info or {})
    for key, value in sorted(identity.items()):
        lines.append(f'archide_info{{{key}="{value}"}} 1')

    lines.append(f"archide_uptime_seconds {time.time() - _STARTED:.3f}")

    for (name, labels), value in sorted(counters.items()):
        lines.append(f"{name}{_fmt_labels(labels)} {value:g}")
    for (name, labels), value in sorted(gauges.items()):
        lines.append(f"{name}{_fmt_labels(labels)} {value:g}")
    for key, count in sorted(sum_count.items()):
        name, _labels = key
        lines.append(f"{name}_count {count:g}")
        lines.append(f"{name}_sum {sum_total.get(key, 0):.6f}")

    return "\n".join(lines) + "\n"


def access_line(method: str, path: str, status: int, seconds: float, user: str = "") -> str:
    """One structured access-log line (JSON) for stdout/Loki."""
    return json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "level": "info" if int(status) < 400 else "warn",
        "msg": "http",
        "method": method,
        "path": path,
        "status": int(status),
        "duration_ms": round(float(seconds) * 1000, 2),
        "user": user or "",
    }, ensure_ascii=False)
