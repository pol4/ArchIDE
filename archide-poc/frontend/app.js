const NS = "http://www.w3.org/2000/svg";

const TYPE_COLORS = {
  System: "#8b5cf6",
  Container: "#3b82f6",
  Component: "#10b981",
  Module: "#14b8a6",
  ExternalSystem: "#f59e0b",
  Actor: "#ec4899",
  Interface: "#a855f7",
  DataStore: "#06b6d4",
  DataEntity: "#06b6d4",
};

const EDGE_COLORS = {
  contains: "#2a323d",
  depends_on: "#8b949e",
  calls: "#3b82f6",
  publishes: "#f59e0b",
  subscribes: "#f59e0b",
  reads: "#06b6d4",
  writes: "#06b6d4",
  exposes: "#a855f7",
  consumes: "#a855f7",
  implements: "#10b981",
  aggregates: "#ec4899",
};

// Aspects (mirror of backend/metamodel.py; overridden by GET /api/metamodel).
const ASPECT_COLORS = {
  structure: "#3b82f6",
  interface: "#a855f7",
  data: "#06b6d4",
  behavior: "#10b981",
  process: "#f59e0b",
  access: "#ec4899",
  deployment: "#eab308",
  quality: "#14b8a6",
  aggregation: "#8b5cf6",
  crosscutting: "#8b949e",
};
let ASPECT_TYPES = {
  structure: ["System", "Container", "Component", "Module", "Actor", "ExternalSystem"],
  interface: ["Interface", "Operation", "Message", "SchemaRef"],
  data: ["DataStore", "DataEntity", "Attribute"],
  behavior: ["StateMachine", "State", "Transition", "Event"],
  process: ["Process", "Activity", "Task", "Gateway", "Participant"],
  access: ["Role", "Permission", "Policy", "Principal", "Resource"],
  deployment: ["Node", "Environment", "Runtime", "InfraResource"],
  quality: ["NFR", "SLO", "Constraint"],
  aggregation: ["AggregationInterface"],
  crosscutting: ["Capability", "Decision", "Requirement", "Artifact", "Agent"],
};
let TYPE_TO_ASPECT = {};
function rebuildTypeMap() {
  TYPE_TO_ASPECT = {};
  Object.entries(ASPECT_TYPES).forEach(([aspect, types]) => {
    (types || []).forEach((t) => { TYPE_TO_ASPECT[t] = aspect; });
  });
}
function aspectOf(node) {
  return TYPE_TO_ASPECT[node && node.type] || "structure";
}
rebuildTypeMap();

const LEVEL_LABELS = ["System (L1)", "Container (L2)", "Component (L3)", "Module (L4)", "Level 5", "Level 6"];

const $ = (id) => document.getElementById(id);
const CONFIG_FIELDS = [
  "base_url", "api_key", "model", "mode", "timeout", "max_repairs", "runner",
  "max_chars", "compress_at", "strategy",
];

const state = {
  config: null, agents: [], runId: null, since: 0, poll: null, saveTimer: null,
  graph: null, focus: null, selected: null, byId: null, kids: null, rootId: null,
  sourceMode: "all", aspects: new Set(["structure"]),
};

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + (kind || "idle");
}

function log(text) {
  $("log").textContent = text || "";
}

function truncate(text, n) {
  text = String(text);
  return text.length > n ? text.slice(0, n - 1) + "…" : text;
}

// ------------------------------------------------------------------ config

function applyConfig(config) {
  state.config = config;
  const llm = config.llm || {};
  $("base_url").value = llm.base_url || "";
  $("api_key").value = llm.api_key || "";
  $("model").value = llm.model || "";
  $("mode").value = llm.mode || "live";
  $("timeout").value = llm.timeout || 180;
  $("runner").value = (config.agent && config.agent.runner) || "docker";
  $("volume").value = (config.agent && config.agent.volume) || "archide-agent-data";
  $("volume_enabled").checked = !(config.agent && config.agent.volume_enabled === false);
  $("max_repairs").value = config.agent && config.agent.max_repairs != null ? config.agent.max_repairs : 3;
  const ctx = config.context || {};
  $("max_chars").value = ctx.max_chars != null ? ctx.max_chars : 400000;
  $("compress_at").value = ctx.compress_at != null ? ctx.compress_at : 0.8;
  $("strategy").value = ctx.strategy || "extract";
  $("compare_docs").checked = ctx.compare_docs !== false;
  $("repo").value = (config.repo && config.repo.last) || "";
}

function collectConfig() {
  return {
    llm: {
      base_url: $("base_url").value.trim(),
      api_key: $("api_key").value,
      model: $("model").value.trim(),
      mode: $("mode").value,
      timeout: Number($("timeout").value) || 180,
    },
    agent: {
      runner: $("runner").value,
      volume: $("volume").value.trim(),
      volume_enabled: $("volume_enabled").checked,
      max_repairs: Number($("max_repairs").value) || 0,
    },
    context: {
      max_chars: Number($("max_chars").value) || 0,
      compress_at: Number($("compress_at").value) || 0,
      strategy: $("strategy").value,
      compare_docs: $("compare_docs").checked,
    },
  };
}

async function saveConfig(silent) {
  try {
    state.config = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectConfig()),
    });
    if (!silent) setStatus("Settings saved", "ok");
  } catch (e) {
    if (!silent) setStatus("Save failed: " + e.message, "error");
  }
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => saveConfig(true), 500);
}

// ------------------------------------------------------------------ activity

function clearActivity() {
  $("activity").innerHTML = "";
}

function appendEvents(events) {
  const box = $("activity");
  const stick = $("auto-scroll").checked;
  (events || []).forEach((ev) => {
    const line = document.createElement("div");
    line.className = "ev " + (ev.type || "log");
    const t = document.createElement("span");
    t.className = "t";
    const ts = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString() : "";
    t.textContent = ts ? `[${ts}] ${ev.type}` : ev.type;
    line.appendChild(t);
    line.appendChild(document.createTextNode(ev.text || ""));
    box.appendChild(line);
  });
  if (stick) box.scrollTop = box.scrollHeight;
}

// ------------------------------------------------------------------ run

async function runReconstruct() {
  const repo = $("repo").value.trim();
  const agent = $("agent").value;
  if (!repo) {
    setStatus("Enter a repository address", "error");
    return;
  }
  await saveConfig(true);
  clearActivity();
  log("");
  $("run").disabled = true;
  $("stop").disabled = false;
  setStatus("Running…", "busy");
  try {
    const res = await api("/api/reconstruct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo, agent }),
    });
    state.runId = res.run_id;
    state.since = 0;
    pollRun();  // start showing activity immediately
    api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: { last: repo } }),
    }).catch(() => {});
  } catch (e) {
    $("run").disabled = false;
    $("stop").disabled = true;
    setStatus("Failed: " + e.message, "error");
  }
}

async function pollRun() {
  if (!state.runId) return;
  try {
    const snap = await api(`/api/runs/${state.runId}?since=${state.since}`);
    appendEvents(snap.events);
    state.since = snap.event_count;

    if (snap.status === "running") {
      state.poll = setTimeout(pollRun, 600);
      return;
    }
    state.poll = null;
    $("run").disabled = false;
    $("stop").disabled = true;

    if (snap.graph) {
      setGraph(snap.graph);
      renderValidation(snap.validation);
    }
    logMeta(snap.meta);
    await showCoverage(snap.meta);
    if (snap.error) appendEvents([{ type: "error", text: snap.error, ts: Date.now() / 1000 }]);

    if (snap.status === "done") setStatus("Done", "ok");
    else if (snap.status === "invalid") setStatus("Done with validation errors", "error");
    else if (snap.status === "cancelled") setStatus("Cancelled", "error");
    else setStatus("Failed: " + (snap.error || "unknown error"), "error");
  } catch (e) {
    state.poll = null;
    $("run").disabled = false;
    $("stop").disabled = true;
    setStatus("Polling failed: " + e.message, "error");
  }
}

async function stopRun() {
  if (!state.runId) return;
  setStatus("Cancelling…", "busy");
  try {
    await api(`/api/runs/${state.runId}/cancel`, { method: "POST" });
  } catch (e) {
    setStatus("Cancel failed: " + e.message, "error");
  }
}

async function showCoverage(meta) {
  if (!meta || !meta.model_path) return;
  const name = String(meta.model_path).split("/").pop().replace(/\.json$/, "");
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/coverage`);
    const cov = res.coverage || {};
    const el = $("validation");
    const div = document.createElement("div");
    div.className = "ok";
    div.textContent = `coverage: ${cov.covered ?? 0}/${cov.requirements ?? 0} requirements`;
    el.appendChild(div);
  } catch (_) {
    /* coverage is best-effort */
  }
}

function logMeta(meta) {
  if (!meta) return;
  const c = meta.context || {};
  const ctxLine = c.compressed
    ? `context: compressed ${c.original_chars}->${c.final_chars} chars ` +
      `(${Math.round((c.ratio || 0) * 100)}%), files ${c.files_in}->${c.files_out}, ` +
      `extracted ${c.extracted}, truncated ${c.truncated}, dropped ${(c.dropped || []).length}`
    : `context: ${c.original_chars || 0} chars, ${c.files_in || 0} files (no compression)`;
  const modelLine = meta.model_hash ? `model: ${meta.model_hash}` : "";
  log(
    "runner: " + meta.runner + "\n" +
    "agent: " + meta.agent + "\n" +
    "repo: " + meta.repo_path + "\n" +
    ctxLine + (modelLine ? "\n" + modelLine : "")
  );
}

// ------------------------------------------------------------------ init

async function init() {
  buildAspectChips();
  if (new URLSearchParams(location.search).has("demo")) {
    await initDemo();
    return;
  }
  const [config, agents, metamodel] = await Promise.all([
    api("/api/config"), api("/api/agents"), api("/api/metamodel").catch(() => null),
  ]);
  applyMetamodel(metamodel);
  applyConfig(config);

  const sel = $("agent");
  sel.innerHTML = "";
  agents.agents.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.name;
    const lim = a.limits && Object.keys(a.limits).length ? ` | limits: ${JSON.stringify(a.limits)}` : "";
    opt.title = (a.description || "") + lim;
    sel.appendChild(opt);
  });

  wireControls();
  loadVolumeInfo();
}

function wireControls() {
  $("save").addEventListener("click", () => saveConfig(false));
  $("run").addEventListener("click", runReconstruct);
  $("stop").addEventListener("click", stopRun);
  $("volume-reset").addEventListener("click", resetVolume);
  $("zoom-in").addEventListener("click", zoomIn);
  $("zoom-out").addEventListener("click", zoomOut);
  $("zoom-reset").addEventListener("click", () => { state.focus = null; state.selected = null; render(); });
  CONFIG_FIELDS.forEach((id) => {
    $(id).addEventListener("change", scheduleSave);
    $(id).addEventListener("input", scheduleSave);
  });
  ["volume", "volume_enabled", "compare_docs"].forEach((id) => {
    $(id).addEventListener("input", scheduleSave);
    $(id).addEventListener("change", scheduleSave);
  });
  [["src-all", "all"], ["src-code", "code"], ["src-docs", "docs"]].forEach(([id, mode]) => {
    $(id).addEventListener("click", () => setSourceMode(mode));
  });
}

// Offline demo mode (no server): load demo.json and render a completed run.
// Used for screenshots / landing page: open index.html?demo=1 (optionally &mode=code|docs).
async function initDemo() {
  const params = new URLSearchParams(location.search);
  const file = params.get("demo") === "aspects" ? "demo-aspects.json" : "demo.json";
  const demo = await (await fetch(file)).json();
  const sel = $("agent");
  sel.innerHTML = "";
  (demo.agents || ["builtin"]).forEach((id) => {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id;
    sel.appendChild(opt);
  });
  $("repo").value = demo.repo || "";
  const llm = demo.llm || {};
  $("base_url").value = llm.base_url || "";
  $("model").value = llm.model || "";
  $("mode").value = llm.mode || "mock";
  $("runner").value = llm.runner || "docker";
  $("max_repairs").value = llm.max_repairs != null ? llm.max_repairs : 3;
  $("timeout").value = llm.timeout != null ? llm.timeout : 180;
  const ctx = demo.context || {};
  $("max_chars").value = ctx.max_chars != null ? ctx.max_chars : 400000;
  $("compress_at").value = ctx.compress_at != null ? ctx.compress_at : 0.8;
  $("strategy").value = ctx.strategy || "extract";
  $("compare_docs").checked = ctx.compare_docs !== false;

  setStatus(demo.status || "Done", demo.validation && demo.validation.ok ? "ok" : "error");
  logMeta(demo.meta);
  appendEvents(demo.events || []);
  setGraph(demo.graph);
  renderValidation(demo.validation);
  wireControls();

  const focus = params.get("focus");
  if (focus) { state.focus = focus; render(); }
  const mode = params.get("mode");
  if (mode) setSourceMode(mode);
}

function setSourceMode(mode) {
  state.sourceMode = mode;
  [["src-all", "all"], ["src-code", "code"], ["src-docs", "docs"]].forEach(([id, m]) => {
    $(id).classList.toggle("active", m === mode);
  });
  render();
}

async function loadVolumeInfo() {
  try {
    const v = await api("/api/volume");
    $("volume-info").textContent = v.enabled
      ? `${v.name} -> ${v.mount}${v.exists ? "" : " (created on first run)"}`
      : "disabled";
  } catch (e) {
    $("volume-info").textContent = "volume: " + e.message;
  }
}

async function resetVolume() {
  try {
    await api("/api/volume/reset", { method: "POST" });
    await loadVolumeInfo();
    setStatus("Shared volume reset", "ok");
  } catch (e) {
    setStatus("Reset failed: " + e.message, "error");
  }
}

function buildAspectChips() {
  const el = $("legend");
  el.innerHTML = "";
  Object.keys(ASPECT_TYPES).forEach((aspect) => {
    const btn = document.createElement("button");
    const active = state.aspects.has(aspect);
    btn.className = "chip" + (active ? " active" : "") + (aspect === "structure" ? " locked" : "");
    btn.textContent = aspect;
    btn.title = aspect === "structure"
      ? "structure (always on, for navigation)"
      : `toggle aspect: ${aspect}`;
    btn.style.borderColor = ASPECT_COLORS[aspect] || "#8b949e";
    if (active) btn.style.background = ASPECT_COLORS[aspect] || "#8b949e";
    btn.addEventListener("click", () => toggleAspect(aspect));
    el.appendChild(btn);
  });
}

function toggleAspect(aspect) {
  if (aspect === "structure") return; // structure stays for navigation
  if (state.aspects.has(aspect)) state.aspects.delete(aspect);
  else state.aspects.add(aspect);
  buildAspectChips();
  render();
}

function applyMetamodel(mm) {
  if (!mm || !mm.aspects) return;
  const rebuilt = {};
  Object.entries(mm.aspects).forEach(([name, spec]) => { rebuilt[name] = spec.node_types || []; });
  if (Object.keys(rebuilt).length) {
    ASPECT_TYPES = rebuilt;
    rebuildTypeMap();
    buildAspectChips();
  }
}

function renderValidation(v) {
  const el = $("validation");
  if (!v) { el.innerHTML = ""; return; }
  const parts = [];
  const s = v.stats || {};
  parts.push(`<div class="ok">nodes: ${s.nodes ?? "?"} · edges: ${s.edges ?? "?"} · roots: ${s.roots ?? "?"}</div>`);
  (v.errors || []).forEach((e) => parts.push(`<div class="err">✕ ${escapeHtml(e)}</div>`));
  (v.warnings || []).forEach((w) => parts.push(`<div class="warn">! ${escapeHtml(w)}</div>`));
  if (v.ok && !(v.warnings || []).length) parts.push(`<div class="ok">model valid</div>`);
  el.innerHTML = parts.join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ------------------------------------------------------------------ graph (one level at a time)

function setGraph(graph) {
  state.graph = graph && graph.nodes && graph.nodes.length ? graph : null;
  state.focus = null;
  state.selected = null;
  render();
}

function zoomIn() {
  const kids = state.kids || new Map();
  if (state.selected && (kids.get(state.selected) || []).length) {
    state.focus = state.selected;
    state.selected = null;
    render();
  }
}

function zoomOut() {
  const byId = state.byId || new Map();
  const cur = state.focus;
  if (!cur) return;
  const node = byId.get(cur);
  state.focus = node && node.parent ? node.parent : null;
  state.selected = null;
  render();
}

function render() {
  const svg = $("canvas");
  const wrap = $("canvas-wrap");
  const empty = $("empty");
  const crumb = $("breadcrumb");
  svg.innerHTML = "";
  crumb.innerHTML = "";

  const graph = state.graph;
  if (!graph) {
    empty.textContent = "Run a reconstruction to render the graph.";
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";

  const nodes = graph.nodes.filter((n) => n && n.id);
  const edges = graph.edges || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const kids = new Map();
  nodes.forEach((n) => {
    if (n.parent) {
      if (!kids.has(n.parent)) kids.set(n.parent, []);
      kids.get(n.parent).push(n);
    }
  });
  const roots = nodes.filter((n) => !n.parent || !byId.has(n.parent));
  const rootId = roots.length ? roots[0].id : nodes[0].id;
  const focusId = state.focus && byId.has(state.focus) ? state.focus : rootId;

  state.byId = byId;
  state.kids = kids;
  state.rootId = rootId;

  // absolute depth from the root
  const absDepth = new Map();
  const depthOf = (n, guard) => {
    guard = guard || new Set();
    if (absDepth.has(n.id)) return absDepth.get(n.id);
    if (!n.parent || !byId.has(n.parent) || guard.has(n.id)) { absDepth.set(n.id, 0); return 0; }
    guard.add(n.id);
    const d = depthOf(byId.get(n.parent), guard) + 1;
    absDepth.set(n.id, d);
    return d;
  };
  nodes.forEach((n) => depthOf(n));

  // exactly one level: the direct children of the focus node, filtered by
  // selected aspects (Ф2.5/Ф2.6) and source mode
  const mode = state.sourceMode;
  const srcOf = (el) => (el && el.sources && el.sources.length ? el.sources : ["code"]);
  const matchMode = (el) => mode === "all" || srcOf(el).includes(mode);
  const matchAspect = (el) => state.aspects.has(aspectOf(el));
  const children = (kids.get(focusId) || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const single = children.length === 0 && byId.has(focusId);
  const visNodes = (single ? [byId.get(focusId)] : children).filter((n) => matchMode(n) && matchAspect(n));
  if (!visNodes.length) {
    renderBreadcrumb(focusId, byId);
    empty.textContent = `No elements of the selected aspects/sources at this level.`;
    empty.style.display = "flex";
    return;
  }

  renderBreadcrumb(focusId, byId);

  const level = single ? absDepth.get(focusId) : (absDepth.get(focusId) || 0) + 1;
  const levelLabel = LEVEL_LABELS[level] || `Level ${level}`;

  const NW = 200, NH = 80, GX = 26, GY = 74, PAD = 60, LABEL_W = 120;
  const n = visNodes.length;
  const cols = Math.max(1, Math.min(5, n));
  const rowsCount = Math.max(1, Math.ceil(n / cols));
  const gridW = cols * (NW + GX) - GX;
  const gridH = rowsCount * (NH + GY) - GY;
  const totalW = LABEL_W + gridW + PAD * 2;
  const totalH = gridH + PAD * 2 + 30;
  const pos = new Map();
  visNodes.forEach((node, i) => {
    const r = Math.floor(i / cols);
    const c = i % cols;
    pos.set(node.id, { x: LABEL_W + PAD + c * (NW + GX), y: PAD + 30 + r * (NH + GY), w: NW, h: NH });
  });

  const defs = document.createElementNS(NS, "defs");
  Object.entries(EDGE_COLORS).forEach(([t, c]) => {
    const m = document.createElementNS(NS, "marker");
    m.setAttribute("id", "arrow-" + t);
    m.setAttribute("viewBox", "0 0 10 10");
    m.setAttribute("refX", "9");
    m.setAttribute("refY", "5");
    m.setAttribute("markerWidth", "7");
    m.setAttribute("markerHeight", "7");
    m.setAttribute("orient", "auto-start-reverse");
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", "M0,0 L10,5 L0,10 z");
    p.setAttribute("fill", c);
    m.appendChild(p);
    defs.appendChild(m);
  });
  svg.appendChild(defs);

  const viewport = document.createElementNS(NS, "g");
  svg.appendChild(viewport);

  const levelText = document.createElementNS(NS, "text");
  levelText.setAttribute("class", "node-type");
  levelText.setAttribute("x", 12);
  levelText.setAttribute("y", 24);
  levelText.textContent = levelLabel;
  viewport.appendChild(levelText);

  // edges between visible nodes only
  const visible = new Set(visNodes.map((x) => x.id));
  const neighbors = new Set();
  if (state.selected) {
    edges.forEach((e) => {
      if (e.source === state.selected) neighbors.add(e.target);
      if (e.target === state.selected) neighbors.add(e.source);
    });
  }
  edges.forEach((e) => {
    if (!visible.has(e.source) || !visible.has(e.target) || !matchMode(e)) return;
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    const x1 = a.x + a.w / 2, y1 = a.y + a.h / 2, x2 = b.x + b.w / 2, y2 = b.y + b.h / 2;
    const t = e.type || "depends_on";
    const path = document.createElementNS(NS, "path");
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - Math.abs(x2 - x1) * 0.08;
    path.setAttribute("d", `M${x1},${y1} Q${mx},${my} ${x2},${y2}`);
    path.setAttribute("class", "edge-line" + (t === "contains" ? " edge-contains" : ""));
    if (t !== "contains") path.setAttribute("marker-end", `url(#arrow-${t})`);
    const title = document.createElementNS(NS, "title");
    title.textContent = `${e.source} → ${e.target}\n${t}${e.description ? "\n" + e.description : ""}`;
    path.appendChild(title);
    viewport.appendChild(path);
  });

  const SRC_COLORS = { code: "#22d3ee", docs: "#f97316" };
  visNodes.forEach((node) => {
    const p = pos.get(node.id);
    if (!p) return;
    const sources = srcOf(node);
    const both = sources.length > 1;
    const drillable = (kids.get(node.id) || []).length > 0;
    const selected = state.selected === node.id;
    const neighbor = !selected && neighbors.has(node.id);
    const aspect = aspectOf(node);
    let color = ASPECT_COLORS[aspect] || "#8b949e";
    if (mode === "all" && !both) color = SRC_COLORS[sources[0]] || color;
    const g = document.createElementNS(NS, "g");
    g.setAttribute("transform", `translate(${p.x},${p.y})`);
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("class", "node-box" + (drillable ? " drillable" : ""));
    rect.setAttribute("width", p.w);
    rect.setAttribute("height", p.h);
    rect.setAttribute("fill", selected ? "#1f2a37" : "#161b22");
    rect.setAttribute("stroke", color);
    rect.setAttribute("stroke-width", selected ? 3 : neighbor ? 2.5 : 1.5);
    if (!both) rect.setAttribute("stroke-dasharray", "6 4");
    g.appendChild(rect);
    const bar = document.createElementNS(NS, "rect");
    bar.setAttribute("width", 4);
    bar.setAttribute("height", p.h);
    bar.setAttribute("fill", color);
    bar.setAttribute("rx", 2);
    g.appendChild(bar);
    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "node-label");
    label.setAttribute("x", 14);
    label.setAttribute("y", 22);
    label.textContent = truncate(node.name || node.id, 22);
    g.appendChild(label);
    const type = document.createElementNS(NS, "text");
    type.setAttribute("class", "node-type");
    type.setAttribute("x", 14);
    type.setAttribute("y", 39);
    type.textContent = (node.type || "?") + (drillable ? "  ⊕" : "");
    g.appendChild(type);
    const src = document.createElementNS(NS, "text");
    src.setAttribute("class", "node-src");
    src.setAttribute("x", 14);
    src.setAttribute("y", 56);
    src.textContent = "source: " + (both ? "code + docs" : sources[0]) + (both ? "" : "  ⚠");
    g.appendChild(src);
    if (node.note) {
      const note = document.createElementNS(NS, "text");
      note.setAttribute("class", "node-note");
      note.setAttribute("x", 14);
      note.setAttribute("y", 72);
      note.textContent = truncate(node.note, 30);
      g.appendChild(note);
    }
    const title = document.createElementNS(NS, "title");
    title.textContent =
      `${node.id}\n${node.type} · ${aspect}\nsources: ${sources.join(", ")}` +
      (node.note ? `\n${node.note}` : "") +
      (drillable ? "\nclick to open the next level" : "") +
      (node.description ? `\n${node.description}` : "");
    g.appendChild(title);
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (drillable) { state.focus = node.id; state.selected = null; }
      else { state.selected = node.id; }
      render();
    });
    viewport.appendChild(g);
  });

  const view = { x: 0, y: 0, k: 1 };
  const r = wrap.getBoundingClientRect();
  const fitK = Math.min(1, Math.min(r.width / totalW, r.height / totalH)) * 0.95;
  view.k = fitK > 0 ? fitK : 1;
  view.x = (r.width - totalW * view.k) / 2;
  view.y = (r.height - totalH * view.k) / 2;
  const apply = () => viewport.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
  apply();

  let dragging = false, sx = 0, sy = 0;
  svg.addEventListener("mousedown", (ev) => {
    dragging = true;
    sx = ev.clientX - view.x;
    sy = ev.clientY - view.y;
    svg.classList.add("dragging");
  });
  window.addEventListener("mouseup", () => { dragging = false; svg.classList.remove("dragging"); });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging) return;
    view.x = ev.clientX - sx;
    view.y = ev.clientY - sy;
    apply();
  });
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nk = Math.max(0.2, Math.min(3, view.k * factor));
    view.x = mx - (mx - view.x) * (nk / view.k);
    view.y = my - (my - view.y) * (nk / view.k);
    view.k = nk;
    apply();
  }, { passive: false });
}

function renderBreadcrumb(focusId, byId) {
  const el = $("breadcrumb");
  const path = [];
  let cur = focusId;
  let guard = 0;
  while (cur && byId.has(cur) && guard++ < 64) {
    path.unshift(byId.get(cur));
    cur = byId.get(cur).parent;
  }
  path.forEach((node, i) => {
    if (i) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      el.appendChild(sep);
    }
    const b = document.createElement("button");
    b.textContent = truncate(node.name || node.id, 28);
    b.title = `${node.id} (${node.type})`;
    b.addEventListener("click", () => { state.focus = node.id; state.selected = null; render(); });
    el.appendChild(b);
  });
}

init().catch((e) => setStatus("Init failed: " + e.message, "error"));
