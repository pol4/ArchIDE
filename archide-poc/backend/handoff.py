"""Handoff bundle: everything a coding agent needs, in one archive (Ф8.1).

Collects what the architect scenario produces into a single deterministic
``.tar.gz``:

* ``manifest.json`` — target model (name + hash), node, generated-at, contents,
  and whether the traceability markers verify;
* ``workpackage.json`` — the slice plus acceptance criteria for the target node;
* ``artifacts/`` — the generated specifications and skeletons;
* ``markers.json`` — signed traceability markers the agent must embed;
* ``README.md`` — instructions for the receiving agent.

The archive is reproducible: member order, timestamps and ownership are fixed,
so the same model yields byte-identical bundles.
"""
from __future__ import annotations

import gzip
import io
import json
import tarfile

from . import generate as generate_mod
from . import markers as markers_mod
from . import model_store

ARTIFACT_FILES = {
    "openapi": "openapi.yaml",
    "ddl": "schema.sql",
    "jsonschema": "schema.json",
    "skeleton": "skeleton.py",
}

README = """# Handoff package — {model}

This archive was produced by ArchIDE for the node `{node}` of the model `{model}`
(hash `{hash}`).

## What is inside

- `workpackage.json` — the slice of the architecture to implement plus the
  acceptance criteria;
- `artifacts/` — generated specifications and skeletons;
- `markers.json` — signed traceability markers;
- `manifest.json` — provenance of this bundle.

## What to do

1. Implement the work package in the target repository (keep the interfaces and
   data shapes from `artifacts/`).
2. **Embed every marker from `markers.json` verbatim** as a comment in the file
   that implements it — this is how the result is traced back to the model.
3. Deliver the result as a repository/branch; the architect reconstructs it and
   diffs it against this model (`Diff vs result model` in the UI).
"""


def _member(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = 0
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(payload))


def build_bundle(cfg: dict, name: str, graph: dict, node: str = "", kinds=None) -> tuple[bytes, str]:
    """Return ``(tar.gz bytes, filename)`` for the handoff of one node.

    An empty ``node`` means "the whole model": the System root is used, so the
    package covers the architecture rather than one component.
    """
    if not node:
        nodes = graph.get("nodes") or []
        node = next((n.get("id") for n in nodes if n.get("type") == "System"), "")
        if not node:
            node = next((n.get("id") for n in nodes if n.get("id")), "")
    package = generate_mod.workpackage(graph, node)
    artifacts = generate_mod.generate(graph, kinds)
    trace = generate_mod.trace_markers(cfg, graph, node)
    marker_list = trace.get("markers") or []
    verified = all(markers_mod.verify_trace(cfg, m) for m in marker_list) if marker_list else True

    manifest = {
        "model": name,
        "hash": model_store.model_hash(graph),
        "node": node or None,
        "contents": ["manifest.json", "workpackage.json", "artifacts/", "markers.json", "README.md"],
        "counts": {
            "slice_nodes": len((package.get("slice") or {}).get("nodes") or []),
            "criteria": len(package.get("criteria") or []),
            "artifacts": len(artifacts.get("artifacts") or {}),
            "markers": len(marker_list),
        },
        "markers_verified": bool(verified),
    }

    buffer = io.BytesIO()
    # gzip stores a timestamp in its header: fix it so bundles are reproducible
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        _member(tar, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode())
        _member(tar, "workpackage.json", json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True).encode())
        _member(tar, "markers.json", json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True).encode())
        _member(tar, "README.md", README.format(model=name, node=node or "—", hash=manifest["hash"]).encode())
        for kind, body in sorted((artifacts.get("artifacts") or {}).items()):
            filename = ARTIFACT_FILES.get(kind, f"{kind}.txt")
            _member(tar, f"artifacts/{filename}", str(body).encode())

    safe = "".join(c for c in name if c.isalnum() or c in "._-") or "model"
    return buffer.getvalue(), f"{safe}-handoff.tar.gz"
