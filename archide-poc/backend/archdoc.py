"""Architecture documentation generated from the model (Ф11.3).

The reconstruction (or the synthesis) produces a graph; the architect also needs
the *document*: what the system is, how it decomposes per C4 level, what each
aspect contains, which requirements are covered and where the evidence is.

Everything here is derived from the graph itself, so the document can never drift
away from the model: it is regenerated from the same file the canvas draws.
"""
from __future__ import annotations

from . import generate as generate_mod
from . import metamodel as metamodel_mod

ASPECT_TITLES = {
    "structure": "Structure (C4)",
    "interface": "Interfaces",
    "data": "Data",
    "behavior": "Behaviour (states)",
    "process": "Processes",
    "deployment": "Deployment",
    "access": "Access",
    "quality": "Quality",
    "aggregation": "Aggregation",
    "crosscutting": "Crosscutting",
}

C4_LEVELS = [("System", "L1 · Systems"), ("Container", "L2 · Containers"),
             ("Component", "L3 · Components"), ("Module", "L4 · Modules")]


def _nodes(graph: dict) -> list[dict]:
    return [n for n in (graph.get("nodes") or []) if n and n.get("id")]


def _edges(graph: dict) -> list[dict]:
    return [e for e in (graph.get("edges") or []) if e]


def _aspect_of(node: dict) -> str:
    return metamodel_mod.aspect_of(node.get("type"))


def _evidence(node: dict) -> str:
    items = [str(e) for e in (node.get("evidence") or []) if e]
    if not items:
        return "—"
    short = [items[0]] + ([f"+{len(items) - 1}"] if len(items) > 1 else [])
    return ", ".join(short)


def _source_of(node: dict) -> str:
    sources = node.get("sources") or []
    return "/".join(str(s) for s in sources) if sources else "—"


def _esc(value) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")[:180]


def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    out += ["| " + " | ".join(_esc(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _tree(nodes: list[dict], parent: str | None, depth: int = 0, limit: int = 400) -> list[str]:
    kids = [n for n in nodes if (n.get("parent") or None) == parent]
    kids.sort(key=lambda n: (str(n.get("type")), str(n.get("name") or n.get("id"))))
    lines: list[str] = []
    for node in kids[:limit]:
        pad = "  " * depth
        lines.append(f"{pad}- **{node.get('name') or node.get('id')}** "
                     f"`[{node.get('type')}]` — {_esc(node.get('description') or '')}")
        lines += _tree(nodes, node["id"], depth + 1, limit)
    return lines


def build(model_name: str, graph: dict, meta: dict | None = None) -> dict:
    """The architecture document (Markdown) plus a section index."""
    nodes = _nodes(graph)
    edges = _edges(graph)
    meta = meta or {}
    sections: list[str] = []

    by_aspect: dict[str, list[dict]] = {}
    for node in nodes:
        by_aspect.setdefault(_aspect_of(node), []).append(node)

    sources = {}
    for node in nodes:
        for src in (node.get("sources") or []):
            sources[src] = sources.get(src, 0) + 1

    parts: list[str] = []
    parts.append(f"# Архитектура: {model_name}\n")
    parts.append(f"_Сгенерировано из модели_ `{model_name}`"
                 + (f" (хеш `{meta.get('model_hash')}`)" if meta.get("model_hash") else "")
                 + f". Элементов: **{len(nodes)}**, связей: **{len(edges)}**.\n")
    sections.append("Overview")

    # ---- 1. overview -------------------------------------------------------
    parts.append("## 1. Overview\n")
    root = next((n for n in nodes if not n.get("parent")), None)
    if root:
        parts.append(f"**{root.get('name') or root.get('id')}** — "
                     f"{_esc(root.get('description') or 'система')}\n")
    if sources:
        parts.append("Источники элементов: " + ", ".join(f"`{k}` — {v}" for k, v in sorted(sources.items())) + ".\n")
    aspect_rows = [[ASPECT_TITLES.get(a, a), str(len(v))] for a, v in sorted(by_aspect.items())]
    parts.append(_table(aspect_rows, ["Аспект", "Элементов"]) + "\n")
    type_counts: dict[str, int] = {}
    for node in nodes:
        type_counts[str(node.get("type"))] = type_counts.get(str(node.get("type")), 0) + 1
    parts.append(_table([[t, str(c)] for t, c in sorted(type_counts.items(), key=lambda kv: -kv[1])],
                        ["Тип", "Количество"]) + "\n")

    # ---- 2. C4 decomposition ----------------------------------------------
    parts.append("## 2. C4 decomposition\n")
    for type_name, title in C4_LEVELS:
        level = [n for n in nodes if n.get("type") == type_name]
        if not level:
            continue
        parts.append(f"### {title}\n")
        parts.append(_table([[n.get("name") or n["id"], n["id"], _esc(n.get("description") or ""), _evidence(n)]
                             for n in level[:200]], ["Название", "id", "Описание", "Источник"]) + "\n")
    sections.append("C4 decomposition")

    # ---- 3. per aspect -----------------------------------------------------
    parts.append("## 3. Aspects\n")
    for aspect in [a for a in ASPECT_TITLES if a in by_aspect]:
        items = by_aspect[aspect]
        parts.append(f"### {ASPECT_TITLES.get(aspect, aspect)} ({len(items)})\n")
        parts.append(_table([[n.get("name") or n["id"], n.get("type"), _esc(n.get("description") or ""),
                              _source_of(n), _evidence(n)] for n in items[:200]],
                            ["Название", "Тип", "Описание", "Источник", "Evidence"]) + "\n")
    sections.append("Aspects")

    # ---- 4. relationships --------------------------------------------------
    parts.append("## 4. Relationships\n")
    by_id = {n["id"]: n for n in nodes}
    edge_rows = []
    for edge in edges[:300]:
        src = by_id.get(edge.get("source"), {})
        dst = by_id.get(edge.get("target"), {})
        edge_rows.append([src.get("name") or edge.get("source"), str(edge.get("type") or "?"),
                          dst.get("name") or edge.get("target")])
    parts.append(_table(edge_rows, ["Откуда", "Тип связи", "Куда"]) + "\n")
    sections.append("Relationships")

    # ---- 5. requirements coverage -----------------------------------------
    requirements = [n for n in nodes if n.get("type") in ("Requirement", "NFR", "SLO", "Constraint")]
    if requirements:
        parts.append("## 5. Requirements\n")
        try:
            coverage = generate_mod.coverage_matrix(graph)
        except Exception:  # noqa: BLE001 - coverage is best-effort here
            coverage = {}
        covered = coverage.get("coverage") or coverage.get("matrix") or {}
        rows = []
        for req in requirements[:200]:
            rows.append([req.get("name") or req["id"], req.get("type"),
                         _esc(req.get("description") or ""), _evidence(req)])
        parts.append(_table(rows, ["Требование", "Тип", "Формулировка", "Источник"]) + "\n")
        sections.append("Requirements")

    # ---- 6. traceability ---------------------------------------------------
    parts.append("## 6. Traceability\n")
    with_evidence = sum(1 for n in nodes if n.get("evidence"))
    parts.append(f"Элементов с `evidence`: **{with_evidence} / {len(nodes)}**.\n")
    parts.append(_tree(nodes, None)[:600] and "\n".join(_tree(nodes, None)) + "\n" or "")
    sections.append("Traceability")

    markdown = "\n".join(parts)
    return {"model": model_name, "markdown": markdown, "sections": sections,
            "nodes": len(nodes), "edges": len(edges)}
