"""Core reconstruction logic.

Shared by the local runner (in-process) and the sandbox agents (in-container).
It receives already-collected source files, builds the prompt messages, calls an
OpenAI-compatible LLM and returns the graph. No filesystem mounting is involved.

Robustness: LLM output is parsed defensively (fenced / wrapped / nested JSON),
the graph shape is validated, and one repair request is attempted before failing
with a snippet of the offending response.
"""
from __future__ import annotations

import json
import re

FileList = list[tuple[str, str]]

REPAIR_INSTRUCTION = (
    "Your previous answer was not a valid ArchIDE graph. Reply with ONLY a JSON "
    'object of the form {"nodes": [ ... ], "edges": [ ... ]} where `nodes` is a '
    "non-empty array. Every node needs `id` and `type`; every edge needs `source`, "
    "`target` and `type`. No prose, no markdown fences."
)


def build_messages(prompt: str, files: FileList) -> list[dict]:
    tree = "\n".join(f"- {name}" for name, _ in files)
    body = "\n\n".join(f"### FILE: {name}\n```\n{content}\n```" for name, content in files)
    user = (
        f"{prompt}\n\n"
        f"## Repository file tree ({len(files)} files)\n{tree}\n\n"
        f"## File contents\n{body}"
    )
    return [{"role": "user", "content": user}]


def _find_graph(obj) -> dict | None:
    """Locate a {nodes, edges} object anywhere in the parsed JSON."""
    if isinstance(obj, dict):
        if isinstance(obj.get("nodes"), list):
            return {"nodes": obj["nodes"], "edges": obj.get("edges", [])}
        for value in obj.values():
            found = _find_graph(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_graph(value)
            if found is not None:
                return found
    return None


def parse_graph(text: str) -> dict:
    """Extract and normalize a graph object from raw LLM text."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")

    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        found = _find_graph(data)
        if found is not None:
            return _normalize(found)

    if '"error"' in text or "'error'" in text:
        raise ValueError(f"LLM returned an error object: {text.strip()[:400]!r}")
    raise ValueError(f"could not parse a graph from the LLM response: {text.strip()[:400]!r}")


def _normalize(graph: dict) -> dict:
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("id")]
    edges = [
        e for e in graph.get("edges", [])
        if isinstance(e, dict) and e.get("source") and e.get("target") and e.get("type")
    ]
    return {"nodes": nodes, "edges": edges}


def _try_parse(text: str) -> tuple[dict | None, str | None]:
    try:
        graph = parse_graph(text)
    except ValueError as exc:
        return None, str(exc)
    if not graph["nodes"]:
        return None, "the model returned an empty graph (no `nodes`)"
    return graph, None


# Metamodel subset used for agent-side validation (mirrors backend/graph_schema.py).
NODE_TYPES = {
    "System", "Container", "Component", "Module", "ExternalSystem", "Actor",
    "Interface", "DataStore", "DataEntity",
}
EDGE_TYPES = {
    "contains", "depends_on", "calls", "publishes", "subscribes", "reads",
    "writes", "exposes", "consumes", "implements", "aggregates",
}


def validate_graph(graph: dict) -> list[str]:
    """Return a list of metamodel errors (empty list = valid)."""
    errors: list[str] = []
    nodes = graph.get("nodes") or []
    if not nodes:
        return ["`nodes` must be a non-empty array"]

    ids: set[str] = set()
    parents: dict[str, str | None] = {}
    for node in nodes:
        nid = node.get("id")
        if not nid:
            errors.append("node without `id`")
            continue
        if nid in ids:
            errors.append(f"duplicate node id: {nid}")
        ids.add(nid)
        if node.get("type") not in NODE_TYPES:
            errors.append(f"node {nid}: unknown type `{node.get('type')}`")
        parents[nid] = node.get("parent")

    for nid, parent in parents.items():
        if parent is not None and parent not in ids:
            errors.append(f"node {nid}: parent `{parent}` not found")

    roots = [nid for nid, parent in parents.items() if parent is None]
    if len(roots) != 1:
        errors.append(f"expected exactly one root `System`, found {len(roots)}")

    for edge in graph.get("edges") or []:
        if edge.get("source") not in ids:
            errors.append(f"edge source `{edge.get('source')}` not found")
        if edge.get("target") not in ids:
            errors.append(f"edge target `{edge.get('target')}` not found")
        if edge.get("type") not in EDGE_TYPES:
            errors.append(f"edge type `{edge.get('type')}` not allowed")
    return errors


def call_llm(llm: dict, messages: list[dict]) -> dict:
    """Call an OpenAI-compatible /chat/completions endpoint using only stdlib.

    Returns the assistant message as ``{"content": ..., "reasoning": ...}``.
    """
    import urllib.error
    import urllib.request

    url = str(llm.get("base_url", "")).rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": llm.get("model", ""),
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    if llm.get("api_key"):
        request.add_header("Authorization", "Bearer " + str(llm["api_key"]))

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=float(llm.get("timeout", 180))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM connection failed: {exc.reason}") from exc

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected LLM response: {json.dumps(data)[:500]}") from exc

    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    return {"content": message.get("content") or "", "reasoning": reasoning}


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-") or "item"


def mock_graph(repo_name: str, files: FileList) -> dict:
    """Deterministic graph from the file tree - lets the PoC run without an LLM."""
    from pathlib import Path

    name = repo_name or "app"
    nodes: list[dict] = [{
        "id": "sys://app",
        "type": "System",
        "name": name,
        "parent": None,
        "description": f"Root system reconstructed from {name} (mock mode).",
        "evidence": [],
    }]
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    file_to_component: dict[str, str] = {}

    for path, _ in files:
        parts = Path(path).parts
        top = parts[0] if len(parts) > 1 else "(root)"
        container_id = f"container://app/{_slug(top)}"
        if not any(n["id"] == container_id for n in nodes):
            nodes.append({
                "id": container_id,
                "type": "Container",
                "name": top,
                "parent": "sys://app",
                "description": f"Directory `{top}`.",
                "evidence": [top + "/"],
            })
        component_id = f"component://app/{_slug(path)}"
        nodes.append({
            "id": component_id,
            "type": "Component",
            "name": Path(path).name,
            "parent": container_id,
            "description": f"Source file `{path}`.",
            "evidence": [path],
        })
        file_to_component[path] = component_id

    stems = {Path(p).stem: cid for p, cid in file_to_component.items()}
    for path, content in files:
        src = file_to_component[path]
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ", "require(", "#include", "using ")):
                continue
            for stem, cid in stems.items():
                if stem and len(stem) > 2 and stem in stripped and cid != src:
                    key = (src, cid)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({
                            "source": src,
                            "target": cid,
                            "type": "depends_on",
                            "description": stripped[:80],
                            "evidence": [path],
                        })

    return {"nodes": nodes, "edges": edges}


def _validate_via_tool(url: str, graph: dict) -> list[str]:
    """Call the formal validation tool over HTTP; return the error list."""
    import urllib.request

    data = json.dumps({"graph": graph}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return list((payload.get("validation") or {}).get("errors") or [])


def _validate(llm: dict, graph: dict) -> list[str]:
    """Prefer the formal validation tool; fall back to the local validator."""
    url = llm.get("validate_url")
    if url:
        try:
            return _validate_via_tool(url, graph)
        except Exception:  # noqa: BLE001 - fall back to the local validator
            return validate_graph(graph)
    return validate_graph(graph)


def run(prompt: str, repo_name: str, files: FileList, llm: dict, on_event=None) -> dict:
    """Produce the graph from collected files, with agent-side validation.

    ``on_event(kind, text)`` (optional) receives live activity: ``status``,
    ``reasoning``, ``output``, ``log``, ``error``.
    """
    emit = on_event or (lambda *_a: None)

    if llm.get("mode") == "mock":
        emit("reasoning", "mock mode: building the graph from the file tree")
        graph = mock_graph(repo_name, files)
        emit("status", f"validated graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, ok")
        return graph

    max_repairs = int(llm.get("max_repairs", 3))
    messages = build_messages(prompt, files)
    emit("status", f"calling the LLM ({llm.get('model', '?')}); up to {max_repairs} repair attempt(s)")

    graph = None
    errors: list[str] = []
    content = ""
    for attempt in range(max_repairs + 1):
        message = call_llm(llm, messages)
        content = message.get("content") or ""
        if message.get("reasoning"):
            emit("reasoning", message["reasoning"])
        if content:
            emit("output", content)

        graph, error = _try_parse(content)
        errors = [error] if graph is None else _validate(llm, graph)
        if not errors:
            break

        emit("error", f"validation ({attempt + 1}/{max_repairs + 1}): " + "; ".join(errors[:6]))
        if attempt >= max_repairs:
            break
        emit("status", f"refining graph ({attempt + 1}/{max_repairs}) to fix {len(errors)} issue(s)")
        messages = messages + [
            {"role": "assistant", "content": content[:4000]},
            {
                "role": "user",
                "content": REPAIR_INSTRUCTION + "\nFix these issues:\n- " + "\n- ".join(errors[:12]),
            },
        ]

    if graph is None:
        raise ValueError(
            f"invalid architecture graph from the LLM: {errors[0] if errors else 'unknown'}. "
            f"Raw response snippet: {content[:600]!r}"
        )
    if errors:
        emit("error", "graph still has issues after repair: " + "; ".join(errors[:6]))
    else:
        emit("status", f"validated graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, ok")
    return graph
