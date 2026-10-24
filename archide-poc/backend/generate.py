"""Artifact generation, work packages, trace markers, conformance (Ф5).

Deterministic generators build artifacts from the model: JSON Schema and DDL from
data entities, an OpenAPI document from interfaces/operations, and code skeletons
from components. Work packages bundle an aspect-aware slice with acceptance
criteria; trace markers are signed; conformance verifies markers and coverage.
"""
from __future__ import annotations

import json

from . import markers
from .slice import slice_graph

_JSON_TYPES = {"int": "integer", "integer": "integer", "float": "number", "double": "number",
               "bool": "boolean", "boolean": "boolean", "str": "string", "string": "string"}
_SQL_TYPES = {"int": "INTEGER", "integer": "INTEGER", "float": "REAL", "double": "DOUBLE PRECISION",
              "bool": "BOOLEAN", "boolean": "BOOLEAN", "str": "TEXT", "string": "TEXT"}


def _nodes(graph: dict) -> dict:
    return {n["id"]: n for n in graph.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}


def _children(nodes: dict, parent: str, type_: str) -> list[dict]:
    return [n for n in nodes.values() if n.get("parent") == parent and n.get("type") == type_]


def _schema(graph: dict, nodes: dict) -> str:
    defs = {}
    for nid, node in nodes.items():
        if node.get("type") != "DataEntity":
            continue
        props = {}
        for attr in _children(nodes, nid, "Attribute"):
            props[attr.get("name") or attr["id"]] = {"type": _JSON_TYPES.get(str(attr.get("data_type", "")).lower(), "string")}
        defs[node.get("name") or nid] = {"type": "object", "properties": props}
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": defs}, indent=2, ensure_ascii=False)


def _ddl(graph: dict, nodes: dict) -> str:
    statements = []
    for nid, node in nodes.items():
        if node.get("type") != "DataEntity":
            continue
        columns = [f"  {a.get('name') or a['id']} {_SQL_TYPES.get(str(a.get('data_type', '')).lower(), 'TEXT')}"
                   for a in _children(nodes, nid, "Attribute")]
        statements.append(f"CREATE TABLE {(node.get('name') or nid).lower()} (\n" + ",\n".join(columns) + "\n);")
    return "\n\n".join(statements) + ("\n" if statements else "")


def _openapi(graph: dict, nodes: dict) -> str:
    paths: dict = {}
    for nid, node in nodes.items():
        if node.get("type") != "Operation":
            continue
        op = node.get("name") or nid
        iface = nodes.get(node.get("parent"), {})
        paths.setdefault("/" + op, {})["post"] = {
            "operationId": op,
            "tags": [iface.get("name") or iface.get("id") or "default"],
            "responses": {"200": {"description": "ok"}},
        }
    doc = {"openapi": "3.1.0", "info": {"title": "ArchIDE-generated", "version": "1.0.0"}, "paths": paths}
    return json.dumps(doc, indent=2, ensure_ascii=False)


def _skeleton(graph: dict, nodes: dict) -> str:
    parts = []
    for nid, node in nodes.items():
        if node.get("type") != "Component":
            continue
        name = (node.get("name") or nid).split("/")[-1]
        parts.append(f"# skeleton for {nid}\nclass {name.title()}:\n    pass\n")
    return "\n".join(parts)


def generate(graph: dict, kinds=None) -> dict:
    kinds = kinds or ["openapi", "ddl", "schema", "skeleton"]
    nodes = _nodes(graph)
    artifacts = {}
    if "schema" in kinds:
        artifacts["schema"] = _schema(graph, nodes)
    if "ddl" in kinds:
        artifacts["ddl"] = _ddl(graph, nodes)
    if "openapi" in kinds:
        artifacts["openapi"] = _openapi(graph, nodes)
    if "skeleton" in kinds:
        artifacts["skeleton"] = _skeleton(graph, nodes)
    return {"artifacts": artifacts, "stats": {k: len(v) for k, v in artifacts.items()}}


def _is_descendant(nodes: dict, nid: str, ancestor: str) -> bool:
    cur, guard = nid, 0
    while cur and guard < 64:
        if cur == ancestor:
            return True
        cur = (nodes.get(cur) or {}).get("parent")
        guard += 1
    return False


def workpackage(graph: dict, node: str) -> dict:
    nodes = _nodes(graph)
    if node not in nodes:
        raise ValueError(f"node `{node}` not found")
    sl = slice_graph(graph, node, depth=2, direction="both")
    criteria = []
    for edge in graph.get("edges", []) or []:
        if edge.get("type") not in ("satisfies", "verifies", "constrains"):
            continue
        other = edge.get("source") if edge.get("target") == node else (edge.get("target") if edge.get("source") == node else None)
        if other and (nodes.get(other) or {}).get("type") == "Requirement":
            criteria.append({"kind": "requirement", "id": other,
                             "text": nodes[other].get("description") or nodes[other].get("name")})
    for nid, n in nodes.items():
        if n.get("type") == "Operation" and _is_descendant(nodes, nid, node):
            criteria.append({"kind": "operation", "id": nid, "text": n.get("signature") or n.get("name")})
    return {"node": node, "slice": sl, "criteria": criteria,
            "stats": {"nodes": len(sl["nodes"]), "criteria": len(criteria)}}


def _chain(nodes: dict, nid: str) -> dict:
    chain, cur, guard = {}, nid, 0
    while cur and guard < 64:
        node = nodes.get(cur) or {}
        level = {"System": "system", "Container": "container", "Component": "component", "Module": "module"}.get(node.get("type"))
        if level and level not in chain:
            chain[level] = cur
        cur = node.get("parent")
        guard += 1
    return chain


def trace_markers(cfg: dict, graph: dict, node: str) -> dict:
    nodes = _nodes(graph)
    if node not in nodes:
        raise ValueError(f"node `{node}` not found")
    sl = slice_graph(graph, node, depth=2, direction="both")
    issued = [markers.sign_trace(cfg, {"artifact": n["id"], "chain": _chain(nodes, n["id"]), "lease": f"wp:{node}"})
              for n in sl["nodes"]]
    return {"node": node, "markers": issued}


def coverage_matrix(graph: dict) -> dict:
    nodes = _nodes(graph)
    by_target: dict[str, list] = {}
    for edge in graph.get("edges", []) or []:
        if edge.get("type") in ("implemented_by", "tested_by", "deployed_as"):
            by_target.setdefault(edge.get("source"), []).append({"artifact": edge.get("target"), "kind": edge.get("type")})
    rows = []
    for nid, n in nodes.items():
        if n.get("type") in ("System", "Container", "Component", "Interface", "Operation", "DataEntity", "Attribute"):
            arts = by_target.get(nid, [])
            rows.append({"element": nid, "type": n.get("type"), "artifacts": arts, "covered": bool(arts)})
    covered = sum(1 for r in rows if r["covered"])
    return {"matrix": rows, "covered": covered, "total": len(rows),
            "ratio": round(covered / len(rows), 3) if rows else 1.0}


def conformance(cfg: dict, graph: dict, artifact_markers) -> dict:
    nodes = _nodes(graph)
    errors = []
    for marker in artifact_markers or []:
        if not markers.verify_trace(cfg, marker):
            errors.append(f"invalid marker for `{marker.get('artifact') if isinstance(marker, dict) else '?'}`")
        elif marker.get("artifact") not in nodes:
            errors.append(f"unknown element `{marker.get('artifact')}`")
    coverage = coverage_matrix(graph)
    uncovered = [r["element"] for r in coverage["matrix"] if not r["covered"]]
    return {"ok": not errors, "errors": errors, "coverage": coverage, "uncovered": uncovered}


def conformance_loop(cfg: dict, graph: dict, node: str, artifact_markers=None) -> dict:
    """Check a work package against the gate; report gaps as repair guidance (Ф5.5)."""
    package = workpackage(graph, node)
    if artifact_markers is None:
        artifact_markers = trace_markers(cfg, graph, node)["markers"]
    conf = conformance(cfg, graph, artifact_markers)
    covered = {r["element"] for r in conf["coverage"]["matrix"] if r["covered"]}
    gaps = [n["id"] for n in package["slice"]["nodes"] if n["id"] not in covered]
    repair = [{"element": g, "action": "bind an artifact/marker (implemented_by / tested_by)"} for g in gaps]
    return {"ok": conf["ok"] and not gaps, "attempts": 1, "gaps": gaps, "repair": repair, "conformance": conf}
