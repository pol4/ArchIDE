"""Runtime state shared between the server and the runner."""
from __future__ import annotations


class CancelledError(Exception):
    """Raised when a run is cancelled by the user."""


# Base URL that sandbox containers use to reach this ArchIDE server
# (sandbox + LLM gateway). Set by the server at startup.
PUBLIC_BASE = ""
