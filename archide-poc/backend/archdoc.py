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
        parts.append(aspect_summary(graph, aspect)["summary"] + "\n")
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

    # ---- 5a. scenarios -----------------------------------------------------
    scenarios = scenario_summary(graph)
    if scenarios:
        parts.append("## 5a. Scenarios\n")
        parts.append("Сценарии выведены из процессов и активностей модели.\n")
        for sc in scenarios[:20]:
            parts.append(f"- **{sc['name']}** — {_esc(sc['description']) or 'без описания'}"
                         + (f"; шаги: {', '.join(sc['steps'][:8])}" if sc["steps"] else ""))
        parts.append("")
        sections.append("Scenarios")

    # ---- 5b. risks and gaps ------------------------------------------------
    risks = risk_summary(graph)
    if risks:
        parts.append("## 5b. Risks and gaps\n")
        parts.append("Ограничения и пробелы модели — то, что стоит закрыть до приёмки.\n")
        for line in risks:
            parts.append(f"- {line}")
        parts.append("")
        sections.append("Risks and gaps")

    # ---- 5c. decisions and assumptions -------------------------------------
    decisions = [n for n in nodes if n.get("type") in ("Policy", "Constraint", "NFR", "SLO", "Requirement")]
    if decisions:
        parts.append("## 5c. Decisions and assumptions\n")
        parts.append("Правила, ограничения и допущения, зафиксированные в модели "
                     "(архитектурные решения и предпосылки).\n")
        parts.append(_table([[n.get("name") or n["id"], n.get("type"),
                              _esc(n.get("description") or ""), _evidence(n)]
                             for n in decisions[:60]],
                            ["Формулировка", "Тип", "Описание", "Источник"]) + "\n")
        sections.append("Decisions and assumptions")

    # ---- 6. traceability ---------------------------------------------------
    parts.append("## 6. Traceability\n")
    with_evidence = sum(1 for n in nodes if n.get("evidence"))
    parts.append(f"Элементов с `evidence`: **{with_evidence} / {len(nodes)}**.\n")
    parts.append(_tree(nodes, None)[:600] and "\n".join(_tree(nodes, None)) + "\n" or "")
    sections.append("Traceability")

    markdown = "\n".join(parts)
    return {"model": model_name, "markdown": markdown, "sections": sections,
            "nodes": len(nodes), "edges": len(edges)}

# ---------------------------------------------------------------------------
# Текстовое описание аспекта: диаграмма без подписи непонятна архитектору
# ---------------------------------------------------------------------------
def aspect_summary(graph: dict, aspect: str) -> dict:
    """Что содержит аспект, из чего состоит и как связан с остальной моделью."""
    nodes = _nodes(graph)
    edges = [_ for _ in (graph.get("edges") or []) if _]
    items = [n for n in nodes if _aspect_of(n) == aspect]
    ids = {n["id"] for n in items}
    by_type: dict[str, int] = {}
    for node in items:
        by_type[str(node.get("type"))] = by_type.get(str(node.get("type")), 0) + 1

    linked: dict[str, int] = {}
    internal = 0
    cross = 0
    cross_kinds: dict[str, int] = {}
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if src in ids and dst in ids:
            internal += 1
        elif src in ids or dst in ids:
            cross += 1
            cross_kinds[str(edge.get("type"))] = cross_kinds.get(str(edge.get("type")), 0) + 1
        if src in ids:
            linked[src] = linked.get(src, 0) + 1
        if dst in ids:
            linked[dst] = linked.get(dst, 0) + 1

    key = [name for name, _ in
           sorted(((n.get("name") or n["id"], linked.get(n["id"], 0)) for n in items),
                  key=lambda pair: -pair[1])[:5]]
    lonely = [n.get("name") or n["id"] for n in items if not linked.get(n["id"])]

    title = ASPECT_TITLES.get(aspect, aspect)
    types_text = ", ".join(f"{count} {name}" for name, count in
                           sorted(by_type.items(), key=lambda pair: -pair[1]))
    parts = [f"{title}: {len(items)} element(s) — {types_text}." if items
             else f"{title}: элементов нет — аспект в модели не представлен."]
    if key:
        parts.append("Ключевые элементы: " + ", ".join(key) + ".")
    if internal or cross:
        parts.append(f"Связей внутри аспекта — {internal}, с другими аспектами — {cross}"
                     + (f" ({', '.join(sorted(cross_kinds))})" if cross_kinds else "") + ".")
    if lonely:
        parts.append(f"Без связей: {len(lonely)} — {', '.join(lonely[:5])}"
                     + (" …" if len(lonely) > 5 else "") + ".")
    return {"aspect": aspect, "title": title, "count": len(items), "by_type": by_type,
            "key_elements": key, "internal_links": internal, "cross_links": cross,
            "cross_kinds": cross_kinds, "unlinked": lonely,
            "summary": " ".join(parts)}


def scenario_summary(graph: dict) -> list[dict]:
    """Сценарии использования, выведенные из процессов модели."""
    out = []
    for node in _nodes(graph):
        if node.get("type") not in ("Process", "Activity"):
            continue
        steps = [n.get("name") or n["id"] for n in _nodes(graph)
                 if n.get("parent") == node["id"] and n.get("type") in ("Activity", "Task")]
        out.append({"id": node["id"], "name": node.get("name") or node["id"],
                    "description": node.get("description") or "", "steps": steps})
    return out


def risk_summary(graph: dict) -> list[str]:
    """Ограничения и пробелы: то, что архитектору нужно видеть до приёмки."""
    out: list[str] = []
    try:
        from . import merge as merge_mod
        cov = merge_mod.coverage_report(graph)
    except Exception:  # noqa: BLE001
        cov = None
    nodes = _nodes(graph)
    ids = {n["id"] for n in nodes}
    linked: set[str] = set()
    for edge in (graph.get("edges") or []):
        if not edge:
            continue
        if edge.get("source") in ids:
            linked.add(edge["source"])
        if edge.get("target") in ids:
            linked.add(edge["target"])
    lonely = [n.get("name") or n["id"] for n in nodes if n["id"] not in linked and n.get("parent")]
    if lonely:
        out.append(f"Элементы без связей: {len(lonely)} — {', '.join(lonely[:6])}"
                   + (" …" if len(lonely) > 6 else ""))
    if cov:
        if cov["missing_levels"]:
            out.append("Не заполнены уровни C4: " + ", ".join(cov["missing_levels"]))
        if cov["missing_aspects"]:
            out.append("Не представлены аспекты: " + ", ".join(cov["missing_aspects"]))
    requirements = [n for n in nodes if n.get("type") in ("Requirement", "NFR", "SLO", "Constraint")]
    if requirements:
        out.append(f"Требований и ограничений в модели: {len(requirements)} — их покрытие "
                   "проверяется отдельной командой приёмки")
    return out
