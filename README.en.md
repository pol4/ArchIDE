[Русский](README.md) · **English**

# ArchIDE

**An IDE for the architecture graph, not for code.** A human and an agent work
with the architecture in a language they understand; code, diagrams and documents
are **projections** of a single model, not its source.

## Why: the next step after the IDE

The history of programming is the history of **raising the level of description**.
Each step did not replace the previous one — it removed one more layer of routine
from the person doing the work:

| Step | What the human describes | Who does the routine work |
|---|---|---|
| Machine code | processor instructions | — |
| Assembler | mnemonics | the assembler |
| High-level languages | algorithms and data structures | the compiler |
| IDE | code + navigation, refactoring, debugging | the compiler and the tooling |
| **ArchIDE** | **the architecture of the system** | **the LLM as the compiler** |

The assembler freed us from machine codes, the compiler from assembly, the IDE
from the mechanical work of editing text. But **a human still writes the code by
hand**: they design the system at the level of architecture and then translate
that architecture into code manually — the most expensive and least reversible
step in the whole cycle.

ArchIDE takes the next step: **the architecture description becomes the source**,
and the LLM becomes the **compiler** into high-level code. The architect states the
system as a graph (C4 spine, aspects, requirements, interfaces, data, states),
ArchIDE checks it for consistency, and a code-generating agent turns it into a
working repository — the way a compiler turns a program into machine code.

This is where the requirements that set ArchIDE apart from "chatting with an LLM"
come from:

- **The source must be checkable.** A program is checked by its compiler — an
  architecture is checked by a closed metamodel and a validator (types, anchors,
  cross-aspect edges, constraints) with a repair loop. A compiler that does not
  check its source is not a compiler.
- **You need debugging, not "it worked / it did not".** Hence aspect views and
  lenses, slices, C4 levels, the **graph diff "target ↔ result"** and an acceptance
  verdict — debugging tools for architecture, not for text.
- **You need traceability.** Every element knows its source (code, document, task
  statement), and the generated code carries signed markers — so you can prove that
  what was built is what was designed.
- **You need an IDE shell.** Command palette, element inspector, level navigation,
  aspect sub-tabs, the entity tree, visual editing, projects, history and the agent
  activity feed — what an architect actually works with every day.

That is why ArchIDE is an **IDE** and not a code generator: it gives architecture
what programming languages gained over decades — a level of description,
validation, debugging and tooling.

> The architecture is one typed graph. Code, diagrams and documents are views of
> that graph. An entity exists exactly once (one "home" place); everywhere else
> refers to it by a stable ID.

## Idea

- **Dynamic stability instead of a "perfect" ontology.** We do **not** try to build
  an absolutely universal architecture model for every possible system — that is
  unreachable. Instead: an **easily extensible graph** with simple rules, whose
  consistency is **checked and maintained by the agent** in an
  "observe → divergence → correct" loop. Not a perfect base construct, but a
  **dynamically stabilised** one (see `docs/Concept.md` §0.1).
- **One graph, not a pile of documents.** In a document-centric model the same
  meaning is duplicated and drifts apart; in a graph model the entity exists once
  and everything else is a reference.
- **A system of subgraphs.** Spine, aspects, aggregates, projections, slices,
  versions and external evidence are all subgraphs of one graph, related by the
  algebra `merge · common · minus · view · slice · agg · pin · changes`
  (see `docs/Concept.md` §2).
- **Spine (C4) + aspects.** C4 is the structural identity
  (System→Container→Component→Module). Behaviour, data, interfaces, processes,
  access and security are orthogonal **aspects** stretched over the same spine
  (see `docs/Concept.md` §4).
- **Projections, not artefacts.** A diagram or a specification is computed from the
  model — so it cannot "drift away" from it.
- **Agents work over bounded slices.** A bounded context slice is generated from the
  model for a specific task; the agent never sees the whole system.
- **Two-pass reconstruction.** First from **source code**, then a comparison against
  the **documentation**; the divergences (deviations) are shown explicitly together
  with their source.
- **Machine-enforced rigour, not discipline.** A closed metamodel + a validator + a
  repair loop; unforgeable traceability markers (see `docs/Concept.md` §11).

In depth: [`docs/Concept.md`](docs/Concept.md) · System architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Scenarios: [`docs/SCENARIOS.md`](docs/SCENARIOS.md) · Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md).

An English version of the architect guides:
[`docs/ARCHITECT_GUIDE.en.md`](docs/ARCHITECT_GUIDE.en.md) ·
[`docs/ARCHITECT_GUIDE_UI.en.md`](docs/ARCHITECT_GUIDE_UI.en.md) ·
[`docs/RECONSTRUCTION_GUIDE.en.md`](docs/RECONSTRUCTION_GUIDE.en.md).

## Documentation

The architecture documents (all under `docs/`; the guides also have Russian
originals):

| Document | What it covers |
|---|---|
| [`docs/Concept.md`](docs/Concept.md) | **concept**: why ArchIDE exists, the system of subgraphs, spine and aspects, rigour, RAG, agent experience · [RU](docs/Concept.md) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | **system architecture**: containers and components, flows, PoC modules, current state |
| [`docs/SCENARIOS.md`](docs/SCENARIOS.md) | working scenarios and their test coverage |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | roadmap by phase |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | implementation plan: tasks and statuses |
| [`docs/ARCHITECT_GUIDE.en.md`](docs/ARCHITECT_GUIDE.en.md) | architect guide: two scenarios (repository / from scratch) and the API |
| [`docs/ARCHITECT_GUIDE_UI.en.md`](docs/ARCHITECT_GUIDE_UI.en.md) | the same, UI-only, with screenshots |
| [`docs/RECONSTRUCTION_GUIDE.en.md`](docs/RECONSTRUCTION_GUIDE.en.md) | a full reconstruction run of a real project across all aspects |
| [`docs/UI_REDESIGN.md`](docs/UI_REDESIGN.md) | the interface redesign proposal (Russian) |

## Screenshots

A graph-first interface: a canvas in C4 notation with **view sub-tabs** (C4 + one
per aspect), a collapsible **C4 entity tree**, drag-and-drop placement with a
saved layout, and the agent chat in the bottom panel. All the images are current
(`docs/screens/`) and were taken on real runs; the step-by-step walkthrough is in
[`docs/ARCHITECT_GUIDE_UI.en.md`](docs/ARCHITECT_GUIDE_UI.en.md).

**C4 view** — elements follow the notation (containers with a header band, external
systems with a dashed border), edges run border to border with an arrow and a type
label:

![Overview](docs/screens/01-overview.png)

**Aspect sub-tabs and the C4 tree** — an aspect opens as its own view for the
selected element, not as a layer on top of the C4 canvas:

![Aspect sub-tab](docs/screens/20-views.png)

**Canvas layout** — automatic groupings and manual dragging:

![Automatic layout](docs/screens/21-layout.png)

**Architecture synthesis from a chat description** — a single paragraph of
requirements produced a real architecture (live run, `runner=docker`):

![Synthesis in the chat](docs/screens/22-synthesis-chat.png)
![Synthesised graph](docs/screens/23-synthesis-graph.png)

The remaining images (element inspector, aspect lens, palette, report, templates,
validation, documentation, acceptance) are in `docs/screens/`.

## Current state (PoC)

Implemented in `archide-poc/`:

- **Agent management environment:** an agent registry (built-in + **Kilo Code CLI**)
  and execution in a **Docker sandbox without bind mounts** (an HTTP sandbox
  protocol).
- **LLM gateway:** agents talk through a gateway using **their own token**; the
  gateway injects the **real** inference key (the key never enters the container).
- **Observability:** a live activity feed (reasoning/logs/statuses), forced stop,
  asynchronous runs.
- **Shared volume** (a PVC analogue) for all agents; a context budget and
  **compression** (extract/truncate/drop).
- **Two-pass reconstruction** code → documentation with **merging and deviations**;
  validation on the host and inside the agent plus a repair loop (configurable
  number of attempts).
- **UI:** the graph level by level over C4, breadcrumbs, zoom in/out, source
  switching (all/code/docs), a self-contained SVG renderer.

Not implemented yet (see the roadmap): the model as a store/editor, aspects,
referential actions/transactions/impact, signed markers, the conformance layer.

## Quick start

There are no PyPI dependencies — the server and the agent run on the Python
standard library.

```bash
cd archide-poc
docker build -t archide-agent:latest agent/
docker build -t archide-kilo:latest agents/kilo/   # optional: Kilo Code CLI
./run.sh            # -> http://127.0.0.1:8000
```

Without Docker or an LLM: in the UI pick `mode = mock`, `runner = local` and the
whole pipeline runs without inference.

UI demo without a server: open `archide-poc/frontend/index.html?demo=1`
(`&mode=code|docs`, `&focus=<node-id>`).

## Tests (e2e)

End-to-end tests of the scenarios (`docs/SCENARIOS.md`) in two modes — **API** and
**browser** (headless Chrome, real user actions) — deterministic (mock/local +
stub docker, no real Docker or LLM):

```bash
bash tests/run_e2e.sh      # API + browser; prints E2E RESULT: PASS|FAIL
```

After any sufficiently large change the e2e run is mandatory (see [`AGENTS.md`](AGENTS.md)).

## Roadmap (short)

`Ф0` PoC (agent environment, partial) → `Ф1` sandbox productisation → `Ф2` a
universal architecture model → `Ф3` full reconstruction from code and
documentation (with validation) → `Ф4` model editing and automatic consistency →
`Ф5` code generation and validation from the model.

In full: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Repository layout

```
README.md            start page (Russian) · README.en.md — this English version
AGENTS.md            mandatory rules for the agent (e2e, tests, agreement)
docs/                the architecture documents (see "Documentation" above)
  Concept.md         the concept (a system of subgraphs: spine, aspects, versions, RAG, markers) + .en.md
  ARCHITECTURE.md    the architecture of the whole system + .en.md
  SCENARIOS.md       the main working scenarios
  ROADMAP.md         roadmap
  IMPLEMENTATION_PLAN.md  phased implementation plan (tasks and statuses)
  ARCHITECT_GUIDE.md      architect guide (repository / from scratch, API) + .en.md
  ARCHITECT_GUIDE_UI.md   UI walkthrough with screenshots + .en.md
  RECONSTRUCTION_GUIDE.md walkthrough of a full real-project run + .en.md
  UI_REDESIGN.md     the interface redesign proposal
  screens/           UI screenshots used by the architect guides
tests/               end-to-end tests (API + browser) + the run_e2e.sh orchestrator
archide-poc/         the working PoC (server, agents, UI)
  server.py          stdlib HTTP: UI + API + sandbox + LLM gateway
  backend/           model/validation/runner/merge/gateway/…
  agent/             the built-in agent (stdlib) + Dockerfile
  agents/kilo/       the Kilo Code CLI sandbox + Dockerfile
  frontend/          the UI (no build step) + the SVG renderer
```

## Status

Proof of concept. Licence — see [LICENSE](LICENSE).
