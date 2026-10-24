"""Edit transactions, validation gate, referential actions and impact (Ф4.1-Ф4.3).

An edit is a list of operations applied to a model; the result is gated by the
validator. Node removal applies **referential actions** (cascade/restrict/nullify)
from the metamodel. Impact analysis lists the nodes affected by a change.
"""
from __future__ import annotations

from .metamodel import REFERENTIAL_ACTIONS

# edge types that cascade to their *target* when the source is removed
_CASCADE_TARGET = {"contains", "owns", "exposes", "implemented_by", "tested_by"}


def _index(graph: dict):
    nodes = {n["id"]: dict(n) for n in graph.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}
    edges = [dict(e) for e in graph.get("edges", []) or [] if isinstance(e, dict)]
    return nodes, edges


def _cascades_for(nodes: dict, edges: list, nid: str, removing: set) -> set:
    """Return ids to cascade; raise on `restrict`."""
    cascade: set[str] = set()
    for edge in edges:
        if nid not in (edge.get("source"), edge.get("target")):
            continue
        etype = edge.get("type")
        action = REFERENTIAL_ACTIONS.get(etype, "restrict")
        other = edge.get("target") if edge.get("source") == nid else edge.get("source")
        if action == "cascade":
            if etype == "anchored_to":
                if edge.get("target") == nid:
                    cascade.add(other)
            elif edge.get("source") == nid and etype in _CASCADE_TARGET:
                cascade.add(other)
        elif action == "restrict":
            if other and other != nid and other not in removing:
                raise ValueError(f"cannot remove {nid}: `{etype}` link to {other} (restrict)")
    return cascade


def apply_operations(graph: dict, operations: list, auto_repair: bool = False):
    nodes, edges = _index(graph)
    applied: list[str] = []

    to_remove = {op.get("id") for op in operations if op.get("op") == "remove_node" and op.get("id")}
    frontier = set(to_remove)
    while frontier:
        nid = frontier.pop()
        if nid not in nodes:
            continue
        for cascade in _cascades_for(nodes, edges, nid, to_remove):
            if cascade and cascade not in to_remove:
                to_remove.add(cascade)
                frontier.add(cascade)

    for op in operations:
        kind = op.get("op")
        if kind == "add_node":
            node = op.get("node") or {}
            if node.get("id"):
                nodes[node["id"]] = dict(node)
                applied.append(kind)
        elif kind == "update_node":
            nid, patch = op.get("id"), op.get("patch") or {}
            if nid in nodes:
                nodes[nid].update(patch)
                applied.append(kind)
        elif kind == "add_edge":
            edge = op.get("edge") or {}
            if edge.get("source") and edge.get("target") and edge.get("type"):
                edges.append(dict(edge))
                applied.append(kind)
        elif kind == "remove_edge":
            s, t, ty = op.get("source"), op.get("target"), op.get("type")
            before = len(edges)
            edges = [e for e in edges
                     if not (e.get("source") == s and e.get("target") == t and (ty is None or e.get("type") == ty))]
            if len(edges) != before:
                applied.append(kind)
        elif kind == "remove_node":
            applied.append(kind)

    for nid in to_remove:
        nodes.pop(nid, None)
    edges = [e for e in edges if e.get("source") not in to_remove and e.get("target") not in to_remove]

    result = {"nodes": list(nodes.values()), "edges": edges}
    if auto_repair:
        known = set(nodes)
        for node in result["nodes"]:
            if node.get("parent") and node["parent"] not in known:
                node["parent"] = None
    return result, applied


def impacted(graph: dict, node: str, depth: int = 1) -> list[str]:
    edges = graph.get("edges", []) or []
    seen = {node}
    frontier = {node}
    for _ in range(max(0, int(depth))):
        nxt: set[str] = set()
        for edge in edges:
            if edge.get("source") in frontier and edge.get("target") not in seen:
                nxt.add(edge.get("target"))
            if edge.get("target") in frontier and edge.get("source") not in seen:
                nxt.add(edge.get("source"))
        seen |= nxt
        frontier = nxt
    seen.discard(node)
    return sorted(x for x in seen if x)
