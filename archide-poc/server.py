#!/usr/bin/env python3
"""Dependency-free HTTP server for the ArchIDE PoC (Python stdlib only).

Serves:
  * the UI and REST API (async runs with live events + cancel),
  * the sandbox endpoints (repo / meta / event / result) used by containers,
  * the LLM gateway (/llm/v1) that agents authenticate against.

No third-party packages are required.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend import agents, analysis, gateway, maintenance, metamodel, model_store, runtime, sandbox
from backend import constraints as constraints_mod
from backend import review as review_mod
from backend import slice as slice_mod
from backend.extract import extract_aspects
from backend import docs as docs_mod
from backend import markers as markers_mod
from backend.merge import candidates
from backend import edit as edit_mod
from backend.evaluate import evaluate
from backend import config as config_mod
from backend import runs
from backend.graph_schema import evidence_errors, registry_errors, validate_graph
from backend.runner import reconstruct

BASE = Path(__file__).resolve().parent
FRONTEND = (BASE / "frontend").resolve()

_SANDBOX_RE = re.compile(r"^/sandbox/(repo|meta|event|result|validate)/([0-9a-f]+)$")
_RUN_RE = re.compile(r"^/api/runs/([0-9a-f]+)(/cancel)?$")
_MODEL_RE = re.compile(r"^/api/models(?:/([A-Za-z0-9._-]+))?$")
_MODEL_SUB_RE = re.compile(r"^/api/models/([A-Za-z0-9._-]+)/(aggregations|coverage|slice|constraints|validate)$")
_DOC_RE = re.compile(r"^/api/docs/([0-9a-f]+)(/requirements)?$")
_EDIT_RE = re.compile(r"^/api/models/([A-Za-z0-9._-]+)/(edit|impact|review|reconcile)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "ArchIDE-PoC/0.3"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, code: int, content_type: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        return json.loads(self._read_body() or b"{}")

    def _query(self, query: str) -> dict:
        return {k: v[0] for k, v in parse_qs(query).items()}

    def _static(self, route: str) -> None:
        rel = unquote(route).lstrip("/") or "index.html"
        target = (FRONTEND / rel).resolve()
        if FRONTEND not in target.parents and target != FRONTEND:
            return self.send_error(404)
        if not target.is_file():
            return self.send_error(404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "application/javascript"
        elif target.suffix == ".css":
            ctype = "text/css"
        elif target.suffix == ".html":
            ctype = "text/html"
        if ctype.startswith("text") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route, query = parsed.path, self._query(parsed.query)

        if route == "/api/agents":
            return self._json(200, {"agents": agents.list_agents_with_limits(config_mod.load_config())})
        if route == "/api/config":
            return self._json(200, config_mod.load_config())
        if route == "/api/volume":
            return self._volume_info()
        if route == "/api/gateway":
            return self._json(200, gateway.info(config_mod.load_config()))
        if route == "/api/gateway/audit":
            return self._json(200, {"audit": gateway.audit_tail()})
        if route == "/api/runs":
            return self._json(200, {"runs": runs.list_runs()})
        if route == "/api/docs":
            return self._json(200, {"docs": docs_mod.list_docs(query.get("user", "default"))})
        match_doc = _DOC_RE.match(route)
        if match_doc and not match_doc.group(2):
            doc = docs_mod.load_doc(query.get("user", "default"), match_doc.group(1))
            if doc is None:
                return self._json(404, {"detail": "unknown document"})
            return self._json(200, doc)
        if route == "/api/metamodel":
            payload = metamodel.registry()
            payload["registry_errors"] = registry_errors()
            return self._json(200, payload)

        match = _MODEL_SUB_RE.match(route)
        if match and match.group(2) in ("aggregations", "coverage", "constraints", "validate"):
            model = model_store.load_model(config_mod.MODELS_DIR, match.group(1))
            if model is None:
                return self._json(404, {"detail": "unknown model"})
            view = match.group(2)
            if view == "aggregations":
                return self._json(200, {"aggregations": analysis.aggregations(model)})
            if view == "coverage":
                return self._json(200, {"coverage": analysis.coverage(model)})
            if view == "constraints":
                return self._json(200, {"constraints": model.get("constraints", [])})
            return self._json(200, {"validation": validate_graph(model), "violations": constraints_mod.evaluate(model)})

        match = _MODEL_RE.match(route)
        if match:
            name = match.group(1)
            if not name:
                return self._json(200, {"models": model_store.list_models(config_mod.MODELS_DIR)})
            model = model_store.load_model(config_mod.MODELS_DIR, name)
            if model is None:
                return self._json(404, {"detail": "unknown model"})
            return self._json(200, model)

        match = _RUN_RE.match(route)
        if match and not match.group(2):
            snapshot = runs.get(match.group(1), int(query.get("since", 0)))
            if snapshot is None:
                return self._json(404, {"detail": "unknown run"})
            return self._json(200, snapshot)

        match = _SANDBOX_RE.match(route)
        if match and match.group(1) in ("repo", "meta"):
            return self._sandbox_get(match.group(1), match.group(2), query.get("token", ""))

        if route == "/llm/v1/models":
            return self._json(200, gateway.models(config_mod.load_config()))

        if route.startswith(("/api/", "/sandbox/", "/llm/")):
            return self._json(404, {"detail": "not found"})
        return self._static("/index.html" if route == "/" else route)

    def do_PUT(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/config":
            try:
                patch = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, config_mod.save_config(patch))

        match_sub = _MODEL_SUB_RE.match(route)
        if match_sub and match_sub.group(2) == "constraints":
            model = model_store.load_model(config_mod.MODELS_DIR, match_sub.group(1))
            if model is None:
                return self._json(404, {"detail": "unknown model"})
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            model["constraints"] = constraints_mod.normalize(body.get("constraints"))
            validation = validate_graph(model)
            if not validation["ok"]:
                return self._json(422, {"ok": False, "validation": validation})
            _, digest = model_store.save_model(model, config_mod.MODELS_DIR, match_sub.group(1))
            return self._json(200, {"ok": True, "constraints": model["constraints"], "hash": digest})

        match = _MODEL_RE.match(route)
        if match and match.group(1):
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            graph = body.get("graph")
            if not isinstance(graph, dict):
                return self._json(400, {"detail": "body must contain a `graph` object"})
            validation = validate_graph(graph)
            if not validation["ok"]:
                return self._json(422, {"ok": False, "validation": validation})
            path, digest = model_store.save_model(graph, config_mod.MODELS_DIR, match.group(1))
            return self._json(200, {"ok": True, "hash": digest, "path": str(path), "validation": validation})

        return self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route, query = parsed.path, self._query(parsed.query)

        if route == "/api/reconstruct":
            return self._reconstruct()
        if route == "/api/slice":
            return self._slice(None)
        match_edit = _EDIT_RE.match(route)
        if match_edit:
            model = model_store.load_model(config_mod.MODELS_DIR, match_edit.group(1))
            if model is None:
                return self._json(404, {"detail": "unknown model"})
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            if match_edit.group(2) == "impact":
                return self._json(200, {"impacted": edit_mod.impacted(model, str(body.get("node") or ""))})
            if match_edit.group(2) == "review":
                return self._json(200, review_mod.review(model))
            if match_edit.group(2) == "reconcile":
                apply_fixes = bool(body.get("apply"))
                result = review_mod.reconcile(model, apply_fixes)
                if apply_fixes and result.get("model"):
                    _, digest = model_store.save_model(result.pop("model"), config_mod.MODELS_DIR, match_edit.group(1))
                    result["hash"] = digest
                return self._json(200, result)
            operations = body.get("operations") or []
            editor = str(body.get("user") or "default")
            expected = body.get("expected_hash")
            if expected and expected != model_store.model_hash(model):
                return self._json(409, {"ok": False, "error": "model changed (hash mismatch)"})
            node_by_id = {n["id"]: n for n in model.get("nodes", []) if isinstance(n, dict) and n.get("id")}
            for op in operations:
                target = op.get("id") if op.get("op") in ("update_node", "remove_node") else None
                if not target and isinstance(op.get("node"), dict):
                    target = op["node"].get("id")
                owner = (node_by_id.get(target) or {}).get("owner") if target else None
                if owner and owner != editor and editor != "admin":
                    return self._json(403, {"ok": False, "error": f"{editor} is not the owner of {target}"})
            try:
                new_model, applied = edit_mod.apply_operations(model, operations, auto_repair=bool(body.get("auto_repair")))
            except ValueError as exc:
                return self._json(422, {"ok": False, "error": str(exc)})
            validation = validate_graph(new_model)
            violations = constraints_mod.evaluate(new_model)
            constraint_errors = [v for v in violations if v.get("severity", "error") == "error"]
            touched = set()
            for op in operations:
                for key in ("id", "source", "target"):
                    if op.get(key):
                        touched.add(op[key])
                if isinstance(op.get("node"), dict) and op["node"].get("id"):
                    touched.add(op["node"]["id"])
                if isinstance(op.get("edge"), dict):
                    touched.add(op["edge"].get("source"))
                    touched.add(op["edge"].get("target"))
            impacted = sorted({n for t0 in touched if t0 for n in ([t0] + edit_mod.impacted(model, t0))})
            if not validation["ok"] or (constraint_errors and not body.get("waive_constraints")):
                return self._json(422, {"ok": False, "validation": validation, "violations": violations, "model": new_model, "impacted": impacted})
            if body.get("dry_run"):
                return self._json(200, {"ok": True, "validation": validation, "violations": violations, "model": new_model, "impacted": impacted, "dry_run": True})
            _, digest = model_store.save_model(new_model, config_mod.MODELS_DIR, match_edit.group(1))
            return self._json(200, {"ok": True, "validation": validation, "violations": violations, "model": new_model, "impacted": impacted, "hash": digest})
        if route == "/api/docs":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            record = docs_mod.ingest(config_mod.load_config(), str(body.get("user") or "default"),
                                     str(body.get("name") or "document.md"), str(body.get("kind") or "md"),
                                     str(body.get("content") or ""))
            return self._json(200, {"id": record["id"], "name": record["name"], "kind": record["kind"],
                                    "chunks": record["chunks"]})
        if route == "/api/rag/search":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, {"results": docs_mod.search(str(body.get("user") or "default"),
                                                               str(body.get("query") or ""), int(body.get("top", 5)))})
        if route == "/api/rag/verify":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, docs_mod.verify_grounding(str(body.get("answer") or ""), body.get("chunks") or []))
        if route == "/api/markers/verify":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            cfg = config_mod.load_config()
            marker = body.get("marker")
            result = {"valid": markers_mod.verify(cfg, marker)}
            if body.get("text") is not None:
                result["content_ok"] = markers_mod.verify_content(cfg, marker, body.get("text"))
            return self._json(200, result)
        match_req = _DOC_RE.match(route)
        if match_req and match_req.group(2) == "/requirements":
            result = docs_mod.extract_requirements(config_mod.load_config(), query.get("user", "default"), match_req.group(1))
            if result is None:
                return self._json(404, {"detail": "unknown document"})
            return self._json(200, result)
        if route == "/api/merge/candidates":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, candidates(body.get("code") or {}, body.get("docs") or {}))
        if route == "/api/eval":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            graph = body.get("graph")
            if not isinstance(graph, dict):
                return self._json(400, {"detail": "body must contain a `graph` object"})
            return self._json(200, evaluate(graph))
        if route == "/api/extract":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            files = [(str(f.get("path") or ""), str(f.get("content") or "")) for f in (body.get("files") or []) if isinstance(f, dict)]
            result = extract_aspects(files, graph=body.get("graph"))
            return self._json(200, {"nodes": result["nodes"], "edges": result["edges"],
                                    "stats": {"nodes": len(result["nodes"]), "edges": len(result["edges"])}})
        match_slice = _MODEL_SUB_RE.match(route)
        if match_slice and match_slice.group(2) == "slice":
            return self._slice(match_slice.group(1))
        if route == "/api/validate":
            return self._validate()
        if route == "/api/volume/reset":
            return self._volume_reset()
        if route == "/api/gateway/lease":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, gateway.issue_lease(config_mod.load_config(), str(body.get("agent") or "builtin")))
        if route == "/api/gc":
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            return self._json(200, maintenance.gc(int(body.get("keep_runs", 20)), int(body.get("keep_models", 20))))
        if route == "/llm/v1/chat/completions":
            return self._llm_chat()

        match = _RUN_RE.match(route)
        if match and match.group(2):
            return self._json(200, {"ok": runs.cancel(match.group(1))})

        match = _SANDBOX_RE.match(route)
        if match:
            kind, job_id, token = match.group(1), match.group(2), query.get("token", "")
            if kind == "result":
                return self._sandbox_result(job_id, token)
            if kind == "event":
                return self._sandbox_event(job_id, token)
            if kind == "validate":
                return self._sandbox_validate(job_id, token)

        return self._json(404, {"detail": "not found"})

    # -- handlers --------------------------------------------------------
    def _reconstruct(self) -> None:
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            return self._json(400, {"detail": f"invalid JSON: {exc}"})

        repo = str(body.get("repo") or "").strip()
        agent_id = body.get("agent") or "builtin"
        if not agents.get_agent(agent_id):
            return self._json(400, {"detail": f"unknown agent: {agent_id}"})

        cfg = config_mod.load_config()
        run_id = runs.start(repo, agent_id, cfg)
        return self._json(200, {"run_id": run_id})

    def _slice(self, model_name) -> None:
        if model_name is None:
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
            graph = body.get("graph")
            if not isinstance(graph, dict):
                return self._json(400, {"detail": "body must contain a `graph` object"})
        else:
            graph = model_store.load_model(config_mod.MODELS_DIR, model_name)
            if graph is None:
                return self._json(404, {"detail": "unknown model"})
            try:
                body = self._read_json()
            except json.JSONDecodeError as exc:
                return self._json(400, {"detail": f"invalid JSON: {exc}"})
        try:
            result = slice_mod.slice_graph(
                graph, str(body.get("scope") or ""), int(body.get("depth", 1)),
                body.get("aspects"), str(body.get("direction") or "down"),
            )
        except ValueError as exc:
            return self._json(400, {"detail": str(exc)})
        return self._json(200, {"slice": result, "stats": {"nodes": len(result["nodes"]), "edges": len(result["edges"])}})

    def _validate(self) -> None:
        """Stateless formal validation tool (metamodel + anchors + cross-aspect)."""
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            return self._json(400, {"detail": f"invalid JSON: {exc}"})
        graph = body.get("graph")
        if not isinstance(graph, dict):
            return self._json(400, {"detail": "body must contain a `graph` object"})
        result = {"validation": validate_graph(graph)}
        if body.get("files") is not None:
            result["missing_evidence"] = evidence_errors(graph, body.get("files"))
        return self._json(200, result)

    def _volume_info(self) -> None:
        cfg = config_mod.load_config()
        agent_cfg = cfg.get("agent", {}) or {}
        name = agent_cfg.get("volume", "archide-agent-data")
        mount = agent_cfg.get("volume_mount", "/agent-data")
        enabled = agent_cfg.get("volume_enabled", True)

        out = subprocess.run(["docker", "volume", "inspect", name], capture_output=True, text=True)
        info = {"name": name, "mount": mount, "enabled": enabled, "exists": out.returncode == 0}
        if info["exists"]:
            try:
                data = json.loads(out.stdout)[0]
                info.update(
                    driver=data.get("Driver"),
                    mountpoint=data.get("Mountpoint"),
                    created=data.get("CreatedAt"),
                )
            except (json.JSONDecodeError, IndexError, KeyError):
                pass
        return self._json(200, info)

    def _volume_reset(self) -> None:
        cfg = config_mod.load_config()
        name = (cfg.get("agent", {}) or {}).get("volume", "archide-agent-data")
        subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True, text=True)
        subprocess.run(["docker", "volume", "create", name], capture_output=True, text=True)
        return self._json(200, {"ok": True, "name": name})

    def _sandbox_get(self, kind: str, job_id: str, token: str) -> None:
        if kind == "repo":
            data = sandbox.get_repo(job_id, token)
            if data is None:
                return self._json(404, {"detail": "unknown job"})
            return self._bytes(200, "application/gzip", data)
        meta = sandbox.get_meta(job_id, token)
        if meta is None:
            return self._json(404, {"detail": "unknown job"})
        return self._json(200, meta)

    def _sandbox_result(self, job_id: str, token: str) -> None:
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            return self._json(400, {"detail": f"invalid JSON: {exc}"})
        ok = sandbox.put_result(job_id, token, body.get("graph"), body.get("error"))
        return self._json(200 if ok else 404, {"ok": ok})

    def _sandbox_event(self, job_id: str, token: str) -> None:
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            return self._json(400, {"detail": f"invalid JSON: {exc}"})
        ok = sandbox.put_event(job_id, token, str(body.get("type") or "log"), str(body.get("text") or ""))
        return self._json(200 if ok else 404, {"ok": ok})

    def _sandbox_validate(self, job_id: str, token: str) -> None:
        """Formal validation tool: agents POST {graph} and get {validation}."""
        if sandbox.get_meta(job_id, token) is None:
            return self._json(404, {"detail": "unknown job"})
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            return self._json(400, {"detail": f"invalid JSON: {exc}"})
        graph = body.get("graph")
        if not isinstance(graph, dict):
            return self._json(400, {"detail": "body must contain a `graph` object"})
        result = {"validation": validate_graph(graph)}
        if body.get("files") is not None:
            result["missing_evidence"] = evidence_errors(graph, body.get("files"))
        return self._json(200, result)

    def _llm_chat(self) -> None:
        cfg = config_mod.load_config()
        ok, principal, err = gateway.consume(cfg, self.headers)
        if not ok:
            return self._json(err, {"error": {"message": "gateway auth or quota exceeded"}})
        body = self._read_body()
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": {"message": "invalid JSON"}})
        model = parsed.get("model")

        if parsed.get("stream"):
            status, ctype, chunks = gateway.stream_chat(cfg, body)
            gateway.record(cfg, {"principal": principal, "model": model, "stream": True, "status": status})
            return self._stream(status, ctype, chunks)

        status, ctype, data = gateway.forward_chat(cfg, body)
        gateway.record(cfg, {"principal": principal, "model": model, "stream": False, "status": status})
        return self._bytes(status, ctype, data)

    def _stream(self, status: int, content_type: str, chunks) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, fmt: str, *args) -> None:  # quiet access log
        pass


def main() -> None:
    host = os.environ.get("ARCHIDE_HOST", "127.0.0.1")
    port = int(os.environ.get("ARCHIDE_PORT", "8000"))
    runtime.PUBLIC_BASE = os.environ.get("ARCHIDE_PUBLIC_URL") or f"http://host.docker.internal:{port}"
    gateway.ensure_token(config_mod.load_config())  # ensure a gateway token exists
    agents.ensure_manifest()  # ensure the agent plugin manifest exists

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"ArchIDE PoC -> http://{host}:{port}  (sandbox/gateway base: {runtime.PUBLIC_BASE})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
