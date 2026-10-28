"""Prompt builder for the architecture reconstruction agent.

The prompt encodes the ArchIDE metamodel subset so that any OpenAI-compatible
model (or CLI agent) can produce a valid graph, plus the context constraints and
(optionally) an output-path instruction for file-writing CLI agents.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are an architecture reconstruction agent for ArchIDE.

Your job: reconstruct the system architecture **strictly from the source code**.
Derive every node and edge from concrete code artifacts - modules, packages,
entry points, imports, class/function declarations, configuration and manifests.
Do NOT infer structure from prose or from directory names alone; documentation is
not provided and must not be invented.

## Model shape

Return a single JSON object with two arrays: `nodes` and `edges`.

Each NODE has:
  - id:          stable string id, e.g. "container://app/api" (scheme://path)
  - type:        one of System, Container, Component, Module, ExternalSystem,
                 Actor, Interface, DataStore, DataEntity
  - name:        short human name
  - parent:      id of the containing node, or null for the root System
  - description: one sentence about responsibility
  - evidence:    array of source file paths that justify this node

Each EDGE has:
  - source:      node id
  - target:      node id
  - type:        one of contains, depends_on, calls, publishes, subscribes,
                 reads, writes, exposes, consumes, implements, aggregates
  - description: optional one-line justification
  - evidence:    optional array of source file paths

## Rules

1. There is exactly ONE root node of type `System` with `parent: null`.
2. Containment (`parent`) forms a strict tree (C4-like levels):
   System -> Container -> Component -> Module.
3. Every non-root node has a `parent` that exists in `nodes`.
4. All edge `source`/`target` ids must exist in `nodes`.
5. Prefer few, meaningful nodes over exhaustive lists. Group by responsibility.
6. External systems, actors, data stores and interfaces are nodes too.
7. Every node should carry `evidence` (source file paths).
8. Output ONLY the JSON object. No prose, no markdown fences.
9. Base CONTAINERS on real runtime/deployable units (entry points, services,
   packages/workspaces; manifests such as package.json, pyproject.toml, go.mod,
   Dockerfile) - not on top-level folders alone.
10. Base COMPONENTS on cohesive code units that actually exist (modules, classes,
    packages) and are referenced by imports.
11. Derive EDGES from real imports / calls / configuration references between
    those units, not from their names.
12. `evidence` MUST list the actual source file paths that justify each node.
"""


def _constraints_block(constraints: dict | None) -> str:
    if not constraints:
        return ""
    max_chars = constraints.get("max_chars")
    compress_at = constraints.get("compress_at")
    strategy = constraints.get("strategy")
    files = constraints.get("files")
    lines = [
        "## Constraints",
        f"- Context budget: at most {max_chars} characters of source "
        f"(compression threshold {compress_at}, strategy `{strategy}`).",
        f"- The repository provided to you has already been reduced to fit this "
        f"budget; do not request additional files outside it.",
    ]
    if files is not None:
        lines.append(f"- Source files provided: {files}.")
    lines.append("- Stay within these limits; prefer summarizing over expanding.")
    return "\n".join(lines)


def _validation_block(max_repairs: int) -> str:
    return (
        "## Validation (required before returning)\n\n"
        "You MUST formally validate the graph before returning it and fix every error.\n\n"
        "Rules the graph must satisfy:\n"
        "- exactly one root node of type `System` with `parent: null`;\n"
        "- containment (`parent`) forms a tree: every non-root parent exists in `nodes`;\n"
        "- every edge `source`/`target` references an existing node id;\n"
        "- `type` is one of the allowed node/edge types listed above.\n\n"
        "A validation tool is available at the URL in the environment variable\n"
        "`ARCHIDE_VALIDATE_URL`. POST `{\"graph\": <your graph>}` to it; it returns\n"
        "`{\"validation\": {\"ok\": <bool>, \"errors\": [...]}}`, e.g.:\n\n"
        "    curl -s -X POST \"$ARCHIDE_VALIDATE_URL\" "
        "-H 'Content-Type: application/json' -d @graph.json\n\n"
        f"Repeat the loop validate -> fix the reported errors -> validate again, up to "
        f"{max_repairs} times, until `ok` is true or no further progress is possible."
    )


ASPECT_PROMPT = """You are extending an EXISTING architecture graph with the `{aspect}` aspect.

You are given the current graph and the repository sources. Add ONLY `{aspect}`
elements: new nodes of type {types}, plus the edges that connect them ({edges}).

## Hard rules
1. Return the COMPLETE updated graph (all existing nodes and edges unchanged) as
   a single JSON object {{"nodes": [...], "edges": [...]}}.
2. Every NEW node must have one of these types: {types}.
3. Every NEW node's `parent` must be an id that already exists in the given graph.
   Do NOT re-parent, modify or delete existing nodes. Anchor each new node to the
   existing node the evidence belongs to; allowed anchor kinds: {anchors}
4. Do not duplicate ids; do not add nodes of other aspects.
5. Every new node/edge must carry `evidence` (source file paths).
6. Output ONLY the JSON object. No prose, no markdown fences.
{extra}

## Current graph
<<<GRAPH
{graph_json}
GRAPH>>>

Derive `{aspect}` strictly from the sources that follow."""


def _sources_block(files, max_chars: int = 60000) -> str:
    if not files:
        return ""
    parts = ["\n## Relevant sources for this aspect", ""]
    total = 0
    for path, content in files:
        block = f"### FILE: {path}\n```\n{content}\n```"
        if total and total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


# Extra fields each notation needs from the model (Ф2.14).
ASPECT_EXTRA_FIELDS = {
    "behavior": '- Every `Transition` node MUST also carry `"from"` and `"to"` — the state names\n'
                '  (or ids) it connects — in addition to its `name` (e.g. name "start_flow", from "Created",\n'
                '  to "Running").',
    "process": '- When the sources name teams/roles/systems that perform the steps, add `Participant`\n'
               '  nodes (one per lane) and set each `Task`/`Activity` `parent` to its participant.',
    "data": '- When the sources imply a relationship multiplicity (a collection, a foreign key, an\n'
            '  optional field), add `"cardinality"` to the relationship edge, e.g. "1..N" or "0..1".',
    "interface": '- For request/response payloads add `Message`/`SchemaRef` nodes anchored to the interface.',
}


def build_aspect_prompt(repo_name: str, aspect: str, node_types, edge_types, anchors,
                        graph_json: str, max_repairs: int = 3, relevant_sources=None) -> str:
    """Prompt for one sequential aspect pass (Ф3.5).

    `relevant_sources` (Ф3.6) carries only the files that matter for this aspect,
    so the prompt does not repeat the whole repository on every pass.
    """
    type_list = ", ".join(sorted(node_types))
    edge_list = ", ".join(sorted(edge_types)) or "owns, anchored_to"
    anchor_list = "; ".join(f"{t} -> {', '.join(sorted(a))}" for t, a in sorted(anchors.items())) or "any existing node"
    body = ASPECT_PROMPT.format(aspect=aspect, types=type_list, edges=edge_list,
                                anchors=anchor_list, graph_json=graph_json,
                                extra=ASPECT_EXTRA_FIELDS.get(aspect, ""))
    return f"{body}\n\n## Target repository\nName: {repo_name}\n" + _sources_block(relevant_sources)


RECONCILE_PROMPT = """You are repairing an EXISTING architecture graph.

Some elements have no relationships and/or no `evidence`. Using the repository
sources already provided, return the COMPLETE graph with:

1. `evidence` filled in for the listed elements (real source file paths), and
2. relationships added ONLY where the sources justify them (every new edge needs
   `source`, `target` and `type`, and both ids must already exist).

## Hard rules
- Do NOT remove, rename or re-parent any existing element; keep every id.
- Do NOT add new nodes.
- Output ONLY the JSON object {{"nodes": [...], "edges": [...]}}.

## Reconciliation
Elements without `evidence`: {no_evidence}
Elements without any relationship: {no_edges}

## Current graph
<<<GRAPH
{graph_json}
GRAPH>>>
"""


def build_reconcile_prompt(graph: dict, gaps: dict) -> str:
    """Prompt for the reconciliation/correction step (Ф3.9)."""
    from .aspect_pass import graph_summary

    return RECONCILE_PROMPT.format(
        no_evidence=", ".join(gaps.get("no_evidence") or []) or "—",
        no_edges=", ".join(gaps.get("no_edges") or []) or "—",
        graph_json=graph_summary(graph),
    )


DESIGN_PROMPT = """You are DESIGNING the architecture of a new system from its requirements.

There is no source code yet: the material below is the specification (ТЗ,
requirements, notes). Derive the architecture the system SHOULD have and return
it as a single JSON object {{"nodes": [...], "edges": [...]}}.

Rules (same as for reconstruction):
1. exactly one root `System` node (parent null); every other node has a `parent`
   that exists in the graph and satisfies the anchor rules of its type;
2. node ids are stable URIs (`component://...`, `iface://...`, `data://...`, …);
3. every node carries `description` and `evidence` — for a design the evidence is
   the document/section the element comes from;
4. edges use the metamodel edge types; no dangling endpoints;
5. cover what the specification asks for: structure, interfaces, data,
   behaviour (states), processes, access roles, deployment and quality needs.

Repair policy: up to {max_repairs} attempts. On each attempt output ONLY the JSON
object. There is no output file to write.

## Specification
"""


def build_design_prompt(repo_name: str, constraints, max_repairs: int) -> str:
    """Prompt for a documentation-only base pass (Ф8.4, design mode)."""
    budget = constraints.get("max_chars", 400000) if isinstance(constraints, dict) else 400000
    return (DESIGN_PROMPT.format(max_repairs=max_repairs)
            + f"\nTarget system name: {repo_name}\nBudget: {budget} chars of material.\n")


def build_prompt(
    repo_name: str,
    constraints: dict | None = None,
    output_path: str | None = None,
    max_repairs: int = 3,
    source: str = "code",
) -> str:
    parts = [SYSTEM_PROMPT, f"\n## Target repository\nName: {repo_name}"]
    block = _constraints_block(constraints)
    if block:
        parts.append("\n" + block)
    parts.append("\n" + _validation_block(max_repairs))
    if source == "docs":
        parts.append(
            "\nThe material provided is the project DOCUMENTATION. Reconstruct the "
            "architecture as described in the documentation (use its stated components, "
            "services and relationships)."
        )
    else:
        parts.append(
            "\nThe material provided is SOURCE CODE. Base the graph strictly on it."
        )
    parts.append("\nReconstruct the architecture graph for the repository whose files follow.")
    if output_path:
        parts.append(
            f"\nWrite the resulting JSON object to the file `{output_path}` "
            f"(create parent directories if needed). Also print nothing else."
        )
    else:
        parts.append("Respond with the JSON object only.")
    return "\n".join(parts)
