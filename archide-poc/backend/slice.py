"""Deterministic slice/view engine (Ф2.7).

Builds a bounded subgraph around a scope node: descendants and/or ancestors up to
a depth, optionally filtered by aspect. Read-only — the model is not modified.
"""
from __future__ import annotations

from .metamodel import ASPECTS


def _aspect_types(aspects) -> set[str] | None:
    if not aspects:
        return None
    out: set[str] = set()
    for name in aspects:
        spec = ASPECTS.get(name)
        if spec:
            out |= set(spec["node_types"])
    return out


def slice_graph(graph: dict, scope: str, depth: int = 1, aspects=None, direction: str = "down") -> dict:
    nodes = {n["id"]: n for n in graph.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}
    if scope not in nodes:
        raise ValueError(f"scope `{scope}` not found")

    kids: dict[str, list[str]] = {}
    for node in nodes.values():
        parent = node.get("parent")
        if parent:
            kids.setdefault(parent, []).append(node["id"])

    depth = max(0, int(depth))
    keep: set[str] = {scope}

    if direction in ("down", "both"):
        frontier = [scope]
        for _ in range(depth):
            nxt: list[str] = []
            for nid in frontier:
                for child in kids.get(nid, []):
                    if child not in keep:
                        keep.add(child)
                        nxt.append(child)
            frontier = nxt

    if direction in ("up", "both"):
        cur = scope
        for _ in range(depth):
            parent = (nodes.get(cur) or {}).get("parent")
            if not parent:
                break
            keep.add(parent)
            cur = parent

    allowed = _aspect_types(aspects)
    if allowed is not None:
        keep = {nid for nid in keep if nid == scope or nodes[nid].get("type") in allowed}

    selected_nodes = [nodes[nid] for nid in nodes if nid in keep]
    selected_edges = [
        e for e in graph.get("edges", []) or []
        if isinstance(e, dict) and e.get("source") in keep and e.get("target") in keep
    ]
    return {"nodes": selected_nodes, "edges": selected_edges}
