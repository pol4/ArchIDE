#!/usr/bin/env python3
"""ArchIDE API-level e2e tests (Python stdlib only).

Runs against a server started by tests/run_e2e.sh. Covers scenarios S1-S3, S5-S9.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
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


def req(method: str, path: str, body=None, headers=None, timeout: float = 40):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _opener.open(request, timeout=timeout) as resp:
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


def run_and_wait(repo: str, agent: str, timeout: float = 60, options: dict | None = None) -> tuple[dict, list]:
    body = {"repo": repo, "agent": agent}
    body.update(options or {})
    status, resp = req("POST", "/api/reconstruct", body)
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
        _, cfg_now = req("GET", "/api/config")
        master = (cfg_now.get("gateway") or {}).get("token") or ""
        check("Ф1.3 container gets a lease, not the master token",
              bool(master) and master not in argv, "master token leaked into the container")

    # gateway auth
    status, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []})
    check("gateway rejects missing token (401)", status == 401, str(status))
    status, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []},
                    headers={"Authorization": "Bearer wrong"})
    check("gateway rejects wrong token (401)", status == 401, str(status))

    # Ф1.3 - gateway leases / audit / per-lease quota
    _, gw = req("GET", "/api/gateway")
    check("Ф1.3 gateway info exposed", "quota_per_lease" in gw and "leases_active" in gw, str(gw))
    _, cfg_g = req("GET", "/api/config")
    master = (cfg_g.get("gateway") or {}).get("token") or ""
    st, lease = req("POST", "/api/gateway/lease", {"agent": "builtin"})
    lease_token = lease.get("token")
    check("Ф1.3 lease issued", st == 200 and bool(lease_token), str(lease))
    check("Ф1.3 lease token differs from master", bool(lease_token) and lease_token != master)
    st_ok, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []},
                   headers={"Authorization": "Bearer " + str(lease_token)})
    check("Ф1.3 lease token authenticates (not 401)", st_ok != 401, str(st_ok))
    _, audit = req("GET", "/api/gateway/audit")
    entries = audit.get("audit") or []
    check("Ф1.3 audit recorded", len(entries) >= 1, str(entries[:1]))
    check("Ф1.3 audit carries no secrets", all("api_key" not in json.dumps(e) for e in entries))
    check("Ф1.3 audit attributes the principal", any(e.get("principal") for e in entries), str(entries[:1]))

    put_config({"gateway": {"quota_per_lease": 1}})
    _, lease2 = req("POST", "/api/gateway/lease", {"agent": "builtin"})
    t2 = str(lease2.get("token"))
    r1, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []}, headers={"Authorization": "Bearer " + t2})
    r2, _ = req("POST", "/llm/v1/chat/completions", {"model": "m", "messages": []}, headers={"Authorization": "Bearer " + t2})
    check("Ф1.3 per-lease quota enforced (429 on 2nd)", r1 != 401 and r2 == 429, f"first={r1} second={r2}")
    put_config({"gateway": {"quota_per_lease": 0}})

    # Ф1.4 - persistent runs
    _, runs_list = req("GET", "/api/runs")
    check("Ф1.4 runs list exposed", isinstance(runs_list.get("runs"), list), str(runs_list)[:120])
    if DATA:
        rd = pathlib.Path(DATA) / "runs"
        files = sorted(rd.glob("*.json")) if rd.exists() else []
        check("Ф1.4 completed runs persisted on disk", len(files) >= 1, str(rd))
        if files:
            rec = json.loads(files[0].read_text())
            check("Ф1.4 run record has repo+status", bool(rec.get("repo")) and bool(rec.get("status")), str(rec)[:140])

    crafted = "deadbeefdeadbeef"
    if DATA:
        rd = pathlib.Path(DATA) / "runs"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / f"{crafted}.json").write_text(json.dumps({
            "id": crafted, "repo": "crafted-repo", "agent": "builtin", "status": "done",
            "created": 0, "updated": 0, "events": [{"ts": 0, "type": "status", "text": "persisted"}],
            "event_count": 1, "graph": None, "validation": {"ok": True}, "error": None, "meta": {},
        }))
    st, loaded = req("GET", f"/api/runs/{crafted}")
    check("Ф1.4 run loaded from disk (restart-safe)", st == 200 and loaded.get("id") == crafted and loaded.get("repo") == "crafted-repo",
          str(loaded)[:140])
    _, runs_list2 = req("GET", "/api/runs")
    ids2 = [r.get("id") for r in (runs_list2.get("runs") or [])]
    check("Ф1.4 runs list includes persisted run", crafted in ids2, str(ids2[:6]))

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

    # Ф2.5/Ф2.6 - aspects: metamodel aspects + aspects demo model validity
    check("Ф2.5 metamodel exposes aspects", len(mm.get("aspects") or {}) >= 8, str(list((mm.get("aspects") or {}).keys())))
    aspects_path = POC / "frontend" / "demo-aspects.json"
    aspects_demo = json.loads(aspects_path.read_text()) if aspects_path.exists() else {}
    st, r = req("POST", "/api/validate", {"graph": aspects_demo.get("graph", {})})
    check("Ф2.6 aspects demo model is valid", st == 200 and r["validation"]["ok"] is True,
          str((r.get("validation") or {}).get("errors")[:3]))

    # Ф3.1 - aspect-aware extraction
    ext_files = [
        {"path": "svc.py", "content": "def charge(order):\n    return order\n\ndef refund(order):\n    return order\n"},
        {"path": "models.py", "content": "@dataclass\nclass Order:\n    id: int\n"},
    ]
    st, ex = req("POST", "/api/extract", {"files": ext_files})
    etypes = sorted({n["type"] for n in (ex.get("nodes") or [])})
    check("Ф3.1 extract finds aspect nodes",
          st == 200 and "Interface" in etypes and "Operation" in etypes and "DataEntity" in etypes, str(etypes))
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True}})
    snap_x, _ = run_and_wait(REPO, "builtin")
    gtypes = {n["type"] for n in (snap_x.get("graph") or {}).get("nodes", [])}
    check("Ф3.1 reconstruction is aspect-aware", "Interface" in gtypes, str(sorted(gtypes)))

    # Ф3.1 - extraction must never invent a dangling parent/edge (regression):
    # the owning component comes from the graph; files with no owner must
    # anchor to an existing node instead of a fabricated `component://` id.
    anchor_graph = {"nodes": [
        {"id": "system://app", "type": "System", "name": "app", "parent": None, "evidence": []},
        {"id": "component://app/svc", "type": "Component", "name": "svc.py",
         "parent": "system://app", "evidence": ["svc.py"]},
    ], "edges": []}
    mixed_files = [
        {"path": "svc.py", "content": "def charge(order):\n    return order\n"},
        {"path": "tests/api/test_api.py", "content": "def helper(x):\n    return x\n"},
    ]
    st, ex2 = req("POST", "/api/extract", {"files": mixed_files, "graph": anchor_graph})
    known = {n["id"] for n in anchor_graph["nodes"]} | {n["id"] for n in (ex2.get("nodes") or [])}
    bad_parent = [n["id"] for n in (ex2.get("nodes") or []) if n.get("parent") and n["parent"] not in known]
    bad_edge = [e for e in (ex2.get("edges") or [])
                if e.get("source") not in known or e.get("target") not in known]
    svc_iface = next((n for n in (ex2.get("nodes") or []) if n.get("id") == "iface://app/svc.py"), None)
    check("Ф3.1 extraction has no dangling parents/edges",
          st == 200 and not bad_parent and not bad_edge, str(bad_parent or bad_edge)[:220])
    check("Ф3.1 extraction anchors an owned file to its component",
          svc_iface is not None and svc_iface.get("parent") == "component://app/svc", str(svc_iface)[:220])

    # Ф3.1 - when several owners claim a file, the most specific one wins
    # (Component over its Container over the System root), not the first seen.
    specific_graph = {"nodes": [
        {"id": "system://app", "type": "System", "name": "app", "parent": None, "evidence": []},
        {"id": "container://app", "type": "Container", "name": "app", "parent": "system://app",
         "evidence": ["mod.py"]},
        {"id": "component://app/mod", "type": "Component", "name": "mod.py",
         "parent": "container://app", "evidence": ["mod.py"]},
    ], "edges": []}
    st, ex3 = req("POST", "/api/extract",
                  {"files": [{"path": "mod.py", "content": "def run():\n    return 1\n"}],
                   "graph": specific_graph})
    mod_iface = next((n for n in (ex3.get("nodes") or []) if n.get("id") == "iface://app/mod.py"), None)
    check("Ф3.1 extraction prefers the most specific owner",
          st == 200 and mod_iface is not None and mod_iface.get("parent") == "component://app/mod",
          str(mod_iface)[:220])

    # Ф3.1 - extraction covers the remaining aspects (access/deployment/quality/
    # process/behavior) and every produced node is anchored per the metamodel.
    aspect_files = [
        {"path": "security.py",
         "content": "ROLE_ADMIN = 'admin'\nPOLICY_STRICT = {}\n\ndef require_permission(user):\n    return True\n"},
        {"path": "Dockerfile", "content": "FROM python:3.12-slim\nVOLUME /agent-data\n"},
        {"path": "constraints.py", "content": "MAX_CHARS = 400000\nTIMEOUT = 180\n"},
        {"path": "flow.py", "content": "def process_run(job):\n    return job\n"},
        {"path": "events.py", "content": "def handle():\n    emit('status', 'ok')\n"},
    ]
    st, exa = req("POST", "/api/extract", {"files": aspect_files, "graph": anchor_graph})
    extra_nodes = exa.get("nodes") or []
    atypes = {n["type"] for n in extra_nodes}
    check("Ф3.1 extraction covers all aspects",
          st == 200 and {"Role", "Policy", "Permission", "Node", "Runtime", "InfraResource",
                         "Constraint", "Process", "Event"} <= atypes, str(sorted(atypes)))
    merged = {"nodes": anchor_graph["nodes"] + extra_nodes, "edges": exa.get("edges") or []}
    st, v = req("POST", "/api/validate", {"graph": merged})
    check("Ф3.1 extracted aspect nodes are anchored validly",
          st == 200 and v["validation"]["ok"] is True,
          str((v.get("validation") or {}).get("errors")[:4]))

    # Ф3.1 - a cyclic `parent` chain must not hang extraction (regression):
    # POST /api/extract passes the client graph in unvalidated.
    cyclic = {"nodes": [
        {"id": "system://app", "type": "System", "parent": None, "evidence": []},
        {"id": "component://app/a", "type": "Component", "parent": "component://app/b",
         "evidence": ["pkg/mod.py"]},
        {"id": "component://app/b", "type": "Component", "parent": "component://app/a",
         "evidence": []},
    ], "edges": []}
    try:
        st, exc2 = req("POST", "/api/extract",
                       {"files": [{"path": "pkg/mod.py", "content": "ROLE_ADMIN = 'x'\n"}],
                        "graph": cyclic}, timeout=15)
        cyc_ok = st == 200
    except Exception:
        cyc_ok, st = False, 0
    check("Ф3.1 extraction tolerates a cyclic parent chain", cyc_ok, f"status={st} (hang/timeout)")

    # Ф3.1 - elements that cannot be anchored are reported, not silently dropped.
    st, exd = req("POST", "/api/extract",
                  {"files": [{"path": "pkg/mod.py", "content": "def task_x():\n    return 1\n"}],
                   "graph": anchor_graph})
    check("Ф3.1 extraction reports unanchored elements",
          st == 200 and any(d.get("type") == "Task" for d in (exd.get("dropped") or [])),
          str(exd.get("dropped"))[:200])

    # Ф3.1 - augment_graph must not delete pre-existing graph data.
    from backend.extract import augment_graph
    base_g = {"nodes": [
        {"id": "system://s", "type": "System", "parent": None},
        {"id": "component://s/a", "type": "Component", "parent": "system://s"},
        {"id": "component://s/b", "type": "Component", "parent": "system://s"},
    ], "edges": [{"source": "component://s/a", "target": "component://s/b", "type": "depends_on"},
                 {"source": "component://s/a", "target": "component://ghost", "type": "calls"}]}
    merged_g, _report = augment_graph(base_g, [])
    check("Ф3.1 augment preserves pre-existing edges",
          any(e.get("target") == "component://ghost" for e in merged_g["edges"]),
          f"edges={len(merged_g['edges'])}")

    # Ф3.1 - the extractor's hardcoded aspect tables must stay coupled to the
    # metamodel: every node type must exist and every attach edge must be an
    # edge type its aspect sanctions (otherwise the merged graph is rejected).
    from backend import extract as _extract
    from backend.metamodel import ASPECTS as _ASPECTS, NODE_TYPES as _NODE_TYPES
    type_aspect = {t: a for a, spec in _ASPECTS.items() for t in spec["node_types"]}
    bad_edge = [(t, e) for t, e in _extract._ASPECT_EDGE.items()
                if e not in _ASPECTS[type_aspect.get(t, "structure")]["edge_types"]]
    bad_type = [t for t in list(_extract._ASPECT_EDGE) + list(_extract._ID_PREFIX) + list(_extract._RANK)
                if t not in _NODE_TYPES]
    check("Ф3.1 extraction tables match the metamodel", not bad_edge and not bad_type,
          f"bad_edge={bad_edge} bad_type={bad_type}")

    # Ф3.1 - extraction runs on the collected sources, not the compressed
    # context: a tiny context budget must not silently drop aspect elements.
    tmp_repo = pathlib.Path(tempfile.mkdtemp(prefix="archide-extract-"))
    padding = "\n".join("# padding line %d" % i for i in range(300))
    (tmp_repo / "svc.py").write_text(padding + "\n\ndef handle():\n    emit('status', 'ok')\n")
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True,
                            "max_chars": 300, "compress_at": 0.05, "strategy": "truncate"}})
    try:
        snap_c, _ = run_and_wait(str(tmp_repo), "builtin")
        ctypes = {n["type"] for n in (snap_c.get("graph") or {}).get("nodes", [])}
        check("Ф3.1 extraction survives a tiny context budget", "Event" in ctypes, str(sorted(ctypes)))
    finally:
        shutil.rmtree(tmp_repo, ignore_errors=True)
    put_config({"context": {"max_chars": 20000, "compress_at": 0.8, "strategy": "extract"}})

    # Ф3.4 - the local runner must hand the agent the formal validator, so the
    # repair loop catches cross-aspect violations the weak local check misses.
    # A stub LLM returns a graph that is locally valid but formally invalid
    # (`reads` between two components): the agent must surface the formal error.
    stub_graph = {"nodes": [
        {"id": "system://s", "type": "System", "name": "s", "parent": None},
        {"id": "component://s/a", "type": "Component", "name": "a", "parent": "system://s"},
        {"id": "component://s/b", "type": "Component", "name": "b", "parent": "system://s"},
    ], "edges": [{"source": "component://s/a", "target": "component://s/b", "type": "reads"}]}

    class _StubLLM(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(stub_graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: D401 - silence the stub
            return

    stub_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubLLM)
    threading.Thread(target=stub_srv.serve_forever, daemon=True).start()
    put_config({"llm": {"base_url": f"http://127.0.0.1:{stub_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 30},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False}})
    try:
        snap_s, events_s = run_and_wait(REPO, "builtin", timeout=90)
        formal = [e.get("text", "") for e in events_s if e.get("type") == "error"
                  and "validation (" in (e.get("text") or "") and "reads" in (e.get("text") or "")]
        check("Ф3.4 local runner validates against the formal rules", bool(formal),
              str([e.get("text") for e in events_s if e.get("type") == "error"][:3])[:220])
    finally:
        stub_srv.shutdown()
        stub_srv.server_close()
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True}})

    # Ф3.5 - sequential, per-aspect reconstruction: a stub LLM answers the base
    # pass with a small structural graph and every aspect pass by echoing the
    # graph back with one new in-aspect node (plus, once, an out-of-aspect node
    # that must be rejected by the merge).
    import re
    from backend.metamodel import ANCHORS as _ANCHORS
    base_stub = {"nodes": [
        {"id": "system://stub", "type": "System", "name": "stub", "parent": None},
        {"id": "container://stub/svc", "type": "Container", "name": "svc", "parent": "system://stub"},
        {"id": "component://stub/core", "type": "Component", "name": "core", "parent": "container://stub/svc"},
    ], "edges": []}
    aspect_types = {"data": "DataEntity", "interface": "Interface", "behavior": "StateMachine",
                    "process": "Process", "deployment": "Node", "access": "Role",
                    "quality": "Constraint"}
    aspect_edges = {"data": "writes", "interface": "exposes", "behavior": "owns", "process": "owns",
                    "deployment": "anchored_to", "access": "owns", "quality": "anchored_to"}

    class _AspectStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            # a continuous session keeps the whole history: answer the LATEST
            # user message (older ones may still contain their own GRAPH block)
            content = str((messages[-1] or {}).get("content") or "") if messages else ""
            found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
            if not found:
                graph = json.loads(json.dumps(base_stub))
            else:
                graph = json.loads(found.group(1))
                aspect = (re.search(r"`(\w+)` aspect", content) or [None, ""])[1]
                node_type = aspect_types.get(aspect)
                if node_type:
                    existing = graph.get("nodes") or []
                    allowed = _ANCHORS.get(node_type) or set()
                    candidates = [n["id"] for n in existing if not allowed or n.get("type") in allowed]
                    parent = candidates[0] if candidates else None
                    new_id = f"{node_type.lower()}://stub/{aspect}-1"
                    if parent and not any(n.get("id") == new_id for n in existing):
                        graph["nodes"].append({"id": new_id, "type": node_type,
                                               "name": f"{aspect}-1", "parent": parent})
                        graph.setdefault("edges", []).append({
                            "source": parent, "target": new_id, "type": aspect_edges.get(aspect, "owns")})
                    if aspect == "quality":   # deliberately out of aspect -> must be dropped
                        graph["nodes"].append({"id": "actor://stub/intruder", "type": "Actor",
                                               "name": "intruder", "parent": "system://stub"})
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    aspect_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AspectStub)
    threading.Thread(target=aspect_srv.serve_forever, daemon=True).start()
    put_config({"llm": {"base_url": f"http://127.0.0.1:{aspect_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 30},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": True,
                            "aspect_list": ["data", "interface", "behavior", "quality"]}})
    try:
        snap_as, events_as = run_and_wait(REPO, "builtin", timeout=120)
        texts = [e.get("text", "") for e in events_as]
        passes = [t for t in texts if t.startswith("aspect `")]
        gtypes = {n["type"] for n in (snap_as.get("graph") or {}).get("nodes", [])}
        ids = {n["id"] for n in (snap_as.get("graph") or {}).get("nodes", [])}
        check("Ф3.5 aspects are reconstructed in sequential passes",
              len(passes) >= 4 and {"DataEntity", "Interface", "StateMachine", "Constraint"} <= gtypes,
              f"passes={passes[:4]} types={sorted(gtypes)}")
        check("Ф3.5 an out-of-aspect node is rejected by the merge",
              "actor://stub/intruder" not in ids and any("skipped" in t for t in texts),
              str([t for t in texts if "skipped" in t][:2]))
    finally:
        aspect_srv.shutdown()
        aspect_srv.server_close()
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True, "aspect_passes": True,
                            "aspect_list": ["data", "interface", "behavior", "process",
                                            "deployment", "access", "quality"]}})

    # Ф3.10 - one sandbox container serves the whole run: with aspect passes on,
    # the base pass and every aspect pass are requests in the SAME session, so
    # `docker run` happens once per run (the repo is uploaded once too).
    argv_path = pathlib.Path(STUB_STATE) / "argv.log"
    session_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AspectStub)
    threading.Thread(target=session_srv.serve_forever, daemon=True).start()
    if argv_path.exists():
        argv_path.unlink()
    put_config({"llm": {"base_url": f"http://127.0.0.1:{session_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                "agent": {"runner": "docker"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": True,
                            "aspect_list": ["data"]}})
    try:
        snap_dk, events_dk = run_and_wait(REPO, "builtin", timeout=180)
        argv = argv_path.read_text() if argv_path.exists() else ""
        runs = [line for line in argv.splitlines() if " run -d " in line]
        dk_texts = [e.get("text", "") for e in events_dk]
        dk_types = {n["type"] for n in (snap_dk.get("graph") or {}).get("nodes", [])}
        check("Ф3.10 a single container is started for the whole run",
              len(runs) == 1, f"docker run x{len(runs)}")
        check("Ф3.10 base and aspect passes share one agent session",
              any(t.startswith("aspect `data`") for t in dk_texts) and "DataEntity" in dk_types,
              f"types={sorted(dk_types)}")
    finally:
        session_srv.shutdown()
        session_srv.server_close()
    put_config({"agent": {"runner": "local"}, "llm": {"mode": "mock"},
                "context": {"compare_docs": False, "extract_aspects": True, "aspect_passes": True,
                            "aspect_list": ["data", "interface", "behavior", "process",
                                            "deployment", "access", "quality"]}})

    # Ф3.10 - local continuous session: the sources are sent once and every later
    # pass is a short message in the SAME conversation (no re-send of the repo).
    requests_log = pathlib.Path(STUB_STATE) / "llm-requests.jsonl"

    class _SessionStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            with requests_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(messages, ensure_ascii=False) + "\n")
            content = str((messages[-1] or {}).get("content") or "") if messages else ""
            found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
            if not found:
                graph = json.loads(json.dumps(base_stub))
            else:
                graph = json.loads(found.group(1))
                if not any(n.get("id") == "data://stub/session-1" for n in (graph.get("nodes") or [])):
                    graph.setdefault("nodes", []).append(
                        {"id": "data://stub/session-1", "type": "DataEntity",
                         "name": "session-1", "parent": "container://stub/svc"})
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    session_stub = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SessionStub)
    threading.Thread(target=session_stub.serve_forever, daemon=True).start()
    requests_log.unlink(missing_ok=True)
    put_config({"llm": {"base_url": f"http://127.0.0.1:{session_stub.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": True,
                            "aspect_list": ["data"]}})
    try:
        snap_sess, _ = run_and_wait(REPO, "builtin", timeout=90)
        calls = [json.loads(line) for line in requests_log.read_text().splitlines() if line.strip()]
        first_last = calls[0][-1]["content"] if calls and calls[0] else ""
        second = calls[1] if len(calls) > 1 else []
        second_last = second[-1]["content"] if second else ""
        gtypes = {n["type"] for n in (snap_sess.get("graph") or {}).get("nodes", [])}
        check("Ф3.10 the local session sends the sources once",
              len(calls) >= 2 and len(second_last) < len(first_last) * 0.9,
              f"calls={len(calls)} first={len(first_last)} second={len(second_last)}")
        check("Ф3.10 later passes continue the same conversation",
              len(second) >= 2 and "`data` aspect" in second_last and "DataEntity" in gtypes,
              f"second_messages={len(second)} types={sorted(gtypes)}")
    finally:
        session_stub.shutdown()
        session_stub.server_close()
    put_config({"llm": {"mode": "mock"}})

    # Ф3.6 - context per aspect: only the relevant sources and the relevant part
    # of the graph are sent to each aspect pass.
    req_log = pathlib.Path(STUB_STATE) / "aspect-ctx.jsonl"

    class _CtxStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            with req_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(messages, ensure_ascii=False) + "\n")
            content = str((messages[-1] or {}).get("content") or "") if messages else ""
            found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
            if not found:
                graph = json.loads(json.dumps(base_stub))
            else:
                graph = json.loads(found.group(1))
                aspect = (re.search(r"`(\w+)` aspect", content) or [None, ""])[1]
                node_type = aspect_types.get(aspect)
                if node_type:
                    existing = graph.get("nodes") or []
                    allowed = _ANCHORS.get(node_type) or set()
                    parents = [n["id"] for n in existing if not allowed or n.get("type") in allowed]
                    node_id = f"{node_type.lower()}://stub/{aspect}-1"
                    if parents and not any(n.get("id") == node_id for n in existing):
                        graph["nodes"].append({"id": node_id, "type": node_type,
                                               "name": f"{aspect}-1", "parent": parents[0]})
                        graph.setdefault("edges", []).append({
                            "source": parents[0], "target": node_id,
                            "type": aspect_edges.get(aspect, "owns")})
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    ctx_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CtxStub)
    threading.Thread(target=ctx_srv.serve_forever, daemon=True).start()
    ctx_url = f"http://127.0.0.1:{ctx_srv.server_address[1]}/v1"

    def _ctx_run(repo, aspect_list, runner_mode):
        req_log.unlink(missing_ok=True)
        put_config({"llm": {"base_url": ctx_url, "api_key": "", "model": "stub", "mode": "live",
                            "timeout": 60},
                    "agent": {"runner": runner_mode},
                    "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": True,
                                "aspect_list": aspect_list, "max_chars": 20000,
                                "reconcile_fix": False, "reconcile_report": False}})
        snap, _ = run_and_wait(repo, "builtin", timeout=120)
        calls = [json.loads(line) for line in req_log.read_text().splitlines() if line.strip()]
        return snap, calls

    try:
        ctx_repo = pathlib.Path(tempfile.mkdtemp(prefix="archide-ctx-"))
        (ctx_repo / "states.py").write_text(
            "class OrderState(enum.Enum):\n    IDLE = 'idle'\n\ndef emit(status):\n    return status\n")
        (ctx_repo / "other.py").write_text(
            "ZZZ_IRRELEVANT_MARKER = 1\n\ndef plain():\n    return 1\n")
        _, calls = _ctx_run(str(ctx_repo), ["behavior"], "local")
        last = calls[-1][-1]["content"] if calls and calls[-1] else ""
        check("Ф3.6 aspect pass gets only the relevant sources",
              "### FILE: states.py" in last and "ZZZ_IRRELEVANT_MARKER" not in last,
              f"calls={len(calls)} last_tail={last[-120:]!r}")
        shutil.rmtree(ctx_repo, ignore_errors=True)

        _, calls = _ctx_run(REPO, ["interface", "data"], "local")
        last = calls[-1][-1]["content"] if calls and calls[-1] else ""
        check("Ф3.6 aspect graph summary keeps anchors and own elements only",
              "container://stub/svc" in last and "interface://stub/interface-1" not in last,
              f"calls={len(calls)}")

        _, calls = _ctx_run(REPO, ["data"], "docker")
        first_last = calls[0][-1]["content"] if calls else ""
        second_last = calls[1][-1]["content"] if len(calls) > 1 else ""
        check("Ф3.6 the docker session does not re-send the sources",
              len(calls) >= 2 and len(second_last) < len(first_last) * 0.9,
              f"calls={len(calls)} first={len(first_last)} second={len(second_last)}")
    finally:
        ctx_srv.shutdown()
        ctx_srv.server_close()
    put_config({"agent": {"runner": "local"}, "llm": {"mode": "mock"}})

    # Ф3.7 - robustness: resume from a saved model, skip aspects the graph
    # already carries, and cap how much one pass may add.
    robust_cfg = {"nodes_per_aspect": 1}

    class _RobustStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            content = str((messages[-1] or {}).get("content") or "") if messages else ""
            found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
            if not found:
                graph = json.loads(json.dumps(base_stub))
            else:
                graph = json.loads(found.group(1))
                aspect = (re.search(r"`(\w+)` aspect", content) or [None, ""])[1]
                node_type = aspect_types.get(aspect)
                if node_type:
                    allowed = _ANCHORS.get(node_type) or set()
                    parents = [n["id"] for n in (graph.get("nodes") or [])
                               if not allowed or n.get("type") in allowed]
                    for i in range(robust_cfg["nodes_per_aspect"]):
                        node_id = f"{node_type.lower()}://stub/{aspect}-{i + 1}"
                        if not parents or any(n.get("id") == node_id for n in graph.get("nodes") or []):
                            continue
                        graph["nodes"].append({"id": node_id, "type": node_type,
                                               "name": f"{aspect}-{i + 1}", "parent": parents[0]})
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    robust_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RobustStub)
    threading.Thread(target=robust_srv.serve_forever, daemon=True).start()
    put_config({"llm": {"base_url": f"http://127.0.0.1:{robust_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": True,
                            "aspect_list": ["data", "interface"], "aspect_skip_existing": True,
                            "aspect_max_nodes": 0}})
    try:
        snap_r1, events_r1 = run_and_wait(REPO, "builtin", timeout=90)
        types_r1 = {n["type"] for n in (snap_r1.get("graph") or {}).get("nodes", [])}
        model_name = "sample_repo"
        snap_r2, events_r2 = run_and_wait(REPO, "builtin", timeout=90,
                                          options={"model": model_name, "aspect_list": ["data", "behavior"]})
        text_r2 = [e.get("text", "") for e in events_r2]
        nodes_r2 = {n["id"] for n in (snap_r2.get("graph") or {}).get("nodes", [])}
        types_r2 = {n["type"] for n in (snap_r2.get("graph") or {}).get("nodes", [])}
        check("Ф3.7 a run resumes from a saved model",
              any("resuming from the saved model" in t for t in text_r2)
              and "DataEntity" in types_r1 and "DataEntity" in types_r2 and "StateMachine" in types_r2,
              f"first={sorted(types_r1)} resumed={sorted(types_r2)}")
        check("Ф3.7 an aspect the graph already carries is skipped",
              any("already covered" in t and "data" in t for t in text_r2),
              str([t for t in text_r2 if "already covered" in t][:2]))
        robust_cfg["nodes_per_aspect"] = 4
        put_config({"context": {"aspect_skip_existing": True, "aspect_max_nodes": 2,
                                "aspect_list": ["quality"]}})
        snap_r3, events_r3 = run_and_wait(REPO, "builtin", timeout=90,
                                          options={"model": model_name, "aspect_list": ["quality"]})
        text_r3 = [e.get("text", "") for e in events_r3]
        quality_nodes = [n for n in (snap_r3.get("graph") or {}).get("nodes", [])
                         if n["type"] in ("Constraint", "NFR", "SLO")]
        check("Ф3.7 an oversized aspect addition is capped",
              any("growth cap" in t for t in text_r3) and len(quality_nodes) == 2,
              f"quality={len(quality_nodes)} events={[t for t in text_r3 if 'growth cap' in t][:1]}")
    finally:
        robust_srv.shutdown()
        robust_srv.server_close()
    robust_cfg["nodes_per_aspect"] = 1
    put_config({"context": {"aspect_skip_existing": True, "aspect_max_nodes": 80,
                            "aspect_list": ["data", "interface", "behavior", "process",
                                            "deployment", "access", "quality"]}})

    # Ф3.8 - per-aspect quality metrics in the evaluation harness
    eval_graph = {"nodes": [
        {"id": "system://ev", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "sm://ev/flow", "type": "StateMachine", "parent": "system://ev", "evidence": []},
        {"id": "state://ev/idle", "type": "State", "parent": "sm://ev/flow", "evidence": ["a"]},
        {"id": "process://ev/p", "type": "Process", "parent": "system://ev", "evidence": ["a"]},
        {"id": "activity://ev/s1", "type": "Activity", "parent": "process://ev/p", "evidence": ["a"]},
    ], "edges": [{"source": "process://ev/p", "target": "activity://ev/s1", "type": "owns"}]}
    st, ev = req("POST", "/api/eval", {"graph": eval_graph})
    ev_metrics = (ev.get("metrics") or {})
    ev_aspects = ev_metrics.get("aspects") or {}
    behavior = ev_aspects.get("behavior") or {}
    process = ev_aspects.get("process") or {}
    check("Ф3.8 per-aspect metrics are reported",
          st == 200 and behavior.get("elements") == 2 and behavior.get("orphans") == 2
          and behavior.get("without_evidence") == 1 and process.get("elements") == 2,
          f"behavior={behavior} process={process}")
    check("Ф3.8 aspect coverage summary is reported",
          "behavior" in (ev_metrics.get("aspect_summary") or {}).get("covered", [])
          and "deployment" in (ev_metrics.get("aspect_summary") or {}).get("empty", []),
          str(ev_metrics.get("aspect_summary"))[:160])

    # Ф3.9 - reconciliation report after a run (orphans / missing evidence)
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True,
                            "reconcile_report": True, "aspect_passes": False}})
    snap_rec, events_rec = run_and_wait(REPO, "builtin", timeout=90)
    rec_texts = [e.get("text", "") for e in events_rec]
    check("Ф3.9 a reconciliation report is emitted for each run",
          any(t.startswith("reconciliation:") for t in rec_texts),
          str([t for t in rec_texts if t.startswith("reconciliation:")][:1])[:200])

    # Ф3.9 (correction) - the agent fills in evidence/relationships; the merge is
    # safe: elements can be enriched but never removed or added.
    reconcile_cfg = {"drop_node": None}

    class _ReconcileStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            content = str((messages[-1] or {}).get("content") or "") if messages else ""
            if "## Reconciliation" in content:
                found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
                graph = json.loads(found.group(1)) if found else json.loads(json.dumps(base_stub))
                graph.setdefault("nodes", [])
                graph.setdefault("edges", [])
                if reconcile_cfg["drop_node"]:
                    graph["nodes"] = [n for n in graph["nodes"] if n.get("id") != reconcile_cfg["drop_node"]]
                for node in graph["nodes"]:
                    if not node.get("evidence"):
                        node["evidence"] = ["reconciled.py"]
                        break
                ids = [n["id"] for n in graph["nodes"]]
                if len(ids) >= 2:
                    graph["edges"].append({"source": ids[0], "target": ids[1], "type": "depends_on"})
                graph["nodes"].append({"id": "component://stub/extra", "type": "Component",
                                       "name": "extra", "parent": ids[0] if ids else "system://stub"})
            else:
                graph = json.loads(json.dumps(base_stub))
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    recon_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ReconcileStub)
    threading.Thread(target=recon_srv.serve_forever, daemon=True).start()
    put_config({"llm": {"base_url": f"http://127.0.0.1:{recon_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": False,
                            "reconcile_fix": True, "reconcile_report": True}})
    try:
        snap_fx, events_fx = run_and_wait(REPO, "builtin", timeout=90)
        fx_graph = snap_fx.get("graph") or {}
        fx_nodes = {n["id"]: n for n in fx_graph.get("nodes") or []}
        fx_text = [e.get("text", "") for e in events_fx]
        check("Ф3.9 the agent fills in missing evidence and relationships",
              any("reconciliation fix: +" in t for t in fx_text)
              and any(n.get("evidence") == ["reconciled.py"] for n in fx_nodes.values())
              and any(e.get("type") == "depends_on" for e in fx_graph.get("edges") or []),
              str([t for t in fx_text if "reconciliation fix" in t][:2])[:200])
        reconcile_cfg["drop_node"] = "component://stub/core"
        snap_fx2, events_fx2 = run_and_wait(REPO, "builtin", timeout=90)
        ids2 = {n["id"] for n in (snap_fx2.get("graph") or {}).get("nodes", [])}
        fx_text2 = [e.get("text", "") for e in events_fx2]
        check("Ф3.9 a correction cannot remove or add elements",
              "component://stub/core" in ids2 and "component://stub/extra" not in ids2
              and any("ignored" in t for t in fx_text2),
              f"ids={sorted(ids2)}")
    finally:
        recon_srv.shutdown()
        recon_srv.server_close()
    reconcile_cfg["drop_node"] = None
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": True}})

    # Ф2.14 - the aspect prompt asks for the fields the notations need
    from backend.prompt import build_aspect_prompt as _aspect_prompt
    behavior_prompt = _aspect_prompt("r", "behavior", ["StateMachine"], ["owns"],
                                    {"StateMachine": ["Component"]}, '{"nodes": [], "edges": []}')
    process_prompt = _aspect_prompt("r", "process", ["Process", "Task"], ["owns"],
                                    {"Process": ["Container"]}, '{"nodes": [], "edges": []}')
    data_prompt = _aspect_prompt("r", "data", ["DataEntity"], ["flows_to"],
                                {"DataEntity": ["Container"]}, '{"nodes": [], "edges": []}')
    # Ф8.3b - precise acceptance: evidence-based matching, scoped verdict,
    # requirement coverage
    target_b = {"nodes": [
        {"id": "system://acc", "type": "System", "name": "acc", "parent": None},
        {"id": "component://acc/api", "type": "Component", "name": "API", "parent": "system://acc",
         "evidence": ["docs/api.md"]},
        {"id": "component://acc/other", "type": "Component", "name": "Other", "parent": "system://acc",
         "evidence": ["docs/other.md"]},
        {"id": "requirement://acc/r1", "type": "Requirement", "name": "R1", "parent": "system://acc"},
    ], "edges": [{"source": "component://acc/api", "target": "requirement://acc/r1", "type": "satisfies"}]}
    result_b = {"nodes": [
        {"id": "component://impl/api-impl", "type": "Component", "name": "ApiImpl",
         "evidence": ["src/api.py", "docs/api.md"]},
        {"id": "system://impl", "type": "System", "name": "acc", "parent": None},
    ], "edges": []}
    st_d2, d2 = req("POST", "/api/graphdiff", {"target": target_b, "result": result_b})
    check("Ф8.3b the diff matches by evidence and reports how",
          st_d2 == 200 and d2["matched_by"]["evidence"] >= 1, str(d2.get("matched_by")))

    st_d3, d3 = req("POST", "/api/graphdiff",
                    {"target": target_b, "result": result_b, "scope": "component://acc/api"})
    check("Ф8.3b out-of-scope elements do not block acceptance",
          st_d3 == 200 and d3["summary"]["missing"] >= 1
          and d3["summary"]["missing_in_scope"] == 0 and d3["verdict"] == "ok",
          f"missing={d3['summary']['missing']} in_scope={d3['summary']['missing_in_scope']} "
          f"verdict={d3['verdict']}")

    result_noreq = {"nodes": [{"id": "system://impl", "type": "System", "name": "acc", "parent": None}],
                    "edges": []}
    st_d4, d4 = req("POST", "/api/graphdiff", {"target": target_b, "result": result_noreq})
    check("Ф8.3b the verdict reports requirement coverage",
          st_d4 == 200 and d4["requirements"]["total"] == 1 and d4["requirements"]["covered"] == 0
          and d4["requirements"]["uncovered"] == ["requirement://acc/r1"] and d4["verdict"] == "gaps",
          str(d4.get("requirements")))

    # Ф10.1 - a project owns the model, its runs and the agent chat
    st_pj, pj = req("POST", "/api/projects", {"name": "proj-one", "repo": "https://example.invalid/repo.git"})
    project_id = (pj.get("project") or {}).get("id")
    check("Ф10.1 a project can be created", st_pj == 200 and project_id == "proj-one", str(pj)[:160])

    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False}})
    run_and_wait(REPO, "builtin", timeout=90, options={"project": project_id})
    _, opened = req("POST", f"/api/projects/{project_id}/open")
    check("Ф10.1 the run's model is attached to the project",
          opened.get("model") == "sample_repo" and (opened.get("graph") or {}).get("nodes"),
          f"model={opened.get('model')}")

    req("POST", f"/api/projects/{project_id}/chat", {"role": "user", "text": "привет"})
    req("POST", f"/api/projects/{project_id}/chat", {"role": "agent", "text": "готов"})
    _, again = req("POST", f"/api/projects/{project_id}/open")
    chat = again.get("chat") or []
    check("Ф10.1 the project keeps the agent chat",
          len(chat) == 2 and chat[0].get("text") == "привет" and chat[1].get("role") == "agent",
          str(chat)[:160])

    st_list, listing = req("GET", "/api/projects")
    summaries = listing.get("projects") or []
    check("Ф10.1 existing models are migrated into projects",
          st_list == 200 and any(p.get("id") == project_id for p in summaries)
          and any(p.get("model") == "sample_repo" for p in summaries)
          and all(p.get("id") for p in summaries),
          str(sorted(p.get("id") for p in summaries))[:200])

    req("PUT", "/api/models/proj-b", {"graph": {"nodes": [
        {"id": "system://pb", "type": "System", "name": "pb", "parent": None, "evidence": ["a"]}], "edges": []}})
    _, opened_b = req("POST", "/api/projects/proj-b/open")
    nodes_b = (opened_b.get("graph") or {}).get("nodes") or []
    check("Ф10.1 switching projects returns their own graphs",
          nodes_b and nodes_b[0].get("id") == "system://pb", str(nodes_b)[:140])

    # Ф8.1 - one handoff archive: work package + artifacts + markers + manifest
    seed_ho = {"nodes": [
        {"id": "system://ho", "type": "System", "name": "ho", "parent": None, "evidence": ["a"]},
        {"id": "container://ho/c", "type": "Container", "name": "ctr", "parent": "system://ho", "evidence": ["a"]},
        {"id": "component://ho/api", "type": "Component", "name": "API", "parent": "container://ho/c", "evidence": ["a"]},
        {"id": "iface://ho/api", "type": "Interface", "name": "API", "parent": "component://ho/api", "evidence": ["a"]},
        {"id": "operation://ho/api/run", "type": "Operation", "name": "run", "parent": "iface://ho/api", "evidence": ["a"]},
        {"id": "data://ho/Order", "type": "DataEntity", "name": "Order", "parent": "container://ho/c", "evidence": ["a"]},
        {"id": "attribute://ho/Order/id", "type": "Attribute", "name": "id", "parent": "data://ho/Order", "evidence": ["a"]},
    ], "edges": [{"source": "component://ho/api", "target": "iface://ho/api", "type": "exposes"}]}
    req("PUT", "/api/models/ho", {"graph": seed_ho})

    def _handoff():
        request = urllib.request.Request(BASE + "/api/models/ho/handoff", data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        with _opener.open(request, timeout=60) as resp:
            return resp.status, resp.headers.get("Content-Disposition", ""), resp.read()

    st_ho, disp, blob = _handoff()
    import io as _io
    import tarfile as _tarfile
    members = {}
    with _tarfile.open(fileobj=_io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            members[member.name] = tar.extractfile(member).read().decode("utf-8", "ignore") if member.isfile() else ""
    manifest = json.loads(members.get("manifest.json") or "{}")
    package = json.loads(members.get("workpackage.json") or "{}")
    trace = json.loads(members.get("markers.json") or "{}")
    check("Ф8.1 the handoff archive carries the package, artifacts, markers and instructions",
          st_ho == 200 and "handoff.tar.gz" in disp
          and {"manifest.json", "workpackage.json", "markers.json", "README.md"} <= set(members)
          and any(name.startswith("artifacts/") for name in members)
          and manifest.get("model") == "ho" and manifest.get("hash")
          and len(package.get("criteria") or []) >= 1 and (trace.get("markers") or [])
          and manifest.get("markers_verified") is True,
          f"status={st_ho} members={sorted(members)[:8]} manifest={manifest}")
    # Ф5.3 (regression) - the marker key must be stable, otherwise markers signed
    # in one call cannot be verified later
    from backend import config as _cfg_mod, generate as _gen_mod, markers as _markers_mod
    ho_graph = {"nodes": [
        {"id": "system://mk", "type": "System", "name": "mk", "parent": None, "evidence": ["a"]},
        {"id": "container://mk/c", "type": "Container", "name": "ctr", "parent": "system://mk", "evidence": ["a"]},
    ], "edges": []}
    cfg_a = _cfg_mod.load_config()
    marker = _gen_mod.trace_markers(cfg_a, ho_graph, "system://mk")["markers"][0]
    cfg_b = _cfg_mod.load_config()
    check("Ф5.3 marker keys are stable across calls",
          _markers_mod.verify_trace(cfg_a, marker) and _markers_mod.verify_trace(cfg_b, marker),
          f"same_cfg={_markers_mod.verify_trace(cfg_a, marker)} reloaded={_markers_mod.verify_trace(cfg_b, marker)}")

    st_ho2, _disp2, blob2 = _handoff()
    check("Ф8.1 the handoff archive is deterministic", st_ho2 == 200 and blob2 == blob,
          f"same={blob2 == blob} bytes={len(blob)}")

    # Ф3.11 - a long session must trim its context instead of overflowing the
    # model window (the "ran out of context space" failure seen in real runs)
    ctx_log = pathlib.Path(STUB_STATE) / "ctx-size.txt"
    ctx_cfg = {"limit": 6000}

    class _CtxBudgetStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            messages = body.get("messages") or []
            size = sum(len(str((m or {}).get("content") or "")) for m in messages)
            with ctx_log.open("a", encoding="utf-8") as fh:
                fh.write(f"{size}\n")
            if size > ctx_cfg["limit"]:
                payload = json.dumps({"error": {"message":
                    "The model ran out of context space while processing the request"}}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            content = str((messages[-1] or {}).get("content") or "")
            found = re.search(r"<<<GRAPH\n(.*?)\nGRAPH>>>", content, re.S)
            graph = json.loads(found.group(1)) if found else json.loads(json.dumps(base_stub))
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(graph)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    ctx_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CtxBudgetStub)
    threading.Thread(target=ctx_srv.serve_forever, daemon=True).start()
    big_repo = pathlib.Path(tempfile.mkdtemp(prefix="archide-bigctx-"))
    (big_repo / "big.py").write_text("\n".join(
        f"def handler_{i}(payload):  # padding line so the base prompt is large" for i in range(150)) + "\n")
    ctx_log.unlink(missing_ok=True)
    put_config({"llm": {"base_url": f"http://127.0.0.1:{ctx_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60,
                        "max_context_chars": 5000},
                "agent": {"runner": "local"},
                "context": {"compare_docs": False, "extract_aspects": False, "aspect_passes": False,
                            "reconcile_fix": False, "reconcile_report": False}})
    try:
        snap_ctx, events_ctx = run_and_wait(str(big_repo), "builtin", timeout=90)
        sizes = [int(x) for x in ctx_log.read_text().split()] if ctx_log.exists() else []
        ctx_texts = [e.get("text", "") for e in events_ctx]
        check("Ф3.11 a long session trims its context to fit the budget",
              snap_ctx.get("status") == "done" and sizes and max(sizes) <= 5000
              and any("context trimmed" in t for t in ctx_texts),
              f"status={snap_ctx.get('status')} max={max(sizes) if sizes else None} "
              f"events={[t for t in ctx_texts if 'context' in t][:1]}")
    finally:
        ctx_srv.shutdown()
        ctx_srv.server_close()
        shutil.rmtree(big_repo, ignore_errors=True)
    put_config({"llm": {"mode": "mock", "max_context_chars": 60000}})

    # Ф8.2/Ф8.3 - code generation handoff + graph diff (acceptance loop)
    codegen_log = pathlib.Path(STUB_STATE) / "codegen.jsonl"
    codegen_cfg = {"unsafe": False}

    class _CodegenStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            content = str(((body.get("messages") or [{}])[-1] or {}).get("content") or "")
            with codegen_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"prompt": content[:400]}, ensure_ascii=False) + "\n")
            files = {"README.md": "# generated\n", "src/app.py": "print('ok')\n"}
            if codegen_cfg["unsafe"]:
                files["../evil.py"] = "boom"
                files["/abs.py"] = "boom"
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps({"files": files})}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    cg_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CodegenStub)
    threading.Thread(target=cg_srv.serve_forever, daemon=True).start()
    codegen_log.unlink(missing_ok=True)
    put_config({"llm": {"base_url": f"http://127.0.0.1:{cg_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60}})
    seed = {"nodes": [
        {"id": "system://cg", "type": "System", "name": "cg", "parent": None, "evidence": ["a"]},
        {"id": "container://cg/c", "type": "Container", "name": "ctr", "parent": "system://cg", "evidence": ["a"]},
        {"id": "component://cg/core", "type": "Component", "name": "Core", "parent": "container://cg/c", "evidence": ["a"]},
        {"id": "iface://cg/api", "type": "Interface", "name": "API", "parent": "component://cg/core", "evidence": ["a"]},
    ], "edges": [{"source": "component://cg/core", "target": "iface://cg/api", "type": "exposes"}]}
    req("PUT", "/api/models/cg", {"graph": seed})
    try:
        st_cg, gen = req("POST", "/api/models/cg/codegen", {"node": "component://cg/core", "target": "cgrepo"},
                         timeout=90)
        prompt_text = codegen_log.read_text() if codegen_log.exists() else ""
        repo = pathlib.Path(str(gen.get("repo") or ""))
        check("Ф8.2 the code-generation agent materialises a repository",
              st_cg == 200 and gen.get("count") == 2 and (repo / "README.md").is_file()
              and (repo / "src" / "app.py").is_file() and "Work package" in prompt_text,
              f"status={st_cg} count={gen.get('count')} repo={repo}")
        codegen_cfg["unsafe"] = True
        st_cg2, gen2 = req("POST", "/api/models/cg/codegen", {"node": "component://cg/core", "target": "cgunsafe"},
                           timeout=90)
        skipped_paths = [s.get("path") for s in (gen2.get("skipped") or []) if isinstance(s, dict)]
        check("Ф8.2 unsafe paths from the agent are refused",
              st_cg2 == 200 and "../evil.py" in skipped_paths and "/abs.py" in skipped_paths
              and not (pathlib.Path(str(gen2.get("repo"))) / "evil.py").exists(),
              f"skipped={gen2.get('skipped')}")
        # Ф8.2b - an unparseable answer is retried, oversized files are skipped
        codegen_calls = {"n": 0}

        class _FlakyStub(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                codegen_calls["n"] += 1
                if codegen_calls["n"] == 1:
                    content = "Sure! Here is the project you asked for. (no JSON at all)"
                else:
                    content = json.dumps({"files": {
                        "README.md": "# ok\n",
                        "src/big.py": "# " + "x" * 400 + "\n",
                    }})
                payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # noqa: D401
                return

        flaky_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FlakyStub)
        threading.Thread(target=flaky_srv.serve_forever, daemon=True).start()
        put_config({"llm": {"base_url": f"http://127.0.0.1:{flaky_srv.server_address[1]}/v1",
                            "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                    "codegen": {"max_file_chars": 100, "max_retries": 2}})
        try:
            st_rb, gen_rb = req("POST", "/api/models/cg/codegen",
                                {"node": "component://cg/core", "target": "cgretry"}, timeout=90)
            rb_files = gen_rb.get("files") or []
            rb_skipped = [s.get("path") for s in (gen_rb.get("skipped") or []) if isinstance(s, dict)]
            check("Ф8.2b an unparseable answer is retried and the project is delivered",
                  st_rb == 200 and gen_rb.get("retries") == 1 and "README.md" in rb_files,
                  f"retries={gen_rb.get('retries')} files={rb_files}")
            check("Ф8.2b an oversized file is skipped with a reason",
                  "src/big.py" in rb_skipped
                  and any("too large" in (s.get("reason") or "")
                          for s in (gen_rb.get("skipped") or []) if isinstance(s, dict)),
                  str(gen_rb.get("skipped"))[:160])
        finally:
            flaky_srv.shutdown()
            flaky_srv.server_close()
        put_config({"codegen": {"max_file_chars": 60000, "max_retries": 2}})

        st_diff, dd = req("POST", "/api/graphdiff",
                          {"target": seed, "result": {"nodes": [
                              {"id": "component://x", "type": "Component", "name": "Core"},
                              {"id": "component://x/new", "type": "Component", "name": "Extra"}], "edges": []}})
        check("Ф8.3 the graph diff scores the delivered repository",
              st_diff == 200 and dd["summary"]["matched"] == 1 and dd["summary"]["missing"] == 3
              and dd["summary"]["extra"] == 1 and dd["verdict"] == "gaps"
              and dd["by_aspect"].get("structure", {}).get("extra") == 1,
              str(dd.get("summary")))
    finally:
        cg_srv.shutdown()
        cg_srv.server_close()
    codegen_cfg["unsafe"] = False
    put_config({"llm": {"mode": "mock"}})

    # --- review fixes -----------------------------------------------------
    from backend import model_store as ms  # noqa: E402

    # (1) saving a model must not delete a differently-named model
    tdir = pathlib.Path(tempfile.mkdtemp(prefix="archide-store-"))
    tiny = {"nodes": [{"id": "system://a", "type": "System", "name": "a", "parent": None, "evidence": ["x"]}],
            "edges": []}
    ms.save_model(json.loads(json.dumps(tiny)), tdir, "X.git")
    ms.save_model(json.loads(json.dumps(tiny)), tdir, "X")
    check("fix(1) saving a model keeps a `.git`-named sibling",
          (tdir / "X.git.json").is_file() and (tdir / "X.json").is_file(),
          str(sorted(q.name for q in tdir.iterdir())))

    ms.save_model(json.loads(json.dumps(tiny)), tdir, "legacy.git")
    moved = ms.migrate_legacy_names(tdir)
    check("fix(1) the legacy migration renames without deleting",
          "legacy" in moved and (tdir / "legacy.json").is_file() and not (tdir / "legacy.git.json").exists(),
          f"{moved} {sorted(q.name for q in tdir.iterdir())}")
    shutil.rmtree(tdir, ignore_errors=True)

    # (2) a scoped acceptance verdict ignores out-of-scope differences
    tgt = {"nodes": [
        {"id": "system://t", "type": "System", "name": "T", "parent": None, "evidence": ["t"]},
        {"id": "component://t/in", "type": "Component", "name": "In", "parent": "system://t", "evidence": ["t"]},
        {"id": "component://t/out", "type": "Component", "name": "Out", "parent": "system://t", "evidence": ["t"]},
    ], "edges": []}
    res = {"nodes": [
        {"id": "system://t", "type": "System", "name": "T", "parent": None, "evidence": ["t"]},
        {"id": "component://t/in", "type": "Component", "name": "In", "parent": "system://t", "evidence": ["t"]},
        {"id": "module://t/out", "type": "Module", "name": "Out", "parent": "system://t", "evidence": ["t"]},
    ], "edges": []}
    st_s, d_s = req("POST", "/api/graphdiff", {"target": tgt, "result": res, "scope": "component://t/in"})
    check("fix(2) an out-of-scope change does not fail a scoped verdict",
          st_s == 200 and d_s.get("verdict") == "ok" and d_s["summary"]["changed"] == 0,
          json.dumps(d_s.get("summary")))
    st_u, d_u = req("POST", "/api/graphdiff", {"target": tgt, "result": res})
    check("fix(2) without a scope the same change still fails the verdict",
          st_u == 200 and d_u.get("verdict") == "gaps" and d_u["summary"]["changed"] == 1,
          json.dumps(d_u.get("summary")))

    # (5) a scaffolded model keeps the case of its name
    st_c, sc_c = req("POST", "/api/models/TplCase/scaffold", {"template": "cli"})
    ids_c = [n["id"] for n in (sc_c.get("model") or {}).get("nodes") or []]
    check("fix(5) a scaffolded model keeps the case of its name",
          st_c == 200 and any("://TplCase" in i for i in ids_c), str(ids_c[:3]))

    # --- Ф6.2: feedback (explicit/implicit) attributed to what it is about -----
    st_f1, f1 = req("POST", "/api/feedback",
                    {"user": "arch", "kind": "explicit", "signal": "up", "comment": "good synthesis",
                     "about": {"turn": 123.0, "run": "r-1", "model": "TripApproval", "view": "c4"}})
    check("Ф6.2 explicit feedback is recorded", st_f1 == 200 and f1.get("feedback", {}).get("signal") == "up",
          f"{st_f1} {json.dumps(f1)[:150]}")

    req("POST", "/api/feedback", {"user": "arch", "kind": "implicit", "signal": "accepted",
                                  "about": {"run": "r-1", "applied": 3, "model": "TripApproval"}})
    req("POST", "/api/feedback", {"user": "arch", "kind": "implicit", "signal": "rejected",
                                  "about": {"run": "r-2", "applied": 0, "model": "TripApproval"}})
    req("POST", "/api/feedback", {"user": "other", "kind": "explicit", "signal": "down",
                                  "about": {"run": "r-9"}})

    st_fa, fb = req("GET", "/api/feedback?user=arch&limit=50")
    items = fb.get("feedback") or []
    check("Ф6.2 feedback is stored and read back",
          st_fa == 200 and len(items) == 3 and items[0]["signal"] == "up", f"{st_fa} {len(items)}")

    check("Ф6.2 feedback is attributed to what it is about",
          items[0]["about"].get("run") == "r-1" and items[0]["about"].get("model") == "TripApproval"
          and items[1]["about"].get("applied") == 3,
          json.dumps([i.get("about") for i in items])[:220])

    st_fo, other_fb = req("GET", "/api/feedback?user=other")
    check("Ф6.2 feedback is isolated per architect",
          len(other_fb.get("feedback") or []) == 1, json.dumps(other_fb)[:140])

    st_fs, fsum = req("GET", "/api/feedback/summary?user=arch")
    agg = fsum.get("summary") or {}
    check("Ф6.2 feedback is summarised for later reflection",
          st_fs == 200 and agg.get("total") == 3 and (agg.get("by_signal") or {}).get("up") == 1
          and (agg.get("by_kind") or {}).get("implicit") == 2
          and (agg.get("by_model") or {}).get("TripApproval") == 3,
          json.dumps(agg)[:220])

    st_fb, bad = req("POST", "/api/feedback", {"user": "arch", "kind": "explicit", "signal": "up"})
    check("Ф6.2 feedback without attribution is rejected",
          st_fb == 400 and "about" in str(bad.get("detail") or ""), f"{st_fb} {bad}")

    st_fc, bad2 = req("POST", "/api/feedback", {"user": "arch", "kind": "explicit", "signal": "meh",
                                                "about": {"run": "r-1"}})
    check("Ф6.2 an unknown signal is rejected", st_fc == 400, f"{st_fc} {bad2}")

    # --- Ф6.1: the dialogue store (per architect, per project, with provenance) --
    req("POST", "/api/projects", {"name": "trip", "repo": "design://trip"})   # the mirror target
    st_d1, t1 = req("POST", "/api/dialogue",
                    {"user": "arch", "role": "user", "text": "design a trip approval service",
                     "project": "trip", "provenance": {"view": "c4", "model": "", "kind": "design"}})
    check("Ф6.1 a dialogue turn is recorded", st_d1 == 200 and t1.get("turn", {}).get("text"),
          f"{st_d1} {json.dumps(t1)[:150]}")

    req("POST", "/api/dialogue", {"user": "arch", "role": "agent", "text": "synthesised TripApproval",
                                  "project": "trip", "provenance": {"run": "r-1", "model": "TripApproval",
                                                                    "applied": 3}})
    req("POST", "/api/dialogue", {"user": "arch", "role": "user", "text": "add a cache component",
                                  "project": "other"})
    req("POST", "/api/dialogue", {"user": "someone", "role": "user", "text": "private note"})

    st_h, hist = req("GET", "/api/dialogue?user=arch&limit=50")
    turns = hist.get("turns") or []
    check("Ф6.1 dialogues are stored and read back in order",
          st_h == 200 and len(turns) == 3 and turns[0]["text"].startswith("design a trip")
          and turns[-1]["text"].startswith("add a cache"),
          f"{st_h} {[t.get('text', '')[:20] for t in turns]}")

    check("Ф6.1 a turn keeps its provenance",
          turns[1].get("provenance", {}).get("run") == "r-1"
          and turns[1]["provenance"].get("model") == "TripApproval"
          and turns[1]["provenance"].get("applied") == 3
          and turns[0]["provenance"].get("view") == "c4",
          json.dumps([t.get("provenance") for t in turns])[:220])

    st_p, per = req("GET", "/api/dialogue?user=arch&project=trip")
    check("Ф6.1 a dialogue is scoped to its project",
          st_p == 200 and len(per.get("turns") or []) == 2
          and all(t.get("project") == "trip" for t in (per.get("turns") or [])),
          json.dumps([t.get("project") for t in (per.get("turns") or [])]))

    st_o, other_user = req("GET", "/api/dialogue?user=someone")
    check("Ф6.1 dialogues are isolated per architect",
          len(other_user.get("turns") or []) == 1
          and (other_user.get("turns") or [])[0]["text"] == "private note",
          json.dumps(other_user)[:160])

    st_s, summ = req("GET", "/api/dialogue/summary?user=arch")
    projs = {d.get("project"): d.get("turns") for d in (summ.get("dialogues") or [])}
    check("Ф6.1 the dialogues are summarised per project",
          st_s == 200 and projs.get("trip") == 2 and projs.get("other") == 1, json.dumps(projs))

    _, proj = req("GET", "/api/projects/trip")
    chat = (proj.get("project") or {}).get("chat") or []
    check("Ф6.1 a project turn is mirrored into the project chat",
          any(c.get("text", "").startswith("design a trip") for c in chat), json.dumps(chat)[:160])

    st_lim, lim = req("GET", "/api/dialogue?user=arch&limit=1")
    check("Ф6.1 the history honours a limit",
          len(lim.get("turns") or []) == 1 and (lim.get("turns") or [])[0]["text"].startswith("add a cache"),
          json.dumps(lim)[:160])

    # --- Ф7.6: a grounded answer (query -> marked chunks -> LLM -> citations) --
    rag_log = pathlib.Path(STUB_STATE) / "rag.jsonl"
    rag_log.unlink(missing_ok=True)

    class _RagStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            content = "".join(str((m or {}).get("content") or "") for m in (body.get("messages") or []))
            with rag_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"prompt": content[:500]}, ensure_ascii=False) + "\n")
            answer = ("Refunds must be supported within 14 days [1]. "
                      "Unicorns approve refunds on Mars.")
            payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": answer}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    rag_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RagStub)
    threading.Thread(target=rag_srv.serve_forever, daemon=True).start()
    # self-contained: the block owns the document it answers from
    req("POST", "/api/docs", {"user": "raguser", "name": "refunds.md", "kind": "md",
                              "content": "# Refunds\n\n- REQ-1: refunds must be supported within 14 days.\n"
                                         "- REQ-2: the customer is notified about the refund.\n"})
    put_config({"llm": {"base_url": f"http://127.0.0.1:{rag_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 30}})
    try:
        st_r, ans = req("POST", "/api/rag/answer", {"user": "raguser", "query": "refunds"})
        prompts = [json.loads(line)["prompt"] for line in rag_log.read_text().splitlines()
                   if line.strip()] if rag_log.exists() else []
        check("Ф7.6 a question is answered from the retrieved marked chunks",
              st_r == 200 and "refund" in str(ans.get("answer") or "").lower()
              and bool(ans.get("chunks")) and any("refund" in p.lower() for p in prompts),
              f"{st_r} {json.dumps(ans)[:150]}")
        gr = ans.get("grounding") or {}
        check("Ф7.6 the answer is attributed claim by claim",
              bool(gr.get("claims")) and len(gr.get("unsupported") or []) >= 1
              and len(gr.get("supported") or []) >= 1,
              json.dumps({k: len(v) for k, v in gr.items() if isinstance(v, list)}))
        check("Ф7.6 every claim carries its source or is marked unsupported",
              all(c.get("fidelity") and (c.get("source") or c.get("fidelity") == "unsupported")
                  for c in (gr.get("claims") or [])),
              json.dumps((gr.get("claims") or [])[:1])[:160])

        st_n, none = req("POST", "/api/rag/answer", {"user": "nobody", "query": "refunds"})
        check("Ф7.6 a question without matching documents is refused clearly",
              st_n == 400 and "document" in str(none.get("detail") or "").lower(), f"{st_n} {none}")
    finally:
        rag_srv.shutdown()
        rag_srv.server_close()
        put_config({"llm": {"mode": "mock"}})

    # --- Ф11: architecture synthesis from a chat description ----------------
    from backend import design as design_mod  # noqa: E402
    from backend import archdoc as archdoc_mod  # noqa: E402

    # the material is the description plus any attached documents
    mat_root, mat_files = design_mod.material(
        "SynthDemo",
        "Система учёта заявок: сотрудник создаёт заявку, руководитель согласует, "
        "бухгалтерия бронирует поездку. Роли: сотрудник, руководитель, бухгалтерия.",
        [{"name": "reqs.md", "content": "- REQ-1: заявка проходит согласование\n"}])
    check("Ф11.1 the synthesis material is the description plus the documents",
          mat_root.is_dir() and len(mat_files) == 2
          and (mat_root / "00-description.md").is_file(), str(mat_files))

    st_syn, syn = req("POST", "/api/design",
                      {"name": "SynthDemo", "project": "SynthDemo",
                       "description": "Система учёта заявок: сотрудник создаёт заявку, "
                                      "руководитель согласует, бухгалтерия бронирует поездку."},
                      timeout=60)
    check("Ф11.1 a description starts a synthesis run",
          st_syn == 200 and syn.get("run_id") and syn.get("files", 0) >= 1,
          f"{st_syn} {json.dumps(syn)[:160]}")

    since_s, events_s, snap_s = 0, [], {}
    deadline = time.time() + 180
    while time.time() < deadline:
        _, snap_s = req("GET", f"/api/runs/{syn['run_id']}?since={since_s}")
        events_s += snap_s.get("events", [])
        since_s = snap_s.get("event_count", since_s)
        if snap_s.get("status") != "running":
            break
        time.sleep(0.4)
    texts_s = [e.get("text", "") for e in events_s]
    graph_s = snap_s.get("graph") or {}
    check("Ф11.1 the synthesis designs a graph from the description",
          snap_s.get("status") == "done" and (graph_s.get("nodes") or [])
          and any("designing the architecture from" in t for t in texts_s),
          f"status={snap_s.get('status')} nodes={len(graph_s.get('nodes') or [])}")

    st_doc, doc = req("GET", "/api/models/SynthDemo/documentation")
    md = doc.get("markdown") or ""
    check("Ф11.3 the model yields architecture documentation",
          st_doc == 200 and len(md) > 300 and "SynthDemo" in md
          and all(h in md for h in ("## 1. Overview", "## 2. C4 decomposition",
                                    "## 4. Relationships", "## 6. Traceability")),
          f"{st_doc} len={len(md)}")

    st_doc2, doc2 = req("GET", "/api/models/no-such-model/documentation")
    check("Ф11.3 documentation for an unknown model is a 404", st_doc2 == 404, str(st_doc2))

    # the documentation must carry the real content, not just headers
    check("Ф11.3 the documentation lists the model's elements",
          md.count("|") >= 20 and doc.get("nodes") and doc.get("edges") is not None
          and "System" in md, f"pipes={md.count('|')} nodes={doc.get('nodes')}")

    # UI: the canvas layout (drag positions and auto-groupings) is stored per model
    st_l0, l0 = req("GET", "/api/models/no-such-model/layout")
    check("UI-layout: an unknown model has an empty layout",
          st_l0 == 200 and l0.get("positions") == {} and l0.get("mode") in (None, "", "auto"),
          json.dumps(l0)[:160])

    st_l1, l1 = req("PUT", "/api/models/layout-demo/layout",
                    {"mode": "manual", "layout": "c4",
                     "positions": {"component://a": {"x": 10, "y": 20}, "component://b": {"x": 30, "y": 40}}})
    st_l2, l2 = req("GET", "/api/models/layout-demo/layout")
    check("UI-layout: positions round-trip through the layout store",
          st_l1 == 200 and l1.get("ok") and st_l2 == 200
          and l2.get("mode") == "manual" and l2.get("layout") == "c4"
          and l2["positions"]["component://b"] == {"x": 30, "y": 40},
          f"{st_l1}/{st_l2} {json.dumps(l2)[:160]}")

    st_l3, l3 = req("PUT", "/api/models/layout-demo/layout", {"positions": "nope"})
    check("UI-layout: a malformed layout is rejected", st_l3 == 400, f"{st_l3} {l3}")

    st_l4, l4 = req("PUT", "/api/models/layout-demo/layout", {"mode": "auto", "layout": "grid", "positions": {}})
    st_l5, l5 = req("GET", "/api/models/layout-demo/layout")
    check("UI-layout: an auto-grouping replaces the manual layout",
          st_l4 == 200 and st_l5 == 200 and l5.get("mode") == "auto"
          and l5.get("layout") == "grid" and l5.get("positions") == {},
          json.dumps(l5)[:160])

    # merge must never emit two entries with the same id: a documented element
    # with the same id but a different name used to collide, which made the whole
    # merged graph invalid ("duplicate node id") and the run status `invalid`
    from backend import merge as merge_mod  # noqa: E402

    code_g = {"nodes": [
        {"id": "system://m", "type": "System", "name": "M", "parent": None, "evidence": ["a"]},
        {"id": "actor://operator", "type": "Actor", "name": "Operator", "parent": "system://m", "evidence": ["a"]},
    ], "edges": []}
    docs_g = {"nodes": [
        {"id": "system://m", "type": "System", "name": "M", "parent": None, "evidence": ["d"]},
        {"id": "actor://operator", "type": "Actor", "name": "Operator (docs)", "parent": "system://m", "evidence": ["d"]},
    ], "edges": []}
    merged_m = merge_mod.merge_graphs(code_g, docs_g)
    ids_m = [n["id"] for n in merged_m["nodes"]]
    check("merge: the merged graph has no duplicate node ids",
          len(ids_m) == len(set(ids_m)) and ids_m.count("actor://operator") == 1, str(ids_m))
    op = [n for n in merged_m["nodes"] if n["id"] == "actor://operator"]
    check("merge: an id-matched element is shared, not a deviation",
          len(op) == 1 and op[0]["sources"] == ["code", "docs"] and op[0]["deviation"] is False,
          str([(n["id"], n.get("sources"), n.get("deviation")) for n in merged_m["nodes"]]))

    # (7b) the material must survive trimming: an aspect pass used to return an
    # empty graph because its prompt (with the sources) was dropped from the tail
    from agent import core as agent_core  # noqa: E402

    def _fit_case(msgs, budget=60000):
        sess = agent_core.Session("repo", [], {"max_context_chars": budget})
        sess.messages = [dict(m) for m in msgs]
        sess._fit()
        return sess.messages, sum(len(m.get("content") or "") for m in sess.messages)

    big = "s" * 134675
    kept, total = _fit_case([{"role": "user", "content": big}, {"role": "user", "content": "p" * 462}])
    check("fix(7b) the base material survives trimming",
          total <= 60000 and len(kept[0].get("content") or "") >= 20000,
          f"{len(kept)} msgs / {total} chars / head={len(kept[0].get('content') or '')}")

    stale = [{"role": "user", "content": "p" * 462}]
    for i in range(6):
        stale += [{"role": "assistant", "content": "a" * 4000}, {"role": "user", "content": "r" * 300}]
    stale.append({"role": "user", "content": big})   # the current aspect prompt with its sources
    kept2, total2 = _fit_case(stale)
    last_len = len(kept2[-1].get("content") or "")
    check("fix(7b) the newest request keeps its sources when older turns are dropped",
          total2 <= 60000 and last_len >= 20000,
          f"{len(kept2)} msgs / {total2} chars / last={last_len}")

    kept3, total3 = _fit_case([{"role": "user", "content": big},
                               {"role": "assistant", "content": "a" * 4000},
                               {"role": "user", "content": "r" * 300}])
    check("fix(7b) a repair round keeps the answer being repaired",
          total3 <= 60000 and len(kept3) == 3 and "a" * 100 in (kept3[1].get("content") or ""),
          f"{len(kept3)} msgs / {total3} chars")

    # (7) the session budget is an actual upper bound
    from agent import core as agent_core  # noqa: E402
    sess = agent_core.Session("repo", [], {"max_context_chars": 1000})
    sess.messages = ([{"role": "user", "content": "s" * 5000}]
                     + [{"role": "assistant", "content": "a" * 300} for _ in range(12)])
    sess._fit()
    size = sum(len(m.get("content") or "") for m in sess.messages)
    check("fix(7) a trimmed session really fits its context budget",
          size <= 1000, f"{len(sess.messages)} messages / {size} chars")

    # Ф8.5 - starter models from templates (scaffolding)
    st, tpl = req("GET", "/api/templates")
    tpl_list = tpl.get("templates") or []
    ids = [t.get("id") for t in tpl_list]
    check("Ф8.5 templates are listed with a description",
          st == 200 and {"web_service", "service_db", "cli"} <= set(ids)
          and all(t.get("title") and t.get("description") and t.get("node_count") for t in tpl_list),
          f"{st} {ids}")

    st, sc = req("POST", "/api/models/tpl-scaffold/scaffold", {"template": "service_db"})
    graph = sc.get("model") or {}
    nodes = graph.get("nodes") or []
    types = [n.get("type") for n in nodes]
    roots = [n for n in nodes if not n.get("parent")]
    check("Ф8.5 a template scaffolds a valid starter model",
          st == 200 and sc.get("ok") and (sc.get("validation") or {}).get("ok")
          and len(roots) == 1 and roots[0].get("type") == "System"
          and "DataStore" in types and "Component" in types and "Interface" in types
          and len(graph.get("edges") or []) >= 3,
          f"{st} {sc.get('detail') or (sc.get('validation') or {}).get('errors')} types={types[:10]}")

    st, stored_tpl = req("GET", "/api/models/tpl-scaffold")
    check("Ф8.5 the scaffolded model is stored and loadable",
          st == 200 and (stored_tpl.get("nodes") or []), f"{st}")

    st, bad_tpl = req("POST", "/api/models/tpl-bad/scaffold", {"template": "nope"})
    check("Ф8.5 an unknown template is rejected", st == 400 and "template" in str(bad_tpl.get("detail") or ""),
          f"{st} {bad_tpl}")

    # Ф8.4 - a repository with only documents (ТЗ) is designed, not reconstructed
    design_repo = pathlib.Path(tempfile.mkdtemp(prefix="archide-design-"))
    (design_repo / "docs").mkdir(parents=True, exist_ok=True)
    (design_repo / "docs" / "tz.md").write_text(
        "# ТЗ: система согласования заявок\n\n"
        "- сотрудник создаёт заявку и отправляет её руководителю;\n"
        "- руководитель согласует или отклоняет;\n"
        "- бухгалтерия бронирует поездку;\n"
        "- данные: заявка, сотрудник, согласование;\n"
        "- роли: сотрудник, руководитель, бухгалтерия.\n",
        encoding="utf-8")
    design_log = pathlib.Path(STUB_STATE) / "design.jsonl"

    class _DesignStub(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            content = "".join(str((m or {}).get("content") or "") for m in (body.get("messages") or []))
            with design_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"prompt": content[:300]}, ensure_ascii=False) + "\n")
            payload = json.dumps({"choices": [{"message": {"role": "assistant",
                        "content": json.dumps(base_stub)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401
            return

    design_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _DesignStub)
    threading.Thread(target=design_srv.serve_forever, daemon=True).start()
    design_log.unlink(missing_ok=True)
    put_config({"llm": {"base_url": f"http://127.0.0.1:{design_srv.server_address[1]}/v1",
                        "api_key": "", "model": "stub", "mode": "live", "timeout": 60},
                "agent": {"runner": "local"},
                "context": {"compare_docs": True, "extract_aspects": False, "aspect_passes": False,
                            "reconcile_fix": False, "reconcile_report": False, "design_from_docs": True}})
    try:
        snap_dz, events_dz = run_and_wait(str(design_repo), "builtin", timeout=90)
        dz_texts = [e.get("text", "") for e in events_dz]
        dz_prompts = [json.loads(line)["prompt"] for line in design_log.read_text().splitlines()
                      if line.strip()] if design_log.exists() else []
        check("Ф8.4 an architecture is designed from documentation alone",
              snap_dz.get("status") == "done"
              and any("designing the architecture from" in t for t in dz_texts)
              and any("DESIGNING the architecture" in p for p in dz_prompts)
              and (snap_dz.get("graph") or {}).get("nodes"),
              f"status={snap_dz.get('status')} events={[t for t in dz_texts if 'design' in t][:1]}")
    finally:
        design_srv.shutdown()
        design_srv.server_close()
        shutil.rmtree(design_repo, ignore_errors=True)

    empty_repo = pathlib.Path(tempfile.mkdtemp(prefix="archide-empty-"))
    try:
        snap_empty, events_empty = run_and_wait(str(empty_repo), "builtin", timeout=60)
        check("Ф8.4 a repository without code and documents fails clearly",
              snap_empty.get("status") == "error"
              and "no readable source files or documents" in str(snap_empty.get("error") or ""),
              str(snap_empty.get("error"))[:160])
    finally:
        shutil.rmtree(empty_repo, ignore_errors=True)
    put_config({"llm": {"mode": "mock"}, "context": {"design_from_docs": True}})

    # Ф8.2 - an external coding agent (a CLI command) delivers the repository
    stub_cli = str(pathlib.Path(ROOT) / "tests" / "stub" / "agent-cli")
    os.chmod(stub_cli, 0o755)
    cli_log = pathlib.Path(STUB_STATE) / "agent-cli.jsonl"
    cli_log.unlink(missing_ok=True)
    put_config({"codegen": {"mode": "cli"},
                "codegen_cli": {"command": f"{sys.executable} {stub_cli}", "timeout": 60,
                                "env": {"STUB_STATE": STUB_STATE}}})
    st_cli, gen_cli = req("POST", "/api/models/cg/codegen",
                          {"node": "component://cg/core", "target": "cgcli"}, timeout=90)
    cli_repo = pathlib.Path(str(gen_cli.get("repo") or ""))
    seen = [json.loads(line) for line in cli_log.read_text().splitlines()] if cli_log.exists() else []
    check("Ф8.2 the external CLI agent delivers a repository",
          st_cli == 200 and gen_cli.get("mode") == "cli" and gen_cli.get("exit_code") == 0
          and (cli_repo / "src" / "app.py").is_file() and (cli_repo / "README.md").is_file()
          and gen_cli.get("commit") and pathlib.Path(str(gen_cli.get("log"))).is_file(),
          f"status={st_cli} body={str(gen_cli)[:200]}")
    check("Ф8.2 the task handed to the agent carries the package and markers",
          bool(seen) and "Work package" in seen[-1]["prompt"] and "Traceability markers" in seen[-1]["prompt"],
          str(seen[-1]["prompt"][:120]) if seen else "no log")

    # a failing agent must be reported, not silently accepted
    put_config({"codegen_cli": {"env": {"STUB_FAIL": "1"}}})
    st_bad, gen_bad = req("POST", "/api/models/cg/codegen",
                          {"node": "component://cg/core", "target": "cgclifail"}, timeout=90)
    check("Ф8.2 a failing external agent is reported",
          st_bad == 200 and gen_bad.get("exit_code") == 1 and gen_bad.get("error"),
          f"exit={gen_bad.get('exit_code')} error={gen_bad.get('error')}")
    put_config({"codegen": {"mode": "llm"}, "codegen_cli": {"command": "", "env": {}}})


    check("Ф2.14 the aspect prompt asks for the fields the notations need",
          '"from"' in behavior_prompt and '"to"' in behavior_prompt
          and "Participant" in process_prompt and "cardinality" in data_prompt,
          f"behavior={'from' in behavior_prompt} process={'Participant' in process_prompt} "
          f"data={'cardinality' in data_prompt}")



    # Ф1.4 - the persisted model name must be loadable by name (strip `.git`)
    sys.path.insert(0, str(POC))
    try:
        from backend.runner import model_name_for
        check("Ф1.4 model name strips the .git suffix",
              model_name_for("https://github.com/pol4/ArchIDE.git") == "ArchIDE",
              model_name_for("https://github.com/pol4/ArchIDE.git"))
        check("Ф1.4 model name from a local path",
              model_name_for(str(REPO).rstrip("/")) == pathlib.Path(REPO).name,
              model_name_for(str(REPO)))
    except ImportError as exc:  # pragma: no cover - import guard
        check("Ф1.4 model name helper importable", False, str(exc))

    # Ф3.4 - the local runner must target the server's loopback base, never the
    # container-only `host.docker.internal`, or validation silently degrades.
    from backend import runtime as _runtime, runner as _runner
    saved_public, saved_local = _runtime.PUBLIC_BASE, _runtime.LOCAL_BASE
    try:
        _runtime.PUBLIC_BASE = "http://host.docker.internal:9999"
        _runtime.LOCAL_BASE = "http://127.0.0.1:9999"
        check("Ф3.4 local validator URL prefers loopback",
              _runner._local_validate_url() == "http://127.0.0.1:9999/api/validate",
              _runner._local_validate_url())
    finally:
        _runtime.PUBLIC_BASE, _runtime.LOCAL_BASE = saved_public, saved_local

    # Ф1.4 - a `<name>.git.json` orphan is cleaned up by an explicit, non-destructive
    # migration; a plain save must never delete a differently-named model.
    from backend import model_store
    mig_dir = pathlib.Path(tempfile.mkdtemp(prefix="archide-models-"))
    try:
        (mig_dir / "Proj.git.json").write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
        model_store.save_model({"nodes": [{"id": "system://p", "type": "System", "parent": None}],
                                "edges": []}, mig_dir, "Proj")
        check("Ф1.4 saving does not delete a legacy .git model",
              (mig_dir / "Proj.git.json").exists() and (mig_dir / "Proj.json").exists(),
              str(sorted(p.name for p in mig_dir.glob("*.json"))))
        (mig_dir / "Proj.json").unlink()
        moved = model_store.migrate_legacy_names(mig_dir)
        check("Ф1.4 the migration renames a legacy .git model into place",
              moved == ["Proj"] and (mig_dir / "Proj.json").exists()
              and not (mig_dir / "Proj.git.json").exists(),
              f"{moved} {sorted(p.name for p in mig_dir.glob('*.json'))}")
    finally:
        shutil.rmtree(mig_dir, ignore_errors=True)

    # Ф3.2 - evidence validation (code) + doc coverage surface
    ev_graph = {"nodes": [
        {"id": "sys://e", "type": "System", "parent": None, "evidence": ["a.py"]},
        {"id": "component://e/c", "type": "Component", "parent": "sys://e", "evidence": ["missing.py"]},
    ], "edges": []}
    st, r = req("POST", "/api/validate", {"graph": ev_graph, "files": ["a.py"]})
    check("Ф3.2 evidence validation flags missing files",
          st == 200 and "component://e/c" in (r.get("missing_evidence") or []), str(r.get("missing_evidence")))
    st, r = req("POST", "/api/validate", {"graph": ev_graph, "files": ["a.py", "missing.py"]})
    check("Ф3.2 evidence validation passes when files exist",
          st == 200 and not (r.get("missing_evidence") or []), str(r.get("missing_evidence")))

    # Ф3.3 - identity resolution candidates (code vs docs)
    code_g = {"nodes": [
        {"id": "component://c/a", "type": "Component", "name": "API", "parent": None, "evidence": ["a"]},
        {"id": "component://c/x", "type": "Component", "name": "Cache", "parent": None, "evidence": ["a"]},
    ], "edges": []}
    docs_g = {"nodes": [
        {"id": "component://d/a", "type": "Component", "name": "API", "parent": None, "evidence": ["d"]},
        {"id": "component://d/y", "type": "Component", "name": "Cache", "parent": None, "evidence": ["d"]},
        {"id": "component://d/z", "type": "Component", "name": "Cache", "parent": None, "evidence": ["d"]},
    ], "edges": []}
    st, r = req("POST", "/api/merge/candidates", {"code": code_g, "docs": docs_g})
    check("Ф3.3 identity candidates computed",
          st == 200 and len(r.get("auto_matched") or []) >= 1 and len(r.get("ambiguous") or []) >= 1,
          str({k: len(v) for k, v in (r or {}).items() if isinstance(v, list)}))

    # Ф3.4 - evaluation harness (property/metamorphic/round-trip)
    eval_graph = {"nodes": [
        {"id": "sys://v", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "container://v/a", "type": "Container", "parent": "sys://v", "evidence": ["a"]},
        {"id": "component://v/c", "type": "Component", "parent": "container://v/a", "evidence": ["a"]},
    ], "edges": []}
    st, r = req("POST", "/api/eval", {"graph": eval_graph})
    m = r.get("metrics") or {}
    check("Ф3.4 eval metrics computed",
          st == 200 and m.get("validator_ok") is True and m.get("canonical_stable") is True and m.get("slice_deterministic") is True,
          str(m))

    # Ф2.7 - slice/view engine (deterministic, read-only)
    sl_graph = {"nodes": [
        {"id": "sys://s", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "container://s/a", "type": "Container", "parent": "sys://s", "evidence": ["a"]},
        {"id": "component://s/c", "type": "Component", "parent": "container://s/a", "evidence": ["a"]},
        {"id": "iface://s/i", "type": "Interface", "parent": "component://s/c", "evidence": ["a"]},
        {"id": "container://s/b", "type": "Container", "parent": "sys://s", "evidence": ["a"]},
    ], "edges": [{"source": "component://s/c", "target": "iface://s/i", "type": "exposes"}]}
    st, r = req("POST", "/api/slice", {"graph": sl_graph, "scope": "component://s/c", "depth": 1, "direction": "down"})
    ids = sorted(n["id"] for n in (r.get("slice") or {}).get("nodes", []))
    check("Ф2.7 slice down = scope + children", st == 200 and ids == ["component://s/c", "iface://s/i"], str(ids))
    st, r = req("POST", "/api/slice", {"graph": sl_graph, "scope": "component://s/c", "depth": 2, "direction": "up"})
    ids = sorted(n["id"] for n in (r.get("slice") or {}).get("nodes", []))
    check("Ф2.7 slice up = scope + ancestors", "sys://s" in ids and "container://s/a" in ids, str(ids))
    st, r = req("POST", "/api/slice", {"graph": sl_graph, "scope": "container://s/a", "depth": 2, "aspects": ["interface"]})
    ids = sorted(n["id"] for n in (r.get("slice") or {}).get("nodes", []))
    check("Ф2.7 aspect-filtered slice", "iface://s/i" in ids and "component://s/c" not in ids, str(ids))
    st, _ = req("POST", "/api/slice", {"graph": sl_graph, "scope": "nope"})
    check("Ф2.7 slice rejects unknown scope (400)", st == 400, str(st))

    # Ф1.5 - volume layout (scratch) + GC over host persistence
    if STUB_STATE:
        argv_path = pathlib.Path(STUB_STATE) / "argv.log"
        argv = argv_path.read_text() if argv_path.exists() else ""
        check("Ф1.5 container gets a scratch dir", "ARCHIDE_SCRATCH_DIR=" in argv, argv[-200:])
    if DATA:
        rd = pathlib.Path(DATA) / "runs"
        md = pathlib.Path(DATA) / "models"
        rd.mkdir(parents=True, exist_ok=True)
        md.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (rd / f"aaaa00000000000{i}.json").write_text(
                json.dumps({"id": f"aaaa00000000000{i}", "status": "done", "created": i, "events": []}))
            (md / f"gcmodel{i}.json").write_text(json.dumps({"nodes": [], "edges": []}))
        st, res = req("POST", "/api/gc", {"keep_runs": 2, "keep_models": 2})
        check("Ф1.5 GC prunes old runs/models",
              st == 200 and res.get("runs_removed", 0) >= 1 and res.get("models_removed", 0) >= 1, str(res))
        remaining_runs = list(rd.glob("aaaa*.json"))
        check("Ф1.5 GC keeps the newest", len(remaining_runs) <= 2, str(len(remaining_runs)))

    # Ф7 - personal documents (md/csv) + chunk markers + RAG verification
    md = "# Requirements\n- REQ-1: system must charge payments\n- REQ-2: refunds must be supported\n"
    st, up = req("POST", "/api/docs", {"user": "alice", "name": "reqs.md", "kind": "md", "content": md})
    doc_id = up.get("id")
    check("Ф7.1 document uploaded", st == 200 and bool(doc_id) and len(up.get("chunks") or []) >= 1, str(up)[:160])
    _, lst = req("GET", "/api/docs?user=alice")
    check("Ф7.1 document in personal history", any(d.get("id") == doc_id for d in (lst.get("docs") or [])), str(lst)[:160])
    _, other = req("GET", "/api/docs?user=bob")
    check("Ф7.1 personal history isolated", not any(d.get("id") == doc_id for d in (other.get("docs") or [])))
    st, doc = req("GET", f"/api/docs/{doc_id}?user=alice")
    check("Ф7.1 original retrievable", st == 200 and "REQ-1" in (doc.get("content") or ""))

    st, res = req("POST", "/api/rag/search", {"user": "alice", "query": "refunds"})
    hits = res.get("results") or []
    check("Ф7.2 RAG finds a relevant chunk",
          st == 200 and len(hits) >= 1 and "refund" in hits[0]["text"].lower(), str(hits[:1])[:160])

    marker = (hits[0].get("marker") if hits else None)
    st, ver = req("POST", "/api/markers/verify", {"marker": marker})
    check("Ф7.3 chunk marker verifies", st == 200 and ver.get("valid") is True, str(ver))
    tampered = dict(marker or {})
    tampered["chunk"] = int(tampered.get("chunk", 0)) + 99
    st, ver2 = req("POST", "/api/markers/verify", {"marker": tampered})
    check("Ф7.3 tampered marker rejected", ver2.get("valid") is False, str(ver2))

    st, g = req("POST", "/api/rag/verify",
                {"answer": "System must charge payments. Unicorns fly over rainbows.", "chunks": [hits[0]] if hits else []})
    check("Ф7.4 grounding separates supported vs generated",
          st == 200 and len(g.get("supported") or []) >= 1 and len(g.get("unsupported") or []) >= 1,
          str({k: len(v) for k, v in (g or {}).items() if isinstance(v, list)}))

    st, rq = req("POST", f"/api/docs/{doc_id}/requirements?user=alice")
    rids = [r.get("id") for r in (rq.get("requirements") or [])]
    check("Ф7.5 requirements extracted from document", st == 200 and any("REQ-1" in str(r) for r in rids), str(rids))

    # Ф7.3b/Ф7.6 - content-bound markers + claim-level fidelity
    _, up2 = req("POST", "/api/docs", {"user": "alice", "name": "cb.md", "kind": "md",
                                       "content": "- REQ-9: payments must be idempotent\n"})
    chunk0 = (up2.get("chunks") or [{}])[0]
    marker0 = chunk0.get("marker")
    text0 = chunk0.get("text")
    st, v = req("POST", "/api/markers/verify", {"marker": marker0, "text": text0})
    check("Ф7.3b marker bound to content verifies", st == 200 and v.get("valid") is True and v.get("content_ok") is True, str(v))
    st, v = req("POST", "/api/markers/verify", {"marker": marker0, "text": text0 + " ALTERED"})
    check("Ф7.3b altered chunk content is detected", v.get("valid") is True and v.get("content_ok") is False, str(v))

    st, g2 = req("POST", "/api/rag/verify",
                 {"answer": "Payments must be idempotent. Unicorns fly over rainbows.", "chunks": [chunk0]})
    claims = g2.get("claims") or []
    fids = [c.get("fidelity") for c in claims]
    check("Ф7.6 claim-level fidelity computed", st == 200 and "verbatim" in fids and "unsupported" in fids, str(fids))
    un = next((c for c in claims if c.get("fidelity") == "unsupported"), {})
    check("Ф7.6 changed/generated parts reported", bool(un.get("added")), str(un.get("added")))

    # Ф4.1-Ф4.3 - edit transactions + gate + referential actions + impact
    base_model = {"nodes": [
        {"id": "sys://m", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "container://m/c", "type": "Container", "parent": "sys://m", "evidence": ["a"]},
        {"id": "component://m/x", "type": "Component", "parent": "container://m/c", "evidence": ["a"]},
        {"id": "iface://m/i", "type": "Interface", "parent": "component://m/x", "evidence": ["a"]},
    ], "edges": [
        {"source": "container://m/c", "target": "component://m/x", "type": "contains"},
        {"source": "component://m/x", "target": "iface://m/i", "type": "exposes"},
    ]}
    req("PUT", "/api/models/edit1", {"graph": base_model})
    st, r = req("POST", "/api/models/edit1/edit",
                {"operations": [{"op": "add_node", "node": {"id": "component://m/y", "type": "Component", "parent": "container://m/c", "evidence": ["a"]}}]})
    check("Ф4.1 edit transaction adds a node", st == 200 and r.get("ok") is True and (r.get("validation") or {}).get("ok") is True, str(r)[:160])

    st, r = req("POST", "/api/models/edit1/edit",
                {"operations": [{"op": "add_node", "node": {"id": "component://m/z", "type": "Component", "parent": "nope", "evidence": ["a"]}}]})
    check("Ф4.1 gate blocks an invalid edit (dangling parent)", st == 422 and r.get("ok") is False, str(r)[:160])

    st, r = req("POST", "/api/models/edit1/edit",
                {"operations": [{"op": "add_node", "node": {"id": "component://m/z", "type": "Component", "parent": "nope", "evidence": ["a"]}}],
                 "auto_repair": True})
    z = next((n for n in (r.get("model") or {}).get("nodes", []) if n["id"] == "component://m/z"), {})
    check("Ф4.3 auto-repair nullifies a dangling parent", st == 200 and r.get("ok") is True and z.get("parent") is None, str(z))

    st, r = req("POST", "/api/models/edit1/edit", {"operations": [{"op": "remove_node", "id": "component://m/x"}]})
    ids_after = [n["id"] for n in (r.get("model") or {}).get("nodes", [])]
    check("Ф4.2 cascade removes the dependent interface", st == 200 and r.get("ok") is True and "iface://m/i" not in ids_after, str(ids_after))

    m2 = {"nodes": [
        {"id": "sys://m2", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "component://m2/a", "type": "Component", "parent": "sys://m2", "evidence": ["a"]},
        {"id": "component://m2/b", "type": "Component", "parent": "sys://m2", "evidence": ["a"]},
    ], "edges": [{"source": "component://m2/a", "target": "component://m2/b", "type": "depends_on"}]}
    req("PUT", "/api/models/edit2", {"graph": m2})
    st, r = req("POST", "/api/models/edit2/edit", {"operations": [{"op": "remove_node", "id": "component://m2/b"}]})
    check("Ф4.2 restrict blocks removal of a dependency target", st == 422 and r.get("ok") is False, str(r)[:160])

    st, r = req("POST", "/api/models/edit2/impact", {"node": "component://m2/a"})
    check("Ф4.3 impact analysis lists affected nodes", st == 200 and "component://m2/b" in (r.get("impacted") or []), str(r)[:160])

    # Ф4.4 - declarative constraints
    cm = {"nodes": [
        {"id": "sys://cm", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "component://cm/a", "type": "Component", "parent": "sys://cm", "evidence": ["a"]},
        {"id": "component://cm/b", "type": "Component", "parent": "sys://cm", "evidence": ["a"]},
    ], "edges": [{"source": "component://cm/a", "target": "component://cm/b", "type": "depends_on"}]}
    req("PUT", "/api/models/cmodel", {"graph": cm})
    st, r = req("PUT", "/api/models/cmodel/constraints", {"constraints": [
        {"kind": "forbid_edge", "edge_type": "depends_on", "from": "Component", "to": "Component"},
        {"kind": "require_evidence", "type": "Component"},
    ]})
    check("Ф4.4 constraints saved", st == 200 and r.get("ok") is True, str(r)[:160])
    _, lst = req("GET", "/api/models/cmodel/constraints")
    check("Ф4.4 constraints listed", len(lst.get("constraints") or []) == 2, str(lst)[:160])
    st, v = req("GET", "/api/models/cmodel/validate")
    viol = v.get("violations") or []
    check("Ф4.4 forbid_edge violation detected",
          st == 200 and any((x.get("constraint") or {}).get("kind") == "forbid_edge" for x in viol), str(viol[:2])[:200])
    st, r = req("POST", "/api/models/cmodel/edit",
                {"operations": [{"op": "add_node", "node": {"id": "component://cm/c", "type": "Component", "parent": "sys://cm"}}]})
    check("Ф4.4 edit gate blocks a constraint violation", st == 422 and r.get("ok") is False, str(r)[:160])

    # Ф4.5 - ownership + optimistic concurrency
    om = {"nodes": [
        {"id": "sys://o", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "component://o/a", "type": "Component", "parent": "sys://o", "evidence": ["a"], "owner": "alice"},
    ], "edges": []}
    req("PUT", "/api/models/omodel", {"graph": om})
    st, r = req("POST", "/api/models/omodel/edit",
                {"user": "bob", "operations": [{"op": "update_node", "id": "component://o/a", "patch": {"name": "X"}}]})
    check("Ф4.5 ownership blocks foreign edits (403)", st == 403, str(r)[:140])
    st, r = req("POST", "/api/models/omodel/edit",
                {"user": "alice", "operations": [{"op": "update_node", "id": "component://o/a", "patch": {"name": "X"}}]})
    check("Ф4.5 owner can edit", st == 200 and r.get("ok") is True, str(r)[:140])
    _, md = req("GET", "/api/models/omodel")
    from backend import model_store as _ms  # noqa: E402
    cur_hash = _ms.model_hash(md)
    st, r = req("POST", "/api/models/omodel/edit",
                {"user": "alice", "expected_hash": "deadbeefdeadbeef",
                 "operations": [{"op": "update_node", "id": "component://o/a", "patch": {"name": "Y"}}]})
    check("Ф4.5 optimistic concurrency conflict (409)", st == 409, str(r)[:140])
    st, r = req("POST", "/api/models/omodel/edit",
                {"user": "alice", "expected_hash": cur_hash,
                 "operations": [{"op": "update_node", "id": "component://o/a", "patch": {"name": "Y"}}]})
    check("Ф4.5 matching hash allows the edit", st == 200 and r.get("ok") is True, str(r)[:140])

    # Ф4.9/Ф4.10 - agent reviewer + reconciler
    rv = {"nodes": [
        {"id": "sys://rv", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "component://rv/a", "type": "Component", "parent": "sys://rv"},
    ], "edges": []}
    req("PUT", "/api/models/rmodel", {"graph": rv})
    st, r = req("POST", "/api/models/rmodel/review")
    kinds = [i.get("kind") for i in (r.get("issues") or [])]
    check("Ф4.9 reviewer finds integrity issues", st == 200 and "missing_evidence" in kinds, str(kinds))

    st, r = req("POST", "/api/models/cmodel/review")
    props = r.get("proposals") or []
    check("Ф4.9 reviewer proposes fixes", st == 200 and any(p.get("op") == "remove_edge" for p in props), str(props)[:160])

    st, r = req("POST", "/api/models/cmodel/reconcile", {"apply": True})
    conv = r.get("convergence") or {}
    check("Ф4.10 reconciler reduces divergences",
          st == 200 and conv.get("before", 0) >= 1 and conv.get("after", 99) < conv.get("before", 0), str(conv))

    # Ф4.8 - custom aspects
    st, r = req("POST", "/api/metamodel/aspects",
                {"name": "compliance", "node_types": ["Policy", "Control"], "edge_types": ["governs"]})
    check("Ф4.8 custom aspect registered", st == 200 and "compliance" in (r.get("aspects") or {}),
          str(list((r.get("aspects") or {}).keys()))[:160])
    _, mm2 = req("GET", "/api/metamodel")
    check("Ф4.8 custom aspect in registry and new type accepted",
          "compliance" in (mm2.get("aspects") or {}) and "Control" in (mm2.get("node_types") or []),
          str((mm2.get("aspects") or {}).get("compliance")))

    # Ф5.1-Ф5.5 - generation, work package, markers, conformance
    gen_model = {"nodes": [
        {"id": "sys://g", "type": "System", "parent": None, "evidence": ["a"]},
        {"id": "container://g/c", "type": "Container", "parent": "sys://g", "evidence": ["a"]},
        {"id": "component://g/svc", "type": "Component", "parent": "container://g/c", "evidence": ["svc.py"]},
        {"id": "iface://g/svc", "type": "Interface", "parent": "component://g/svc", "evidence": ["svc.py"]},
        {"id": "operation://g/charge", "type": "Operation", "parent": "iface://g/svc", "evidence": ["svc.py"], "signature": "charge(order: Order) -> Receipt"},
        {"id": "data://g/order", "type": "DataEntity", "parent": "component://g/svc", "evidence": ["models.py"]},
        {"id": "attribute://g/order/id", "type": "Attribute", "parent": "data://g/order", "evidence": ["models.py"], "data_type": "int"},
        {"id": "req://REG-1", "type": "Requirement", "parent": None, "evidence": ["docs.md"]},
    ], "edges": [
        {"source": "component://g/svc", "target": "iface://g/svc", "type": "exposes"},
        {"source": "iface://g/svc", "target": "operation://g/charge", "type": "has"},
        {"source": "req://REG-1", "target": "component://g/svc", "type": "satisfies"},
    ]}
    req("PUT", "/api/models/gmodel", {"graph": gen_model})
    st, gen = req("POST", "/api/models/gmodel/generate", {"kinds": ["openapi", "ddl", "schema", "skeleton"]})
    arts = gen.get("artifacts") or {}
    check("Ф5.1 generators produce artifacts",
          st == 200 and {"openapi", "ddl", "schema", "skeleton"}.issubset(arts.keys()), str(list(arts.keys())))
    check("Ф5.1 OpenAPI contains the operation", "charge" in (arts.get("openapi") or ""), (arts.get("openapi") or "")[:120])
    check("Ф5.1 DDL contains the table", "order" in (arts.get("ddl") or "").lower(), (arts.get("ddl") or "")[:120])

    st, wp = req("POST", "/api/models/gmodel/workpackage", {"node": "component://g/svc"})
    check("Ф5.2 work package has slice + criteria",
          st == 200 and wp.get("node") == "component://g/svc"
          and len((wp.get("slice") or {}).get("nodes", [])) >= 1 and len(wp.get("criteria") or []) >= 1, str(wp)[:160])

    st, mk = req("POST", "/api/models/gmodel/markers", {"node": "component://g/svc"})
    markers = mk.get("markers") or []
    check("Ф5.3 signed trace markers issued", st == 200 and len(markers) >= 1, str(markers[:1])[:160])
    st, v = req("POST", "/api/markers/verify", {"marker": markers[0] if markers else {}, "kind": "trace"})
    check("Ф5.3 trace marker verifies", st == 200 and v.get("valid") is True, str(v))

    st, cmx = req("POST", "/api/models/gmodel/coverage-matrix", {})
    check("Ф5.4 coverage matrix computed",
          st == 200 and isinstance(cmx.get("matrix"), list) and len(cmx.get("matrix") or []) >= 1, str(cmx)[:160])
    st, cf = req("POST", "/api/models/gmodel/conformance", {"markers": markers})
    check("Ф5.4 conformance verifies markers and coverage",
          st == 200 and cf.get("ok") is True and "coverage" in cf, str(cf)[:160])

    st, loop = req("POST", "/api/models/gmodel/conformance-loop", {"node": "component://g/svc"})
    check("Ф5.5 conformance loop reports repair guidance",
          st == 200 and "gaps" in loop and "repair" in loop and loop.get("ok") is False, str(loop)[:160])

    # restore a sane config
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"}, "context": {"compare_docs": False}})

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\nAPI: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
