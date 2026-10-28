"""Repository resolution and agent execution.

Pipeline:
  repo address -> collect + compress sources -> build prompt -> run agent -> graph

Agents run in the Docker sandbox using a **network job protocol** (no bind
mounts): the host serves the repo tarball, prompt and constraints from the
sandbox endpoints, the container streams activity events back, and uploads the
resulting graph. All agents reach the LLM through the ArchIDE LLM gateway.

When a ``run`` object is provided, the pipeline emits live events and honours
cancellation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from . import agents, config as config_mod, gateway, runtime, sandbox
from .compress import compress
from .context import collect_files
from .merge import merge_graphs
from .prompt import build_aspect_prompt, build_design_prompt, build_prompt
from .runtime import CancelledError

BASE = Path(__file__).resolve().parent.parent
_URL_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")
CLI_OUTPUT = "/work/archide/graph.json"


def model_name_for(repo: str) -> str:
    """Derive a loadable model name from a repository address or path.

    Strips a trailing ``.git`` (``.../ArchIDE.git`` -> ``ArchIDE``) so the saved
    model can be loaded by its plain project name in the UI.
    """
    name = Path(str(repo or "").rstrip("/")).name
    if name.endswith(".git"):
        name = name[:-4]
    return name or "model"


def _local_validate_url() -> str:
    """Formal-validator URL the in-process local runner must use.

    Prefers the server's loopback base: ``runtime.PUBLIC_BASE`` may point at
    ``host.docker.internal``, which only resolves inside a container, so using
    it from the host would fail and silently degrade to the weak local validator.
    """
    base = runtime.LOCAL_BASE or runtime.PUBLIC_BASE
    return base.rstrip("/") + "/api/validate" if base else ""


def resolve_repo(repo: str) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    repo = (repo or "").strip()
    if not repo:
        raise ValueError("repository address is empty")

    is_url = repo.startswith(_URL_PREFIXES) or repo.endswith(".git")
    if is_url:
        tmp = tempfile.TemporaryDirectory(prefix="archide-repo-")
        name = model_name_for(repo)
        dest = Path(tmp.name) / (name if name != "model" else "repo")
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", repo, str(dest)],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            tmp.cleanup()
            raise RuntimeError("git clone timed out after 600s")
        if proc.returncode != 0:
            tmp.cleanup()
            raise RuntimeError(f"git clone failed: {proc.stderr.strip()[-500:]}")
        return dest, tmp

    path = Path(repo).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"local path not found: {repo}")
    if not path.is_dir():
        raise NotADirectoryError(f"not a directory: {repo}")
    return path, None


def _constraints(ctx_cfg: dict, files: int) -> dict:
    return {
        "max_chars": int(ctx_cfg.get("max_chars", 400000)),
        "compress_at": float(ctx_cfg.get("compress_at", 0.8)),
        "strategy": str(ctx_cfg.get("strategy", "extract")),
        "files": files,
    }


def _run_aspect_passes(agent, repo_path, code_files, graph, ctx_cfg, llm, runner, cfg, run, emit,
                       max_repairs, output_path, session=None):
    """Reconstruct each remaining aspect as its own small, checked step (Ф3.5)."""
    from .aspect_pass import enforce_aspect, graph_summary, select_files
    from .graph_schema import validate_graph
    from .metamodel import ANCHORS, ASPECTS

    max_nodes = int(ctx_cfg.get("aspect_max_nodes", 0) or 0)
    skip_existing = bool(ctx_cfg.get("aspect_skip_existing", True))
    for aspect in ctx_cfg.get("aspect_list") or []:
        spec = ASPECTS.get(aspect)
        if not spec:
            emit("error", f"aspect pass `{aspect}` skipped: unknown aspect")
            continue
        # Ф3.7: an aspect the graph already carries is not rebuilt
        existing = sum(1 for n in graph.get("nodes") or [] if n.get("type") in set(spec["node_types"]))
        if skip_existing and existing >= int(ctx_cfg.get("aspect_skip_min", 1) or 1):
            emit("status", f"aspect `{aspect}`: already covered ({existing} element(s)), skipped")
            continue
        emit("status", f"aspect pass `{aspect}`: reconstructing…")
        anchors = {t: sorted(ANCHORS.get(t) or []) for t in spec["node_types"] if ANCHORS.get(t)}
        # Ф3.6: hand the pass only the relevant sources and the relevant part of
        # the graph (its own elements + the kinds it may anchor to)
        relevant = select_files(aspect, code_files, int(ctx_cfg.get("max_chars", 400000) or 400000))
        emit("status", f"aspect `{aspect}`: {len(relevant)} relevant source file(s) selected")
        prompt = build_aspect_prompt(repo_path.name, aspect, spec["node_types"],
                                     spec.get("edge_types") or [], anchors,
                                     graph_summary(graph, aspect, ASPECTS), max_repairs,
                                     relevant_sources=relevant)
        try:
            candidate, _meta = _run_pass(
                agent, f"aspect:{aspect}", repo_path.name, code_files,
                _constraints(ctx_cfg, len(code_files)), max_repairs, None, llm, runner, cfg, run, emit,
                prompt=prompt, session=session,
            )
        except Exception as exc:  # noqa: BLE001 - a failed aspect must not kill the run
            emit("error", f"aspect pass `{aspect}` failed: {exc}")
            continue
        merged, report = enforce_aspect(graph, candidate, aspect, ASPECTS, max_nodes=max_nodes)
        validation = validate_graph(merged)
        if not validation["ok"]:
            emit("error", f"aspect pass `{aspect}` rejected (invalid graph): "
                          + "; ".join(validation["errors"][:4]))
            continue
        graph = merged
        emit("status", f"aspect `{aspect}`: +{len(report['added_nodes'])} nodes, "
                       f"+{len(report['added_edges'])} edges")
        if report["skipped"]:
            emit("error", f"aspect `{aspect}` skipped {len(report['skipped'])} element(s): "
                          + "; ".join(report["skipped"][:4]))
        if report.get("capped"):
            emit("error", f"aspect `{aspect}`: {len(report['capped'])} element(s) dropped by the "
                          f"growth cap ({max_nodes})")
    return graph


def _run_reconcile_fix(session, graph: dict, emit) -> dict:
    """Ф3.9: ask the agent to fill missing evidence/relationships, merge safely."""
    from .aspect_pass import apply_correction, find_gaps
    from .graph_schema import validate_graph
    from .prompt import build_reconcile_prompt

    gaps = find_gaps(graph)
    if not gaps["no_evidence"] and not gaps["no_edges"]:
        return graph
    if session is None:
        emit("status", "reconciliation fix: no agent session available (CLI agent), report only")
        return graph
    emit("status", f"reconciliation fix: {len(gaps['no_evidence'])} element(s) without evidence, "
                   f"{len(gaps['no_edges'])} without relationships — asking the agent")
    try:
        candidate = session.ask(build_reconcile_prompt(graph, gaps), {})
    except Exception as exc:  # noqa: BLE001 - a failed fix must not kill the run
        emit("error", f"reconciliation fix failed: {exc}")
        return graph
    fixed, report = apply_correction(graph, candidate)
    validation = validate_graph(fixed)
    if not validation["ok"]:
        emit("error", "reconciliation fix rejected (invalid graph): "
                      + "; ".join(validation["errors"][:4]))
        return graph
    if report["filled"] or report["added_edges"]:
        emit("status", f"reconciliation fix: +{len(report['filled'])} evidence, "
                       f"+{len(report['added_edges'])} relationship(s) (validated)")
    if report["ignored_nodes"]:
        emit("error", f"reconciliation fix: {len(report['ignored_nodes'])} new node(s) ignored "
                      "(corrections cannot add elements)")
    if report["ignored_edges"]:
        emit("error", f"reconciliation fix: {len(report['ignored_edges'])} relationship(s) ignored")
    return fixed


def _report_reconciliation(graph: dict, emit) -> None:
    """Ф3.9: point out elements that ended a run without relationships/evidence."""
    from .evaluate import aspect_metrics

    metrics = aspect_metrics(graph)
    orphans = sum(m["orphans"] for m in metrics.values())
    no_evidence = sum(m["without_evidence"] for m in metrics.values())
    if not orphans and not no_evidence:
        emit("status", "reconciliation: every element has a relationship and evidence")
        return
    worst = sorted(((a, m["orphans"], m["without_evidence"]) for a, m in metrics.items()
                    if m["orphans"] or m["without_evidence"]),
                   key=lambda item: -(item[1] + item[2]))[:3]
    sample = "; ".join(f"`{a}`: {o} orphan(s), {e} without evidence" for a, o, e in worst)
    emit("error", f"reconciliation: {orphans} element(s) without relationships, "
                  f"{no_evidence} without evidence — {sample}")


def _prepare(files, ctx_cfg: dict):
    base = _constraints(ctx_cfg, 0)
    return compress(files, base["max_chars"], base["compress_at"], base["strategy"])


def _run_pass(agent, source, repo_name, files, constraints, max_repairs, output_path, llm, runner, cfg, run, emit,
              prompt=None, session=None, include_files=False):
    prompt = prompt or build_prompt(repo_name, constraints, output_path, max_repairs, source)
    if session is not None:
        # one agent session for the whole run: ask it instead of starting a new
        # one (Ф3.10); `include_files` marks the request that carries the sources
        emit("status", f"{source}: request sent to the agent session")
        graph = session.ask(prompt, constraints, include_files=include_files)
        emit("status", f"{source}: graph received from the agent session")
        return graph, {"container": session.name, "session": session.session_id, "limits": session.limits}
    # no session: CLI agents run as a one-shot sandbox job
    return _run_docker(agent, prompt, constraints, repo_name, files, cfg, run, emit)


def reconstruct(repo: str, agent_id: str, cfg: dict, run=None, options: dict | None = None) -> tuple[dict, dict]:
    emit = run.add_event if run else (lambda *_a: None)
    emit("status", f"run started for `{repo}`")

    agent = agents.get_agent(agent_id)
    if not agent:
        raise ValueError(f"unknown agent: {agent_id}")

    session: DockerSession | None = None
    emit("status", "resolving repository (cloning if it is a URL)…")
    repo_path, tmp = resolve_repo(repo)
    try:
        emit("status", f"repository ready: {repo_path.name}")
        ctx_cfg = dict(cfg.get("context", {}) or {})
        options = options or {}
        if options.get("aspect_list") is not None and "aspect_list" in options:
            # an explicit empty list means "base pass only" (fast acceptance runs)
            ctx_cfg["aspect_list"] = list(options["aspect_list"] or [])
        agent_cfg = cfg.get("agent", {}) or {}
        max_repairs = int(agent_cfg.get("max_repairs", 3))
        compare_docs = bool(ctx_cfg.get("compare_docs", True))
        runner = agent_cfg.get("runner", "docker")
        output_path = CLI_OUTPUT if agent["kind"] == "cli" else None

        llm = dict(cfg.get("llm", {}))
        llm["max_repairs"] = max_repairs

        # ---- pass 1: reconstruct from source code (or design from documents)
        emit("status", "collecting source files…")
        raw_code_files = collect_files(repo_path, include_docs=False)
        design_mode = False
        if not raw_code_files and ctx_cfg.get("design_from_docs", True):
            design_docs = collect_files(repo_path, docs_only=True)
            if design_docs:
                design_mode = True
                raw_code_files = design_docs
                emit("status", f"no source code: designing the architecture from "
                               f"{len(design_docs)} document(s)")
        if not raw_code_files:
            raise RuntimeError("no readable source files or documents found in the repository")
        if not design_mode:
            emit("status", f"collected {len(raw_code_files)} source files; compressing context…")
        code_files, code_report = _prepare(raw_code_files, ctx_cfg)
        emit(
            "status",
            f"pass 1/2 - {'design from documents' if design_mode else 'source code'}: "
            f"{code_report['files_out']} files, "
            f"{code_report['original_chars']}->{code_report['final_chars']} chars",
        )
        if runner != "docker" and agent["kind"] != "builtin":
            raise ValueError(f"agent `{agent['id']}` requires the docker runner")
        # one agent session per material set (Ф3.10): the sources go once, every
        # later pass is a short message in the same conversation
        session = _open_session(agent, repo_path.name, code_files, cfg, run, emit, runner)

        # Ф3.7: resume — start from a saved model instead of reconstructing the base
        graph = None
        meta: dict = {}
        resume_model = str(options.get("model") or "").strip()
        if resume_model and resume_model != "repo":
            from . import model_store
            graph = model_store.load_model(config_mod.MODELS_DIR, resume_model)
            if graph is None:
                raise ValueError(f"unknown model to resume from: {resume_model}")
            emit("status", f"resuming from the saved model `{resume_model}`: "
                           f"{len(graph.get('nodes') or [])} nodes, base pass skipped")
            meta = {"resumed_from": resume_model}
        resumed = graph is not None

        if not resumed:
            base_prompt = build_design_prompt(repo_path.name, _constraints(ctx_cfg, len(code_files)),
                                          max_repairs) if design_mode else None
            graph, meta = _run_pass(
                agent, "code", repo_path.name, code_files, _constraints(ctx_cfg, len(code_files)),
                max_repairs, output_path, llm, runner, cfg, run, emit, session=session, include_files=True,
                prompt=base_prompt,
            )

        if not resumed and ctx_cfg.get("extract_aspects", True):
            from .extract import augment_graph
            # Extraction runs on the full collected sources (not the compressed
            # context) so a tight context budget cannot drop aspect elements.
            graph, extract_report = augment_graph(graph, raw_code_files)
            emit("status", f"aspect-aware extraction: {len(graph['nodes'])} nodes")
            dropped = extract_report.get("dropped") or []
            if dropped:
                sample = ", ".join(f"{d['type']}({d['name']})" for d in dropped[:5])
                emit("error", f"aspect extraction skipped {len(dropped)} element(s) with no valid "
                              f"anchor: {sample}")

        # ---- pass 1b: sequential, agent-verified aspect reconstruction (Ф3.5)
        if ctx_cfg.get("aspect_passes", True) and llm.get("mode") != "mock":
            graph = _run_aspect_passes(agent, repo_path, code_files, graph, ctx_cfg, llm, runner,
                                       cfg, run, emit, max_repairs, output_path, session=session)

        # ---- Ф3.9: reconciliation — let the agent fill gaps, then report
        if ctx_cfg.get("reconcile_fix", True):
            graph = _run_reconcile_fix(session, graph, emit)
        if ctx_cfg.get("reconcile_report", True):
            _report_reconciliation(graph, emit)

        # ---- pass 2: reconstruct from documentation and compare
        meta["docs_pass"] = False
        if design_mode:
            emit("status", "design mode: the documentation already shaped the base pass; "
                           "skipping the comparison pass")
            compare_docs = False
        if compare_docs:
            emit("status", "collecting documentation…")
            docs_files = collect_files(repo_path, docs_only=True)
            if docs_files:
                docs_files, docs_report = _prepare(docs_files, ctx_cfg)
                emit(
                    "status",
                    f"pass 2/2 - documentation: {docs_report['files_out']} files; comparing",
                )
                # the docs pass uses a different material set, so it gets its
                # own session (one container / conversation for that pass)
                docs_session = _open_session(agent, repo_path.name, docs_files, cfg, run, emit, runner)
                try:
                    docs_graph, _ = _run_pass(
                        agent, "docs", repo_path.name, docs_files, _constraints(ctx_cfg, len(docs_files)),
                        max_repairs, output_path, llm, runner, cfg, run, emit,
                        session=docs_session, include_files=True,
                    )
                finally:
                    if docs_session is not None:
                        try:
                            docs_session.close()
                        except Exception:  # noqa: BLE001 - closing is best-effort
                            pass
                graph = merge_graphs(graph, docs_graph)
                deviations = sum(1 for n in graph["nodes"] if n.get("deviation"))
                emit(
                    "status",
                    f"merged code + docs: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
                    f"{deviations} node deviation(s) flagged",
                )
                meta["docs_pass"] = True
            else:
                emit("status", "no documentation found; comparison skipped")

        meta.update({
            "runner": runner,
            "agent": agent_id,
            "repo_path": str(repo_path),
            "files": len(code_files),
            "context": code_report,
        })
        return graph, meta
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
        if tmp is not None:
            tmp.cleanup()


def _image(agent: dict, cfg: dict) -> str:
    overrides = (cfg.get("agent", {}) or {}).get("images", {}) or {}
    return overrides.get(agent["id"]) or agent["image"]


def _limit_args(limits: dict) -> list[list[str]]:
    """Translate sandbox limits into `docker run` flags (Ф1.2)."""
    args: list[list[str]] = []
    if limits.get("memory"):
        args.append(["--memory", str(limits["memory"])])
    if limits.get("cpus"):
        args.append(["--cpus", str(limits["cpus"])])
    if limits.get("pids"):
        args.append(["--pids-limit", str(int(limits["pids"]))])
    if limits.get("read_only"):
        args.append(["--read-only"])
        args.append(["--tmpfs", "/tmp"])
    if limits.get("cap_drop_all"):
        args.append(["--cap-drop", "ALL"])
    if limits.get("no_new_privileges"):
        args.append(["--security-opt", "no-new-privileges"])
    return args


class LocalSession:
    """In-process continuous conversation for one run (Ф3.10).

    The collected sources are sent once; every later pass is a short message in
    the same conversation, so nothing is re-sent per pass.
    """

    def __init__(self, agent, repo_name, files, cfg, run, emit):
        if agent["kind"] != "builtin":
            raise ValueError(f"agent `{agent['id']}` cannot run in-process")
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        from agent.core import Session as CoreSession

        self.agent, self.name = agent, "local"
        self.session_id = uuid.uuid4().hex[:12]
        self.limits: dict = {}
        self.run = run
        llm = dict(cfg.get("llm", {}))
        llm["max_repairs"] = int((cfg.get("agent", {}) or {}).get("max_repairs", 3))
        validate_url = _local_validate_url()
        if validate_url and not llm.get("validate_url"):
            llm["validate_url"] = validate_url
        emit("status", "opening the in-process agent session (one conversation for the whole run)")
        self._session = CoreSession(repo_name, files, llm, on_event=emit)

    def ask(self, prompt, constraints, include_files=False) -> dict:
        return self._session.ask(prompt, include_files=include_files)

    def close(self) -> None:
        self._session = None


def _open_session(agent, repo_name, files, cfg, run, emit, runner):
    """Open one agent session per material set; CLI agents use the sandbox job."""
    if agent["kind"] != "builtin":
        return None
    if runner == "docker":
        return DockerSession(agent, repo_name, files, cfg, run, emit)
    return LocalSession(agent, repo_name, files, cfg, run, emit)


class DockerSession:
    """One sandbox container serving every request of a run (Ф3.10).

    The repository snapshot is uploaded once (``sandbox.create_session``) and the
    container is started once; the base pass, every aspect pass and the docs pass
    are then queued into the same session, so nothing is re-created or re-uploaded
    per pass. ``close()`` ends the session and removes the container.
    """

    def __init__(self, agent, repo_name, files, cfg, run, emit):
        if not runtime.PUBLIC_BASE:
            raise RuntimeError("server public base URL is not set; start via server.py")
        self.agent, self.cfg, self.run, self.emit = agent, cfg, run, emit
        self.llm = dict(cfg.get("llm", {}) or {})
        self.emit = emit
        gateway.ensure_token(cfg)
        self.lease = gateway.issue_lease(cfg, agent["id"])["token"]
        self.session_id, self.token = sandbox.create_session(
            files, repo_name, agent["id"], on_event=(run.add_event if run else None))

        agent_cfg = cfg.get("agent", {}) or {}
        base = runtime.PUBLIC_BASE.rstrip("/")
        image = _image(agent, cfg)
        volume = agent_cfg.get("volume", "archide-agent-data")
        volume_mount = agent_cfg.get("volume_mount", "/agent-data")
        volume_enabled = agent_cfg.get("volume_enabled", True)
        env = {
            "ARCHIDE_SANDBOX_BASE": base,
            "ARCHIDE_SESSION_ID": self.session_id,
            "ARCHIDE_SESSION_TOKEN": self.token,
            "ARCHIDE_DATA": volume_mount,
            "ARCHIDE_AGENT_DIR": f"{volume_mount}/{agent['id']}",
            "ARCHIDE_SHARED_DIR": f"{volume_mount}/shared",
            "ARCHIDE_SCRATCH_DIR": f"{volume_mount}/scratch/{self.session_id}",
            "ARCHIDE_MAX_REPAIRS": str(agent_cfg.get("max_repairs", 3)),
            "ARCHIDE_MAX_CONTEXT": str(self.llm.get("max_context_chars", 0)),
            "ARCHIDE_VALIDATE_URL": f"{base}/api/validate",
            "LLM_BASE_URL": f"{base}/llm/v1",
            "LLM_API_KEY": self.lease,
            "LLM_MODEL": str(self.llm.get("model", "")),
            "LLM_MODE": str(self.llm.get("mode", "live")),
            "LLM_TIMEOUT": str(self.llm.get("timeout", 180)),
            "KILO_PROVIDER": "archide",
            "KILO_MODEL": f"archide/{self.llm.get('model', '')}",
        }
        limits = dict(agent_cfg.get("limits", {}) or {})
        limits.update(agent.get("limits", {}) or {})
        self.limits = limits

        name = "archide-sbx-" + uuid.uuid4().hex[:12]
        self.name = name
        cmd = ["docker", "run", "-d", "--name", name,
               "--add-host", "host.docker.internal:host-gateway"]
        for flag in _limit_args(limits):
            cmd += flag
        if volume_enabled and volume:
            subprocess.run(["docker", "volume", "create", volume], capture_output=True, text=True)
            cmd += ["-v", f"{volume}:{volume_mount}"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd.append(image)
        emit("status", f"starting the agent session (one container for the whole run) image `{image}`")
        started = subprocess.run(cmd, capture_output=True, text=True)
        if started.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {started.returncode}):\n{started.stderr.strip()[-1500:]}\n"
                f"Hint: build the image with `docker build -t {image} "
                f"{'agents/kilo' if agent['kind'] == 'cli' else 'agent'}/`."
            )
        if run:
            run.container = name
        self.last_log = 0
        emit("log", f"container {name} started (session {self.session_id})")

    def _stream_logs(self, full: bool = False) -> str:
        logs = _container_logs(self.name)
        if not full and len(logs) > self.last_log:
            self.emit("log", logs[self.last_log:])
        self.last_log = len(logs)
        return logs

    def ask(self, prompt, constraints, include_files: bool = False) -> dict:
        # `include_files` is a local-session concern: the container already holds
        # the repository snapshot uploaded when the session was opened.
        request_id = sandbox.session_submit(self.session_id, prompt, constraints)
        if request_id is None:
            raise RuntimeError("the agent session is closed")
        deadline = time.time() + float(self.llm.get("timeout", 180)) + 120
        while True:
            if self.run and self.run.cancel.is_set():
                raise CancelledError()
            graph, error = sandbox.session_wait(self.session_id, request_id, 5.0)
            if error:
                raise RuntimeError(f"agent failed: {error}\n{self._stream_logs(full=True)[-1500:]}")
            if graph:
                self._stream_logs()
                return graph
            self._stream_logs()
            if time.time() > deadline:
                raise RuntimeError("agent timed out\n" + self._stream_logs(full=True)[-1500:])

    def close(self) -> None:
        try:
            sandbox.session_close(self.session_id, self.token)
        finally:
            try:
                self._stream_logs()
            finally:
                subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, text=True)


def _run_docker(agent, prompt, constraints, repo_name, files, cfg, run, emit) -> tuple[dict, dict]:
    if not runtime.PUBLIC_BASE:
        raise RuntimeError("server public base URL is not set; start via server.py")

    gateway.ensure_token(cfg)  # master token exists
    gw_token = gateway.issue_lease(cfg, agent["id"])["token"]  # container gets a lease
    llm = cfg.get("llm", {})
    job_id, job_token = sandbox.create_job(
        files, prompt, constraints, repo_name, agent["id"],
        on_event=(run.add_event if run else None),
    )

    agent_cfg = cfg.get("agent", {}) or {}
    base = runtime.PUBLIC_BASE.rstrip("/")
    image = _image(agent, cfg)
    volume = agent_cfg.get("volume", "archide-agent-data")
    volume_mount = agent_cfg.get("volume_mount", "/agent-data")
    volume_enabled = agent_cfg.get("volume_enabled", True)

    env = {
        "ARCHIDE_SANDBOX_BASE": base,
        "ARCHIDE_JOB_ID": job_id,
        "ARCHIDE_JOB_TOKEN": job_token,
        "ARCHIDE_OUTPUT": CLI_OUTPUT,
        "ARCHIDE_DATA": volume_mount,
        "ARCHIDE_AGENT_DIR": f"{volume_mount}/{agent['id']}",
        "ARCHIDE_SHARED_DIR": f"{volume_mount}/shared",
        "ARCHIDE_SCRATCH_DIR": f"{volume_mount}/scratch/{job_id}",
        "ARCHIDE_MAX_REPAIRS": str(agent_cfg.get("max_repairs", 3)),
        "ARCHIDE_VALIDATE_URL": f"{base}/sandbox/validate/{job_id}?token={job_token}",
        "LLM_BASE_URL": f"{base}/llm/v1",
        "LLM_API_KEY": gw_token,
        "LLM_MODEL": str(llm.get("model", "")),
        "LLM_MODE": str(llm.get("mode", "live")),
        "LLM_TIMEOUT": str(llm.get("timeout", 180)),
        "KILO_PROVIDER": "archide",
        "KILO_MODEL": f"archide/{llm.get('model', '')}",
    }

    limits = dict(agent_cfg.get("limits", {}) or {})
    limits.update(agent.get("limits", {}) or {})

    name = "archide-sbx-" + uuid.uuid4().hex[:12]
    cmd = ["docker", "run", "-d", "--name", name,
           "--add-host", "host.docker.internal:host-gateway"]
    for flag in _limit_args(limits):
        cmd += flag
    if volume_enabled and volume:
        # a named volume: shared by all agents, no host file-sharing needed
        subprocess.run(["docker", "volume", "create", volume], capture_output=True, text=True)
        cmd += ["-v", f"{volume}:{volume_mount}"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    cmd.append(image)

    mount_note = f" (shared volume `{volume}` -> {volume_mount})" if (volume_enabled and volume) else ""
    emit("status", f"starting sandbox image `{image}`{mount_note}")
    started = subprocess.run(cmd, capture_output=True, text=True)
    if started.returncode != 0:
        raise RuntimeError(
            f"docker run failed (exit {started.returncode}):\n{started.stderr.strip()[-1500:]}\n"
            f"Hint: build the image with `docker build -t {image} "
            f"{'agents/kilo' if agent['kind'] == 'cli' else 'agent'}/`."
        )
    if run:
        run.container = name
    emit("log", f"container {name} started")

    deadline = time.time() + float(llm.get("timeout", 180)) + 120
    last_log = 0
    try:
        while True:
            if run and run.cancel.is_set():
                raise CancelledError()
            if sandbox.is_done(job_id):
                break
            if time.time() > deadline:
                raise RuntimeError("agent timed out")
            logs = _container_logs(name)
            if len(logs) > last_log:
                emit("log", logs[last_log:])
                last_log = len(logs)
            time.sleep(1.0)

        logs = _container_logs(name)
        if len(logs) > last_log:
            emit("log", logs[last_log:])
        graph, error = sandbox.get_outcome(job_id)
        if error:
            raise RuntimeError(f"agent failed: {error}\n{logs[-1500:]}")
        if not graph:
            raise RuntimeError(f"agent returned no graph\n{logs[-1500:]}")
        emit("status", "graph received from the agent")
        return graph, {"container": name, "job": job_id, "limits": limits}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def _container_logs(name: str) -> str:
    out = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return (out.stdout or "") + (out.stderr or "")
