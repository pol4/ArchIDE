"""ArchIDE metamodel validation (Ф2.2).

Validates a graph against the metamodel registry: closed vocabularies, containment
spine, referential integrity, **anchor rules** (how aspect nodes attach to the
backbone) and **cross-aspect edge rules** (how aspects may link to each other).
"""
from __future__ import annotations

from .metamodel import ANCHORS, ASPECTS, EDGE_TYPES, NODE_TYPES, REFERENTIAL_ACTIONS

_STRUCT = {"System", "Container", "Component", "Module"}


def _anchor_error(nid: str, ntype: str, parent_type: str | None) -> str | None:
    allowed = ANCHORS.get(ntype)
    if allowed and parent_type is not None and parent_type not in allowed:
        return f"node {nid} ({ntype}) cannot be anchored to {parent_type} (allowed: {sorted(allowed)})"
    return None


def _cross_aspect_error(etype: str, stype: str, dtype: str) -> str | None:
    if etype == "exposes" and not (stype in {"Component", "Container", "System"} and dtype == "Interface"):
        return "`exposes` must go from a structural node to an `Interface`"
    if etype in ("implements", "realizes") and dtype != "Interface":
        return f"`{etype}` target must be an `Interface`"
    if etype in ("reads", "writes") and not ({stype, dtype} & {"DataEntity", "DataStore"}):
        return f"`{etype}` must involve a `DataEntity` or `DataStore`"
    if etype == "allowed" and not (stype in {"Role", "Permission"} and dtype in {"Operation", "Interface"}):
        return "`allowed` must go from a `Role`/`Permission` to an `Operation`/`Interface`"
    if etype == "governs" and stype != "Policy":
        return "`governs` source must be a `Policy`"
    if etype == "emits" and not (stype in {"Transition", "State", "StateMachine"} and dtype == "Event"):
        return "`emits` must go from a state/transition to an `Event`"
    if etype == "flows_to" and not ({stype, dtype} & {"DataStore", "DataEntity", "Process", "Activity", "Component"}):
        return "`flows_to` endpoints must be data/process nodes"
    if etype == "aggregates" and dtype != "AggregationInterface":
        return "`aggregates` target must be an `AggregationInterface`"
    if etype in ("satisfies", "constrains", "verifies") and "Requirement" not in {stype, dtype}:
        return f"`{etype}` must involve a `Requirement`"
    return None


def registry_errors() -> list[str]:
    """Self-consistency check of the metamodel registry."""
    errors: list[str] = []
    aspect_nodes: set[str] = set().union(*[spec["node_types"] for spec in ASPECTS.values()])
    aspect_edges: set[str] = set().union(*[spec["edge_types"] for spec in ASPECTS.values()])
    for ntype in sorted(NODE_TYPES - aspect_nodes):
        errors.append(f"node type `{ntype}` belongs to no aspect")
    for etype in sorted(EDGE_TYPES - aspect_edges):
        errors.append(f"edge type `{etype}` belongs to no aspect")
    for ntype in sorted(ANCHORS):
        if ntype not in NODE_TYPES:
            errors.append(f"anchor rule for unknown node type `{ntype}`")
    for etype in sorted(REFERENTIAL_ACTIONS):
        if etype not in EDGE_TYPES:
            errors.append(f"referential action for unknown edge type `{etype}`")
    return errors


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
    types: dict[str, str] = {}
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
        types[nid] = ntype
        if ntype not in NODE_TYPES:
            errors.append(f"node {nid}: unknown type `{ntype}` (allowed: {sorted(NODE_TYPES)})")
        parents[nid] = node.get("parent")

    # containment: parent exists + anchor rules
    for nid, parent in parents.items():
        if parent is not None and parent not in ids:
            errors.append(f"node {nid}: parent `{parent}` not found")
        elif parent is not None:
            err = _anchor_error(nid, types.get(nid, ""), types.get(parent))
            if err:
                errors.append(err)

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
            err = _cross_aspect_error(etype, types.get(src, ""), types.get(dst, ""))
            if err:
                errors.append(f"edge {src}->{dst}: {err}")
            # anchor edges: A owns/anchors B -> A must be a valid anchor for B's type
            if etype in ("owns", "anchored_to"):
                allowed = ANCHORS.get(types.get(dst, ""))
                if allowed and types.get(src) not in allowed:
                    errors.append(
                        f"edge {src}->{dst}: `{types.get(src)}` is not a valid anchor for "
                        f"`{types.get(dst)}` (allowed: {sorted(allowed)})"
                    )

    # Ф2.3 - aggregation interfaces must have members and a valid contract
    aggregation_members: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("type") == "aggregates" and edge.get("target") in ids:
            aggregation_members.setdefault(edge["target"], []).append(edge.get("source"))
    node_by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    for nid, ntype in types.items():
        if ntype != "AggregationInterface":
            continue
        if not aggregation_members.get(nid):
            errors.append(f"aggregation {nid}: no members (no `aggregates` edge targets it)")
        contract = (node_by_id.get(nid) or {}).get("contract")
        if contract:
            if contract not in ids:
                errors.append(f"aggregation {nid}: contract `{contract}` not found")
            elif types.get(contract) != "Interface":
                errors.append(f"aggregation {nid}: contract `{contract}` must be an Interface")

    for node in nodes:
        if isinstance(node, dict) and not node.get("evidence"):
            warnings.append(f"node {node.get('id')}: no `evidence` (file references)")

    by_type: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, dict):
            key = node.get("type", "?")
            by_type[key] = by_type.get(key, 0) + 1

    stats = {
        "nodes": len([n for n in nodes if isinstance(n, dict) and n.get("id")]),
        "edges": edge_count,
        "roots": len(roots),
        "node_types": by_type,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}
