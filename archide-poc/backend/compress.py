"""Context compression.

Keeps the reconstruction prompt within a configured character budget. When the
collected sources exceed the threshold (``compress_at * max_chars``), they are
compressed in stages:

  1. structural extraction - keep imports / declarations / comments, drop bodies
  2. per-file truncation   - head-limit each file to a fair share of the budget
  3. priority dropping     - drop the least relevant whole files

Strategies:
  ``extract``  - stages 1-3 (default; best for architecture reconstruction)
  ``truncate`` - stages 2-3
  ``none``     - stage 3 only (drop whole files, no per-file rewriting)
"""
from __future__ import annotations

from pathlib import Path

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".c", ".h", ".cpp", ".hpp", ".kt", ".swift", ".scala", ".sh",
}
DECL_PREFIXES = (
    "import ", "from ", "using ", "#include", "require(", "export ", "package ",
    "class ", "def ", "async def ", "function ", "func ", "interface ", "type ",
    "struct ", "enum ", "trait ", "public ", "private ", "protected ", "impl ",
    "fn ", "module ", "namespace ", "@", "const ", "var ", "let ", "static ",
    "abstract ", "final ", "sealed ", "record ",
)
COMMENT_PREFIXES = ("#", "//", "/*", "*", "///", "<!--", '"""', "'''")

MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "go.mod",
    "cargo.toml", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml", "requirements.txt", "pom.xml",
    "build.gradle", "build.gradle.kts", "makefile", "gemfile", "composer.json",
}
DOC_SEGMENTS = {"docs", "doc", "documentation"}
DOC_EXTS = {".md", ".rst", ".adoc", ".txt"}


def _extract(path: str, content: str) -> str:
    if Path(path).suffix.lower() not in CODE_EXTS:
        return content
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(DECL_PREFIXES) or stripped.startswith(COMMENT_PREFIXES):
            out.append(line.rstrip())
    return "\n".join(out) if out else content


def _truncate(content: str, budget: int) -> str:
    if len(content) <= budget:
        return content
    head = content[:budget]
    cut = head.rfind("\n")
    if cut > budget * 0.5:
        head = head[:cut]
    return head + "\n... [compressed: truncated]"


def _priority(path: str) -> int:
    """Lower = kept longer. Source code and manifests first, docs last."""
    p = path.lower().replace("\\", "/")
    parts = Path(p).parts
    name = Path(p).name
    if name in MANIFEST_NAMES:
        return 0
    if any(seg in DOC_SEGMENTS for seg in parts) or Path(p).suffix.lower() in DOC_EXTS:
        return 3
    if Path(p).suffix.lower() in CODE_EXTS:
        return 0
    return 1


def compress(
    files: list[tuple[str, str]],
    max_chars: int,
    compress_at: float = 0.8,
    strategy: str = "extract",
) -> tuple[list[tuple[str, str]], dict]:
    original = sum(len(c) for _, c in files)
    report = {
        "strategy": strategy,
        "max_chars": max_chars,
        "compress_at": compress_at,
        "original_chars": original,
        "final_chars": original,
        "compressed": False,
        "extracted": 0,
        "truncated": 0,
        "dropped": [],
        "files_in": len(files),
        "files_out": len(files),
        "ratio": 1.0,
    }

    if max_chars <= 0 or original <= compress_at * max_chars:
        return list(files), report

    work = list(files)

    if strategy == "extract":
        rebuilt = []
        for path, content in work:
            extracted = _extract(path, content)
            if extracted != content:
                report["extracted"] += 1
            rebuilt.append((path, extracted))
        work = rebuilt

    if strategy in ("extract", "truncate"):
        budget = max(500, max_chars // max(1, len(work)))
        rebuilt = []
        for path, content in work:
            truncated = _truncate(content, budget)
            if truncated != content:
                report["truncated"] += 1
            rebuilt.append((path, truncated))
        work = rebuilt

    # stage 3: drop the least relevant whole files until we fit the hard cap
    work.sort(key=lambda item: (_priority(item[0]), item[0]))
    kept: list[tuple[str, str]] = []
    used = 0
    for path, content in work:
        if used + len(content) > max_chars and kept:
            report["dropped"].append(path)
            continue
        kept.append((path, content))
        used += len(content)

    report.update(
        compressed=True,
        final_chars=used,
        files_out=len(kept),
        ratio=round(used / original, 3) if original else 1.0,
    )
    return kept, report
