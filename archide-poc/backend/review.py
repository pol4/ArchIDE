"""Agent reviewer and reconciler (Ф4.9/Ф4.10).

The reviewer observes the model (validation-independent integrity + constraints +
coverage) and **proposes fixes**. The reconciler runs that loop and can apply the
proposals, reporting convergence (divergences before → after).
"""
from __future__ import annotations

from . import analysis, constraints
from . import edit as edit_mod


def review(graph: dict) -> dict:
    issues: list[dict] = []
    proposals: list[dict] = []
    nodes = {n["id"]: n for n in graph.get("nodes", []) or [] if isinstance(n, dict) and n.get("id")}

    for nid, node in nodes.items():
        if node.get("parent") and node["parent"] not in nodes:
            issues.append({"kind": "dangling_parent", "node": nid})
            proposals.append({"op": "update_node", "id": nid, "patch": {"parent": None}})
        if not node.get("evidence"):
            issues.append({"kind": "missing_evidence", "node": nid})

    for violation in constraints.evaluate(graph):
        issues.append({
            "kind": "constraint", "severity": violation.get("severity", "error"),
            "message": violation.get("message"), "node": violation.get("node"),
        })
        rule = violation.get("constraint") or {}
        edge = violation.get("edge")
        if rule.get("kind") == "forbid_edge" and edge:
            proposals.append({"op": "remove_edge", "source": edge.get("source"),
                              "target": edge.get("target"), "type": edge.get("type")})

    coverage = analysis.coverage(graph)
    for rid in coverage.get("uncovered", []):
        issues.append({"kind": "uncovered_requirement", "node": rid})

    return {"issues": issues, "proposals": proposals, "stable": not issues}


def reconcile(graph: dict, apply_fixes: bool = False) -> dict:
    report = review(graph)
    result = {"divergences": report["issues"], "proposals": report["proposals"], "stable": report["stable"]}
    if apply_fixes and report["proposals"]:
        new_model, _ = edit_mod.apply_operations(graph, report["proposals"], auto_repair=True)
        after = review(new_model)
        result["applied"] = len(report["proposals"])
        result["model"] = new_model
        result["convergence"] = {"before": len(report["issues"]), "after": len(after["issues"])}
        result["stable"] = not after["issues"]
    return result
