"""Evaluation harness over model operations (Ф3.4).

Deterministic checks: validator conformance, canonical round-trip stability,
slice determinism. Used to measure reconstruction/model quality.
"""
from __future__ import annotations

import tempfile

from .graph_schema import validate_graph
from .model_store import load_model, model_hash, normalize, save_model
from .slice import slice_graph


def aspect_metrics(graph: dict) -> dict:
    """Per-aspect quality metrics (Ф3.8).

    For every aspect: how many of its declared node types are present, how many
    elements and relationships it has, and how many of its elements are orphans
    (no edge) or carry no evidence.
    """
    from .metamodel import ASPECTS

    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    linked: set[str] = set()
    for edge in edges:
        linked.add(edge.get("source"))
        linked.add(edge.get("target"))
    out: dict[str, dict] = {}
    for aspect, spec in ASPECTS.items():
        declared = list(spec.get("node_types") or [])
        own = [n for n in nodes if n.get("type") in set(declared)]
        present = sorted({n.get("type") for n in own})
        orphans = [n["id"] for n in own if n["id"] not in linked]
        no_evidence = [n["id"] for n in own if not n.get("evidence")]
        own_ids = {n["id"] for n in own}
        aspect_edges = [e for e in edges if e.get("source") in own_ids or e.get("target") in own_ids]
        out[aspect] = {
            "elements": len(own),
            "types_present": present,
            "types_declared": len(declared),
            "coverage": round(len(present) / len(declared), 3) if declared else 0.0,
            "edges": len(aspect_edges),
            "orphans": len(orphans),
            "without_evidence": len(no_evidence),
        }
    return out


def evaluate(graph: dict) -> dict:
    metrics: dict = {}

    validation = validate_graph(graph)
    metrics["validator_ok"] = bool(validation["ok"])

    with tempfile.TemporaryDirectory() as tmp:
        _, digest1 = save_model(graph, tmp, "eval")
        loaded = load_model(tmp, "eval")
        digest2 = model_hash(loaded) if loaded is not None else None
        metrics["canonical_stable"] = digest1 == digest2
        metrics["roundtrip_ok"] = loaded is not None and normalize(loaded) == normalize(graph)

    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    root = next((n["id"] for n in nodes if not n.get("parent")), nodes[0]["id"] if nodes else None)
    if root:
        first = slice_graph(graph, root, depth=2, direction="down")
        second = slice_graph(graph, root, depth=2, direction="down")
        metrics["slice_deterministic"] = first == second and len(first["nodes"]) >= 1
    else:
        metrics["slice_deterministic"] = False

    keys = ("validator_ok", "canonical_stable", "roundtrip_ok", "slice_deterministic")
    metrics["score"] = round(sum(1 for k in keys if metrics.get(k)) / len(keys), 3)
    metrics["aspects"] = aspect_metrics(graph)
    metrics["aspect_summary"] = {
        "covered": sorted(a for a, m in metrics["aspects"].items() if m["elements"]),
        "empty": sorted(a for a, m in metrics["aspects"].items() if not m["elements"]),
    }
    return {"metrics": metrics}
