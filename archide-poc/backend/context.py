"""Host-side source collection.

Reads a bounded set of text files from a repository so they can be uploaded to
the agent service over the network (no bind mounts required).

By default documentation is excluded so the architecture graph is reconstructed
from **source code**, not from prose.
"""
from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "venv", ".venv", "__pycache__",
    ".idea", ".vscode", "target", "vendor", ".mypy_cache", ".pytest_cache",
    "coverage", ".next", ".nuxt", "bin", "obj",
}
DOC_SEGMENTS = {"docs", "doc", "documentation"}
DOC_EXTS = {".md", ".rst", ".adoc"}
TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".c", ".h", ".cpp", ".hpp", ".kt", ".swift", ".scala", ".sql",
    ".md", ".yaml", ".yml", ".json", ".toml", ".proto", ".sh", ".dockerfile",
}
MAX_FILES = 1000
MAX_FILE_BYTES = 200000
MAX_TOTAL_BYTES = 4_000_000


def _is_doc(path: Path) -> bool:
    return path.suffix.lower() in DOC_EXTS or any(seg in DOC_SEGMENTS for seg in path.parts)


def collect_files(repo: Path, include_docs: bool = False, docs_only: bool = False) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    total = 0
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        is_doc = _is_doc(path)
        if docs_only and not is_doc:
            continue
        if not docs_only and not include_docs and is_doc:
            continue
        if path.suffix.lower() not in TEXT_EXT and path.name.lower() != "dockerfile":
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(content) > MAX_FILE_BYTES:
            content = content[:MAX_FILE_BYTES] + "\n... [truncated]"
        total += len(content)
        files.append((str(path.relative_to(repo)), content))
        if len(files) >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            break
    return files
