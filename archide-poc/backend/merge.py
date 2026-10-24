"""Merge two reconstructed graphs (from source code and from documentation).

Elements present in both graphs get ``sources: ["code","docs"]``; single-source
elements are **deviations** and carry a human-readable ``note``. The result is a
single annotated graph that the UI can filter by source.
"""
from __future__ import annotations

import re


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _key(node: dict, root) -> str:
    if root is not None and node is root:
        return "__root__"
    return f"{node.get('type', '?')}::{_norm(node.get('name') or node.get('id'))}"


def _deviation_note(sources: list[str]) -> str:
    if sources == ["code"]:
        return "only in source code (not documented)"
    if sources == ["docs"]:
        return "only in documentation (not in code)"
    return ""


def merge_graphs(code: dict, docs: dict) -> dict:
    cn = [n for n in (code.get("nodes") or []) if n.get("id")]
    dn = [n for n in (docs.get("nodes") or []) if n.get("id")]
    croot = next((n for n in cn if not n.get("parent")), None)
    droot = next((n for n in dn if not n.get("parent")), None)

    merged: dict[str, dict] = {}
    src_key: dict[tuple[str, str], str] = {}

    def add_nodes(source: str, nodes: list[dict], root) -> None:
        for node in nodes:
            base = _key(node, root)
            key = base
            if key in merged and source in merged[key]["sources"]:
                i = 2
                while f"{base}#{i}" in merged:
                    i += 1
                key = f"{base}#{i}"
            src_key[(source, node["id"])] = key
            if key not in merged:
                item = dict(node)
                item["sources"] = [source]
                merged[key] = item
            else:
                item = merged[key]
                if source not in item["sources"]:
                    item["sources"].append(source)
                    item["sources"].sort()
                item["evidence"] = list(dict.fromkeys((item.get("evidence") or []) + (node.get("evidence") or [])))
                if not item.get("description"):
                    item["description"] = node.get("description")
                if _norm(item.get("parent")) and _norm(node.get("parent")) and _norm(item["parent"]) != _norm(node["parent"]):
                    item["note"] = "containment differs between code and documentation"

    add_nodes("code", cn, croot)
    add_nodes("docs", dn, droot)

    id_of = {key: item["id"] for key, item in merged.items()}

    for item in merged.values():
        source = item["sources"][0]
        parent = item.get("parent")
        if parent:
            mapped = src_key.get((source, parent))
            if mapped:
                item["parent"] = id_of.get(mapped, parent)

    merged_edges: dict[str, dict] = {}
    for source, graph in (("code", code), ("docs", docs)):
        for edge in (graph.get("edges") or []):
            sk = src_key.get((source, edge.get("source")))
            tk = src_key.get((source, edge.get("target")))
            if not sk or not tk:
                continue
            ekey = f"{edge.get('type', '?')}::{sk}->{tk}"
            if ekey not in merged_edges:
                merged_edges[ekey] = {
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "type": edge.get("type"),
                    "description": edge.get("description"),
                    "evidence": list(edge.get("evidence") or []),
                    "sources": [source],
                }
            else:
                item = merged_edges[ekey]
                if source not in item["sources"]:
                    item["sources"].append(source)
                    item["sources"].sort()
                item["evidence"] = list(dict.fromkeys(item["evidence"] + list(edge.get("evidence") or [])))

    for edge in merged_edges.values():
        source = edge["sources"][0]
        edge["source"] = id_of.get(src_key.get((source, edge["source"])), edge["source"])
        edge["target"] = id_of.get(src_key.get((source, edge["target"])), edge["target"])

    for item in merged.values():
        item["deviation"] = len(item["sources"]) < 2
        if item["deviation"]:
            item.setdefault("note", _deviation_note(item["sources"]))
    for edge in merged_edges.values():
        edge["deviation"] = len(edge["sources"]) < 2
        if edge["deviation"]:
            edge.setdefault("note", _deviation_note(edge["sources"]))

    return {"nodes": list(merged.values()), "edges": list(merged_edges.values())}


def candidates(code: dict, docs: dict) -> dict:
    """Identity-resolution candidates between two graphs (Ф3.3)."""
    def keys(graph):
        out: dict[str, list[str]] = {}
        for node in graph.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            key = f"{node.get('type', '?')}::{_norm(node.get('name') or node.get('id'))}"
            out.setdefault(key, []).append(node.get("id"))
        return out

    ck, dk = keys(code), keys(docs)
    auto_matched: list[dict] = []
    ambiguous: list[dict] = []
    for key, cids in ck.items():
        dids = dk.get(key)
        if not dids:
            continue
        if len(cids) == 1 and len(dids) == 1:
            auto_matched.append({"code": cids[0], "docs": dids[0]})
        else:
            ambiguous.append({"key": key, "code": cids, "docs": dids})
    code_only = [cid for key, ids in ck.items() if key not in dk for cid in ids]
    docs_only = [did for key, ids in dk.items() if key not in ck for did in ids]
    return {"auto_matched": auto_matched, "ambiguous": ambiguous, "code_only": code_only, "docs_only": docs_only}
