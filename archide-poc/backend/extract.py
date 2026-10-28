"""Aspect-aware extraction from source files (Ф3.1).

Deterministic (heuristic) extraction of aspect elements for several concerns:

* interface — interfaces (function groups) and operations (function declarations),
* data — data entities (class/struct/type declarations),
* behavior — state machines (enums) and events (emit/publish calls),
* access — roles, policies, principals and permissions,
* deployment — Dockerfile units, the container runtime and volumes,
* quality — numeric constraints and non-functional markers,
* process — process/pipeline and task/step functions.

Every element is anchored to an existing node that satisfies the metamodel anchor
rules (``metamodel.ANCHORS``), so merging extraction results into a graph can
never introduce a dangling parent or break a valid model.
"""
from __future__ import annotations

import re
from pathlib import Path

from .metamodel import ANCHORS

_FUNC = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function|func|fn)\s+([A-Za-z_]\w*)\s*[(<]")
_CLASS = re.compile(r"^\s*(?:export\s+)?(?:@dataclass\s*)?(?:class|struct|interface|type)\s+([A-Z]\w*)")
_ENUM = re.compile(r"^\s*(?:export\s+)?enum\s+([A-Z]\w*)")

_ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:=|:)", re.M)
_KEY = re.compile(r"^\s*[\"']([A-Za-z_]\w*)[\"']\s*:", re.M)
_DEF_NAME = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)
_CONSTRAINT = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*(?:MAX|MIN|TIMEOUT|QUOTA|LIMIT|BUDGET|CAP|TTL|CHARS|SIZE)[A-Z0-9_]*)\s*(?:=|:)",
    re.M,
)
_EVENT = re.compile(r"(?:emit|publish|dispatch|add_event|on_event)\s*\(\s*[\"']([A-Za-z0-9_.:-]+)[\"']")
_VOLUME = re.compile(r"^\s*VOLUME\s+(.+?)\s*$", re.M)
_DOCKER = re.compile(r"\bdocker\s+(?:run|volume|build|create|compose)\b")

# Keyword -> aspect node type for identifiers (assignments) and function names.
_ACCESS_KEYWORDS = (
    ("permission", "Permission"), ("policy", "Policy"),
    ("principal", "Principal"), ("role", "Role"),
)
_PROCESS_KEYWORDS = (
    ("process", "Process"), ("pipeline", "Process"), ("workflow", "Process"),
    ("workpackage", "Process"), ("task", "Task"), ("step", "Task"), ("stage", "Task"),
)
_QUALITY_KEYWORDS = (
    ("nfr", "NFR"), ("slo", "SLO"), ("latency", "NFR"), ("throughput", "NFR"),
    ("timeout", "Constraint"), ("quota", "Constraint"), ("limit", "Constraint"),
    ("ttl", "Constraint"), ("budget", "Constraint"), ("chars", "Constraint"),
)


def _tokens(name: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", name.lower()) if token]


def _classify_constant(name: str, table) -> str | None:
    """Uppercase constants only (``ROLE_ADMIN``, ``POLICY_STRICT``)."""
    if not name.isupper():
        return None
    tokens = _tokens(name)
    for keyword, node_type in table:
        if keyword in tokens:
            return node_type
    return None


def _classify_key(name: str, table) -> str | None:
    """Exact singular/plural configuration keys (``"principal"``, ``"role"``)."""
    lowered = name.lower()
    for keyword, node_type in table:
        if lowered in (keyword, keyword + "s"):
            return node_type
    return None


def _classify_token(name: str, table) -> str | None:
    """Any token match (``quota_per_lease`` -> ``quota``)."""
    tokens = _tokens(name)
    for keyword, node_type in table:
        if keyword in tokens:
            return node_type
    return None


def _classify_substr(name: str, table) -> str | None:
    """Descriptive function names (``require_permission``, ``process_run``)."""
    lowered = name.lower()
    for keyword, node_type in table:
        if keyword in lowered:
            return node_type
    return None

# Edge type used to attach an extracted element to its anchor. Every value must
# be an edge type allowed by the element's aspect in `metamodel.ASPECTS`
# (guarded by a test, so extraction can never emit an unsanctioned edge).
_ASPECT_EDGE = {
    "Interface": "exposes", "Operation": "has",
    "StateMachine": "owns", "Event": "owns",
    "Role": "owns", "Permission": "owns", "Policy": "owns", "Principal": "owns",
    "Node": "anchored_to", "Runtime": "anchored_to", "InfraResource": "anchored_to",
    "Process": "owns", "Task": "owns", "Activity": "owns",
    "NFR": "anchored_to", "SLO": "anchored_to", "Constraint": "anchored_to",
}

# Stable id prefixes (kept for backwards compatibility with earlier extraction).
_ID_PREFIX = {
    "Interface": "iface", "Operation": "operation", "DataEntity": "data", "StateMachine": "sm",
    "Event": "event", "Role": "role", "Permission": "permission", "Policy": "policy",
    "Principal": "principal", "Node": "node", "Runtime": "runtime", "InfraResource": "infra",
    "Process": "process", "Task": "task", "Activity": "activity",
    "NFR": "nfr", "SLO": "slo", "Constraint": "constraint",
}
_RANK = {"Component": 0, "Module": 1, "Container": 2, "System": 3}


def _slug(text) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(text)).strip("-") or "item"


def _owners(graph: dict | None):
    """Index existing owner nodes by evidence + the System root.

    Returns ``(exact, dirs, base, system_id)`` where ``exact`` maps an evidence
    path to its node id, ``dirs`` is a list of ``(dir_prefix, id)`` sorted by
    decreasing prefix length, ``base`` maps a file name to the set of ids that
    claim it, and ``system_id`` is the graph's root System node. When several
    owners claim the same path the most specific one wins (Component over its
    Container over the System), so aspects attach to the tightest owner.
    """
    exact: dict[str, tuple[int, str]] = {}
    dirs: dict[str, tuple[int, str]] = {}
    base_raw: dict[str, dict[str, int]] = {}
    system_id: str | None = None
    for node in (graph or {}).get("nodes", []) or []:
        node_id = node.get("id")
        node_type = node.get("type")
        if not node_id:
            continue
        if node_type == "System" and system_id is None:
            system_id = node_id
        if node_type not in _RANK:
            continue
        rank = _RANK[node_type]
        for evidence in node.get("evidence") or []:
            evidence = str(evidence)
            if evidence not in exact or rank < exact[evidence][0]:
                exact[evidence] = (rank, node_id)
            parent = Path(evidence).parent.as_posix()
            if parent and parent != ".":
                prefix = parent.rstrip("/") + "/"
                if prefix not in dirs or rank < dirs[prefix][0]:
                    dirs[prefix] = (rank, node_id)
            name = Path(evidence).name
            claims = base_raw.setdefault(name, {})
            if node_id not in claims or rank < claims[node_id]:
                claims[node_id] = rank
    dirs_list = sorted(((k, v[1]) for k, v in dirs.items()), key=lambda item: len(item[0]), reverse=True)
    base: dict[str, set[str]] = {}
    for name, claims in base_raw.items():
        best = min(claims.values())
        base[name] = {node_id for node_id, rank in claims.items() if rank == best}
    return {k: v[1] for k, v in exact.items()}, dirs_list, base, system_id


def _owner_for(path: str, index) -> str | None:
    """Find an existing node that owns ``path``; ``None`` when there is none.

    Resolution order: exact evidence match, longest directory prefix, a unique
    file-name match, then the System root. Nothing is ever fabricated here, so
    extracted elements can never dangle off a missing parent.
    """
    exact, dirs, base, system_id = index
    if path in exact:
        return exact[path]
    for prefix, node_id in dirs:
        if path.startswith(prefix):
            return node_id
    candidates = base.get(Path(path).name)
    if candidates and len(candidates) == 1:
        return next(iter(candidates))
    return system_id


def extract_aspects(files, graph: dict | None = None, prefix: str = "app") -> dict:
    strict = graph is not None
    graph_nodes = (graph or {}).get("nodes", []) or []
    types = {n["id"]: n.get("type") for n in graph_nodes}
    parents = {n["id"]: n.get("parent") for n in graph_nodes}
    system_id = next((n["id"] for n in graph_nodes if n.get("type") == "System"), None)
    index = _owners(graph)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()
    dropped: list[dict] = []

    def placeholder(path: str) -> str:
        pid = f"component://{prefix}/{_slug(path)}"
        if pid not in seen:
            seen.add(pid)
            nodes.append({
                "id": pid, "type": "Component", "name": Path(path).name, "parent": None,
                "evidence": [path], "description": "Placeholder owner for extracted aspects (heuristic).",
            })
        return pid

    def owner_for(path: str) -> str | None:
        return _owner_for(path, index) if strict else placeholder(path)

    def anchor(node_type: str, owner: str | None) -> str | None:
        """Nearest owner (or ancestor) allowed by the metamodel anchor rules.

        The walk stops on a repeat, so a cyclic ``parent`` chain (a malformed or
        hostile graph posted to the API) cannot make this loop forever.
        """
        if not strict:
            return owner if owner is not None else system_id
        allowed = ANCHORS.get(node_type)
        current = owner if owner is not None else system_id
        if not allowed:
            return current
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            if types.get(current) in allowed:
                return current
            current = parents.get(current)
        return None

    def add(node_type: str, name: str, owner: str | None, path: str, edge_key: str | None = None,
            parent: str | None = None, nid: str | None = None) -> str | None:
        node_id = nid or f"{_ID_PREFIX.get(node_type, node_type.lower())}://{prefix}/{_slug(path)}/{_slug(name)}"
        if node_id in seen:
            return node_id
        parent_id = parent if parent is not None else anchor(node_type, owner)
        if not parent_id:
            dropped.append({
                "type": node_type, "name": str(name), "path": path,
                "reason": "no existing node satisfies the anchor rules for this element type",
            })
            return None
        seen.add(node_id)
        nodes.append({
            "id": node_id, "type": node_type, "name": str(name), "parent": parent_id,
            "evidence": [path], "description": f"Extracted {node_type} (heuristic).",
        })
        edge_type = edge_key if edge_key is not None else _ASPECT_EDGE.get(node_type)
        if edge_type:
            edges.append({"source": parent_id, "target": node_id, "type": edge_type})
        return node_id

    def declare(pattern, classifier, content: str, owner: str | None, path: str,
                role_ids: list[str]) -> None:
        """Emit access/quality elements for `name = ...` and `"name": ...` forms."""
        for match in pattern.finditer(content):
            name = match.group(1)
            access_type = classifier(name, _ACCESS_KEYWORDS)
            if access_type:
                role_id = add(access_type, name, owner, path)
                if access_type == "Role" and role_id:
                    role_ids.append(role_id)
            quality_type = _classify_token(name, _QUALITY_KEYWORDS)
            if quality_type:
                add(quality_type, name, owner, path)

    for path, content in files:
        owner = owner_for(path)
        lines = content.splitlines()
        functions = [m.group(1) for m in (_FUNC.match(line) for line in lines) if m]
        if functions:
            iface = add("Interface", f"{Path(path).stem} API", owner, path,
                        nid=f"iface://{prefix}/{_slug(path)}")
            for fn in functions:
                add("Operation", fn, owner, path, parent=iface,
                    nid=f"operation://{prefix}/{_slug(path)}/{_slug(fn)}")
        for line in lines:
            cls = _CLASS.match(line)
            if cls:
                add("DataEntity", cls.group(1), owner, path,
                    nid=f"data://{prefix}/{_slug(path)}/{_slug(cls.group(1))}")
            enum = _ENUM.match(line)
            if enum:
                add("StateMachine", enum.group(1), owner, path,
                    nid=f"sm://{prefix}/{_slug(path)}/{_slug(enum.group(1))}")
        for event in dict.fromkeys(_EVENT.findall(content)):
            add("Event", event, owner, path)
        role_ids: list[str] = []
        declare(_ASSIGN, _classify_constant, content, owner, path, role_ids)
        declare(_KEY, _classify_key, content, owner, path, role_ids)
        for match in _CONSTRAINT.finditer(content):
            add("Constraint", match.group(1), owner, path)
        for match in _DEF_NAME.finditer(content):
            name = match.group(1)
            if _classify_substr(name, _ACCESS_KEYWORDS) == "Permission" and role_ids:
                add("Permission", name, owner, path, parent=role_ids[0])
            process_type = _classify_substr(name, _PROCESS_KEYWORDS)
            if process_type:
                add(process_type, name, owner, path)
        if Path(path).name.startswith("Dockerfile"):
            unit = Path(path).parent.name or Path(path).stem
            add("Node", unit, owner, path)
            add("Runtime", "docker", owner, path)
            for match in _VOLUME.finditer(content):
                add("InfraResource", match.group(1), owner, path)
        elif _DOCKER.search(content):
            add("Runtime", "docker", owner, path)
    return {"nodes": nodes, "edges": edges, "dropped": dropped}


def augment_graph(graph: dict, files) -> tuple[dict, dict]:
    """Merge extracted aspect nodes/edges into an existing graph.

    Only extraction output is sanitized: extracted nodes are anchored to nodes
    that already exist and extracted edges with a missing endpoint are skipped,
    so extraction can never make a valid graph invalid. Pre-existing graph data
    (nodes and edges) is preserved verbatim — a dangling base edge must remain
    visible to validation instead of being silently deleted.

    Returns ``(graph, report)`` where ``report["dropped"]`` lists the elements
    the extractor recognised but could not anchor.
    """
    extra = extract_aspects(files, graph=graph)
    nodes = [dict(n) for n in (graph.get("nodes", []) or [])]
    edges = list(graph.get("edges", []) or [])
    have = {n.get("id") for n in nodes}
    have_edges = {(e.get("source"), e.get("target"), e.get("type")) for e in edges}
    for node in extra["nodes"]:
        if node["id"] not in have:
            nodes.append(node)
            have.add(node["id"])
    for edge in extra["edges"]:
        key = (edge["source"], edge["target"], edge["type"])
        if key in have_edges or edge["source"] not in have or edge["target"] not in have:
            continue
        edges.append(edge)
        have_edges.add(key)
    return {"nodes": nodes, "edges": edges}, {"dropped": extra.get("dropped", [])}
