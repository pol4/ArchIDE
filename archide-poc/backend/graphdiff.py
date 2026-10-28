"""Graph diff: target architecture vs a delivered repository (Ф8.3, Ф8.3b).

Closes the acceptance loop of both architect scenarios: the architect keeps the
target model, an external code-generation agent delivers a repository, that
repository is reconstructed into a graph, and the two graphs are compared into a
verdict.

Matching is layered, strongest signal first (Ф8.3b):

1. **id** — the same generator may keep ids stable;
2. **markers/evidence** — the delivered code embeds ArchIDE markers and lives in
   files, so overlapping evidence (file paths, or a marker artifact id found in
   the result) means the same element;
3. **type + normalized name** — the fallback used when nothing else matches.

The verdict is scoped (Ф8.3b): with a ``scope`` node, only elements inside that
slice of the target decide acceptance — the rest is reported as informational —
and the requirements the target claims to satisfy must be covered by the result.
"""
from __future__ import annotations

import re

from . import slice as slice_mod
from .metamodel import ASPECTS


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _basename(path: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", str(path).split("/")[-1].lower())


def _aspect_of(node_type: str) -> str:
    for aspect, spec in ASPECTS.items():
        if node_type in set(spec.get("node_types") or []):
            return aspect
    return "structure"


def _evidence_keys(node: dict) -> set[str]:
    keys = set()
    for item in node.get("evidence") or []:
        text = str(item)
        keys.add(text)
        keys.add(_basename(text))
    # a marker embedded by the coding agent mentions the target element id
    blob = " ".join(str(node.get(k) or "") for k in ("description", "note", "notes", "marker"))
    for marker in re.findall(r"\"artifact\"\s*:\s*\"([^\"]+)\"", blob):
        keys.add(marker)
    return {k for k in keys if k}


def _index(graph: dict):
    by_id: dict[str, dict] = {}
    by_key: dict[tuple[str, str], dict] = {}
    by_evidence: dict[str, dict] = {}
    for node in graph.get("nodes") or []:
        if not node.get("id"):
            continue
        by_id[node["id"]] = node
        by_key.setdefault((str(node.get("type")), _norm(node.get("name") or node["id"].split("/")[-1])), node)
        for key in _evidence_keys(node):
            by_evidence.setdefault(key, node)
    return by_id, by_key, by_evidence


def _find(target_node: dict, by_id, by_key, by_evidence) -> tuple[dict | None, str | None]:
    """Strongest available match for one target element."""
    direct = by_id.get(target_node["id"])
    if direct is not None:
        return direct, "id"
    for key in _evidence_keys(target_node):
        hit = by_evidence.get(key)
        if hit is not None:
            return hit, "evidence"
    hit = by_key.get((str(target_node.get("type")),
                      _norm(target_node.get("name") or target_node["id"].split("/")[-1])))
    if hit is not None:
        return hit, "name"
    return None, None


def diff(target: dict, result: dict, scope: str | None = None) -> dict:
    by_id, by_key, by_evidence = _index(target)
    result_id, result_key, result_evidence = _index(result)

    in_scope: set[str] | None = None
    if scope and scope in by_id:
        try:
            in_scope = {n["id"] for n in (slice_mod.slice_graph(target, scope, depth=2, direction="both").get("nodes") or [])}
        except Exception:  # noqa: BLE001 - an unknown scope must not break acceptance
            in_scope = None

    matched: list[dict] = []
    missing: list[dict] = []
    consumed: set[str] = set()

    for key, node in by_key.items():
        counterpart, how = _find(node, result_id, result_key, result_evidence)
        if counterpart is None:
            missing.append({"id": node["id"], "type": node.get("type"), "name": node.get("name"),
                            "aspect": _aspect_of(node.get("type")),
                            "in_scope": True if in_scope is None else node["id"] in in_scope})
            continue
        consumed.add(counterpart["id"])
        entry = {"target": node["id"], "result": counterpart["id"], "type": node.get("type"),
                 "name": node.get("name"), "aspect": _aspect_of(node.get("type")), "matched_by": how,
                 "in_scope": True if in_scope is None else node["id"] in in_scope}
        if str(node.get("type")) != str(counterpart.get("type")):
            entry["changed"] = f"type {node.get('type')} -> {counterpart.get('type')}"
        matched.append(entry)

    seen_extra: set[str] = set()
    extra = []
    for node in result.get("nodes") or []:
        node_id = node.get("id")
        if not node_id or node_id in consumed or node_id in seen_extra:
            continue
        seen_extra.add(node_id)
        extra.append({"id": node_id, "type": node.get("type"), "name": node.get("name"),
                      "aspect": _aspect_of(node.get("type"))})

    # requirement coverage: a requirement counts as covered when the element that
    # satisfies it in the target has a counterpart in the result
    satisfied_by: dict[str, list[str]] = {}
    for edge in target.get("edges") or []:
        if edge.get("type") not in ("satisfies", "verifies", "constrains"):
            continue
        for source, other in ((edge.get("source"), edge.get("target")), (edge.get("target"), edge.get("source"))):
            if by_id.get(other, {}).get("type") == "Requirement" and source in by_id:
                satisfied_by.setdefault(other, []).append(source)
    in_scope_matched = {m["target"] for m in matched if m["in_scope"]}
    requirements = []
    for req_id, node in by_id.items():
        if node.get("type") != "Requirement":
            continue
        sources = satisfied_by.get(req_id) or []
        covered = any(src in in_scope_matched for src in sources) if sources else False
        # with a scope, a requirement nothing in scope satisfies is informational
        scoped = in_scope is None or not sources or any(src in in_scope for src in sources)
        requirements.append({"id": req_id, "name": node.get("name"),
                             "satisfied_by": sources, "covered": covered, "in_scope": scoped})
    uncovered = [r for r in requirements if not r["covered"] and r["in_scope"]]

    by_aspect: dict[str, dict] = {}
    for item in missing:
        bucket = by_aspect.setdefault(item["aspect"], {"missing": 0, "extra": 0, "missing_in_scope": 0})
        bucket["missing"] += 1
        if item["in_scope"]:
            bucket["missing_in_scope"] += 1
    for item in extra:
        bucket = by_aspect.setdefault(item["aspect"], {"missing": 0, "extra": 0, "missing_in_scope": 0})
        bucket["extra"] += 1
    changed = [m for m in matched if m.get("changed") and m["in_scope"]]
    blocking = [m for m in missing if m["in_scope"]]

    target_nodes = len(by_key)
    checked = len(matched)
    return {
        "scope": scope if in_scope is not None else None,
        "summary": {
            "target_elements": target_nodes,
            "result_elements": len(result_key),
            "matched": checked,
            "missing": len(missing),
            "missing_in_scope": len(blocking),
            "extra": len(extra),
            "changed": len(changed),
            "coverage": round(checked / target_nodes, 3) if target_nodes else 0.0,
        },
        "matched_by": {how: sum(1 for m in matched if m["matched_by"] == how)
                       for how in ("id", "evidence", "name")},
        "requirements": {"total": len(requirements), "covered": len(requirements) - len(uncovered),
                         "uncovered": [r["id"] for r in uncovered]},
        "by_aspect": by_aspect,
        "missing": missing[:50],
        "extra": extra[:50],
        "changed": changed[:50],
        "verdict": "ok" if not blocking and not changed and not uncovered else "gaps",
    }
