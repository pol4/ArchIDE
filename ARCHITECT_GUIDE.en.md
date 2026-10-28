[Русский](ARCHITECT_GUIDE.md) · **English**

# ArchIDE — architect's guide

> **Step-by-step guide to the interface with screenshots:** `ARCHITECT_GUIDE_UI.md`
> (the same two scenarios, but driven entirely through the UI).

This document describes two working flows and the **exact steps** for each:

- **Scenario A** — exploring an existing repository of code, making and validating
  architectural changes, assembling a set of documentation/tasks for an arbitrary coding
  agent, receiving the result as a new repository, and validating that result against the
  modified graph.
- **Scenario B** — developing an application's architecture **from scratch**, generating
  artifacts, and handing them off to an external code-generation agent, reading the result
  back from a separate repository, and validating it against the graph.

For each step we state: what to do (UI and API), the expected result, and the **current
support status** (✅ supported, 🟡 partially/manually supported, ❌ not yet — with a
workaround and a reference to the relevant item in the roadmap, section 4).

---

## 0. Preparation

**Concepts.** The *model* is the architecture itself: a graph of nodes and edges in
canonical JSON (`archide-poc/data/models/<Name>.json`). *Aspects* (structure, interface,
data, behavior, process, access, deployment, quality) are orthogonal subgraphs of a single
model. Each node has an *anchor* (`parent` according to the metamodel rules) and *evidence*
(source files that justify it). *Markers* are signed traceability markers for handing work
off externally. A *work package* is a slice of the graph with acceptance criteria for the
implementer.

**Starting the server and UI**

```bash
cd archide-poc && python3 server.py          # UI и API: http://127.0.0.1:8200
# переменные: ARCHIDE_PORT, ARCHIDE_HOST, ARCHIDE_DATA (каталог состояния)
```

**Configuring the LLM and agent (UI, panels 2–4):** `Base URL` of the OpenAI-compatible
endpoint, `API key`, `Model`, `Mode` (`live`/`mock`), `Runner` (`local` — in the host
process; `docker` — the agent inside a sandbox container). Readiness check:

```bash
curl -s localhost:8200/api/agents           # список агентов и лимитов
curl -s localhost:8200/api/metamodel        # типы узлов/рёбер, аспекты, якоря
curl -s localhost:8200/api/models           # сохранённые модели
```

**Where things live.** `data/models/<Name>.json` — models; `data/runs/<id>.json` — run
history with events; `data/users/<user>/docs/` — per-user documents (RAG);
`data/local-llm.pid|log` — if a local backend was started. All of these directories are
`git-ignored` (only the seed manifest `data/agents.json` goes into the repository).

---

## 1. Scenario A — existing repository

### A1. Reconstructing the architecture from code ✅

UI: panel **1 · Repository** → URL or local path → agent (`Built-in Reconstructor` or
`Kilo Code CLI`) → **Reconstruct graph**. The feed shows the steps: the base C4 pass,
aspect extraction, and **sequential aspect passes** (`aspect `data`: +N nodes…`).

API:

```bash
curl -s -X POST localhost:8200/api/reconstruct \
  -H 'Content-Type: application/json' \
  -d '{"repo":"https://github.com/ORG/REPO.git","agent":"builtin"}'
# → {"run_id":"..."}   дальше опрос:
curl -s "localhost:8200/api/runs/<run_id>?since=0"
# события: run started → collected N files → pass 1/2 → aspect `x` → reconciliation …
# финал: status=done|invalid|error; модель сохранена под именем проекта
curl -s localhost:8200/api/models/REPO
```

Useful run options: `{"model":"ArchIDE"}` — **continue from a saved model** (the base pass
is skipped), `{"aspect_list":["behavior","process"]}` — choose the aspects for this run
(aspects already covered are skipped).

### A2. Exploring the model ✅

- **Aspect windows** (a button to open a window plus an aspect selector): each aspect is
  drawn with its own notation, and the view can be switched (`,` / `.`): structure `c4`,
  interface `api`/`sequence`, data `er`, behavior `state`, process `bpmn`, deployment
  `tree`, quality/access `table`. The selected view is remembered per aspect; the window can
  be collapsed and resized by dragging its edge; `⤓ all` exports all views into a single
  SVG, `⤓` exports one view.
- **Navigation**: `↑`/`↓` — how many layers around the focus to show; the **C4 level**
  selector (L1…L4) — a whole level; search node by name (opens the relevant aspect and
  highlights the node); `+`/`−`/`⌂` — zoom and "home".
- **Slices and metrics**:

```bash
curl -s -X POST localhost:8200/api/models/REPO/slice \
  -H 'Content-Type: application/json' \
  -d '{"scope":"component://backend/runner","depth":2,"direction":"down"}'
curl -s localhost:8200/api/models/REPO/coverage        # покрытие требований
curl -s localhost:8200/api/models/REPO/aggregations
curl -s -X POST localhost:8200/api/eval -H 'Content-Type: application/json' \
  -d "{\"graph\": $(curl -s localhost:8200/api/models/REPO)}"
# metrics: score, validator_ok, canonical_stable, roundtrip_ok, slice_deterministic,
#          aspects.<аспект>.{elements,coverage,orphans,without_evidence}, aspect_summary
```

- **Documents (RAG)**: the Documents panel — upload md/csv (`POST /api/docs`), search and
  verify claims (`POST /api/rag/search`, `/api/rag/verify`), signed chunk markers.

### A3. Changing the architecture ✅

All edits go through a transaction with a gate (validation + constraints) and referential
actions (automatic repair of references). The manual path is the UI (canvas/forms/chat),
the programmatic path is the API:

```bash
HASH=$(curl -s localhost:8200/api/models/REPO | python3 -c 'import sys,json;print(json.load(sys.stdin).get("hash",""))')
curl -s -X POST localhost:8200/api/models/REPO/edit -H 'Content-Type: application/json' -d '{
  "user": "architect", "expected_hash": "'"$HASH"'", "auto_repair": true,
  "operations": [
    {"op": "add_node",  "node": {"id":"component://backend/cache","type":"Component",
                                 "name":"Cache","parent":"container://backend","evidence":[]}},
    {"op": "add_edge",  "edge": {"source":"component://backend/cache",
                                 "target":"datastore://config-store","type":"reads"}},
    {"op": "update_node","id":"component://backend/runner","patch":{"name":"Reconstruction Runner"}}
  ]}'
```

Assessing and tracking changes:

```bash
curl -s -X POST localhost:8200/api/models/REPO/impact   -d '{"node":"component://backend/cache"}'
curl -s -X POST localhost:8200/api/models/REPO/review
curl -s -X POST localhost:8200/api/models/REPO/reconcile -d '{"apply": true}'   # авто-починка
curl -s -X POST localhost:8200/api/models/REPO/constraints -d '{"constraints":[...]}'
```

Edit operations: `add_node`, `update_node`, `remove_node`, `add_edge`, `remove_edge`.
`expected_hash` guards against races (409 on mismatch), and node ownership yields 403.

### A4. Validating the modified model ✅

```bash
curl -s localhost:8200/api/models/REPO/validate      # {"validation":{ok,errors}, "violations":[...]}
curl -s -X POST localhost:8200/api/validate -d "{\"graph\": $(cat archide-poc/data/models/REPO.json)}"
curl -s -X POST localhost:8200/api/eval     -d "{\"graph\": $(cat archide-poc/data/models/REPO.json)}"
```

Hard invariants (nesting tree, aspect anchors, cross-aspect edge rules, a single root) +
the model's declarative constraints + per-aspect metrics (type coverage, orphans, elements
without evidence).

### A5. The kit for a coding agent ✅ (assembled piece by piece)

```bash
# 1) задание: срез графа + критерии приёмки по цели
curl -s -X POST localhost:8200/api/models/REPO/workpackage -d '{"node":"component://backend/cache"}'
# 2) артефакты (спецификации/скелеты)
curl -s -X POST localhost:8200/api/models/REPO/generate \
  -d '{"kinds":["openapi","ddl","jsonschema","skeleton"]}'
# 3) подписанные метки трассируемости (для проверки «что сделано»)
curl -s -X POST localhost:8200/api/models/REPO/markers -d '{"node":"component://backend/cache"}'
# 4) матрица покрытия (цель ↔ артефакты/метки)
curl -s -X POST localhost:8200/api/models/REPO/coverage-matrix
```

UI: panel **8 · Generate** (the "Artifacts" and "Coverage" windows).

### A6. Handing off to an external coding agent 🟡 (currently manual)

The assembled kit is handed to the agent as an ordinary task in its own repository. What to
put in the task: (1) context — the `work package` (slice + criteria) and/or a graph slice
(`/api/models/REPO/slice`); (2) specifications from `/generate`; (3) markers from
`/markers` (the agent must insert them into the artifacts/code so the result can be
verified); (4) result requirements — a branch/PR and the build/test command. The result is
accepted as a **new repository** (or branch) and recorded by commit.

*Workaround:* do this through whatever interface you use for code (for example, Kilo Code),
pointing the prompt at the path of the saved package. There is no automatic
"package → launch external agent → assemble repository" adapter in the system yet (see
Ф8.1, Ф8.2 in section 4).

### A7. Accepting the result against the modified graph 🟡 (partial)

```bash
# 1) реконструировать полученный репозиторий
curl -s -X POST localhost:8200/api/reconstruct \
  -d '{"repo":"https://github.com/ORG/NEW-REPO.git","agent":"builtin"}'
# 2) сопоставить с целевой моделью
curl -s -X POST localhost:8200/api/merge/candidates -d "{\"code\": <граф результата>, \"docs\": <целевой граф>}"
# 3) трассируемость артефактов: конформанс по меткам
curl -s -X POST localhost:8200/api/models/REPO/conformance-loop -d '{"node":"component://backend/cache"}'
curl -s -X POST localhost:8200/api/models/REPO/conformance -d '{"markers": [...]}'
```

What already gives an answer: requirement coverage, conformance by markers, reconstruction
quality metrics for the result, and the list of ambiguous matches.

What is **missing**: a **"target model ↔ result repository"** report (what appeared, what
disappeared, what changed, per element and aspect, with a verdict). Today this is assembled
manually from `eval`/`coverage`/`candidates` (see Ф8.3).

---

## 2. Scenario B — designing an architecture from scratch

### B1. Creating a model ❌/🟡 (no "design mode" and no templates)

There is no ready-made "architecture from a spec without code" scenario yet: the base
reconstruction pass requires **source files**. The paths available today:

- create a model programmatically and fill it in with edits (the A3 operations):

```bash
curl -s -X PUT localhost:8200/api/models/NewApp -H 'Content-Type: application/json' -d '{
  "graph": {"nodes":[{"id":"system://newapp","type":"System","name":"NewApp","parent":null,
                      "description":"...","evidence":[]}],
            "edges":[]}}'
curl -s -X POST localhost:8200/api/models/NewApp/edit -d '{
  "user":"architect","auto_repair":true,"operations":[
    {"op":"add_node","node":{"id":"container://newapp/api","type":"Container","name":"API",
                             "parent":"system://newapp","evidence":[]}},
    {"op":"add_node","node":{"id":"component://newapp/orders","type":"Component","name":"Orders",
                             "parent":"container://newapp/api","evidence":[]}}]}'
```

- or lay out the spec and requirements as documents and reconstruct from them — **this
  requires work** (Ф8.4: a documentation-only pass).

UI paths: the canvas (add/link/rename a node), chat with the agent ("add a cache component
to the backend"), importing an existing JSON (the model's `Load`/import file).

### B2. Validation and metrics ✅
As in A4 (for a model without reconstruction, requirement coverage and conformance are
computed the same way).

### B3. Generating artifacts and tasks ✅
As in A5: `workpackage`, `generate`, `markers`, `coverage-matrix`. It is precisely these
artifacts that are handed to the external code generator (specifications + acceptance
criteria + markers).

### B4. Handing off to the code-generation agent 🟡
As in A6 (manually; the adapter is Ф8.2).

### B5. Reading the result and validating against the graph 🟡
As in A7: obtain the result repository → `POST /api/reconstruct` → compare with the model
(`eval`, `coverage`, `candidates`, `conformance-loop`); on discrepancies, edit the model
(B2) and hand off again. The automatic graph diff is Ф8.3.

---

## 3. API cheat sheet

| Method | Path | Body | What it does |
|---|---|---|---|
| POST | `/api/reconstruct` | `{repo, agent, model?, aspect_list?}` | start reconstruction (a run) |
| GET | `/api/runs/<id>?since=N` | — | run events and status |
| POST | `/api/runs/<id>` | — | cancel the run |
| GET | `/api/models` | — | list of models |
| GET/PUT | `/api/models/<n>` | `{graph}` | read/replace a model |
| POST | `/api/models/<n>/edit` | `{operations,user,expected_hash?,auto_repair?}` | edit transaction with a gate |
| POST | `/api/models/<n>/impact` | `{node}` | consequences of editing a node |
| POST | `/api/models/<n>/review` | — | review report for the model |
| POST | `/api/models/<n>/reconcile` | `{apply?}` | automatic integrity repair |
| GET | `/api/models/<n>/validate` | — | validation + constraint violations |
| GET | `/api/models/<n>/coverage` | — | requirement coverage |
| GET | `/api/models/<n>/aggregations` | — | aggregation interfaces |
| POST | `/api/models/<n>/slice` | `{scope,depth,direction,aspects?}` | model slice |
| POST | `/api/models/<n>/constraints` | `{constraints}` | declarative constraints |
| POST | `/api/models/<n>/generate` | `{kinds?}` | specifications and skeletons |
| POST | `/api/models/<n>/workpackage` | `{node}` | task for the implementer (slice + criteria) |
| POST | `/api/models/<n>/markers` | `{node}` | signed traceability markers |
| POST | `/api/models/<n>/coverage-matrix` | — | "goal ↔ artifacts" matrix |
| POST | `/api/models/<n>/conformance` | `{markers}` | conformance by markers |
| POST | `/api/models/<n>/conformance-loop` | `{node}` | conformance + repair loop |
| POST | `/api/validate` | `{graph, files?}` | formal validation (metamodel+anchors+cross) |
| POST | `/api/eval` | `{graph}` | quality metrics (including per aspect) |
| POST | `/api/merge/candidates` | `{code, docs}` | match two graphs |
| POST | `/api/slice` | `{graph,scope,depth,direction,aspects?}` | slice an arbitrary graph |
| POST | `/api/docs` | `{user,name,kind,content}` | upload a document (RAG) |
| POST | `/api/rag/search`, `/api/rag/verify` | `{...}` | search and verify claims |

## 3a. Verified in practice (Scenario B, "Trip Approval")

A full run of Scenario B on the "trip approval" demo application (all aspects):

1. **Model assembled** (`PUT /api/models/TripApproval`): 55 nodes / 33 edges, valid; 10
   aspects covered; requirement coverage 3/3 (`GET .../coverage`).
2. **Kit**: `POST .../generate` → specifications; `POST .../workpackage
   {"node":"component://trip/api"}` → a slice of 10 nodes + **5 acceptance criteria**;
   `POST .../markers` → **10 signed markers**.
3. **Code generation by an external agent**: `POST .../codegen {"node":"component://trip/api",
   "target":"triprepo"}` → the agent returned a file map, the repository was created and
   committed (`data/generated/triprepo`, commit `e07bbc8`): `trip_api.py` (190 lines, an HTTP
   server with the operations `createTrip/submitTrip/approveTrip/rejectTrip`), tests,
   `README`, `pyproject.toml`; the **traceability markers were inserted into the code** (10
   each in `trip_api.py`/`app.py`).
4. **Acceptance against the graph**: the delivered repository was reconstructed
   (`POST /api/reconstruct {"repo":"…/triprepo","aspect_list":[]}` → the model `triprepo`,
   88 elements) and matched against the target model:
   `POST /api/graphdiff {"target_model":"TripApproval","result_model":"triprepo"}` → a
   **`gaps` verdict**: matched 14/55 (coverage 0.26), missing 41, extra 77, broken down by
   aspect (`process` 8 missing, `access` 5, `behavior` 6, `interface` 2 + 32 extra…).

The run's conclusion (an honest one): the agent implemented a **narrow slice** (the API
component), while the target model describes the whole application — the diff showed this
correctly. Precise acceptance requires matching by markers/evidence and evaluation within
the slice's boundaries (Ф8.3b), and for long aspect passes, managing session context (Ф3.11).

**Full reconstruction in docker mode** (the ArchIDE repository, 8 LLM calls in a single
container): `status=done`, **443 nodes / 501 edges**, valid; the aspect passes yielded
`data +8/+15`, `interface +23/+29`, `behavior +18/+26`, `deployment +3/+6`, `access +6/+4`.
Two passes failed honestly, and that went into the plan: `process` — **"The model ran out of
context space"** (the accumulated context of a continuous session exceeded the model's
window → Ф3.11), `quality` — an invalid model response (→ Ф8.2b/retry).

## 4. What is supported now and what goes into the plan

| Step | Status | What is missing | Plan item |
|---|---|---|---|
| A1–A2 exploration | ✅ | — | — |
| A3–A4 edits and validation | ✅ | — | — |
| A5 kit for the implementer | ✅ | a single **handoff package** in one call | **Ф8.1** |
| A6 launching the code-generation agent | 🟡 | there is a code-generation agent (`/codegen` + a button in the UI); what is missing is launching an **arbitrary external** CLI agent and the robustness of its response | **Ф8.2**, **Ф8.2b** |
| A7 accepting the result against the graph | 🟡 | a graph diff exists (`/api/graphdiff` + a button); precise matching is needed (markers/evidence, slice boundaries) | **Ф8.3**, **Ф8.3b** |
| B1 architecture from scratch | ❌/🟡 | a **documentation/spec-only** pass, templates, and a new-model wizard | **Ф8.4**, **Ф8.5** |
| B3–B5 generation and acceptance | ✅/🟡 | the same as A5–A7 (+ context management for long passes) | Ф8.1–Ф8.3b, **Ф3.11** |

## 5. Discipline for working with the model

1. **The model is the single source.** Diagrams and specifications are derived from the
   graph and are not stored separately.
2. **Edits only through operations** (`/edit`) or via the UI: transaction, gate, referential
   actions, audit. Editing the JSON directly is the exception (import/diagnostics).
3. **Always pass `expected_hash`** when editing — protection against races (409) and
   auditability.
4. **Always after edits** `.../validate` + `/api/eval`: invariants, constraints, aspect
   metrics (type coverage, orphans, elements without evidence).
5. **Every element with evidence.** For reconstruction these are source files; for design,
   documents/decisions; when handing off externally, markers are signed with the exchange
   key (`/markers`, verification — `/api/markers/verify`).
6. **Accept the result against the graph, not "by eye":** reconstruct the delivered
   repository, check aspects and coverage, record a verdict (and refine the model or the task
   on discrepancies).
