"""Starter architectures (Ф8.5) — a new model in a couple of steps.

A template is a small, always-valid starter graph: the C4 spine plus the typical
aspects of a common system shape (web service, service + database, CLI). It is
pure data — no LLM involved — so "design from scratch" starts from a valid model
the architect extends, instead of a set of hand-written ops.
"""


def _slug(name: str) -> str:
    # preserve case: the id namespace is the model name, and the UI derives ids
    # from the same name without case folding (see projects.slug / app.js)
    keep = [c if c.isalnum() else "-" for c in str(name or "system")]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "system"


def _node(nid: str, ntype: str, name: str, parent, evidence: list[str]) -> dict:
    return {"id": nid, "type": ntype, "name": name, "parent": parent, "evidence": list(evidence)}


def _edge(src: str, dst: str, etype: str) -> dict:
    return {"source": src, "target": dst, "type": etype}


def _web_service(name: str) -> dict:
    s = _slug(name)
    sys_id = f"system://{s}"
    web, api, core = f"container://{s}/web", f"iface://{s}/http", f"component://{s}/api"
    ui, store, sm = f"component://{s}/ui", f"store://{s}/session", f"sm://{s}/request"
    role, node = f"role://{s}/user", f"node://{s}/prod"
    ev = ["template:web_service"]
    nodes = [
        _node(sys_id, "System", name, None, ev),
        _node(web, "Container", "Web App", sys_id, ev),
        _node(core, "Component", "API", web, ev),
        _node(ui, "Component", "Web UI", web, ev),
        _node(api, "Interface", "HTTP API", core, ev),
        _node(f"{api}/op-login", "Operation", "POST /login", api, ev),
        _node(store, "DataStore", "Session store", web, ev),
        _node(sm, "StateMachine", "Request lifecycle", core, ev),
        _node(f"{sm}/idle", "State", "idle", sm, ev),
        _node(f"{sm}/serving", "State", "serving", sm, ev),
        _node(role, "Role", "user", sys_id, ev),
        _node(node, "Node", "prod", sys_id, ev),
        _node(f"nfr://{s}/latency", "NFR", "p95 latency < 200ms", sys_id, ev),
    ]
    edges = [
        _edge(sys_id, web, "contains"),
        _edge(web, core, "contains"),
        _edge(web, ui, "contains"),
        _edge(core, api, "exposes"),
        _edge(ui, api, "calls"),
        _edge(core, store, "writes"),
        _edge(core, store, "reads"),
        _edge(role, api, "allowed"),
        _edge(web, node, "deployed_on"),
    ]
    return {"nodes": nodes, "edges": edges}


def _service_db(name: str) -> dict:
    s = _slug(name)
    sys_id = f"system://{s}"
    service, core, workers = f"container://{s}/service", f"component://{s}/core", f"component://{s}/workers"
    db, store, entity = f"container://{s}/db", f"store://{s}/primary", f"data://{s}/order"
    api, sm = f"iface://{s}/http", f"sm://{s}/order"
    role, node = f"role://{s}/customer", f"node://{s}/prod"
    ev = ["template:service_db"]
    nodes = [
        _node(sys_id, "System", name, None, ev),
        _node(service, "Container", "Service", sys_id, ev),
        _node(core, "Component", "Core", service, ev),
        _node(workers, "Component", "Workers", service, ev),
        _node(api, "Interface", "HTTP API", core, ev),
        _node(f"{api}/op-create", "Operation", "POST /orders", api, ev),
        _node(db, "Container", "Database", sys_id, ev),
        _node(store, "DataStore", "Primary DB", db, ev),
        _node(entity, "DataEntity", "Order", db, ev),
        _node(f"{entity}/status", "Attribute", "status", entity, ev),
        _node(sm, "StateMachine", "Order lifecycle", core, ev),
        _node(f"{sm}/created", "State", "created", sm, ev),
        _node(f"{sm}/paid", "State", "paid", sm, ev),
        _node(role, "Role", "customer", sys_id, ev),
        _node(node, "Node", "prod", sys_id, ev),
    ]
    edges = [
        _edge(sys_id, service, "contains"),
        _edge(sys_id, db, "contains"),
        _edge(service, core, "contains"),
        _edge(service, workers, "contains"),
        _edge(core, api, "exposes"),
        _edge(core, store, "writes"),
        _edge(core, store, "reads"),
        _edge(workers, entity, "writes"),
        _edge(core, entity, "reads"),
        _edge(role, api, "allowed"),
        _edge(service, node, "deployed_on"),
    ]
    return {"nodes": nodes, "edges": edges}


def _cli(name: str) -> dict:
    s = _slug(name)
    sys_id = f"system://{s}"
    cli, parser, commands = f"container://{s}/cli", f"component://{s}/parser", f"component://{s}/commands"
    iface, sm = f"iface://{s}/stdin", f"sm://{s}/run"
    role, node = f"role://{s}/operator", f"node://{s}/workstation"
    ev = ["template:cli"]
    nodes = [
        _node(sys_id, "System", name, None, ev),
        _node(cli, "Container", "CLI", sys_id, ev),
        _node(parser, "Component", "Argument parser", cli, ev),
        _node(commands, "Component", "Commands", cli, ev),
        _node(iface, "Interface", "Command line", cli, ev),
        _node(f"{iface}/op-run", "Operation", "run <cmd>", iface, ev),
        _node(f"module://{s}/io", "Module", "io helpers", commands, ev),
        _node(sm, "StateMachine", "Run lifecycle", parser, ev),
        _node(f"{sm}/parsing", "State", "parsing", sm, ev),
        _node(f"{sm}/executing", "State", "executing", sm, ev),
        _node(role, "Role", "operator", sys_id, ev),
        _node(node, "Node", "workstation", sys_id, ev),
    ]
    edges = [
        _edge(sys_id, cli, "contains"),
        _edge(cli, parser, "contains"),
        _edge(cli, commands, "contains"),
        _edge(cli, iface, "exposes"),
        _edge(parser, commands, "calls"),
        _edge(role, iface, "allowed"),
        _edge(cli, node, "deployed_on"),
    ]
    return {"nodes": nodes, "edges": edges}


def _blank(name: str) -> dict:
    s = _slug(name)
    ev = ["template:blank"]
    return {"nodes": [_node(f"system://{s}", "System", name, None, ev)], "edges": []}


TEMPLATES: dict[str, dict] = {
    "web_service": {
        "title": "Web service",
        "description": "Web app + API component, HTTP interface, session store, request lifecycle.",
        "build": _web_service,
    },
    "service_db": {
        "title": "Service + database",
        "description": "Service container (core + workers), database container with a store and an entity.",
        "build": _service_db,
    },
    "cli": {
        "title": "CLI utility",
        "description": "Command-line container, argument parser and commands, run lifecycle.",
        "build": _cli,
    },
    "blank": {
        "title": "Blank",
        "description": "An empty model with a single System root to build on.",
        "build": _blank,
    },
}


def catalog() -> list[dict]:
    """The template list for the UI: id, title, description and a size hint."""
    out = []
    for tid, spec in TEMPLATES.items():
        graph = spec["build"](spec["title"])
        out.append({
            "id": tid,
            "title": spec["title"],
            "description": spec["description"],
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
        })
    return out


def scaffold(template_id: str, name: str) -> dict | None:
    """Build a starter graph for `name`; `None` when the template is unknown."""
    spec = TEMPLATES.get(str(template_id or ""))
    if spec is None:
        return None
    graph = spec["build"](name)
    graph["name"] = name
    graph["constraints"] = []
    return graph
