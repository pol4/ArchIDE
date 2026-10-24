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

    # restore a sane config
    put_config({"llm": {"mode": "mock"}, "agent": {"runner": "local"}, "context": {"compare_docs": False}})

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\nAPI: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
