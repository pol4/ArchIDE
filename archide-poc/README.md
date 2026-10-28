# ArchIDE PoC

Minimal proof-of-concept for the ArchIDE flow: **repository → architecture graph**,
with pluggable agents (built-in and Kilo Code CLI) running in a Docker sandbox and
reaching the LLM through a dedicated ArchIDE gateway.

## Scenario

1. In the UI, the human enters a code repository address (git URL or local path).
2. The human picks an agent: **Built-in Reconstructor** or **Kilo Code CLI**.
3. ArchIDE collects and compresses the source, then builds a prompt that encodes
   the metamodel, the context constraints and (for CLI agents) the output path.
4. The agent runs in a **separate Docker container**. There are **no bind mounts**:
   ArchIDE serves the repo tarball, prompt and constraints over a **sandbox HTTP
   protocol**, and the container uploads the reconstructed graph back.
5. All agents call the LLM through the **ArchIDE LLM gateway**: they carry a single
   shared **gateway token**; the gateway injects the **real** upstream key when it
   calls the actual inference. The real key never reaches an agent.
6. ArchIDE validates the returned graph and renders it.

## Layout

```
archide-poc/
  server.py            stdlib HTTP server: UI + API + sandbox + LLM gateway
  backend/
    agents.py          agent registry (builtin, kilocode)
    gateway.py         LLM gateway (token validation + real-key injection)
    sandbox.py         in-memory sandbox job store (repo/meta/result)
    runner.py          orchestration: collect -> compress -> prompt -> run agent
    context.py         host-side source collection
    compress.py        context compression (extract / truncate / drop)
    prompt.py          prompt builder (metamodel + constraints + output path)
    graph_schema.py    minimal ArchIDE metamodel validation
    main.py            optional FastAPI variant of the same API
  agent/               built-in agent (core.py + sandbox.py) + Dockerfile
  agents/kilo/         Kilo Code CLI sandbox (entrypoint.js) + Dockerfile
  frontend/            single-page UI + self-contained SVG graph renderer
  data/                persisted config.json (created at runtime)
```

## Run

No third-party Python packages are required — the server and the built-in agent
use only the standard library.

```bash
# 1. build the sandbox images
docker build -t archide-agent:latest agent/
docker build -t archide-kilo:latest agents/kilo/      # installs the Kilo Code CLI

# 2. start the backend + UI (binds 0.0.0.0 so containers can reach it)
./run.sh            # -> http://127.0.0.1:8000
```

Then in the UI:

- **Repository** — a git URL (cloned with `--depth 1`) or a local path.
- **Agent** — `Built-in Reconstructor` or `Kilo Code CLI`.
- **LLM connection** — `base_url` of the *upstream* OpenAI-compatible endpoint
  (e.g. `http://localhost:11434/v1` for Ollama), `api_key` (the **real** key, kept
  on the host), `model`, and **Max repair attempts** (`agent.max_repairs`).
- **Context budget** — max size, compression threshold, strategy.
- Click **Reconstruct graph**.

Without Docker or an LLM you can still exercise the whole pipeline:
`runner = local` + `mode = mock`.

## UI design

The interface is designed graph-first (aspects and actions open from the graph element
itself): see [`../UI_REDESIGN.md`](../UI_REDESIGN.md).

## Architect workflow

Two end-to-end workflows (study an existing repository and hand changes to a coding agent;
design from scratch and accept generated code) are described step by step in
[`../ARCHITECT_GUIDE.md`](../ARCHITECT_GUIDE.md) (CLI + API) and
[`../ARCHITECT_GUIDE_UI.md`](../ARCHITECT_GUIDE_UI.md) (UI only, with screenshots in
[`../docs/screens`](../docs/screens)).

## LLM gateway

All agents talk to `POST /llm/v1/chat/completions` (OpenAI-compatible) on the
ArchIDE server.

- Agents authenticate with the **gateway token** (`Authorization: Bearer …` or
  `X-OpenAI-Api-Key: …`). The token is generated on first use and persisted in
  `data/config.json` → `gateway.token`.
- The gateway validates the token and forwards the request to the **real upstream**
  (`llm.base_url` + `llm.api_key`), injecting the real key. A wrong token → `401`.
- Streaming (`"stream": true`) is passed through as SSE.

So a sandbox agent only ever holds the gateway token; the real inference credential
stays on the host.

## Sandbox protocol

The host creates a job and serves it; the container fetches and returns:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/sandbox/repo/{job}?token=…` | repo tarball (already compressed to the budget) |
| `GET` | `/sandbox/meta/{job}?token=…` | `{repo_name, prompt, constraints, agent}` |
| `POST` | `/sandbox/result/{job}?token=…` | `{graph}` or `{error}` from the agent |
| `POST` | `/sandbox/validate/{job}?token=…` | formal validation tool: `{graph}` → `{validation:{ok,errors,warnings,stats}}` |

## Graph validation (tool + repair loop)

Formal validation is a first-class **tool** (`backend/graph_schema.py`) exposed at
`POST /sandbox/validate/{job}` (token-scoped). Rules: exactly one root `System`,
tree containment, referential integrity, closed node/edge type sets.

The agent prompt **requires** validating the graph before returning and fixing all
errors. The agent then runs a **repair loop**: validate → fix the reported errors →
validate again, repeating up to **Max repair attempts** (`agent.max_repairs`, UI
field, default 3). The built-in agent calls the validation tool over HTTP
(`ARCHIDE_VALIDATE_URL`) and falls back to the identical in-process validator if the
tool is unreachable; CLI agents (Kilo) get the tool URL in the prompt and can call
it via `curl`. Activity events show each attempt (`validation (i/N): …`).

The container receives (env): `ARCHIDE_SANDBOX_BASE`, `ARCHIDE_JOB_ID`,
`ARCHIDE_JOB_TOKEN`, `ARCHIDE_OUTPUT`, `LLM_BASE_URL` (the gateway), `LLM_API_KEY`
(the gateway token), `LLM_MODEL`, plus Kilo-specific `KILO_PROVIDER`/`KILO_MODEL`.

## Agents

| id | name | image | how it works |
|---|---|---|---|
| `builtin` | Built-in Reconstructor | `archide-agent:latest` | `agent/sandbox.py` fetches repo+meta, calls the LLM via the gateway, uploads the graph |
| `kilocode` | Kilo Code CLI | `archide-kilo:latest` | `agents/kilo/entrypoint.js` fetches repo+meta, writes a `kilo.jsonc` provider pointing at the gateway, runs `kilo run <prompt> --dir <repo> --model archide/<model> --auto --format json`, uploads the graph |

The Kilo provider is configured exactly like Kilo's own config format
(`@ai-sdk/openai-compatible`, `options.baseURL`, `options.headers`), so the CLI
reaches the gateway with the gateway token.

## Shared agent storage (Docker volume)

All agents share one Docker **named volume** (the Docker analog of a K8s PVC),
mounted into every sandbox container. A named volume needs **no host
file-sharing** (unlike bind mounts), so it works on Docker Desktop as-is.

- Default: volume `archide-agent-data` mounted at `/agent-data` (configurable via
  the **Agent storage** panel: `agent.volume`, `agent.volume_mount`,
  `agent.volume_enabled`).
- Layout inside the volume: `/agent-data/shared` (common) and
  `/agent-data/<agent_id>` (per agent).
- The container receives `ARCHIDE_DATA`, `ARCHIDE_AGENT_DIR`, `ARCHIDE_SHARED_DIR`.
- Agents persist artifacts there: reconstructed graphs are saved to
  `<agent_dir>/graphs/<repo>.json`; the Kilo CLI also keeps its caches and session
  data on the volume (`npm_config_cache`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`), so
  state survives across runs and is shared between agents.

API: `GET /api/volume` (inspect), `POST /api/volume/reset` (recreate empty).

## Graph view (one level at a time)

For any non-trivial system the whole tree is unreadable, so the canvas shows
**exactly one C4 level at a time**:

- default view = the children of the root (containers, L2);
- the **breadcrumb** (`System › Container › …`) and **zoom out** go up a level;
- clicking a node with `⊕` (**zoom in**) opens the next level (its children);
- the current level is labelled in the canvas corner (`Container (L2)`, …).

Only edges between the nodes of the current level are drawn. The reconstruction is
**source-code-driven**: the compressor keeps source files and manifests first and
drops documentation last, and the prompt forbids inferring structure from prose.
Documentation is then used in a second pass (see below).

## Sources & deviations

Reconstruction is a **two-pass** analysis (when **Compare with documentation** is on):

1. **Pass 1 - source code**: the graph is reconstructed from code only.
2. **Pass 2 - documentation**: the graph is reconstructed from the docs, then
   **merged** with the code graph (`backend/merge.py`). Elements found in both get
   `sources: ["code","docs"]`; single-source elements are **deviations** with a
   human-readable note ("only in source code (not documented)" /
   "only in documentation (not in code)").

The UI shows the source per element and offers three visibility modes (graph
toolbar): **all** (everything, deviations highlighted by colour + dashed border +
note text), **code** (only code-derived elements), **docs** (only docs-derived
elements). In `all` mode code-only nodes are cyan, docs-only nodes orange.

## Context budget

Controls how much source is provided to the agent (host-side, before upload).

| Setting | Meaning |
|---|---|
| **Max context size, chars** | hard cap on the context size |
| **Compress at (0–1)** | compression triggers once sources exceed `compress_at × max_chars` |
| **Strategy** | `extract` (default), `truncate`, or `none` |
| **Compare with documentation** | two-pass analysis: reconstruct from code, then compare with docs (default on) |

Compression stages: **structural extraction** (keep imports/declarations/comments,
drop bodies) → **per-file truncation** → **priority dropping** (tests/docs first,
README/entrypoints last). `truncate` runs stages 2–3; `none` runs stage 3 only.
The constraints are also embedded in the prompt.

## Modes

| Setting | Values | Meaning |
|---|---|---|
| `mode` | `live` | call the upstream LLM through the gateway |
| `mode` | `mock` | deterministic graph from the file tree (no LLM) |
| `runner` | `docker` | run the agent in the sandbox (supports both agents) |
| `runner` | `local` | run the built-in agent in-process on the host |

## Notes

- **No bind mounts** — repo/prompt/results move over HTTP, so Docker Desktop file
  sharing is not required.
- Containers reach the host via `host.docker.internal`
  (`--add-host host.docker.internal:host-gateway`), which requires the server to
  bind a reachable interface (`./run.sh` uses `0.0.0.0`).
- A locally-hosted upstream LLM is reachable from the host process directly; only
  the *agent* goes through the gateway.
- The graph is validated against a minimal ArchIDE metamodel subset
  (`backend/graph_schema.py`): single root `System`, tree containment, referential
  integrity, closed node/edge type sets.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agents` | list available agents |
| `GET` | `/api/config` | read persisted config |
| `PUT` | `/api/config` | patch persisted config |
| `POST` | `/api/reconstruct` | `{repo, agent}` → `{run_id}` (starts a background run) |
| `GET` | `/api/runs/{id}?since=N` | run status + activity events (incremental) + graph/validation |
| `POST` | `/api/runs/{id}/cancel` | forcibly stop the run (kills the sandbox container) |
| `GET` | `/api/volume` | inspect the shared agent volume |
| `POST` | `/api/volume/reset` | recreate the shared agent volume (empty) |
| `POST` | `/llm/v1/chat/completions` | LLM gateway (gateway token) |
| `GET` | `/llm/v1/models` | gateway model list |

### Sandbox activity endpoint

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sandbox/event/{job}?token=…` | agent posts an activity event `{type, text}` |

## Live activity & cancel

Reconstruction runs asynchronously. The UI polls `GET /api/runs/{id}` and shows a
live **Agent activity** feed:

- `status` — pipeline steps (collect, compress, start sandbox, …),
- `reasoning` — the model's reasoning/thinking (when provided) and the repair step,
- `output` — the model's textual answer,
- `log` — container logs,
- `error` — failures.

Agents post these through `/sandbox/event/{job}`; the runner adds host-side events
and streams container logs. The **Stop** button calls
`POST /api/runs/{id}/cancel`, which sets the cancel flag and removes the container.

Settings (LLM, runner, context) are **auto-saved** (debounced) on change, so they
survive a page reload (Ctrl+Shift+R); config responses are sent with
`Cache-Control: no-store` and persisted server-side in `data/config.json`.
