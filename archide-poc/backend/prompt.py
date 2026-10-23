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
