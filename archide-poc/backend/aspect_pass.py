"""Sequential aspect passes (Ф3.5).

After the structural C4 pass, each remaining aspect is reconstructed by its own
agent call and merged into the graph as a small, checked step:

* the candidate graph must keep every existing node/edge untouched;
* only new nodes of the aspect's declared types are accepted;
* a new node must be anchored to a node that already exists;
* new edges must use an edge type the aspect sanctions;
* the merged graph is formally validated and the pass is rolled back if invalid.

The report returned alongside the merged graph is what the runner logs, so the
activity feed shows exactly what each aspect contributed.
"""
from __future__ import annotations


def enforce_aspect(current: dict, candidate: dict, aspect: str, specs: dict,
                   max_nodes: int = 0) -> tuple[dict, dict]:
    """Merge only the in-aspect additions of ``candidate`` into ``current``.

    ``max_nodes`` caps how many elements one pass may add (Ф3.7); extra elements
    are reported in ``capped`` instead of being silently dropped.
    """
    spec = specs.get(aspect) or {}
    types = set(spec.get("node_types") or [])
    allowed_edges = set(spec.get("edge_types") or [])
    nodes = [dict(n) for n in (current.get("nodes") or [])]
    edges = [dict(e) for e in (current.get("edges") or [])]
    have = {n.get("id") for n in nodes}
    have_edges = {(e.get("source"), e.get("target"), e.get("type")) for e in edges}
    added_nodes: list[str] = []
    added_edges: list[str] = []
    skipped: list[str] = []

    capped: list[str] = []
    for node in candidate.get("nodes") or []:
        node_id = node.get("id")
        if not node_id or node_id in have:
            continue
        if max_nodes and len(added_nodes) >= max_nodes:
            capped.append(str(node_id))
            continue
        if node.get("type") not in types:
            skipped.append(f"{node_id} (type {node.get('type')} is not part of `{aspect}`)")
            continue
        parent = node.get("parent")
        if parent is not None and parent not in have:
            skipped.append(f"{node_id} (unknown parent {parent})")
            continue
        nodes.append(node)
        have.add(node_id)
        added_nodes.append(node_id)

    for edge in candidate.get("edges") or []:
        key = (edge.get("source"), edge.get("target"), edge.get("type"))
        if key in have_edges:
            continue
        if edge.get("source") not in have or edge.get("target") not in have:
            skipped.append(f"edge {key} (missing endpoint)")
            continue
        if allowed_edges and edge.get("type") not in allowed_edges:
            skipped.append(f"edge {key} (edge type not part of `{aspect}`)")
            continue
        edges.append(edge)
        have_edges.add(key)
        added_edges.append(f"{key[0]} -{key[2]}-> {key[1]}")

    return {"nodes": nodes, "edges": edges}, {
        "aspect": aspect, "added_nodes": added_nodes, "added_edges": added_edges,
        "skipped": skipped, "capped": capped,
    }


# Keyword hints used to pick the sources that matter for one aspect (Ф3.6).
ASPECT_KEYWORDS = {
    "data": ("dataclass", "schema", "entity", "model", "struct", "sql", "proto", "json", "table"),
    "interface": ("route", "endpoint", "handler", "api", "openapi", "request", "response", "export "),
    "behavior": ("state", "transition", "status", "enum", "event", "emit", "publish", "phase"),
    "process": ("process", "pipeline", "workflow", "task", "step", "queue", "job", "stage", "orchestrat"),
    "deployment": ("dockerfile", "compose", "kubernetes", "k8s", "helm", "deploy", "systemd", "container"),
    "access": ("role", "permission", "policy", "auth", "token", "principal", "acl", "login", "lease"),
    "quality": ("timeout", "quota", "limit", "retry", "budget", "nfr", "slo", "latency", "threshold", "max_chars"),
    "aggregation": ("aggregate", "facade", "channel", "gateway"),
    "crosscutting": ("requirement", "decision", "artifact", "capability", "trace"),
}
_PATH_HINTS = (
    ("deployment", ("dockerfile", ".yaml", ".yml", "compose", "helm", "deploy")),
    ("data", (".sql", ".proto", "schema", "model", "entity")),
    ("interface", ("api", "route", "handler", "openapi")),
)


def select_files(aspect: str, files: list, max_chars: int = 60000) -> list:
    """Pick the sources most relevant to `aspect` (best-scored first).

    The selection is bounded by `max_chars`; if nothing scores, the files are
    still taken in order, so an aspect pass is never starved of material.
    """
    keywords = ASPECT_KEYWORDS.get(aspect, ())
    scored = []
    for path, content in files:
        low_path, low = str(path).lower(), (content or "").lower()
        score = 0
        for hint_aspect, hints in _PATH_HINTS:
            if hint_aspect == aspect and any(h in low_path for h in hints):
                score += 5
        for keyword in keywords:
            if keyword in low_path:
                score += 3
            if keyword in low:
                score += 1
        scored.append((score, str(path), content or ""))
    scored.sort(key=lambda item: (-item[0], item[1]))
    # keep only the files that actually look relevant; if none does, fall back to
    # the first few so the pass is never starved of material
    relevant = [(path, content) for score, path, content in scored if score > 0]
    source = relevant or [(path, content) for _score, path, content in scored[:10]]
    picked, total = [], 0
    for path, content in source:
        if picked and total + len(content) > max_chars:
            continue
        picked.append((path, content))
        total += len(content)
    return picked


def find_gaps(graph: dict) -> dict:
    """Elements that ended a run without relationships and/or without evidence (Ф3.9)."""
    nodes = [n for n in (graph.get("nodes") or []) if n.get("id")]
    linked: set[str] = set()
    for edge in graph.get("edges") or []:
        linked.add(edge.get("source"))
        linked.add(edge.get("target"))
    return {
        "no_evidence": [n["id"] for n in nodes if not n.get("evidence")],
        "no_edges": [n["id"] for n in nodes if n["id"] not in linked],
    }


def apply_correction(current: dict, candidate: dict) -> tuple[dict, dict]:
    """Merge a reconciliation answer safely (Ф3.9).

    Only two things are accepted: `evidence` filled in for existing elements and
    new relationships between existing elements. Nodes are never removed,
    renamed or re-parented, and new nodes are ignored — so a correction can
    improve the graph but cannot damage it.
    """
    from .metamodel import EDGE_TYPES

    nodes = [dict(n) for n in (current.get("nodes") or [])]
    index = {n.get("id"): n for n in nodes}
    filled: list[str] = []
    ignored_nodes: list[str] = []
    ignored_edges: list[str] = []
    added_edges: list[str] = []

    for node in candidate.get("nodes") or []:
        node_id = node.get("id")
        if node_id not in index:
            ignored_nodes.append(str(node_id))
            continue
        if not index[node_id].get("evidence") and node.get("evidence"):
            index[node_id]["evidence"] = list(node["evidence"])
            filled.append(str(node_id))

    edges = [dict(e) for e in (current.get("edges") or [])]
    have = {(e.get("source"), e.get("target"), e.get("type")) for e in edges}
    for edge in candidate.get("edges") or []:
        key = (edge.get("source"), edge.get("target"), edge.get("type"))
        if key in have:
            continue
        if key[0] not in index or key[1] not in index or key[2] not in EDGE_TYPES:
            ignored_edges.append(str(key))
            continue
        edges.append({"source": key[0], "target": key[1], "type": key[2],
                      "evidence": list(edge.get("evidence") or [])})
        have.add(key)
        added_edges.append(f"{key[0]} -{key[2]}-> {key[1]}")

    return {"nodes": nodes, "edges": edges}, {
        "filled": filled, "added_edges": added_edges,
        "ignored_nodes": ignored_nodes, "ignored_edges": ignored_edges,
    }


def graph_summary(graph: dict, aspect: str | None = None, specs: dict | None = None) -> str:
    """Compact, deterministic view of a graph for the aspect prompt.

    With `aspect`/`specs` the summary is limited to what an aspect pass needs:
    the elements of that aspect plus the node kinds it may anchor to (Ф3.6), so
    the prompt does not carry every unrelated aspect node.
    """
    import json

    from .metamodel import ANCHORS

    nodes = graph.get("nodes") or []
    if aspect and specs:
        spec = specs.get(aspect) or {}
        own = set(spec.get("node_types") or [])
        anchors: set[str] = set()
        for node_type in own:
            anchors |= set(ANCHORS.get(node_type) or ())
        keep = own | anchors | {"System", "Container", "Component", "Module"}
        nodes = [n for n in nodes if n.get("type") in keep]
    ids = {n.get("id") for n in nodes}
    edges = [e for e in (graph.get("edges") or [])
             if e.get("source") in ids and e.get("target") in ids]
    nodes_out = [{"id": n.get("id"), "type": n.get("type"), "name": n.get("name"), "parent": n.get("parent")}
                 for n in nodes]
    edges_out = [{"source": e.get("source"), "target": e.get("target"), "type": e.get("type")} for e in edges]
    return json.dumps({"nodes": nodes_out, "edges": edges_out}, ensure_ascii=False, sort_keys=True)
