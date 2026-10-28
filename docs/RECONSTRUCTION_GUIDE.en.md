[Русский](RECONSTRUCTION_GUIDE.md) · **English**

# ArchIDE — full reconstruction of a real project (all aspects)

A practical guide captured from a **real run**: the repository
[`pol4/MLScan`](https://github.com/pol4/MLScan) together with its architectural
documentation folder [`Arch_v3`](https://github.com/pol4/MLScan/tree/main/Arch_v3)
(17 documents: overview, requirements, architecture, data plane, ingest, static /
archival / dynamic analysis, security, observability, deployment, migration,
testing, formats, limitations, implementation plan, LLM inference testing).

This describes the **process**: what to feed in, what happens at each step, how to
read the feed, what can go wrong, and how to verify the result. The numbers come
from a run on the local model `Qwen3.8-27B-UD-Q5_K_XL` (`runner=local`, Docker
unavailable).

---

## 0. What you get out

A single run produces:

- an **architecture model** (`data/models/<name>.json`) — nodes and relationships
  across all aspects, each node with `evidence` (source file) and a `code`/`docs`
  marker;
- a **full run log** (events: what was collected, how many nodes, what repairs);
- a **reconciliation report** by aspect (how many elements without relationships,
  without evidence);
- a run record in the project (history, link to the model).

The model is then used as usual: viewing in C4 notation, aspect views, validation,
a handoff package, code generation, graph-based acceptance.

---

## 1. Preparation

### 1.1. What you need

| Item | Value in the example | Note |
|---|---|---|
| Repository | `https://github.com/pol4/MLScan.git` | URL (cloned) or a local path |
| Documentation | `Arch_v3/*.md` inside the repository | feeds the **second pass** |
| LLM | `http://192.168.20.20:8000/v1`, `Qwen3.8-27B-UD-Q5_K_XL` | configured in `⚙ Setup` |
| Agent | `builtin` (built-in) | an external CLI agent — for code generation |
| Runner | `local` (or `docker`, if the daemon is up) | affects isolation, not the logic |
| Session budget | 60 000 characters | `Session context budget` |

### 1.2. Settings that matter for "all aspects"

- **LLM mode = live** (otherwise the run falls back to the mock fixture).
- **Compare with documentation = on** — otherwise `Arch_v3` documentation is not
  used (the second pass).
- **Extract aspects = on**, **aspect passes = on**.
- **Aspect list** (order matters, each one is a separate pass):
  `data → interface → behavior → process → deployment → access → quality`.
- **Session context budget** — the ceiling for a single session; on overflow, old
  exchanges are dropped while the material (sources) is preserved.

All of this lives in `⚙ Setup` (`R`) in the UI, or via `PUT /api/config`:

```bash
curl -X PUT http://127.0.0.1:8000/api/config -H 'Content-Type: application/json' -d '{
  "llm": {"mode": "live"},
  "agent": {"runner": "local"},
  "context": {"compare_docs": true, "aspect_passes": true, "reconcile_report": true}
}'
```

---

## 2. Launch

### Through the UI

1. `⚙ Setup` — check the LLM, the agent, the budget, and enable comparison with
   the documentation.
2. The bottom panel → the **`Projects`** tab → the repository address field →
   **Reconstruct graph**.
3. The panel switches itself to **`Events`** — that is where the log runs.

### Through the API

```bash
curl -X POST http://127.0.0.1:8000/api/projects -H 'Content-Type: application/json' \
     -d '{"name":"MLScan","repo":"https://github.com/pol4/MLScan.git"}'

curl -X POST http://127.0.0.1:8000/api/reconstruct -H 'Content-Type: application/json' \
     -d '{"repo":"https://github.com/pol4/MLScan.git","agent":"builtin","project":"MLScan"}'
# → {"run_id": "…"}
```

The run is asynchronous: `POST` returns a `run_id` immediately, and the work
continues in the background.

---

## 3. What happens, step by step

Below is the real event sequence of the MLScan run.

### Step 0. Repository resolution

```
run started for `https://github.com/pol4/MLScan.git`
resolving repository (cloning if it is a URL)…
repository ready: MLScan
```

A URL is cloned into a temporary directory; a local path is taken as is.

### Step 1. Collecting sources and compressing the context

```
collecting source files…
collected 287 source files; compressing context…
pass 1/2 - source code: 287 files, 1394544->107779 chars
```

From 287 files, ~1.39 million characters were collected; the `extract` strategy
compressed the material to ~108 thousand characters (files with no useful
architectural information are filtered out).

### Step 2. Base pass over the code (pass 1/2)

```
opening the in-process agent session (one conversation for the whole run)
code: request sent to the agent session
session context trimmed: 136378 → 60000 chars (budget 60000)
calling the LLM (Qwen3.8-27B-UD-Q5_K_XL); up to 3 repair attempt(s)
refining graph (1/3) to fix 3 issue(s)
refining graph (2/3) to fix 1 issue(s)
validated graph: 39 nodes, 43 edges, ok
```

- One session for the whole run: the material is sent **once**, and after that
  only short messages go into the same conversation.
- The context is trimmed to the budget (60 000) — yet the **sources are preserved**
  (the tail of the old conversation is cut, not the material).
- The model returned a graph, and the validator found schema violations (anchors,
  relationship types) — two repair rounds, and on the third the graph is valid:
  **39 nodes / 43 edges**.

### Step 3. Aspect-aware extraction (the structural backbone)

```
aspect-aware extraction: 1407 nodes
aspect extraction skipped 9 element(s) with no valid anchor: Task(_task_priority), …
```

Elements with a valid anchor (operations, interfaces, data entities, and so on)
are extracted mechanically from the code — **1407 nodes**. This is the "skeleton"
that the aspect passes will attach to. 9 elements were skipped: they have no
permitted parent in the metamodel (internal `Task` wrappers) — that is normal, not
an error.

### Step 4. Seven aspect passes

Each aspect is a **separate request** in the same session: only the relevant files
are selected for it, a fragment of the graph is placed into the prompt (its own
elements plus the types it can attach to), the model returns **only the elements
of that aspect**, and they are merged into the graph.

| Aspect | Files selected | Gain (run 2) | Gain (run 1) |
|---|---|---|---|
| `data` | 140 | **+51 nodes, +42 edges** | +3 nodes, +0 edges |
| `interface` | 102 | +0 nodes, +0 edges | +0 nodes, +0 edges |
| `behavior` | 6 | **+73 nodes, +73 edges** | +24 nodes, +32 edges |
| `process` | 102 | **+41 nodes, +53 edges** | +39 nodes, +13 edges |
| `deployment` | 155 | **+10 nodes, +17 edges** | +0 (see §5.2) |
| `access` | 52 | **+21 nodes, +7 edges** | +18 nodes, +11 edges |
| `quality` | 34 | **+26 nodes, +26 edges** | +48 nodes, +48 edges |

The spread between runs is normal model variability: the set of elements is fixed
by the metamodel and enforcement, not by the generation temperature.

An example of the feed for one aspect:

```
aspect pass `behavior`: reconstructing…
aspect `behavior`: 6 relevant source file(s) selected
aspect:behavior: request sent to the agent session
refining graph (1/3) to fix 1 issue(s)
validated graph: 53 nodes, 51 edges, ok
aspect `behavior`: +24 nodes, +32 edges
```

Important: `+0 nodes` for `interface` is a **normal result**, not a failure:
interfaces and operations already arrived from the base pass (the graph has 136
`Interface` and 1090 `Operation`). The aspect is "already covered".

### Step 5. Reconciliation report

```
reconciliation: 123 element(s) without relationships, 0 without evidence —
  `data`: 89 orphan(s), 0 without evidence; `process`: 31 orphan(s), 0 without evidence;
  `structure`: 2 orphan(s), 0 without evidence
```

Reconciliation shows where the model is "leaky": elements without relationships.
`0 without evidence` means that **every** node has a source. If the report is
enabled in fix mode (`reconcile_fix`), the agent builds the missing relationships
in a separate pass.

### Step 6. Second pass over the documentation and the merge (pass 2/2)

```
collecting documentation…
pass 2/2 - documentation: 132 files; comparing
docs: request sent to the agent session
refining graph (1/3) to fix 4 issue(s)
validated graph: 28 nodes, 54 edges, ok
merged code + docs: 1562 nodes, 1482 edges, 1557 node deviation(s) flagged
```

The documentation (`Arch_v3` plus other `*.md`) is read in a separate pass and
**merged** with the model from the code. Discrepancies are flagged: a node that is
present in the documents but not confirmed by the code (or vice versa) gets a
source marker.

### Step 7. Saving

```
model saved: MLScan.json (9a889a2897d5e724)
```

The model is saved, the run is linked to the project, and the status is `done`.

---

## 4. How to read the feed

| Event | Meaning |
|---|---|
| `status … collecting/collected N source files` | collecting and compressing the material |
| `pass 1/2 - source code: N files, X->Y chars` | base pass: how much remained after compression |
| `session context trimmed: X → Y` | trimming the session context to the budget |
| `calling the LLM (model); up to N repair attempt(s)` | request to the model |
| `refining graph (k/N) to fix M issue(s)` | a repair round: the validator found M violations |
| `validated graph: N nodes, M edges, ok` | the model's response passed the schema |
| `aspect-aware extraction: N nodes` | the structural backbone from the code |
| `aspect pass \`X\`: reconstructing…` | start of an aspect pass |
| `aspect \`X\`: N relevant source file(s) selected` | how many files went into the pass |
| `aspect \`X\`: +n nodes, +m edges` | the aspect's contribution |
| `reconciliation: … orphan(s)` | reconciliation: elements without relationships / without evidence |
| `merged code + docs: N nodes, M edges` | the result of merging code and documentation |
| `error validation (k/N): …` | a schema violation with the reason text |
| `error aspect … skipped/failed/rejected` | the aspect was skipped (no anchor / empty response / invalid graph) |

---

## 5. What can go wrong (and what to do)

Every case below comes from the real run.

### 5.1. "Elements with no valid anchor"

```
aspect extraction skipped 9 element(s) with no valid anchor: Task(_task_priority), …
```

The metamodel allows `Task` only inside `Process/Activity`. The internal `_task_*`
functions do not attach — and they are not supposed to. **Action:** none; if there
are many elements, check whether irrelevant code ended up in the material.

### 5.2. An empty model response on an aspect (fixed and verified)

The first run:

```
aspect `deployment`: 155 relevant source file(s) selected
session context trimmed: 134722 → 509 chars (budget 60000)      ← anomaly
validation (1/4): the model returned an empty graph (no `nodes`)
aspect `deployment`: +0 nodes, +0 edges
```

The symptom: the context trim yields an **anomalously small** number (hundreds of
characters instead of tens of thousands), and the model responds with an empty
graph. The cause: the aspect prompt carries its own sources and arrives as the
**last** message; the old trimming logic treated the tail as "junk" and discarded
the material, leaving only the prompt header.

**Fixed:** the trim preserves the **material** (the introductory sources) and the
**newest request**, discarding only the middle of the conversation. The second run:

```
aspect `deployment`: 155 relevant source file(s) selected
session context trimmed: 133888 → 60000 chars (budget 60000)     ← normal
aspect `deployment`: +10 nodes, +17 edges
```

**How to check on your side:** after `session context trimmed`, the number should
be close to the budget (tens of thousands), not hundreds of characters.

### 5.3. An element from the wrong aspect

```
aspect `deployment` skipped 1 element(s): unknown-system (type System is not part of `deployment`)
```

The model returned an element that is not in the aspect's type list. It is
discarded and the pass continues. **Action:** none, if these are isolated cases.

### 5.4. An aspect that is "already covered"

```
aspect `interface`: +0 nodes, +0 edges
```

Not a failure: the aspect's elements are already in the graph from the base pass.
This is exactly why the order of the aspects and the base pass matter — the base
provides the "skeleton", and the aspects add to it.

### 5.5. Many validator notes on an aspect

```
refining graph (1/3) to fix 30 issue(s)
```

`process` produced 30 notes (mostly anchors and relationship types), but after the
repair round the graph became valid. If the rounds are exhausted and the graph is
still invalid, the aspect is **rejected** entirely (`rejected (invalid graph)`) —
the graph is not corrupted, and the aspect's contribution is lost.

### 5.6. Duplicate id after merging code and documentation (fixed)

The second run finished with the status **`invalid`**:

```
merged code + docs: 1676 nodes, 1655 edges, 1674 node deviation(s) flagged
model saved: MLScan.json (60f964cd45d219c9)
error graph failed validation: duplicate node id: actor://operator
```

The merge matched elements by "type + name" rather than by id: an element from the
documentation with **the same id** but a slightly different name (`Operator`
versus `Operator (docs)`) landed in a second record — and the graph ended up with
two nodes sharing one id.

**Fixed:** the merge matches by **id** (id is a stable URI), and at the end it
guarantees id uniqueness in the result. The final graph is valid.

**How to check on your side:** after `merged code + docs` there should be no
`error graph failed validation`; the run status should be `done`, not `invalid`.
If the status is `invalid`, open `Validation` — the specific reason will be there.

### 5.7. Holes in the model (orphans)

`reconciliation` shows elements without relationships (`data: 89 orphan(s)`). This
is not an error but a map of incompleteness. **Action:** enable `reconcile_fix` so
the agent builds the missing relationships from the sources, or finish them by
hand in the inspector.

---

## 6. Verifying the result

1. **Validation** — the bottom panel → `Validation` → **Check**: schema and
   constraints.
2. **Report** — `📊 Report`: by aspect, requirements coverage, metrics.
3. **Viewing** — the graph in C4 notation: a person is a stick figure, an external
   system is a dashed frame, a data store is a cylinder, a container has a header;
   relationships run from boundary to boundary, with an arrow and a type label.
4. **Aspect views** — the aspect lens (`state`, `er`, `bpmn`, `sequence`, `api`…).
5. **Slices and levels** — `L1…L4`, node search, focus on an element.
6. **Acceptance** — for a code-generation result: `arch-diff` (a graph diff of
   "target ↔ result") with a verdict.

---

## 7. Run metrics (MLScan, Arch_v3)

| Metric | Value |
|---|---|
| Source files collected | 287 |
| Material after compression | 1 394 544 → 107 779 characters |
| Base pass | 39 nodes / 43 edges (2 repairs) |
| Structural extraction | 1 407 nodes |
| Aspect passes | 7 |
| Documentation in the 2nd pass | 132 files |
| **Total (run 1)** | **1 562 nodes / 1 482 edges** |
| **Total (run 2)** | **1 676 nodes / 1 655 edges** |
| Nodes with `evidence` | 100 % (both runs) |
| Source: code / documents / both (run 1) | 1 534 / 23 / 5 |
| Duration | ~53 min (local 27B model, `runner=local`) |

Run 2 is after the context-trim fix: the `deployment` aspect contributed (in run 1
it was lost). The final graph of run 2 was marked `invalid` because of a duplicate
id in the merge (see §5.6); the merge was fixed and covered by a test.

Distribution by type (top): `Operation` 1090, `Interface` 136, `DataEntity` 86,
`Constraint` 64, `Component` 30, `NFR` 26, `Task` 24, `Container` 18, `Permission` 11,
`Process` 10, `State` 8, `Role` 7, `DataStore` 6, `Policy` 6, `Runtime` 5, `SLO` 5,
`Gateway` 4, `ExternalSystem` 3, `StateMachine` 3, `System` 1.

Relationships (top): `has` 1090, `exposes` 136, `anchored_to` 106, `owns` 47,
`contains` 27, `depends_on` 21, `calls` 18, `reads` 11, `writes` 10, `emits` 8.

---

## 8. Run artifacts

| Item | Where |
|---|---|
| Model | `data/models/<name>.json` (the name comes from the repository name) |
| Run log | `GET /api/runs/<run_id>?since=0` |
| Project (history, chat, view) | `data/projects/<id>.json` |
| Reconciliation report | in the run feed (`reconciliation: …`) |
| Validation | `POST /api/models/<name>/validate` |

---

## 9. A short checklist

- [ ] LLM `mode=live`, model and key verified in `⚙ Setup`.
- [ ] **Compare with documentation = on** (otherwise `Arch_v3` will not make it
      into the model).
- [ ] **aspect passes = on**, the aspect list is all seven.
- [ ] The session budget is sufficient (60 000 in the example); make sure that
      after `session context trimmed` tens of thousands of characters remain.
- [ ] Launch from `Projects` (or `POST /api/reconstruct` with `project`).
- [ ] Wait for `model saved: …` and the status `done`.
- [ ] Check `Validation` and `Report`, look at the graph and the aspect views.
- [ ] Work through `reconciliation` (orphans) — enable `reconcile_fix` if needed.
