"""External code-generation handoff (Ф8.2).

Takes a work package + generated artifacts and asks the language model for a
**file map**, then materialises it as a new repository on disk. The call goes
through the same LLM configuration (and, for sandboxed runs, the same gateway
lease) as every other agent, so no new trust boundary is introduced.

The delivered repository is what the architect then validates against the model
(see `graphdiff`), closing the loop of the two scenarios in docs/ARCHITECT_GUIDE.md.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

from . import config as config_mod

BASE = pathlib.Path(__file__).resolve().parent.parent
MAX_FILES = 60

CODEGEN_PROMPT = """You are a code-generation agent. Implement the slice described below.

## Work package
{package}

## Specifications (generated artifacts)
{artifacts}

## Traceability markers to embed
Insert these markers verbatim as comments in the files that implement them:
{markers}

## Rules
1. Answer with ONLY a JSON object of the form
   {{"files": {{"relative/path.ext": "<file content>", ...}}}}
2. Paths are relative (no absolute paths, no `..`); keep the project small but
   coherent: sources, package/build file, README, and a test where it makes sense.
3. Do not invent endpoints or entities outside the work package.
4. No binaries; text only.
"""


def build_prompt(package: dict, artifacts: dict, markers: list) -> str:
    return CODEGEN_PROMPT.format(
        package=json.dumps(package, ensure_ascii=False, indent=2)[:20000],
        artifacts=json.dumps(artifacts, ensure_ascii=False, indent=2)[:20000],
        markers=json.dumps(markers or [], ensure_ascii=False)[:4000],
    )


def _file_map(text: str) -> dict:
    """Pull ``{"files": {...}}`` out of a possibly noisy model answer."""
    candidates = []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text or "", re.S)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text or "")
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, dict):
            return {str(k): str(v) for k, v in files.items()}
    raise ValueError(f"the agent did not return a file map: {(text or '')[:300]!r}")


def _safe_rel(path: str) -> str | None:
    if not path or path.startswith("/") or path.startswith("\\") or ":" in path.split("/")[0]:
        return None
    parts = [p for p in pathlib.PurePosixPath(path).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


REPAIR_PROMPT = """Your previous answer could not be used: {reason}

Return ONLY the JSON object {{"files": {{"relative/path.ext": "<content>"}}}}.
Keep the project small: at most {max_files} text files, no binaries, relative paths."""


def generate(cfg: dict, name: str, package: dict, artifacts: dict, markers: list) -> dict:
    """Run the code-generation agent and write the repository it returns.

    Robust by design (Ф8.2b): an unparseable answer is retried with a repair
    prompt, oversized files are skipped with a reason, and the whole answer is
    bounded by a file-count and total-size budget.
    """
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from agent.core import call_llm  # shared agent plumbing (same LLM config)

    limits = dict(cfg.get("codegen", {}) or {})
    max_files = int(limits.get("max_files", MAX_FILES) or MAX_FILES)
    max_file_chars = int(limits.get("max_file_chars", 60000) or 60000)
    max_total_chars = int(limits.get("max_total_chars", 400000) or 400000)
    max_retries = int(limits.get("max_retries", 2) or 0)

    llm = dict(cfg.get("llm", {}) or {})
    if llm.get("mode") == "mock":
        raise RuntimeError("code generation needs a live LLM (set llm.mode=live)")

    prompt = build_prompt(package, artifacts, markers)
    messages = [{"role": "user", "content": prompt}]
    files: dict = {}
    retries = 0
    last_error = "the agent did not return a file map"
    for attempt in range(max_retries + 1):
        answer = call_llm(llm, messages)
        content = answer.get("content") or ""
        try:
            files = _file_map(content)
            break
        except ValueError as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                raise
            retries += 1
            messages = messages + [
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": REPAIR_PROMPT.format(reason=last_error[:300], max_files=max_files)},
            ]

    root = pathlib.Path(config_mod.DATA_DIR) / "generated" / _slug(name)
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[dict] = []
    total = 0
    for path, content in list(files.items()):
        if len(written) >= max_files:
            skipped.append({"path": path, "reason": f"file budget reached ({max_files})"})
            continue
        rel = _safe_rel(path)
        if rel is None:
            skipped.append({"path": path, "reason": "unsafe path"})
            continue
        if len(content) > max_file_chars:
            skipped.append({"path": path, "reason": f"file too large ({len(content)} > {max_file_chars} chars)"})
            continue
        if total + len(content) > max_total_chars:
            skipped.append({"path": path, "reason": f"total size budget reached ({max_total_chars} chars)"})
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
        total += len(content)

    commit = _commit(root)
    return {"repo": str(root), "files": written, "skipped": skipped,
            "count": len(written), "commit": commit, "retries": retries,
            "total_chars": total, "mode": "llm"}


TASK_FILE = "TASK.md"


def _task_text(name: str, package: dict, artifacts: dict, markers: list) -> str:
    return (f"# Code generation task — {name}\n\n"
            "## Work package\n" + json.dumps(package, ensure_ascii=False, indent=2)[:20000] +
            "\n\n## Specifications\n" + json.dumps(artifacts, ensure_ascii=False, indent=2)[:20000] +
            "\n\n## Traceability markers (embed them verbatim in the code)\n"
            + json.dumps(markers or [], ensure_ascii=False)[:6000] + "\n")


def generate_with_cli(cfg: dict, name: str, package: dict, artifacts: dict, markers: list) -> dict:
    """Hand the package to an external coding agent (Ф8.2).

    The agent runs as a plain command (a CLI or a container wrapper) inside a
    fresh working directory, receives the task as a file, and everything it
    writes there becomes the delivered repository. stdout/stderr, the exit code
    and the commit are recorded, so a failed or empty run is visible rather than
    silently accepted.
    """
    import shlex
    import subprocess

    cli = dict(cfg.get("codegen_cli", {}) or {})
    command = str(cli.get("command") or "").strip()
    if not command:
        raise RuntimeError("codegen_cli.command is not configured")
    timeout = float(cli.get("timeout", 1800) or 1800)

    root = pathlib.Path(config_mod.DATA_DIR) / "generated" / _slug(name)
    work = root / "repo"
    work.mkdir(parents=True, exist_ok=True)
    prompt_file = root / TASK_FILE
    prompt_file.write_text(_task_text(name, package, artifacts, markers), encoding="utf-8")

    argv = [part.format(workdir=str(work), prompt_file=str(prompt_file), package=str(prompt_file))
            for part in shlex.split(command)]
    # least privilege: the external agent is a third-party command chosen by
    # config, so it gets a minimal environment, not the server's whole one
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR", "TERM")
           if key in os.environ}
    env.update({k: str(v) for k, v in (cli.get("env") or {}).items()})
    env.update({
        "ARCHIDE_CLI_WORK": str(work),
        "ARCHIDE_CLI_PROMPT": str(prompt_file),
        "ARCHIDE_PACKAGE": str(prompt_file),
    })
    log_path = root / "cli.log"
    exit_code = None
    error = None
    with log_path.open("ab") as log:
        log.write(("$ " + " ".join(argv) + "\n").encode())
        try:
            completed = subprocess.run(argv, cwd=str(work), env=env, timeout=timeout,
                                       stdout=log, stderr=subprocess.STDOUT)
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            error = f"the agent did not finish within {int(timeout)}s"
        except OSError as exc:
            error = f"cannot run the agent: {exc}"
    if exit_code not in (0, None) and not error:
        error = f"the agent exited with code {exit_code}"

    written = sorted(str(p.relative_to(work)) for p in work.rglob("*")
                     if p.is_file() and ".git" not in p.parts)
    commit = _commit(work) if written else None
    return {"repo": str(work), "files": written, "count": len(written), "commit": commit,
            "mode": "cli", "exit_code": exit_code, "log": str(log_path), "error": error,
            "task": str(prompt_file)}


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(text)).strip("-") or "generated"


def _commit(root: pathlib.Path) -> str | None:
    """Best-effort git commit so the delivered repository has a revision."""
    try:
        def run(*args):
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)

        if not (root / ".git").exists():
            run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=archide", "-c", "user.email=archide@local", "commit", "-q", "-m", "generated from model")
        out = run("rev-parse", "--short", "HEAD")
        return (out.stdout or "").strip() or None
    except Exception:  # noqa: BLE001 - git is optional
        return None
