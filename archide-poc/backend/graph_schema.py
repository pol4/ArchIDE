"""Minimal ArchIDE metamodel validation for the reconstructed graph.

The PoC validates the subset of the ArchIDE metamodel needed to render a graph:
a containment spine (nodes with optional `parent`) plus typed edges with
referential integrity.
"""
from __future__ import annotations

from .metamodel import EDGE_TYPES, NODE_TYPES  # single source of truth


def validate_graph(graph) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(graph, dict):
        return {"ok": False, "errors": ["graph must be a JSON object"], "warnings": [], "stats": {}}

    nodes = graph.get("nodes")
    edges = graph.get("edges", [])

    if not isinstance(nodes, list) or not nodes:
        errors.append("`nodes` must be a non-empty array")
        nodes = []
    if not isinstance(edges, list):
        errors.append("`edges` must be an array")
        edges = []

    ids: set[str] = set()
    parents: dict[str, str | None] = {}
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("each node must be an object")
            continue
        nid = node.get("id")
        if not nid or not isinstance(nid, str):
            errors.append("node is missing a string `id`")
            continue
        if nid in ids:
            errors.append(f"duplicate node id: {nid}")
        ids.add(nid)
        ntype = node.get("type")
        if ntype not in NODE_TYPES:
            errors.append(f"node {nid}: unknown type `{ntype}` (allowed: {sorted(NODE_TYPES)})")
        parents[nid] = node.get("parent")

    for nid, parent in parents.items():
        if parent is not None and parent not in ids:
            errors.append(f"node {nid}: parent `{parent}` not found")

    # containment cycle detection
    for nid in ids:
        seen: set[str] = set()
        cur = nid
        while cur is not None and cur in parents:
            if cur in seen:
                errors.append(f"containment cycle detected at {cur}")
                break
            seen.add(cur)
            cur = parents[cur]

    roots = [nid for nid, p in parents.items() if p is None]
    if len(roots) > 1:
        warnings.append(f"{len(roots)} root nodes (expected a single System root for a clean spine)")

    edge_count = 0
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("each edge must be an object")
            continue
        src, dst, etype = edge.get("source"), edge.get("target"), edge.get("type")
        if src not in ids:
            errors.append(f"edge source `{src}` not found")
        if dst not in ids:
            errors.append(f"edge target `{dst}` not found")
        if etype not in EDGE_TYPES:
            errors.append(f"edge {src}->{dst}: unknown type `{etype}` (allowed: {sorted(EDGE_TYPES)})")
        if src in ids and dst in ids and etype in EDGE_TYPES:
            edge_count += 1

    for node in nodes:
        if isinstance(node, dict) and not node.get("evidence"):
            warnings.append(f"node {node.get('id')}: no `evidence` (file references)")

    by_type: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, dict):
            by_type[node.get("type", "?")] = by_type.get(node.get("type", "?"), 0) + 1

    stats = {
        "nodes": len([n for n in nodes if isinstance(n, dict) and n.get("id")]),
        "edges": edge_count,
        "roots": len(roots),
        "node_types": by_type,
    }

    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}
