"""Evaluation harness over model operations (Ф3.4).

Deterministic checks: validator conformance, canonical round-trip stability,
slice determinism. Used to measure reconstruction/model quality.
"""
from __future__ import annotations

import tempfile

from .graph_schema import validate_graph
from .model_store import load_model, model_hash, normalize, save_model
from .slice import slice_graph


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
    return {"metrics": metrics}
