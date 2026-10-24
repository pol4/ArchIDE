"""Declarative constraints over the model (Ф4.4).

Constraints live on the model under `constraints` (a list of rules). Kinds:
  require_evidence {type?}                 - nodes of `type` (or all) need evidence
  forbid_edge      {edge_type, from?, to?} - no such edge between these types
  require_edge     {edge_type, from}       - every `from` node has an outgoing edge
  require_parent   {type, parent_type}     - nodes of `type` need such a parent
Severity is `error` (blocks the gate) or `warn`.
"""
from __future__ import annotations

import json


def normalize(constraints) -> list:
    return sorted(
        (dict(c) for c in (constraints or []) if isinstance(c, dict)),
        key=lambda c: json.dumps(c, sort_keys=True, ensure_ascii=False),
    )


def evaluate(graph: dict) -> list[dict]:
    nodes = {n["id"]: n for n in graph.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}
    edges = [e for e in graph.get("edges", []) or [] if isinstance(e, dict)]
    out: list[dict] = []

    for c in graph.get("constraints", []) or []:
        if not isinstance(c, dict):
            continue
        kind = c.get("kind")
        severity = c.get("severity", "error")

        if kind == "require_evidence":
            for nid, node in nodes.items():
                if (not c.get("type") or node.get("type") == c["type"]) and not node.get("evidence"):
                    out.append({"constraint": c, "severity": severity, "message": f"{nid}: missing `evidence`", "node": nid})

        elif kind == "forbid_edge":
            for edge in edges:
                if edge.get("type") != c.get("edge_type"):
                    continue
                if c.get("from") and nodes.get(edge.get("source"), {}).get("type") != c["from"]:
                    continue
                if c.get("to") and nodes.get(edge.get("target"), {}).get("type") != c["to"]:
                    continue
                out.append({
                    "constraint": c, "severity": severity,
                    "message": f"forbidden `{edge.get('type')}` edge {edge.get('source')}->{edge.get('target')}",
                    "edge": {"source": edge.get("source"), "target": edge.get("target"), "type": edge.get("type")},
                })

        elif kind == "require_edge":
            for nid, node in nodes.items():
                if c.get("from") and node.get("type") != c["from"]:
                    continue
                if not any(e.get("type") == c.get("edge_type") and e.get("source") == nid for e in edges):
                    out.append({"constraint": c, "severity": severity,
                                "message": f"{nid}: missing `{c.get('edge_type')}` edge", "node": nid})

        elif kind == "require_parent":
            for nid, node in nodes.items():
                if c.get("type") and node.get("type") != c["type"]:
                    continue
                parent = nodes.get(node.get("parent"))
                if not parent or (c.get("parent_type") and parent.get("type") != c["parent_type"]):
                    out.append({"constraint": c, "severity": severity,
                                "message": f"{nid}: parent must be `{c.get('parent_type')}`", "node": nid})

        else:
            out.append({"constraint": c, "severity": "warn", "message": f"unknown constraint kind `{kind}`"})

    return out


def errors(graph: dict) -> list[dict]:
    return [v for v in evaluate(graph) if v.get("severity", "error") == "error"]
