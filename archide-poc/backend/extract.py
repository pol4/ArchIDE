"""Aspect-aware extraction from source files (Ф3.1).

Heuristic (deterministic) extraction of aspect elements — interfaces/operations
from function declarations, data entities from class/struct/type declarations,
state machines from enums — anchored to the structural component that owns the
file (by `evidence`).
"""
from __future__ import annotations

import re
from pathlib import Path

_FUNC = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function|func|fn)\s+([A-Za-z_]\w*)\s*[(<]")
_CLASS = re.compile(r"^\s*(?:export\s+)?(?:@dataclass\s*)?(?:class|struct|interface|type)\s+([A-Z]\w*)")
_ENUM = re.compile(r"^\s*(?:export\s+)?enum\s+([A-Z]\w*)")


def _slug(text) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(text)).strip("-") or "item"


def _component_index(graph: dict | None) -> dict[str, str]:
    index: dict[str, str] = {}
    if not graph:
        return index
    for node in graph.get("nodes", []) or []:
        if node.get("type") in ("Component", "Container", "Module"):
            for evidence in node.get("evidence") or []:
                index[str(evidence)] = node["id"]
    return index


def extract_aspects(files, graph: dict | None = None, prefix: str = "app") -> dict:
    comp_for = _component_index(graph)
    nodes: list[dict] = []
    edges: list[dict] = []

    def parent_for(path: str) -> str:
        return comp_for.get(path) or f"component://{prefix}/{_slug(path)}"

    for path, content in files:
        parent = parent_for(path)
        functions = []
        for line in content.splitlines():
            match = _FUNC.match(line)
            if match:
                functions.append(match.group(1))
        if functions:
            iface = f"iface://{prefix}/{_slug(path)}"
            nodes.append({
                "id": iface, "type": "Interface", "name": f"{Path(path).stem} API",
                "parent": parent, "evidence": [path],
                "description": "Extracted interface (heuristic).",
            })
            edges.append({"source": parent, "target": iface, "type": "exposes"})
            for fn in functions:
                op = f"operation://{prefix}/{_slug(path)}/{fn}"
                nodes.append({
                    "id": op, "type": "Operation", "name": fn, "parent": iface,
                    "evidence": [path], "description": "Extracted operation (heuristic).",
                })
                edges.append({"source": iface, "target": op, "type": "has"})
        for line in content.splitlines():
            cls = _CLASS.match(line)
            if cls:
                nodes.append({
                    "id": f"data://{prefix}/{_slug(path)}/{cls.group(1)}", "type": "DataEntity",
                    "name": cls.group(1), "parent": parent, "evidence": [path],
                    "description": "Extracted data entity (heuristic).",
                })
            enum = _ENUM.match(line)
            if enum:
                nodes.append({
                    "id": f"sm://{prefix}/{_slug(path)}/{enum.group(1)}", "type": "StateMachine",
                    "name": enum.group(1), "parent": parent, "evidence": [path],
                    "description": "Extracted state machine (heuristic).",
                })
    return {"nodes": nodes, "edges": edges}


def augment_graph(graph: dict, files) -> dict:
    """Merge extracted aspect nodes/edges into an existing graph."""
    extra = extract_aspects(files, graph=graph)
    nodes = list(graph.get("nodes", []) or [])
    edges = list(graph.get("edges", []) or [])
    have = {n.get("id") for n in nodes}
    have_edges = {(e.get("source"), e.get("target"), e.get("type")) for e in edges}
    for node in extra["nodes"]:
        if node["id"] not in have:
            nodes.append(node)
            have.add(node["id"])
    for edge in extra["edges"]:
        key = (edge["source"], edge["target"], edge["type"])
        if key not in have_edges:
            edges.append(edge)
            have_edges.add(key)
    return {"nodes": nodes, "edges": edges}
