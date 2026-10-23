"""Derived views over a model: aggregation interfaces (Ф2.3) and requirement
coverage / traceability (Ф2.4)."""
from __future__ import annotations

_TRACE_EDGES = ("satisfies", "verifies", "constrains")


def _index(model: dict):
    nodes = {n["id"]: n for n in model.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}
    return nodes


def aggregations(model: dict) -> list[dict]:
    """Aggregation interfaces with their members and contract."""
    nodes = _index(model)
    members: dict[str, list[str]] = {}
    for edge in model.get("edges", []) or []:
        if isinstance(edge, dict) and edge.get("type") == "aggregates":
            members.setdefault(edge.get("target"), []).append(edge.get("source"))
    out = []
    for nid, node in nodes.items():
        if node.get("type") == "AggregationInterface":
            out.append({
                "id": nid,
                "name": node.get("name"),
                "contract": node.get("contract"),
                "members": sorted(m for m in members.get(nid, []) if m),
            })
    return out


def coverage(model: dict) -> dict:
    """Requirement traceability: which requirements are linked to the model."""
    nodes = _index(model)
    requirements = [nid for nid, node in nodes.items() if node.get("type") == "Requirement"]
    covered: set[str] = set()
    for edge in model.get("edges", []) or []:
        if isinstance(edge, dict) and edge.get("type") in _TRACE_EDGES:
            for endpoint in (edge.get("source"), edge.get("target")):
                if endpoint in requirements:
                    covered.add(endpoint)
    uncovered = sorted(r for r in requirements if r not in covered)
    ratio = (len(covered) / len(requirements)) if requirements else 1.0
    return {
        "requirements": len(requirements),
        "covered": len(covered),
        "uncovered": uncovered,
        "ratio": round(ratio, 3),
    }
