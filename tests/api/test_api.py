#!/usr/bin/env python3
"""ArchIDE API-level e2e tests (Python stdlib only).

Runs against a server started by tests/run_e2e.sh. Covers scenarios S1-S3, S5-S9.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ARCHIDE_TEST_URL", "http://127.0.0.1:8901")
ROOT = pathlib.Path(__file__).resolve().parents[2]
POC = ROOT / "archide-poc"
REPO = str(ROOT / "tests" / "fixtures" / "sample_repo")
DATA = os.environ.get("ARCHIDE_DATA", "")
STUB_STATE = os.environ.get("STUB_STATE", "")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS: list[tuple[str, bool, str]] = []


def req(method: str, path: str, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _opener.open(request, timeout=40) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode("utf-8", "ignore")}


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + (("  :: " + detail) if (detail and not cond) else ""))


def put_config(patch: dict) -> None:
    req("PUT", "/api/config", patch)


def run_and_wait(repo: str, agent: str, timeout: float = 60) -> tuple[dict, list]:
    status, resp = req("POST", "/api/reconstruct", {"repo": repo, "agent": agent})
    run_id = resp.get("run_id")
    if not run_id:
        raise RuntimeError(f"no run_id: {status} {resp}")
    since, events = 0, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, snap = req("GET", f"/api/runs/{run_id}?since={since}")
        events += snap.get("events", [])
        since = snap.get("event_count", since)
        if snap.get("status") != "running":
            return snap, events
        time.sleep(0.4)
    raise RuntimeError("run timed out")


def main() -> int:
    print(f"API e2e against {BASE}  (repo={REPO})")
    sys.path.insert(0, str(POC))

    # S6 - agent registry
    _, agents = req("GET", "/api/agents")
    ids = [a.get("id") for a in agents.get("agents", [])]
    check("S6 agents registry lists builtin+kilocode", "builtin" in ids and "kilocode" in ids, str(ids))

    # S1 - config persistence
    put_config({"llm": {"mode": "mock", "model": "test-model"},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "max_chars": 20000, "compress_at": 0.8, "strategy": "extract"}})
    _, cfg = req("GET", "/api/config")
    check("S1 config round-trip (GET == PUT)", cfg.get("llm", {}).get("mode") == "mock"
          and cfg.get("context", {}).get("compare_docs") is False)
    if DATA:
        p = pathlib.Path(DATA) / "config.json"
        on_disk = json.loads(p.read_text()) if p.exists() else {}
        check("S1 config persisted to disk", on_disk.get("llm", {}).get("mode") == "mock", str(p))

    # S2 - reconstruction from source code (single pass, mock/local)
    snap, events = run_and_wait(REPO, "builtin")
    val = snap.get("validation") or {}
    graph = snap.get("graph") or {}
    check("S2 run completes", snap.get("status") == "done", str(snap.get("status")))
    check("S2 graph valid and non-empty", bool(val.get("ok")) and len(graph.get("nodes", [])) > 0,
          json.dumps(val.get("errors", [])[:3]))
    check("S2 activity events present", any("pass 1/2" in e.get("text", "") for e in events))

    # Ф2.1 - metamodel registry + model persistence
    _, mm = req("GET", "/api/metamodel")
    check("Ф2.1 metamodel registry exposed",
          bool(mm.get("node_types")) and bool(mm.get("edge_types")) and "structure" in (mm.get("aspects") or {}),
          str(list((mm.get("aspects") or {}).keys())))
    check("Ф2.1 referential actions exposed", "contains" in (mm.get("referential_actions") or {}))
    check("Ф2.1 run meta carries the model hash", bool((snap.get("meta") or {}).get("model_hash")))

    _, models = req("GET", "/api/models")
    names = [m.get("name") for m in models.get("models", [])]
    check("Ф2.1 model persisted after a run", len(names) > 0, str(names))
    if names:
        _, stored = req("GET", f"/api/models/{names[0]}")
        check("Ф2.1 stored model has nodes", bool(stored.get("nodes")))
        from backend import model_store  # noqa: E402
        check("Ф2.1 canonical hash is stable", model_store.model_hash(stored) == models["models"][0]["hash"])

    # Ф2.2 - aspect / anchor / cross-aspect validation
    check("Ф2.2 registry is self-consistent", mm.get("registry_errors") == [], str(mm.get("registry_errors")))
    aspect_nodes = [
        {"id": "sys://a", "type": "System", "parent": None, "evidence": ["a.py"]},
        {"id": "component://a/svc", "type": "Component", "parent": "sys://a", "evidence": ["svc.py"]},
        {"id": "iface://a/svc", "type": "Interface", "parent": "component://a/svc", "evidence": ["svc.py"]},
    ]
    st, r = req("POST", "/api/validate",
                {"graph": {"nodes": aspect_nodes, "edges": [{"source": "component://a/svc", "target": "iface://a/svc", "type": "exposes"}]}})
    check("Ф2.2 valid aspect graph accepted", st == 200 and r["validation"]["ok"] is True,
          str(r.get("validation", {}).get("errors")))

    st, r = req("POST", "/api/validate",
                {"graph": {"nodes": aspect_nodes, "edges": [{"source": "component://a/svc", "target": "component://a/svc", "type": "exposes"}]}})
    check("Ф2.2 cross-aspect violation rejected", r["validation"]["ok"] is False and any("exposes" in e for e in r["validation"]["errors"]),
          str(r["validation"]["errors"][:2]))

    st, r = req("POST", "/api/validate", {"graph": {"nodes": [
        {"id": "sys://a", "type": "System", "parent": None, "evidence": ["a.py"]},
        {"id": "actor://u", "type": "Actor", "parent": "sys://a", "evidence": ["a.py"]},
        {"id": "iface://x", "type": "Interface", "parent": "actor://u", "evidence": ["a.py"]},
    ], "edges": []}})
    check("Ф2.2 anchor violation rejected", r["validation"]["ok"] is False and any("anchored" in e for e in r["validation"]["errors"]),
          str(r["validation"]["errors"][:2]))

    st, _ = req("POST", "/api/validate", {"nope": 1})
    check("Ф2.2 validate rejects missing graph (400)", st == 400, str(st))

    # Ф2.3/Ф2.4 - aggregation interfaces + requirement coverage
    spectest = {"nodes": [
        {"id": "sys://s", "type": "System", "parent": None, "evidence": ["a.py"]},
        {"id": "component://s/svc", "type": "Component", "parent": "sys://s", "evidence": ["svc.py"]},
        {"id": "iface://s/a", "type": "Interface", "parent": "component://s/svc", "evidence": ["svc.py"]},
        {"id": "iface://s/b", "type": "Interface", "parent": "component://s/svc", "evidence": ["svc.py"]},
        {"id": "agg://s/channel", "type": "AggregationInterface", "parent": "sys://s", "contract": "iface://s/a", "evidence": ["svc.py"]},
        {"id": "req://REG-1", "type": "Requirement", "parent": None, "evidence": ["docs.md"]},
    ], "edges": [
        {"source": "iface://s/a", "target": "agg://s/channel", "type": "aggregates"},
        {"source": "iface://s/b", "target": "agg://s/channel", "type": "aggregates"},
        {"source": "component://s/svc", "target": "iface://s/a", "type": "exposes"},
        {"source": "req://REG-1", "target": "component://s/svc", "type": "satisfies"},
    ]}
    st, r = req("PUT", "/api/models/spectest", {"graph": spectest})
    check("Ф2.3 model with aggregation accepted", st == 200 and r.get("ok") is True, str(r.get("validation", {}).get("errors")))

    st, aggs = req("GET", "/api/models/spectest/aggregations")
    agg = (aggs.get("aggregations") or [{}])[0]
    check("Ф2.3 aggregation listed with members+contract",
          st == 200 and len(agg.get("members", [])) == 2 and agg.get("contract") == "iface://s/a", str(agg))

    st, cov = req("GET", "/api/models/spectest/coverage")
    c = cov.get("coverage") or {}
    check("Ф2.4 coverage computed", st == 200 and c.get("requirements") == 1 and c.get("covered") == 1 and c.get("ratio") == 1.0, str(c))

    bad_agg = {"nodes": [
        {"id": "sys://s", "type": "System", "parent": None, "evidence": ["a.py"]},
        {"id": "agg://s/x", "type": "AggregationInterface", "parent": "sys://s", "evidence": ["a.py"]},
    ], "edges": []}
    st, r = req("PUT", "/api/models/badagg", {"graph": bad_agg})
    check("Ф2.3 empty aggregation rejected (422)", st == 422 and r.get("ok") is False, str(st))

    # S3 - two-pass code + docs, merge + deviations + source filter data
    put_config({"context": {"compare_docs": True}})
    snap, events = run_and_wait(REPO, "builtin")
    graph = snap.get("graph") or {}
    sources = [tuple(n.get("sources", [])) for n in graph.get("nodes", [])]
    devs = [n for n in graph.get("nodes", []) if n.get("deviation")]
    check("S3 docs pass executed", bool((snap.get("meta") or {}).get("docs_pass")))
    check("S3 merged sources include code+docs", ("code", "docs") in sources, str(set(sources)))
    check("S3 deviations flagged", len(devs) > 0, f"deviations={len(devs)}")
    check("S3 merge event present", any("merged code + docs" in e.get("text", "") for e in events))

    # S8 - validation surface + validation tool route
    status, _ = req("POST", "/sandbox/validate/deadbeef?token=x", {"graph": {"nodes": []}})
    check("S8 validate tool rejects unknown job (404)", status == 404, str(status))
    sys.path.insert(0, str(POC))
    from backend.graph_schema import validate_graph  # noqa: E402
    good = {"nodes": [{"id": "sys://a", "type": "System", "parent": None, "evidence": ["a.py"]}], "edges": []}
    bad = {"nodes": [{"id": "sys://a", "type": "System", "parent": None}, {"id": "b", "type": "Nope", "parent": "a"}], "edges": []}
    check("S8 validator accepts a valid graph", validate_graph(good)["ok"] is True)
    check("S8 validator rejects an invalid graph", validate_graph(bad)["ok"] is False)

    # S6 - shared volume API
    _, vol = req("GET", "/api/volume")
    check("S6 volume info available", bool(vol.get("name")), str(vol))
    status, reset = req("POST", "/api/volume/reset")
    check("S6 volume reset ok", status == 200 and reset.get("ok") is True, str(reset))

    # Ф1.1/Ф1.2 - agent manifest + sandbox limits
    entries = agents.get("agents", [])
    check("Ф1.1 registry entries carry kind+limits",
          len(entries) >= 2 and all("kind" in a and "limits" in a for a in entries),
          str([(a.get("id"), a.get("kind")) for a in entries]))
    if DATA:
        check("Ф1.1 agent manifest created on disk", (pathlib.Path(DATA) / "agents.json").exists())

    if STUB_STATE:
        argv_log = pathlib.Path(STUB_STATE) / "argv.log"
        argv_log.unlink(missing_ok=True)
        put_config({"agent": {"runner": "docker"}, "llm": {"mode": "mock"}, "context": {"compare_docs": False}})
        snap, _ = run_and_wait(REPO, "builtin", timeout=60)
        argv = argv_log.read_text() if argv_log.exists() else ""
        check("Ф1.2 sandbox limits applied to docker run",
              "--pids-limit 256" in argv and "--security-opt no-new-privileges" in argv,
              argv[:200])
        check("Ф1.2 run meta records limits", bool((snap.get("meta") or {}).get("limits")),
              str((snap.get("meta") or {}).get("limits")))

    # gateway auth
    status, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []})
    check("gateway rejects missing token (401)", status == 401, str(status))
    status, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []},
                    headers={"Authorization": "Bearer wrong"})
    check("gateway rejects wrong token (401)", status == 401, str(status))

    # S5 - cancel a running (docker-path) run
    if STUB_STATE:
        pathlib.Path(STUB_STATE, "slow").write_text("1")
        try:
            put_config({"agent": {"runner": "docker"}, "llm": {"mode": "mock"}})
            _, resp = req("POST", "/api/reconstruct", {"repo": REPO, "agent": "builtin"})
            run_id = resp.get("run_id")
            time.sleep(1.5)
            req("POST", f"/api/runs/{run_id}/cancel")
            deadline = time.time() + 30
            status = "running"
            while time.time() < deadline:
                _, snap = req("GET", f"/api/runs/{run_id}")
                status = snap.get("status")
                if status != "running":
                    break
                time.sleep(0.4)
            check("S5 cancel stops a running agent", status == "cancelled", str(status))
        finally:
            pathlib.Path(STUB_STATE, "slow").unlink(missing_ok=True)

    # S9 - error surfaced for unreachable LLM (live, local runner)
    put_config({"llm": {"mode": "live", "base_url": "http://127.0.0.1:9/v1", "model": "x", "timeout": 3},
                "agent": {"runner": "local"}})
    snap, _ = run_and_wait(REPO, "builtin", timeout=40)
    check("S9 failed run surfaces an error", snap.get("status") == "error" and bool(snap.get("error")),
          f"status={snap.get('status')} error={str(snap.get('error'))[:80]}")

    # S10 - demo fixture is valid
    demo_path = POC / "frontend" / "demo.json"
    demo = json.loads(demo_path.read_text()) if demo_path.exists() else {}
    check("S10 demo.json present and valid", bool(demo.get("graph", {}).get("nodes")) and bool(demo.get("validation")))

    # restore a sane config
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"}, "context": {"compare_docs": False}})

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\nAPI: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
