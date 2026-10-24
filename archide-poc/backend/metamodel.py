"""ArchIDE metamodel registry — single source of truth.

Defines the closed vocabularies (node/edge types), the aspect schemas
(which types belong to which concern), anchor rules and referential actions.
`backend/graph_schema.py` and the agents validate against this registry.
"""
from __future__ import annotations

import json
import re

from . import config as config_mod

NODE_TYPES = {
    # structure (C4 backbone)
    "System", "Container", "Component", "Module", "Actor", "ExternalSystem",
    # interface / contracts
    "Interface", "Operation", "Message", "SchemaRef",
    # data
    "DataStore", "DataEntity", "Attribute",
    # behavior
    "StateMachine", "State", "Transition", "Event",
    # process
    "Process", "Activity", "Task", "Gateway", "Participant",
    # access / security
    "Role", "Permission", "Policy", "Principal", "Resource",
    # deployment
    "Node", "Environment", "Runtime", "InfraResource",
    # quality
    "NFR", "SLO", "Constraint",
    # cross-cutting
    "AggregationInterface", "Capability", "Decision", "Requirement", "Artifact", "Agent",
}

EDGE_TYPES = {
    # containment
    "contains",
    # interaction
    "depends_on", "calls", "publishes", "subscribes", "reads", "writes",
    # contracts
    "exposes", "consumes", "implements", "realizes", "aggregates",
    # traceability
    "satisfies", "derived_from", "constrains", "verifies", "conflicts_with",
    # realization
    "implemented_by", "tested_by", "deployed_as", "generated_from",
    # anchors / aspect links
    "owns", "anchored_to", "governs", "allowed", "emits", "flows_to", "has", "deployed_on",
    # governance
    "owned_by", "decided_by", "assigned_to",
}

# Aspect schemas: which node/edge types belong to each concern.
ASPECTS: dict[str, dict[str, set[str]]] = {
    "structure": {
        "node_types": {"System", "Container", "Component", "Module", "Actor", "ExternalSystem"},
        "edge_types": {"contains", "depends_on", "calls", "publishes", "subscribes", "reads", "writes"},
    },
    "interface": {
        "node_types": {"Interface", "Operation", "Message", "SchemaRef"},
        "edge_types": {"exposes", "consumes", "implements", "realizes", "has"},
    },
    "data": {
        "node_types": {"DataStore", "DataEntity", "Attribute"},
        "edge_types": {"reads", "writes", "flows_to"},
    },
    "behavior": {
        "node_types": {"StateMachine", "State", "Transition", "Event"},
        "edge_types": {"owns", "emits", "anchored_to"},
    },
    "process": {
        "node_types": {"Process", "Activity", "Task", "Gateway", "Participant"},
        "edge_types": {"owns", "flows_to", "anchored_to"},
    },
    "access": {
        "node_types": {"Role", "Permission", "Policy", "Principal", "Resource"},
        "edge_types": {"allowed", "governs", "owns"},
    },
    "deployment": {
        "node_types": {"Node", "Environment", "Runtime", "InfraResource"},
        "edge_types": {"deployed_on", "contains", "anchored_to"},
    },
    "quality": {
        "node_types": {"NFR", "SLO", "Constraint"},
        "edge_types": {"constrains", "verifies", "anchored_to"},
    },
    "aggregation": {
        "node_types": {"AggregationInterface"},
        "edge_types": {"aggregates"},
    },
    "crosscutting": {
        "node_types": {"Capability", "Decision", "Requirement", "Artifact", "Agent"},
        "edge_types": {"satisfies", "derived_from", "conflicts_with", "implemented_by",
                       "tested_by", "deployed_as", "generated_from", "owned_by",
                       "decided_by", "assigned_to", "verifies"},
    },
}

_BUILTIN_ASPECTS = set(ASPECTS)
_CUSTOM_PATH = config_mod.DATA_DIR / "aspects.json"


def _load_custom() -> dict:
    if not _CUSTOM_PATH.exists():
        return {}
    try:
        data = json.loads(_CUSTOM_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _apply_custom(name: str, spec: dict) -> None:
    node_types = set(spec.get("node_types") or [])
    edge_types = set(spec.get("edge_types") or [])
    ASPECTS[name] = {"node_types": node_types, "edge_types": edge_types}
    NODE_TYPES.update(node_types)
    EDGE_TYPES.update(edge_types)


for _name, _spec in _load_custom().items():
    if isinstance(_spec, dict):
        _apply_custom(_name, _spec)


def register_aspect(name, node_types, edge_types) -> dict:
    """Register a custom aspect (Ф4.8): new grouping and/or new node/edge types."""
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name or "")).strip("-")
    if not clean:
        raise ValueError("aspect name is required")
    _apply_custom(clean, {"node_types": node_types, "edge_types": edge_types})
    custom = {
        key: {"node_types": sorted(spec["node_types"]), "edge_types": sorted(spec["edge_types"])}
        for key, spec in ASPECTS.items() if key not in _BUILTIN_ASPECTS
    }
    try:
        config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CUSTOM_PATH.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return registry()


# Anchor rules: which backbone types an aspect node may attach to.
ANCHORS: dict[str, set[str]] = {
    "Interface": {"Component", "Container", "System"},
    "Operation": {"Interface"},
    "Message": {"Interface"},
    "SchemaRef": {"Interface", "Operation", "DataEntity"},
    "DataEntity": {"Component", "Container", "System"},
    "DataStore": {"Component", "Container", "System"},
    "Attribute": {"DataEntity"},
    "StateMachine": {"Component", "Container"},
    "State": {"StateMachine"},
    "Transition": {"StateMachine"},
    "Process": {"Container", "System", "Component"},
    "Activity": {"Process"},
    "Task": {"Process", "Activity"},
    "Role": {"System", "Container"},
    "Permission": {"Role"},
    "Policy": {"System", "Container"},
    "Node": {"System"},
    "Environment": {"System"},
    "NFR": {"System", "Container", "Component"},
    "SLO": {"System", "Container", "Component"},
    "AggregationInterface": {"System", "Container", "Component"},
}

# Referential actions per edge type (applied on delete/move — see ROADMAP Ф4.2).
REFERENTIAL_ACTIONS: dict[str, str] = {
    "contains": "cascade",
    "depends_on": "restrict",
    "calls": "restrict",
    "reads": "nullify",
    "writes": "nullify",
    "exposes": "cascade",
    "consumes": "restrict",
    "implements": "restrict",
    "satisfies": "nullify",
    "verifies": "nullify",
    "anchored_to": "cascade",
    "owns": "cascade",
    "governs": "restrict",
    "allowed": "restrict",
    "implemented_by": "cascade",
    "tested_by": "cascade",
}


def registry() -> dict:
    """Serializable view of the registry (for the API)."""
    return {
        "node_types": sorted(NODE_TYPES),
        "edge_types": sorted(EDGE_TYPES),
        "aspects": {
            name: {"node_types": sorted(spec["node_types"]), "edge_types": sorted(spec["edge_types"])}
            for name, spec in ASPECTS.items()
        },
        "anchors": {k: sorted(v) for k, v in ANCHORS.items()},
        "referential_actions": dict(sorted(REFERENTIAL_ACTIONS.items())),
    }
