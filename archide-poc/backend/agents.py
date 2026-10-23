"""Agent registry.

Agents that can run in the Docker sandbox. ``kind`` selects the container
entrypoint/protocol:

  builtin  - our stdlib reconstruction agent (agent/sandbox.py)
  cli      - an external CLI agent (Kilo Code CLI), wrapped by agents/kilo
"""
from __future__ import annotations

AGENTS: list[dict] = [
    {
        "id": "builtin",
        "name": "Built-in Reconstructor",
        "kind": "builtin",
        "image": "archide-agent:latest",
        "description": "Stdlib agent: reconstructs the graph via an OpenAI-compatible LLM through the gateway.",
    },
    {
        "id": "kilocode",
        "name": "Kilo Code CLI",
        "kind": "cli",
        "image": "archide-kilo:latest",
        "description": "Runs the Kilo Code CLI in the sandbox; reaches the LLM through the ArchIDE gateway.",
    },
]

_BY_ID = {agent["id"]: agent for agent in AGENTS}


def list_agents() -> list[dict]:
    return AGENTS


def get_agent(agent_id: str) -> dict | None:
    return _BY_ID.get(agent_id)
