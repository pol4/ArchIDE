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

// C4 notation: the shape of an element is decided by its kind, not by its aspect.
// Person/Actor get the C4 stick figure, databases a cylinder, external systems a
// dashed border, containers a header band, deployment nodes a double border.
const C4_KIND = {
  Person: "person", Actor: "person", Principal: "person",
  System: "system", ExternalSystem: "external",
  Container: "container", Component: "component", Module: "component",
  DataStore: "database", DataEntity: "database", Attribute: "database",
  Interface: "interface", Operation: "interface", Message: "interface",
  Node: "deployment", Environment: "deployment", InfraResource: "deployment", Runtime: "deployment",
  Role: "person", Permission: "access", Policy: "access",
  NFR: "quality", SLO: "quality", Requirement: "quality", Constraint: "quality",
  Process: "process", Activity: "process", Task: "process", Gateway: "process", Participant: "process",
  StateMachine: "behavior", State: "behavior", Transition: "behavior",
};
const C4_KIND_LABEL = {
  person: "Person", system: "Software System", external: "External System",
  container: "Container", component: "Component", database: "Database",
  interface: "Interface", deployment: "Deployment Node", access: "Role",
  quality: "Quality", process: "Process", behavior: "State",
};

function c4Kind(type) {
  return C4_KIND[String(type || "")] || "generic";
}

// point where the segment centre→target leaves a box (so edges touch borders,
// not centres, which is what made them hard to see)
function edgeAnchor(cx, cy, w, h, dx, dy, gap) {
  const hw = w / 2 + gap, hh = h / 2 + gap;
  const sx = Math.abs(dx) < 1e-6 ? Infinity : hw / Math.abs(dx);
  const sy = Math.abs(dy) < 1e-6 ? Infinity : hh / Math.abs(dy);
  const k = Math.min(sx, sy);
  if (!Number.isFinite(k)) return { x: cx, y: cy };
  return { x: cx + dx * k, y: cy + dy * k };
}

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
  graph: null, fullGraph: null, focus: null, selected: null, byId: null, kids: null, rootId: null,
  awinMode: {}, awinSeq: {}, lastAwin: null, lens: null, lensFocus: null,
  levelOnly: null, levelsUp: 1, levelsDown: 2,
  sourceMode: "all", aspects: new Set(["structure"]),
  view: "c4", treeSel: null, treeHidden: false,
  layout: { mode: "", layout: "", positions: {} }, layoutModel: null, layoutTimer: null,
  ragHits: [], promptHistory: [], promptIndex: 0, pendingDoc: null, modelName: null,
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
  const badge = $("drawer-status");
  if (badge) { badge.textContent = text; badge.className = "status " + (kind || "idle"); }
  if (state._drawer && /running|reconstruct|pass \d/i.test(String(text))) state._drawer.classList.add("open");
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
  $("max_context").value = (config.llm && config.llm.max_context_chars != null) ? config.llm.max_context_chars : 60000;
  $("compress_at").value = ctx.compress_at != null ? ctx.compress_at : 0.8;
  $("strategy").value = ctx.strategy || "extract";
  $("compare_docs").checked = ctx.compare_docs !== false;
  if ($("bp_repo")) $("bp_repo").value = (config.repo && config.repo.last) || "";
}

function collectConfig() {
  return {
    llm: {
      max_context_chars: Number($("max_context").value) || 0,
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
    if (ev.type === "answer") {
      // Ф6.2: every answer can be rated; the rating is attributed to the answer
      attachFeedback(line, ev.about || { model: state.modelName || null, run: state.runId || null,
                                         view: state.view || "c4" });
    }
    if (ev.attach) {
      const b = document.createElement("button");
      b.className = "attach-btn";
      b.textContent = "\u2192 prompt";
      b.title = "attach this RAG result to the prompt";
      b.addEventListener("click", () => {
        const input = $("prompt_input");
        input.value = (input.value ? input.value + " " : "") + ev.attach;
        input.focus();
      });
      line.appendChild(b);
    }
    box.appendChild(line);
  });
  if (stick) box.scrollTop = box.scrollHeight;
}

// ------------------------------------------------------------------ run

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

async function stopRun() {
  if (!state.runId) return;
  setStatus("Cancelling…", "busy");
  try {
    await api(`/api/runs/${state.runId}/cancel`, { method: "POST" });
  } catch (e) {
    setStatus("Cancel failed: " + e.message, "error");
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
    if ($("stop")) $("stop").disabled = true;
    if (snap.graph) {
      setGraph(snap.graph);
      renderValidation(snap.validation);
    }
    logMeta(snap.meta);
    if (snap.meta && snap.meta.model_path) {
      const name = String(snap.meta.model_path).split("/").pop().replace(/\.json$/, "");
      state.modelName = name;
      ["model_load", "gen_model", "arch_model"].forEach((id) => { if ($(id)) $(id).value = name; });
      // a model that arrived from a run must accept canvas edits too: remember
      // which model the arrangement belongs to (otherwise a drag is not saved)
      loadLayout(name).then(() => render()).catch(() => {});
      loadProjects().catch(() => {});
    }
    await showCoverage(snap.meta);
    if (snap.error) appendEvents([{ type: "error", text: snap.error, ts: Date.now() / 1000 }]);
    if (snap.status === "done") setStatus("Done", "ok");
    else if (snap.status === "invalid") setStatus("Done with validation errors", "error");
    else if (snap.status === "cancelled") setStatus("Cancelled", "error");
    else setStatus("Failed: " + (snap.error || "unknown error"), "error");
  } catch (e) {
    state.poll = null;
    if ($("stop")) $("stop").disabled = true;
    setStatus("Polling failed: " + e.message, "error");
  }
}

async function startReconstruction(repo, agent, projectId, options) {
  if (!repo) {
    setStatus("Enter a repository address", "error");
    return null;
  }
  await saveConfig(true);
  clearActivity();
  log("");
  if ($("stop")) $("stop").disabled = false;
  setStatus("Running…", "busy");
  try {
    const res = await api("/api/reconstruct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ repo, agent: agent || "builtin",
                                           project: projectId || state.project || null }, options || {})),
    });
    state.runId = res.run_id;
    state.since = 0;
    pollRun();  // start showing activity immediately
    api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: { last: repo } }),
    }).catch(() => {});
    return res.run_id;
  } catch (e) {
    if ($("stop")) $("stop").disabled = true;
    setStatus("Failed: " + e.message, "error");
    return null;
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
  wireCanvas();
  wireArchitectPanel();
  wireLayout();
  wireInspector();
  wirePalette();
  wireProjects();
  wireValidation();
  await loadProjects();
  await restoreUi();
  loadVolumeInfo();
}

// ---------------------------------------------------------------------------
// Architect scenario panel (Ф8/ARCHITECT_GUIDE): every workflow operation in UI
// ---------------------------------------------------------------------------
function archShow(text) {
  const out = $("arch-out");
  if (out) out.textContent = typeof text === "string" ? text : JSON.stringify(text, null, 2);
}

function archModel() {
  const name = ($("arch_model") ? $("arch_model").value : "").trim() || state.modelName || "";
  if (!name) { archShow("pick a model first (Load)"); return null; }
  return name;
}

async function archCall(label, method, path, body) {
  archShow(`${label}: running…`);
  try {
    const options = method === "POST"
      ? { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }
      : undefined;
    const res = await api(path, options);
    archShow(`${label}:\n` + JSON.stringify(res, null, 2));
    revertFromResponse(res);   // panel steps update the app state (e.g. reconcile)
    return res;
  } catch (e) {
    archShow(`${label} failed: ${e.message}`);
    return null;
  }
}

// after a step that may have changed the saved model, refresh what the app shows
function revertFromResponse(res) {
  if (res && res.model) { setGraph(res.model); }
  if (res && res.hash && state.modelName) { refreshLocalModelHash(); }
}

async function refreshLocalModelHash() {
  const name = state.modelName;
  if (!name) return;
  try {
    const saved = await api(`/api/models/${encodeURIComponent(name)}`);
    if (saved && saved.graph) { state.fullGraph = saved.graph; state.graph = saved.graph; render(); }
  } catch (e) { /* keep the current view */ }
}

async function loadArchModel() {
  const name = ($("arch_model") ? $("arch_model").value : "").trim();
  if (!name) { archShow("enter a model name"); return; }
  try {
    const saved = await api(`/api/models/${encodeURIComponent(name)}`);
    const graph = saved.graph || saved;
    setGraph(graph);
    state.modelName = name;
    if ($("model_load")) $("model_load").value = name;
    if ($("gen_model")) $("gen_model").value = name;
    archShow(`loaded ${name}: ${(graph.nodes || []).length} nodes, ${(graph.edges || []).length} edges`
      + (saved.hash ? `\nhash: ${saved.hash}` : ""));
  } catch (e) {
    archShow(`load failed: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// UI-1: graph-first layout — panels hidden by default, setup in a modal,
// events in a bottom drawer, reports in a modal
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Ф10.2: projects — the top-bar switcher, per-project view and chat
// ---------------------------------------------------------------------------
let projectViewTimer = null;

async function loadProjects() {
  try {
    const listing = await api("/api/projects");
    state.projects = listing.projects || [];
  } catch (e) {
    state.projects = [];
  }
  const select = $("project-select");
  if (!select) return state.projects;
  select.innerHTML = "";
  if (!state.projects.length) {
    const option = document.createElement("option");
    option.value = ""; option.textContent = "no projects";
    select.appendChild(option);
    return state.projects;
  }
  state.projects.forEach((project) => {
    const option = document.createElement("option");
    option.value = project.id;
    const counts = project.nodes != null ? ` · ${project.nodes}/${project.edges}` : "";
    option.textContent = `${project.name}${counts}`;
    option.title = `model: ${project.model || "—"}${project.hash ? " · " + project.hash : ""}`;
    select.appendChild(option);
  });
  if (state.project) select.value = state.project;
  return state.projects;
}

function projectView() {
  return {
    lens: state.lens || null,
    lensFocus: state.lensFocus || null,
    levelOnly: state.levelOnly,
    levelsUp: state.levelsUp,
    levelsDown: state.levelsDown,
    focus: state.focus || null,
    aspects: [...(state.aspects || new Set())],
    sourceMode: state.sourceMode || "all",
    awinMode: state.awinMode || {},
  };
}

function applyProjectView(view) {
  const v = view || {};
  state.lens = v.lens || null;
  state.lensFocus = v.lensFocus || null;
  state.levelOnly = v.levelOnly === undefined ? null : v.levelOnly;
  if (typeof v.levelsUp === "number") state.levelsUp = v.levelsUp;
  if (typeof v.levelsDown === "number") state.levelsDown = v.levelsDown;
  state.focus = v.focus || null;
  if (Array.isArray(v.aspects) && v.aspects.length) state.aspects = new Set(v.aspects);
  if (v.sourceMode) state.sourceMode = v.sourceMode;
  if (v.awinMode) state.awinMode = v.awinMode;
  const levelSelect = $("level-jump");
  if (levelSelect) levelSelect.value = state.levelOnly === null ? "" : String(state.levelOnly);
  buildAspectChips();
}

function saveProjectView() {
  if (!state.project || !state.uiReady) return;
  clearTimeout(projectViewTimer);
  projectViewTimer = setTimeout(() => {
    api(`/api/projects/${encodeURIComponent(state.project)}/view`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ view: projectView() }),
    }).catch(() => {});
  }, 400);
}

function renderProjectChat(chat) {
  const entries = (chat || []).map((entry) => ({
    ts: entry.ts || Date.now() / 1000,
    type: entry.role === "agent" ? "reasoning" : "user",
    text: entry.text || "",
  }));
  if (entries.length) appendEvents(entries);
}

function sendFeedback(kind, signal, about, comment) {
  const user = ($("doc_user") && $("doc_user").value) || "default";
  api("/api/feedback", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, kind, signal, about, comment: comment || null }),
  }).then(() => setStatus(`feedback recorded: ${signal}`, "ok"))
    .catch(() => {});
}

// Ф6.2: the architect can rate an answer; the system also observes outcomes
function attachFeedback(el, about) {
  if (!el || !about) return;
  const row = document.createElement("span");
  row.className = "fb-row";
  [["up", "👍"], ["down", "👎"]].forEach(([signal, glyph]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fb-btn";
    btn.dataset.signal = signal;
    btn.title = signal === "up" ? "this answer was useful" : "this answer was not useful";
    btn.textContent = glyph;
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      row.querySelectorAll(".fb-btn").forEach((b) => b.classList.remove("picked"));
      btn.classList.add("picked");
      sendFeedback("explicit", signal, about);
    });
    row.appendChild(btn);
  });
  el.appendChild(row);
}

function recordProjectChat(role, text, extra) {
  // Ф6.1: every turn goes into the architect's dialogue store with its provenance
  // (which model/run/selection/view it was about); the project chat is mirrored
  // server-side, so both views stay in sync.
  const provenance = Object.assign({
    model: state.modelName || null,
    project: state.project || null,
    view: state.view || "c4",
    node: state.selected || null,
    run: state.runId || null,
  }, extra || {});
  const user = ($("doc_user") && $("doc_user").value) || "default";
  api("/api/dialogue", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user, role, text, project: state.project || null, provenance }),
  }).catch(() => {});
}

async function openProject(projectId) {
  if (!projectId) return;
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(projectId)}/open`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    state.project = projectId;
    state.modelName = payload.model || null;
    if (payload.graph) setGraph(payload.graph);
    ["model_load", "arch_model", "gen_model"].forEach((id) => {
      if (!$(id)) return;
      if (payload.model) $(id).value = payload.model;
    });
    if (!payload.graph) {
      // a project without a model must not keep the previous project's graph
      setGraph(null);
      ["model_load", "arch_model", "gen_model"].forEach((id) => { if ($(id)) $(id).value = ""; });
    }
    applyProjectView(payload.view);
    renderProjectChat(payload.chat);
    if ($("project-select")) $("project-select").value = projectId;
    try { localStorage.setItem("archide.project", projectId); } catch (e) { /* ignore */ }
    render();
    setStatus(`project ${projectId} · ${payload.model || "no model yet"}`, "ok");
  } catch (e) {
    setStatus(`project ${projectId}: ${e.message}`, "error");
  }
}

async function createProjectFromModal() {
  const name = ($("project_name") ? $("project_name").value : "").trim();
  if (!name) { setStatus("project name is required", "error"); return; }
  try {
    const res = await api("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, repo: ($("project_repo") ? $("project_repo").value.trim() : "") }),
    });
    $("project-modal").classList.add("hidden");
    $("project_name").value = "";
    $("project_repo").value = "";
    await loadProjects();
    const id = (res.project || {}).id || name;
    state.project = id;
    renderProjectsPane();
    await openProject(id);
    setStatus(`project ${id} created`, "ok");
  } catch (e) {
    setStatus("project creation failed: " + e.message, "error");
  }
}

async function openTemplateWizard() {
  try {
    const res = await api("/api/templates");
    const list = res.templates || [];
    const select = $("tpl-select");
    select.innerHTML = "";
    list.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = `${t.title} · ${t.node_count}/${t.edge_count}`;
      opt.dataset.desc = t.description || "";
      select.appendChild(opt);
    });
    if (!select.options.length) { setStatus("no templates available", "error"); return; }
    $("tpl-desc").textContent = select.options[0].dataset.desc || "";
    $("tpl-name").value = "";
    $("tpl-modal").classList.remove("hidden");
    $("tpl-name").focus();
  } catch (e) {
    setStatus("templates failed to load: " + e.message, "error");
  }
}

async function createFromTemplate() {
  const template = $("tpl-select").value;
  const name = ($("tpl-name").value || "").trim();
  if (!name) { setStatus("model name is required", "error"); return; }
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/scaffold`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template }),
    });
    $("tpl-modal").classList.add("hidden");
    ["model_load", "gen_model", "arch_model"].forEach((id) => { if ($(id)) $(id).value = name; });
    await loadModel();
    await loadProjects();
    setStatus(`model ${name} created from template ${template}`, "ok");
  } catch (e) {
    setStatus("template failed: " + e.message, "error");
  }
}

function bpInfo(text) {
  const el = $("bp_info");
  if (el) el.textContent = text;
}

function renderProjectsPane() {
  const select = $("bp_project");
  if (!select) return;
  const current = state.project || "";
  select.innerHTML = "";
  (state.projects || []).forEach((project) => {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = `${project.name}${project.nodes != null ? ` · ${project.nodes}/${project.edges}` : ""}`
      + (project.model ? ` · ${project.model}` : " · no model");
    select.appendChild(option);
  });
  if (!state.projects || !state.projects.length) {
    const option = document.createElement("option");
    option.value = ""; option.textContent = "no projects yet";
    select.appendChild(option);
  }
  if (current) select.value = current;
  const agents = $("bp_agent");
  if (agents && !agents.options.length && $("agent")) {
    [...$("agent").options].forEach((opt) => {
      const copy = document.createElement("option");
      copy.value = opt.value; copy.textContent = opt.textContent;
      agents.appendChild(copy);
    });
  }
  if (agents && $("agent") && $("agent").value) agents.value = $("agent").value;
  bpInfo(state.project
    ? `active project: ${state.project}${state.modelName ? " · model " + state.modelName : ""}`
    : "pick a project, or create one and reconstruct a repository");
}

// Ф10.3/Ф10.4: the reconstruction starts from the project, not from Setup
async function runFromProjects() {
  const repo = ($("bp_repo") ? $("bp_repo").value : "").trim();
  if (!repo) { bpInfo("enter a repository (URL or path) first"); return; }
  let projectId = state.project;
  try {
    if (!projectId) {
      const guess = repo.replace(/\/+$/, "").split("/").pop().replace(/\.git$/, "") || "project";
      const created = await api("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: guess, repo }),
      });
      projectId = (created.project || {}).id || guess;
      state.project = projectId;
      await loadProjects();
    }
    const agent = ($("bp_agent") && $("bp_agent").value) || ($("agent") ? $("agent").value : "") || "builtin";
    const runId = await startReconstruction(repo, agent, projectId);
    if (!runId) { bpInfo("reconstruction was not started"); return; }
    bpInfo(`run ${runId} started for project ${projectId} — watch the Events tab`);
    showBottom("events");
  } catch (e) {
    bpInfo("reconstruction failed: " + e.message);
  }
}

// ---------------------------------------------------------------------------
// Ф10.6: the validation surface — check, agent review, propose, apply
// ---------------------------------------------------------------------------
function valShow(summary, history) {
  const sum = $("val-summary");
  if (sum && summary != null) sum.textContent = summary;
  const hist = $("val-history");
  if (hist && history != null) hist.textContent = history;
}

function valAppend(line) {
  const hist = $("val-history");
  if (!hist) return;
  const stamp = new Date().toLocaleTimeString();
  hist.textContent = `[${stamp}] ${line}\n` + (hist.textContent || "");
}

function valModel() {
  const name = state.modelName;
  if (!name) { valShow("load a model first (Projects tab or Model name + Load)"); return null; }
  return name;
}

async function valCheck() {
  const name = valModel();
  if (!name) return null;
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/validate`);
    const validation = res.validation || {};
    const violations = res.violations || [];
    const errors = validation.errors || [];
    valShow(`model ${name}\nvalidation: ${validation.ok ? "ok" : errors.length + " error(s)"}`
      + `\nconstraint violations: ${violations.length}`
      + (errors.length ? "\n" + errors.slice(0, 6).map((e) => "· " + e).join("\n") : "")
      + (violations.length ? "\n" + violations.slice(0, 6).map((v) => "· " + (v.message || JSON.stringify(v))).join("\n") : ""));
    valAppend(`check: ${validation.ok ? "ok" : "errors"} · violations ${violations.length}`);
    recordProjectChat("agent", `validation ${name}: ${validation.ok ? "ok" : "errors"} · violations ${violations.length}`,
                      { outcome: "validation", ok: !!validation.ok, violations: violations.length });
    return res;
  } catch (e) {
    valShow("validation failed: " + e.message);
    return null;
  }
}

async function valAgent() {
  const name = valModel();
  if (!name) return null;
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const issues = res.issues || res.divergences || [];
    const proposals = res.proposals || [];
    const byKind = {};
    issues.forEach((issue) => { const k = issue.kind || "issue"; byKind[k] = (byKind[k] || 0) + 1; });
    valShow(`agent review of ${name}: ${issues.length} issue(s), ${proposals.length} proposed fix(es)`
      + "\n" + Object.entries(byKind).map(([k, n]) => `· ${k}: ${n}`).join("\n")
      + (proposals.length ? "\npress «Apply fixes» to run them as a transaction" : ""));
    valAppend(`agent review: ${issues.length} issue(s), ${proposals.length} fix(es)`);
    state.valProposals = proposals;
    return res;
  } catch (e) {
    valShow("agent review failed: " + e.message);
    return null;
  }
}

async function valReconcile(apply) {
  const name = valModel();
  if (!name) return null;
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/reconcile`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apply: !!apply }),
    });
    const convergence = res.convergence || {};
    const applied = res.applied || [];
    valShow(`${apply ? "applied fixes" : "proposed fixes"} for ${name}: `
      + `${applied.length || (res.proposals || []).length} change(s)`
      + (convergence.before != null ? `\nconvergence: ${convergence.before} -> ${convergence.after}` : ""));
    valAppend(`${apply ? "apply" : "propose"}: ${applied.length || (res.proposals || []).length} change(s)`);
    if (apply) {
      const saved = await api(`/api/models/${encodeURIComponent(name)}`);
      const graph = saved.graph || saved;
      setGraph(graph);
      state.fullGraph = graph;
      render();
      await valCheck();
    }
    return res;
  } catch (e) {
    valShow(`${apply ? "apply" : "propose"} failed: ` + e.message);
    return null;
  }
}

function wireValidation() {
  if (!$("val-check")) return;
  $("btab-validation").addEventListener("click", () => showBottom("validation"));
  $("val-check").addEventListener("click", valCheck);
  $("val-agent").addEventListener("click", valAgent);
  $("val-propose").addEventListener("click", () => valReconcile(false));
  $("val-apply").addEventListener("click", () => valReconcile(true));
  if ($("insp-validate")) {
    $("insp-validate").addEventListener("click", async () => {
      showBottom("validation");
      await valCheck();   // the agent review stays an explicit action in the panel
    });
  }
}

function wireProjects() {
  if (!$("project-select")) return;
  $("project-select").addEventListener("change", () => openProject($("project-select").value));
  $("project-new").addEventListener("click", () => {
    $("project-modal").classList.remove("hidden");
    if ($("project_name")) $("project_name").focus();
  });
  $("project-close").addEventListener("click", () => $("project-modal").classList.add("hidden"));
  $("project-create").addEventListener("click", createProjectFromModal);
  $("project-modal").addEventListener("click", (ev) => {
    if (ev.target.id === "project-modal") $("project-modal").classList.add("hidden");
  });
}

// ---------------------------------------------------------------------------
// UI-6: polish — persisted view state, theme, and the full hotkey set
// ---------------------------------------------------------------------------
const UI_KEY = "archide.ui";

function persistUi() {
  if (!state.uiReady) return;   // do not clobber the saved state while wiring up
  saveProjectView();
  try {
    const layout = document.querySelector("main.layout");
    localStorage.setItem(UI_KEY, JSON.stringify({
      model: state.modelName || null,
      lens: state.lens || null,
      lensFocus: state.lensFocus || null,
      levelOnly: state.levelOnly,
      levelsUp: state.levelsUp,
      levelsDown: state.levelsDown,
      sidebarHidden: !!(layout && layout.classList.contains("sidebar-hidden")),
      bottom: state.bottom || "projects",
      open: !!(layout && layout.classList.contains("inspector-open")),
      awinMode: state.awinMode || {},
      theme: document.documentElement.dataset.theme || "dark",
      view: state.view || "c4",
      treeHidden: !!state.treeHidden,
      treeSel: state.treeSel || null,
      panels: {
        drawer: $("events-drawer") ? $("events-drawer").style.height || "" : "",
        sidebar: document.querySelector("aside.sidebar") ? document.querySelector("aside.sidebar").style.width || "" : "",
        tree: $("c4-tree") ? $("c4-tree").style.width || "" : "",
      },
    }));
  } catch (e) { /* storage may be unavailable */ }
}

function savedUi() {
  try { return JSON.parse(localStorage.getItem(UI_KEY) || "null"); } catch (e) { return null; }
}

async function restoreUi() {
  // the shell is wired now: user changes may be persisted (the initial layout
  // wiring happens before this call and stays unpersisted)
  state.uiReady = true;
  const saved = savedUi();
  if (!saved) return;
  if (saved.theme === "light") document.documentElement.dataset.theme = "light";
  if (saved.awinMode) state.awinMode = saved.awinMode;
  if (typeof saved.levelsUp === "number") state.levelsUp = saved.levelsUp;
  if (typeof saved.view === "string" && VIEW_TABS.includes(saved.view)) state.view = saved.view;
  if (saved.treeSel) state.treeSel = saved.treeSel;
  if (saved.treeHidden && $("c4-tree")) {
    state.treeHidden = true;
    $("c4-tree").classList.add("hidden");
    if ($("tree-resize")) $("tree-resize").classList.add("hidden");
  }
  if (saved.panels) {
    if (saved.panels.drawer && $("events-drawer")) {
      $("events-drawer").style.height = saved.panels.drawer;
      $("events-drawer").style.maxHeight = "none";
    }
    if (saved.panels.sidebar) {
      const sb = document.querySelector("aside.sidebar");
      if (sb) sb.style.width = saved.panels.sidebar;
    }
    if (saved.panels.tree && $("c4-tree")) $("c4-tree").style.width = saved.panels.tree;
  }
  if (typeof saved.levelsDown === "number") state.levelsDown = saved.levelsDown;
  if (saved.levelOnly !== null && saved.levelOnly !== undefined) state.levelOnly = saved.levelOnly;
  let activeProject = null;
  try { activeProject = localStorage.getItem("archide.project"); } catch (e) { /* ignore */ }
  if (activeProject) {
    await openProject(activeProject);
    return;
  }
  if (saved.model) {
    try {
      const loaded = await api(`/api/models/${encodeURIComponent(saved.model)}`);
      const graph = loaded.graph || loaded;
      setGraph(graph);
      state.modelName = saved.model;
      if ($("model_load")) $("model_load").value = saved.model;
      if ($("arch_model")) $("arch_model").value = saved.model;
      if ($("gen_model")) $("gen_model").value = saved.model;
    } catch (e) { /* the model may be gone */ }
  }
  if (saved.lens && state.fullGraph) {
    state.lens = saved.lens;
    state.lensFocus = saved.lensFocus || null;
  }
  if (saved.open) {
    const layout = document.querySelector("main.layout");
    if (layout) layout.classList.add("inspector-open");
  }
  if (saved.bottom && saved.bottom !== "events") showBottom(saved.bottom);
  render();
}

function toggleTheme() {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
  setStatus(`theme: ${root.dataset.theme}`, "ok");
  persistUi();
}

// hotkeys that act on the selected element / open a surface
function handleHotkey(ev) {
  if (ev.target && /^(input|select|textarea)$/i.test(ev.target.tagName)) return false;
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return false;
  const key = String(ev.key);
  if (key === " ") {
    showBottom("agent");
    ev.preventDefault();
    return true;
  }
  if (key === "r" || key === "R") { $("setup-modal").classList.remove("hidden"); return true; }
  if (key === "a" || key === "A") {
    if (!state.selected) { setStatus("select an element first (click it), then press A", "error"); return true; }
    const chips = aspectChips(state.selected);
    if (chips.length) showAspectLens(chips[0][0], state.selected);
    return true;
  }
  if (key === "e" || key === "E") {
    if (!state.selected) { setStatus("select an element first (click it), then press E", "error"); return true; }
    if ($("node-rename")) $("node-rename").focus();
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// UI-5: command palette — every operation without hunting through panels
// ---------------------------------------------------------------------------
function paletteCommands() {
  const model = state.modelName;
  const selected = state.selected;
  const commands = [
    { group: "project", label: "New project…", run: () => $("project-new").click() },
    { group: "model", label: "Load model…", hint: "from data/models", run: () => loadModelFromPalette() },
    { group: "model", label: "New model from template…", hint: "scaffold", run: () => openTemplateWizard() },
    { group: "model", label: "Download architecture documentation…", hint: "markdown", run: () => exportDocumentation() },
    { group: "run", label: "Reconstruct graph…", hint: "setup + run", run: () => $("setup-open").click() },
    { group: "model", label: "Export model", run: () => exportGraph() },
    { group: "model", label: "Import model…", run: () => $("import_file").click() },
    { group: "view", label: "Back to C4 view", run: () => { if (state.lens) closeLens(); } },
    { group: "view", label: "Fit the whole view (home)", run: () => goHome() },
    { group: "view", label: "Export all aspect views (SVG)", run: () => exportAllAspects() },
    { group: "view", label: "Level L1 · System", run: () => setLevel(0) },
    { group: "view", label: "Level L2 · Container", run: () => setLevel(1) },
    { group: "view", label: "Level L3 · Component", run: () => setLevel(2) },
    { group: "view", label: "Level L4 · Module", run: () => setLevel(3) },
    { group: "report", label: "Report: validation + metrics + coverage", run: () => $("report-open").click() },
    { group: "validation", label: "Validation: check the model", run: () => { showBottom("validation"); valCheck(); } },
    { group: "validation", label: "Validation: ask the agent to review", run: () => { showBottom("validation"); valAgent(); } },
    { group: "validation", label: "Validation: propose fixes", run: () => { showBottom("validation"); valReconcile(false); } },
    { group: "validation", label: "Validation: apply fixes", run: () => { showBottom("validation"); valReconcile(true); } },
    { group: "report", label: "Validate the model", run: () => archRun("validate", "GET", `/api/models/${encodeURIComponent(model)}/validate`) },
    { group: "report", label: "Metrics per aspect", run: () => archRun("metrics", "POST", "/api/eval", { graph: state.fullGraph || state.graph }) },
    { group: "report", label: "Requirement coverage", run: () => archRun("coverage", "GET", `/api/models/${encodeURIComponent(model)}/coverage`) },
    { group: "model", label: "Generate artifacts & package", run: () => archRun("generate", "POST", `/api/models/${encodeURIComponent(model)}/generate`) },
    { group: "handoff", label: "Download handoff package (.tar.gz)", run: () => downloadHandoff() },
    { group: "model", label: "Coverage matrix", run: () => archRun("coverage matrix", "POST", `/api/models/${encodeURIComponent(model)}/coverage-matrix`) },
    { group: "model", label: "Review the model", run: () => archRun("review", "POST", `/api/models/${encodeURIComponent(model)}/review`) },
    { group: "model", label: "Reconcile (apply fixes)", run: () => archRun("reconcile", "POST", `/api/models/${encodeURIComponent(model)}/reconcile`, { apply: true }) },
    { group: "handoff", label: "Code generation (external agent)", run: () => archRun("code generation", "POST", `/api/models/${encodeURIComponent(model)}/codegen`, { node: selected || "", target: ($("arch_target").value || model) }) },
    { group: "handoff", label: "Acceptance: diff vs result model", run: () => archRun("graph diff", "POST", "/api/graphdiff", { target_model: model, result_model: $("arch_target").value }) },
  ];
  (state.projects || []).forEach((project) => {
    commands.push({ group: "project", label: `Switch project: ${project.name}`,
      run: () => openProject(project.id) });
  });
  Object.keys(ASPECT_TYPES || {}).forEach((aspect) => {
    commands.push({ group: "aspect", label: `Aspect lens: ${aspect}`, run: () => showAspectLens(aspect, selected || null) });
  });
  if (selected) {
    commands.push({ group: "element", label: "Element: work package", run: () => archRun("work package", "POST", `/api/models/${encodeURIComponent(model)}/workpackage`, { node: selected }) });
    commands.push({ group: "element", label: "Element: traceability markers", run: () => archRun("markers", "POST", `/api/models/${encodeURIComponent(model)}/markers`, { node: selected }) });
    commands.push({ group: "element", label: "Element: impact", run: () => archRun("impact", "POST", `/api/models/${encodeURIComponent(model)}/impact`, { node: selected }) });
    commands.push({ group: "element", label: "Element: conformance loop", run: () => archRun("conformance", "POST", `/api/models/${encodeURIComponent(model)}/conformance-loop`, { node: selected }) });
  }
  return commands;
}

function setLevel(index) {
  state.levelOnly = index;
  state.focus = null;
  const select = $("level-jump");
  if (select) select.value = String(index);
  render();
  setStatus(`level L${index + 1}`, "ok");
  persistUi();
}

function loadModelFromPalette() {
  // no blocking dialogs: reveal the panels and focus the model field
  const layout = document.querySelector("main.layout");
  if (layout && layout.classList.contains("sidebar-hidden")) {
    layout.classList.remove("sidebar-hidden");
    if (state.graph || state.fullGraph) render();
  }
  if ($("arch_model") && state.modelName) $("arch_model").value = state.modelName;
  if ($("model_load")) $("model_load").focus();
  setStatus("type a model name in «Model name» and press Load", "ok");
}

async function archRun(label, method, path, body) {
  const res = await archCall(label, method, path, body);
  return res;
}

function paletteFiltered() {
  const query = ($("palette-input").value || "").trim().toLowerCase();
  const all = paletteCommands();
  if (!query) return all;
  return all.filter((c) => (c.group + " " + c.label).toLowerCase().includes(query));
}

function renderPalette() {
  const list = $("palette-list");
  const items = paletteFiltered();
  list.innerHTML = "";
  items.forEach((command, index) => {
    const row = document.createElement("div");
    row.className = "palette-item" + (index === state.paletteIndex ? " active" : "");
    row.dataset.index = String(index);
    row.innerHTML = `<span class="group">${command.group}</span><span>${command.label}</span>`
      + (command.hint ? `<span class="hint">${command.hint}</span>` : "");
    row.addEventListener("click", () => runPaletteItem(index));
    list.appendChild(row);
  });
  if (!items.length) list.innerHTML = "<div class='palette-item'><span>no matching command</span></div>";
  if (state.paletteIndex >= items.length) state.paletteIndex = Math.max(0, items.length - 1);
  return items;
}

function runPaletteItem(index) {
  const items = paletteFiltered();
  const command = items[index];
  closePalette();
  if (command) {
    try { command.run(); } catch (e) { setStatus("command failed: " + e.message, "error"); }
  }
}

function openPalette() {
  state.paletteIndex = 0;
  $("palette").classList.remove("hidden");
  $("palette-input").value = "";
  renderPalette();
  $("palette-input").focus();
}

function closePalette() {
  const palette = $("palette");
  if (palette) palette.classList.add("hidden");
}

function wirePalette() {
  if (!$("palette")) return;
  $("palette-open").addEventListener("click", openPalette);
  $("palette").addEventListener("click", (ev) => { if (ev.target.id === "palette") closePalette(); });
  $("palette-input").addEventListener("input", () => { state.paletteIndex = 0; renderPalette(); });
  $("palette-input").addEventListener("keydown", (ev) => {
    const items = paletteFiltered();
    if (ev.key === "ArrowDown") { state.paletteIndex = Math.min(items.length - 1, state.paletteIndex + 1); renderPalette(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { state.paletteIndex = Math.max(0, state.paletteIndex - 1); renderPalette(); ev.preventDefault(); }
    else if (ev.key === "Enter") { runPaletteItem(state.paletteIndex); ev.preventDefault(); }
    else if (ev.key === "Escape") { closePalette(); ev.preventDefault(); }
  });
}

function wireLayout() {
  const layout = document.querySelector("main.layout");
  const setHidden = (hidden) => {
    if (layout) layout.classList.toggle("sidebar-hidden", hidden);
    state.sidebarHidden = hidden;
    // the canvas just changed size: re-fit so the graph stays centred and visible
    if (state.graph || state.fullGraph) render();
    persistUi();
  };
  setHidden(true);   // the graph gets the whole screen by default
  if (layout) layout.dataset.wired = "1";
  $("sidebar-toggle").addEventListener("click", () => {
    setHidden(!(layout && layout.classList.contains("sidebar-hidden")));
  });

  const open = (id) => $(id).classList.remove("hidden");
  const close = (id) => $(id).classList.add("hidden");
  $("setup-open").addEventListener("click", () => open("setup-modal"));
  $("setup-close").addEventListener("click", () => close("setup-modal"));
  $("report-open").addEventListener("click", async () => { open("report-modal"); await refreshReport(); });
  $("report-close").addEventListener("click", () => close("report-modal"));
  ["setup-modal", "report-modal"].forEach((id) => {
    $(id).addEventListener("click", (ev) => { if (ev.target.id === id) close(id); });
  });
  window.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && String(ev.key).toLowerCase() === "k") { openPalette(); ev.preventDefault(); return; }
    if (handleHotkey(ev)) return;
    if (ev.key === "Escape") { close("setup-modal"); close("report-modal"); closePalette();
      if (state.lens) closeLens(); else closeInspector(); }
  });

  const drawer = $("events-drawer");
  const refit = () => { if (state.graph || state.fullGraph) render(); };
  $("drawer-toggle").addEventListener("click", () => { drawer.classList.toggle("open"); });
  // the drawer animates its height: re-fit when the animation settles
  drawer.addEventListener("transitionend", refit);
  state._drawer = drawer;

  // any panel that changes the canvas size (drawer, comm panel, aspect windows)
  // must trigger a re-fit, otherwise the graph drifts out of the visible area
  const wrap = $("canvas-wrap");
  if (wrap && typeof ResizeObserver === "function") {
    let last = { w: 0, h: 0 };
    let timer = null;
    new ResizeObserver(() => {
      const r = wrap.getBoundingClientRect();
      if (Math.abs(r.width - last.w) < 2 && Math.abs(r.height - last.h) < 2) return;
      last = { w: r.width, h: r.height };
      if (!state.graph && !state.fullGraph) return;
      clearTimeout(timer);
      timer = setTimeout(() => render(), 120);
    }).observe(wrap);
  }
}

// ---------------------------------------------------------------------------
// UI-2: element inspector — everything about an element opens from the element
// ---------------------------------------------------------------------------
function inspectorOpen() {
  const layout = document.querySelector("main.layout");
  return !!(layout && layout.classList.contains("inspector-open"));
}

function closeInspector() {
  const layout = document.querySelector("main.layout");
  if (layout) layout.classList.remove("inspector-open");
  state.selected = null;
  updateAgentContext();
  render();
}

// aspects the element takes part in: itself, its parent, its children, its neighbours
function aspectChips(nodeId) {
  const graph = state.fullGraph || state.graph;
  if (!graph) return [];
  const byId = new Map((graph.nodes || []).map((n) => [n.id, n]));
  const node = byId.get(nodeId);
  if (!node) return [];
  const related = new Set([node.id]);
  (graph.nodes || []).forEach((n) => { if (n.parent === nodeId) related.add(n.id); });
  if (node.parent && byId.has(node.parent)) related.add(node.parent);
  (graph.edges || []).forEach((e) => {
    if (e.source === nodeId && byId.has(e.target)) related.add(e.target);
    if (e.target === nodeId && byId.has(e.source)) related.add(e.source);
  });
  const counts = new Map();
  related.forEach((id) => {
    const n = byId.get(id);
    if (!n) return;
    const a = aspectOf(n);
    counts.set(a, (counts.get(a) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function openInspector(nodeId) {
  const graph = state.fullGraph || state.graph;
  const byId = new Map(((graph && graph.nodes) || []).map((n) => [n.id, n]));
  const node = byId.get(nodeId);
  const layout = document.querySelector("main.layout");
  if (!node || !layout) return;
  layout.classList.add("inspector-open");
  updateAgentContext();
  $("insp-name").textContent = node.name || node.id;
  $("insp-type").textContent = node.type || "";
  const parent = node.parent ? byId.get(node.parent) : null;
  $("insp-facts").textContent = [
    `id: ${node.id}`,
    `parent: ${parent ? (parent.name || parent.id) : "—"}`,
    `evidence: ${(node.evidence || []).join(", ") || "—"}`,
    node.description ? `about: ${node.description}` : "",
  ].filter(Boolean).join("\n");

  const chips = $("insp-aspects");
  chips.innerHTML = "";
  aspectChips(nodeId).forEach(([aspect, count]) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.dataset.aspect = aspect;
    chip.innerHTML = `${aspect}<span class="count">${count}</span>`;
    chip.title = `show the ${aspect} aspect around this element`;
    chip.addEventListener("click", () => showAspectLens(aspect, nodeId));
    chips.appendChild(chip);
  });
  if (!chips.children.length) chips.textContent = "no related elements";

  const childTypes = $("insp-child-type");
  childTypes.innerHTML = "";
  (ASPECT_TYPES[aspectOf(node)] || ["Component"]).forEach((t) => {
    const option = document.createElement("option");
    option.value = t; option.textContent = t;
    childTypes.appendChild(option);
  });

  const targets = $("insp-link-target");
  targets.innerHTML = "";
  ((graph && graph.nodes) || []).forEach((n) => {
    if (n.id === nodeId) return;
    const option = document.createElement("option");
    option.value = n.id;
    option.textContent = `${n.name || n.id} · ${n.type}`;
    targets.appendChild(option);
  });
  const linkTypes = $("insp-link-type");
  linkTypes.innerHTML = "";
  ["calls", "depends_on", "flows_to", "owns", "anchored_to", "reads", "writes", "has", "satisfies"].forEach((t) => {
    const option = document.createElement("option");
    option.value = t; option.textContent = t;
    linkTypes.appendChild(option);
  });
  $("node-rename").value = node.name || "";
}

// ---------------------------------------------------------------------------
// UI-3: aspect lens — the whole canvas shows the notation of one aspect,
// opened from an element (chip in the inspector)
// ---------------------------------------------------------------------------
function renderLensBar() {
  const bar = $("lens-bar");
  if (!bar || !state.lens) return;
  bar.classList.remove("hidden");
  $("lens-aspect").textContent = state.lens;
  const graph = state.fullGraph || state.graph;
  const node = state.lensFocus ? ((graph && graph.nodes) || []).find((n) => n.id === state.lensFocus) : null;
  $("lens-element").textContent = node ? `· ${node.name || node.id}` : "";
  const modes = ASPECT_MODES[state.lens] || ["graph"];
  const select = $("lens-mode");
  if (select.dataset.aspect !== state.lens) {
    select.innerHTML = "";
    modes.forEach((m) => {
      const option = document.createElement("option");
      option.value = m; option.textContent = m;
      select.appendChild(option);
    });
    select.dataset.aspect = state.lens;
  }
  select.value = (state.awinMode && state.awinMode[state.lens]) || ASPECT_DEFAULT_MODE[state.lens] || "graph";
}

function hideLensBar() {
  const bar = $("lens-bar");
  if (bar) bar.classList.add("hidden");
}

function drawLens(aspect) {
  const svg = $("canvas");
  const graph = state.fullGraph || state.graph;
  svg.innerHTML = "";
  if (!graph) return;
  const nodes = (graph.nodes || []).filter((n) => aspectOf(n) === aspect);
  if (!nodes.length) {
    svg.innerHTML = `<text x="20" y="40" class="node-detail">no elements of the ${aspect} aspect</text>`;
    return;
  }
  const mode = (state.awinMode && state.awinMode[aspect]) || ASPECT_DEFAULT_MODE[aspect] || "graph";
  let diagram;
  try { diagram = (ASPECT_RENDERERS[mode] || rGraph)(aspect, graph, nodes); }
  catch (e) { svg.innerHTML = `<text x="20" y="40" class="node-detail">render failed: ${e.message}</text>`; return; }
  const w = Number(diagram.getAttribute("width")) || 800;
  const h = Number(diagram.getAttribute("height")) || 400;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.innerHTML = diagram.innerHTML;
  // mark the element the lens was opened from
  const focus = state.lensFocus ? (graph.nodes || []).find((n) => n.id === state.lensFocus) : null;
  if (focus) {
    const name = String(focus.name || focus.id);
    [...svg.querySelectorAll("text")].forEach((t) => {
      if (t.textContent.trim() === name) { t.setAttribute("fill", "var(--accent)"); t.setAttribute("font-weight", "700"); }
    });
  }
  state._cam = null;   // the lens is fitted by viewBox: pan/zoom of the C4 view do not apply
  state._viewport = null;
}

function showAspectLens(aspect, nodeId) {
  state.lens = aspect;
  state.lensFocus = nodeId || state.selected || null;
  closeInspectorPanelKeepSelection();
  render();
  persistUi();
  setStatus(`aspect ${aspect}${state.lensFocus ? " · " + state.lensFocus.split("/").pop() : ""}`, "ok");
}

function closeLens() {
  state.lens = null;
  state.lensFocus = null;
  // the lens fitted the canvas with a viewBox: drop it, the layered view has its own camera
  const svg = $("canvas");
  if (svg) { svg.removeAttribute("viewBox"); svg.removeAttribute("preserveAspectRatio"); }
  render();
  persistUi();
}

// hide the inspector column without clearing the selection (used when opening a lens)
function closeInspectorPanelKeepSelection() {
  const layout = document.querySelector("main.layout");
  if (layout) layout.classList.remove("inspector-open");
}

// "show the aspect of this element": filter the graph to that aspect and centre on it
function showAspectForElement(aspect, nodeId) {
  state.aspects = new Set([aspect]);
  buildAspectChips();
  const graph = state.fullGraph || state.graph;
  const node = ((graph && graph.nodes) || []).find((n) => n.id === nodeId);
  state.focus = node && node.parent ? node.parent : null;
  state.levelOnly = null;
  render();
  setStatus(`aspect ${aspect} · ${node ? (node.name || node.id) : ""}`, "ok");
}

function createChildFromInspector() {
  const name = $("insp-child-name").value.trim();
  const type = $("insp-child-type").value;
  if (!state.selected || !name || !type) return;
  const parent = state.selected;
  const id = `${type.toLowerCase()}://${(state.modelName || "app")}/${name.replace(/[^A-Za-z0-9._-]+/g, "-")}`;
  applyCanvasEdit([{ op: "add_node", node: { id, type, name, parent, evidence: [] } }]);
  $("insp-child-name").value = "";
}

function linkFromInspector() {
  const target = $("insp-link-target").value;
  const type = $("insp-link-type").value;
  if (!state.selected || !target || !type) return;
  applyCanvasEdit([{ op: "add_edge", edge: { source: state.selected, target, type } }]);
}

// UI-4: the agent lives next to the inspector and knows what is selected
function showBottom(tab) {
  const drawer = $("events-drawer");
  if (drawer) drawer.classList.add("open");
  ["events", "agent", "projects", "validation"].forEach((name) => {
    const pane = $("bpane-" + name);
    if (pane) pane.classList.toggle("hidden", name !== tab);
    const tabEl = $("btab-" + name);
    if (tabEl) tabEl.classList.toggle("active", name === tab);
  });
  state.bottom = tab;
  if (tab === "agent") {
    updateAgentContext();
    if ($("prompt_input")) $("prompt_input").focus();
  }
  if (tab === "projects") renderProjectsPane();
  persistUi();
}

function updateAgentContext() {
  const el = $("agent-context");
  if (!el) return;
  const graph = state.fullGraph || state.graph;
  const node = state.selected && graph ? (graph.nodes || []).find((n) => n.id === state.selected) : null;
  el.textContent = "context: "
    + (node ? `${node.name || node.id} (${node.type})` : "no element selected")
    + (state.lens ? ` · aspect ${state.lens}` : "")
    + (state.modelName ? ` · model ${state.modelName}` : "");
}

// "apply as transaction": the agent's answer (a JSON array of edit operations)
// goes through the same gated edit pipeline as a manual change
async function applyAgentAnswer() {
  const text = ($("rag_answer") ? $("rag_answer").value : "").trim();
  if (!text) { setStatus("paste the agent's operations JSON into “Answer to verify” first", "error"); return; }
  let operations = null;
  try {
    const parsed = JSON.parse(text);
    operations = Array.isArray(parsed) ? parsed
      : (parsed && Array.isArray(parsed.operations) ? parsed.operations : null);
  } catch (e) { /* not JSON at all */ }
  if (!operations || !operations.length) {
    setStatus("the answer is not a JSON array of edit operations", "error");
    return;
  }
  await applyCanvasEdit(operations);
  setStatus(`applied ${operations.length} operation(s) from the agent`, "ok");
  sendFeedback("implicit", "accepted", { model: state.modelName || null, run: state.runId || null,
                                         applied: operations.length, source: "agent-answer" });
}

function wireInspector() {
  if (!$("inspector")) return;
  // NOTE: every lookup below must exist — a throw here silently drops the rest
  // of the wiring (that is how a removed top-bar button broke the bottom tabs)
  $("theme-toggle").addEventListener("click", toggleTheme);
  $("btab-events").addEventListener("click", () => showBottom("events"));
  $("btab-agent").addEventListener("click", () => showBottom("agent"));
  $("btab-projects").addEventListener("click", () => showBottom("projects"));
  $("agent-apply").addEventListener("click", applyAgentAnswer);
  $("bp_open").addEventListener("click", () => openProject($("bp_project").value));
  $("bp_new").addEventListener("click", () => $("project-new").click());
  if ($("bp_tpl")) $("bp_tpl").addEventListener("click", () => { showBottom("projects"); openTemplateWizard(); });
  if ($("tpl-select")) $("tpl-select").addEventListener("change", () => {
    const opt = $("tpl-select").selectedOptions[0];
    $("tpl-desc").textContent = (opt && opt.dataset.desc) || "";
  });
  if ($("tpl-create")) $("tpl-create").addEventListener("click", createFromTemplate);
  if ($("tpl-close")) $("tpl-close").addEventListener("click", () => $("tpl-modal").classList.add("hidden"));
  if ($("tpl-modal")) $("tpl-modal").addEventListener("click", (ev) => {
    if (ev.target.id === "tpl-modal") $("tpl-modal").classList.add("hidden");
  });
  $("bp_run").addEventListener("click", runFromProjects);
  $("lens-back").addEventListener("click", closeLens);
  $("lens-mode").addEventListener("change", () => {
    if (!state.lens) return;
    state.awinMode[state.lens] = $("lens-mode").value;
    render();
  });
  $("insp-close").addEventListener("click", closeInspector);
  $("insp-create-child").addEventListener("click", createChildFromInspector);
  $("insp-link").addEventListener("click", linkFromInspector);
  const layout = document.querySelector("main.layout");
  const wrap = $("canvas-wrap");
  if (wrap && layout && typeof ResizeObserver === "function") {
    new ResizeObserver(() => { if (state.graph || state.fullGraph) setTimeout(() => render(), 60); }).observe(layout);
  }
}

// Report tab: validation + per-aspect metrics + requirement coverage
async function refreshReport() {
  const body = $("report-body");
  const name = state.modelName;
  const graph = state.fullGraph || state.graph;
  if (!name || !graph) { body.textContent = "load a model first"; return; }
  body.textContent = "collecting…";
  try {
    const [validation, metrics, coverage] = await Promise.all([
      api(`/api/models/${encodeURIComponent(name)}/validate`),
      api("/api/eval", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph }) }),
      api(`/api/models/${encodeURIComponent(name)}/coverage`),
    ]);
    const m = metrics.metrics || {};
    const aspects = m.aspects || {};
    const rows = Object.entries(aspects).map(([a, v]) =>
      `${a.padEnd(12)} elements=${String(v.elements).padStart(4)}  coverage=${String(v.coverage).padEnd(5)}`
      + ` orphans=${String(v.orphans).padStart(3)}  no_evidence=${v.without_evidence}`).join("\n");
    body.textContent = [
      `model: ${name}`,
      `validation: ${(validation.validation || {}).ok ? "ok" : "FAILED"}`,
      `score: ${m.score}`,
      `covered: ${((m.aspect_summary || {}).covered || []).join(", ") || "-"}`,
      `empty: ${((m.aspect_summary || {}).empty || []).join(", ") || "-"}`,
      `requirements: ${JSON.stringify(coverage.coverage)}`,
      "",
      rows,
    ].join("\n");
    const resultModel = ($("arch_target") && $("arch_target").value || "").trim();
    if (resultModel) {
      try {
        const diff = await api("/api/graphdiff", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_model: name, result_model: resultModel }) });
        const s2 = diff.summary || {};
        const reqs = diff.requirements || {};
        body.textContent += "\n\nacceptance (" + resultModel + "): " + diff.verdict
          + `  matched=${s2.matched}/${s2.target_elements} missing=${s2.missing}`
          + ` (in scope ${s2.missing_in_scope ?? s2.missing}) extra=${s2.extra}`
          + ` coverage=${s2.coverage}`
          + ` requirements=${reqs.covered ?? 0}/${reqs.total ?? 0}`;
      } catch (e) {
        body.textContent += "\n\nacceptance: " + e.message;
      }
    }
  } catch (e) {
    body.textContent = "report failed: " + e.message;
  }
}

// Ф8.1: one archive with the work package, artifacts, markers and instructions
async function downloadHandoff() {
  const name = archModel();
  if (!name) return;
  archShow("handoff: building the package…");
  try {
    const response = await fetch(`/api/models/${encodeURIComponent(name)}/handoff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node: ($("arch_node") ? $("arch_node").value.trim() : "") }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}-handoff.tar.gz`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    archShow(`handoff: downloaded ${a.download} (${Math.round(blob.size / 1024)} KB)\n`
      + "contents: manifest.json · workpackage.json · artifacts/ · markers.json · README.md");
  } catch (e) {
    archShow("handoff failed: " + e.message);
  }
}

function wireArchitectPanel() {
  if (!$("arch-panel")) return;
  $("arch-load").addEventListener("click", loadArchModel);
  $("arch-validate").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("validate", "GET", `/api/models/${encodeURIComponent(name)}/validate`);
  });
  $("arch-eval").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("metrics", "POST", "/api/eval", { graph: state.fullGraph || state.graph });
  });
  $("arch-coverage").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("coverage", "GET", `/api/models/${encodeURIComponent(name)}/coverage`);
  });
  $("arch-impact").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("impact", "POST", `/api/models/${encodeURIComponent(name)}/impact`,
      { node: $("arch_node").value.trim() });
  });
  $("arch-workpackage").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("work package", "POST", `/api/models/${encodeURIComponent(name)}/workpackage`,
      { node: $("arch_node").value.trim() });
  });
  $("arch-markers").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("markers", "POST", `/api/models/${encodeURIComponent(name)}/markers`,
      { node: $("arch_node").value.trim() });
  });
  $("arch-matrix").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("coverage matrix", "POST", `/api/models/${encodeURIComponent(name)}/coverage-matrix`);
  });
  $("arch-conformance").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("conformance", "POST", `/api/models/${encodeURIComponent(name)}/conformance-loop`,
      { node: $("arch_node").value.trim() });
  });
  $("arch-review").addEventListener("click", () => {
    const name = archModel(); if (name) archCall("review", "POST", `/api/models/${encodeURIComponent(name)}/review`);
  });
  $("arch-reconcile").addEventListener("click", () => {
    const name = archModel();
    if (name) archCall("reconcile", "POST", `/api/models/${encodeURIComponent(name)}/reconcile`, { apply: true });
  });
  $("arch-generate").addEventListener("click", () => {
    const name = archModel();
    if (name) archCall("generate", "POST", `/api/models/${encodeURIComponent(name)}/generate`);
  });
  $("arch-handoff").addEventListener("click", downloadHandoff);
  $("arch-codegen").addEventListener("click", () => {
    const name = archModel(); if (!name) return;
    archCall("code generation", "POST", `/api/models/${encodeURIComponent(name)}/codegen`,
      { node: $("arch_node").value.trim(), target: $("arch_target").value.trim() || name,
        mode: ($("arch_codegen_mode") ? $("arch_codegen_mode").value : "") || undefined });
  });
  $("arch-diff").addEventListener("click", () => {
    const name = archModel(); if (!name) return;
    const result = ($("arch_target").value || "").trim();
    if (!result) { archShow("enter the result model name (reconstruct the delivered repository first)"); return; }
    archCall("graph diff", "POST", "/api/graphdiff", {
      target_model: name, result_model: result,
      scope: ($("arch_node") ? $("arch_node").value.trim() : "") || undefined,
    });
  });
}

function wireControls() {
  $("save").addEventListener("click", () => saveConfig(false));
  $("stop").addEventListener("click", stopRun);
  $("volume-reset").addEventListener("click", resetVolume);
  $("zoom-in").addEventListener("click", () => zoomCanvas(1.2));
  $("zoom-out").addEventListener("click", () => zoomCanvas(1 / 1.2));
  $("zoom-reset").addEventListener("click", goHome);
  $("level-in").addEventListener("click", zoomIn);
  $("level-out").addEventListener("click", zoomOut);
  $("lvl-up").addEventListener("change", () => {
    state.levelsUp = Math.max(0, Math.min(4, Number($("lvl-up").value) || 0));
    render();
  });
  $("lvl-down").addEventListener("change", () => {
    state.levelsDown = Math.max(0, Math.min(4, Number($("lvl-down").value) || 0));
    render();
  });
  window.addEventListener("keydown", (ev) => {
    if (ev.target && /^(input|select|textarea)$/i.test(ev.target.tagName)) return;
    if (ev.key === "+" || ev.key === "=") { zoomCanvas(1.2); }
    else if (ev.key === "-" || ev.key === "_") { zoomCanvas(1 / 1.2); }
    else if (ev.key === "0") { goHome(); }
    else if (ev.key === "Escape" || ev.key === "Backspace") { zoomOut(); }
    else if (ev.key === "," || ev.key === ".") {
      const aspect = state.lastAwin;
      if (!aspect) { setStatus("no aspect window open", "error"); return; }
      const modes = ASPECT_MODES[aspect] || ["graph"];
      const current = (state.awinMode && state.awinMode[aspect]) || ASPECT_DEFAULT_MODE[aspect] || "graph";
      const step = ev.key === "." ? 1 : modes.length - 1;
      const next = modes[(modes.indexOf(current) + step) % modes.length];
      state.awinMode[aspect] = next;
      const select = document.querySelector(`#awin-${aspect} .awin-mode`);
      if (select) select.value = next;
      renderAspectWindow(aspect);
      setStatus(`view type: ${aspect} · ${next}`, "ok");
    }
  });
  $("level-jump").addEventListener("change", applyLevelJump);
  $("node-find").addEventListener("click", findNode);
  $("node-search").addEventListener("keydown", (ev) => { if (ev.key === "Enter") findNode(); });
  $("export-all").addEventListener("click", exportAllAspects);
  $("view-apply").addEventListener("click", applySlice);
  $("view-reset").addEventListener("click", resetSlice);
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
  buildViewTabs();
  if ($("tree-toggle")) $("tree-toggle").addEventListener("click", () => {
    state.treeHidden = !state.treeHidden;
    $("c4-tree").classList.toggle("hidden", state.treeHidden);
    $("tree-resize").classList.toggle("hidden", state.treeHidden);
    render();
  });
  if ($("layout-auto")) $("layout-auto").addEventListener("click", autoLayout);
  if ($("layout-reset")) $("layout-reset").addEventListener("click", resetLayout);
  wirePanelResize();
  $("rag-verify").addEventListener("click", ragVerify);
  $("doc_file").addEventListener("change", () => attachDoc($("doc_file").files[0]));
  $("doc-confirm").addEventListener("click", confirmDoc);
  $("doc-cancel").addEventListener("click", hideDocPreview);
  $("gen-run").addEventListener("click", generateArtifacts);
  $("gen-coverage").addEventListener("click", showCoverageMatrix);
  $("model-load").addEventListener("click", loadModel);
  $("add-node").addEventListener("click", openAddPopover);
  $("pop-create").addEventListener("click", createNodeFromPopover);
  $("pop-cancel").addEventListener("click", () => $("popover").classList.add("hidden"));
  $("node-delete").addEventListener("click", deleteSelectedNode);
  $("node-focus").addEventListener("click", focusSelectedNode);
  $("node-rename-apply").addEventListener("click", renameSelectedNode);
  $("win-open").addEventListener("click", () => openAspectWindow($("win_aspect").value));
  $("export").addEventListener("click", exportGraph);
  if ($("bp_doc")) $("bp_doc").addEventListener("click", exportDocumentation);
  $("import_file").addEventListener("change", () => importGraph($("import_file").files[0]));
  document.querySelectorAll("#comm .tab").forEach((btn) => {
    btn.addEventListener("click", () => setCommTab(btn.dataset.tab));
  });
  $("prompt-form").addEventListener("submit", (e) => { e.preventDefault(); sendPrompt($("prompt_input").value); });
  wirePromptInput();
  wirePromptResize();
  wireCommResize();
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
  if ($("bp_repo")) $("bp_repo").value = demo.repo || "";
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
  wireCanvas();
  wireArchitectPanel();
  wireLayout();

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

function currentUser() {
  return ($("doc_user").value || "default").trim() || "default";
}

async function attachDoc(file) {
  if (!file) return;
  let content = "";
  try {
    content = await file.text();
  } catch (e) {
    appendEvents([{ ts: Date.now() / 1000, type: "error", text: "cannot read file: " + e.message }]);
    return;
  }
  showDocPreview({ name: file.name, kind: /\.csv$/i.test(file.name) ? "csv" : "md", content });
  $("doc_file").value = "";
}

function showDocPreview(pending) {
  state.pendingDoc = pending;
  const lines = pending.content.split("\n").length;
  const chunks = pending.kind === "csv"
    ? Math.max(1, Math.ceil(Math.max(0, lines - 1) / 5))
    : pending.content.split(/\n\s*\n/).filter((p) => p.trim()).length;
  $("doc-preview-name").textContent = pending.name;
  $("doc-preview-meta").textContent = `${pending.kind} · ${pending.content.length} chars · ~${chunks} chunks`;
  $("doc-preview-body").textContent = pending.content.slice(0, 800) + (pending.content.length > 800 ? "\n…" : "");
  $("doc-preview").classList.remove("hidden");
}

function hideDocPreview() {
  state.pendingDoc = null;
  $("doc-preview").classList.add("hidden");
}

async function confirmDoc() {
  const pending = state.pendingDoc;
  if (!pending) return;
  const now = () => Date.now() / 1000;
  appendEvents([{ ts: now(), type: "user", text: `attach: ${pending.name}` }]);
  try {
    const res = await api("/api/docs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser(), name: pending.name, kind: pending.kind, content: pending.content }),
    });
    appendEvents([{ ts: now(), type: "status", text: `indexed ${pending.name} into RAG (${(res.chunks || []).length} chunks)` }]);
  } catch (e) {
    appendEvents([{ ts: now(), type: "error", text: "attach failed: " + e.message }]);
  }
  hideDocPreview();
}

async function ragVerify() {
  try {
    const res = await api("/api/rag/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer: $("rag_answer").value, chunks: state.ragHits || [] }),
    });
    const el = $("rag-ground");
    el.innerHTML = "";
    const summary = document.createElement("div");
    summary.textContent = `grounded: ${res.grounded} | supported: ${(res.supported || []).length} | unsupported: ${(res.unsupported || []).length}`;
    el.appendChild(summary);
    (res.claims || []).forEach((c) => {
      const div = document.createElement("div");
      div.className = "claim " + (c.fidelity || "log");
      const src = c.source ? ` [${c.source.doc}#${c.source.chunk}]` : "";
      const changed = c.added && c.added.length ? ` (+${c.added.length} new)` : "";
      div.textContent = `${c.fidelity}${src}: ${c.text}${changed}`;
      el.appendChild(div);
    });
  } catch (e) {
    $("rag-ground").textContent = "verify failed: " + e.message;
  }
}

function setCommTab(name) {
  [["activity", "tab-activity"], ["answer", "tab-answer"]].forEach(([key, pane]) => {
    const btn = document.querySelector(`#comm .tab[data-tab="${key}"]`);
    if (btn) btn.classList.toggle("active", key === name);
    const el = $(pane);
    if (el) el.classList.toggle("hidden", key !== name);
  });
}

function parseIntent(text) {
  // specific commands first: "add aspect …" must not be read as "add <type> <id>"
  if (/^(?:yes|y|да|ок|ok|confirm|подтверждаю)$/i.test(text)) return { kind: "confirm" };
  if (/^(?:no|n|нет|отмена|cancel)$/i.test(text)) return { kind: "cancel" };
  if (/^(?:apply|примени)$/i.test(text)) return { kind: "apply" };
  if (/^(?:validate|check|проверь)(?:\s+(?:model|модель))?$/i.test(text)) return { kind: "validate" };
  const ask = text.match(/^(?:rag|ask|спроси|вопрос|что\s+сказано)\s*:?\s+([\s\S]+)$/i);
  if (ask) return { kind: "ask", query: ask[1].trim() };
  const design = text.match(/^(?:design|synthesi[sz]e|синтез|спроектируй|постановка)\s+([\s\S]+)$/i);
  if (design) return { kind: "design", description: design[1].trim() };
  const rebuild = text.match(/^(?:rebuild|reconstruct|пересобери)\s+aspects?\s+([\w,\s]+)$/i);
  if (rebuild) return { kind: "aspects", list: rebuild[1].split(/[,\s]+/).filter(Boolean) };
  const aspect = text.match(/^(?:add\s+aspect|добавь\s+аспект|аспект)\s+([\w,\s]+)$/i);
  if (aspect) return { kind: "aspects", list: aspect[1].split(/[,\s]+/).filter(Boolean) };
  const run = text.match(/^(?:reconstruct|run|реконструируй|запусти)\s+(\S+)\s*$/i);
  if (run) return { kind: "reconstruct", repo: run[1] };
  const add = text.match(/^add\s+(\w+)\s+(\S+)(?:\s+under\s+(\S+))?$/i);
  if (add) return { kind: "add", type: add[1], id: add[2], parent: add[3] || null };
  const rem = text.match(/^remove\s+(\S+)$/i);
  if (rem) return { kind: "remove", id: rem[1] };
  if (/^review\b/i.test(text)) return { kind: "review" };
  if (/^fix\b/i.test(text)) return { kind: "fix" };
  return { kind: "rag" };
}

async function executePendingCommand(pending) {
  const now = () => Date.now() / 1000;
  const agent = ($("agent") ? $("agent").value : "") || "builtin";
  try {
    if (pending.kind === "design") {
      const res = await api("/api/design", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: pending.description, name: pending.name,
                               project: state.project || null }),
      });
      state.runId = res.run_id;
      state.since = 0;
      pollRun();
      showBottom("events");
      appendEvents([{ ts: now(), type: "status",
        text: `synthesis ${res.run_id} started — material: ${res.files} file(s) from the description` }]);
      return;
    }
    if (pending.kind === "reconstruct") {
      const runId = await startReconstruction(pending.repo, agent, state.project);
      if (runId) {
        appendEvents([{ ts: now(), type: "status", text: `run ${runId} started — see the Events tab` }]);
        showBottom("events");
      }
    } else if (pending.kind === "aspects") {
      const project = (state.projects || []).find((p) => p.id === state.project);
      const repo = ((project && (project.repos || [])[0]) || "").trim();
      if (!repo) {
        appendEvents([{ ts: now(), type: "error",
          text: "this project has no repository — reconstruct one first, then extend aspects" }]);
        return;
      }
      const runId = await startReconstruction(repo, agent, state.project,
        { model: pending.model, aspect_list: pending.list });
      if (runId) {
        appendEvents([{ ts: now(), type: "status",
          text: `run ${runId} started for aspects ${pending.list.join(", ")} — see the Events tab` }]);
        showBottom("events");
      }
    }
  } catch (e) {
    appendEvents([{ ts: now(), type: "error", text: "command failed: " + e.message }]);
  }
}

async function sendPrompt(text) {
  const value = String(text || "").trim();
  if (!value) return;
  const now = () => Date.now() / 1000;
  appendEvents([{ ts: now(), type: "user", text: value }]);
  state.promptHistory.push(value);
  state.promptIndex = state.promptHistory.length;
  recordProjectChat("user", value, { kind: (parseIntent(value) || {}).kind || "chat" });
  $("prompt_input").value = "";

  const intent = parseIntent(value);
  const model = state.modelName || "";

  // Ф10.5: long/structural commands are proposed and executed on confirmation
  if (intent.kind === "confirm" || intent.kind === "cancel") {
    if (!state.pendingCommand) {
      appendEvents([{ ts: now(), type: "question", text: "nothing to confirm" }]);
      return;
    }
    const pending = state.pendingCommand;
    state.pendingCommand = null;
    if (intent.kind === "cancel") {
      appendEvents([{ ts: now(), type: "status", text: `cancelled: ${pending.label}` }]);
      return;
    }
    appendEvents([{ ts: now(), type: "status", text: `running: ${pending.label}` }]);
    await executePendingCommand(pending);
    return;
  }
  if (intent.kind === "ask") {
    // a question over the personal documents: answer is generated from the
    // retrieved marked chunks and every claim is attributed (Ф7.6)
    try {
      const res = await api("/api/rag/answer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: intent.query, user: ($("doc_user") && $("doc_user").value) || "default" }),
      });
      const gr = res.grounding || {};
      const sources = (res.chunks || []).map((c, i) => `[${i + 1}] ${c.doc}#${c.chunk}`).join(" · ");
      appendEvents([
        { ts: now(), type: "answer", text: res.answer || "(no answer)",
          about: { model: state.modelName || null, run: state.runId || null, view: state.view || "c4",
                   kind: "rag", chunks: (res.chunks || []).length } },
        { ts: now(), type: "status",
          text: `sources: ${sources || "—"} · supported: ${(gr.supported || []).length} · unconfirmed: ${(gr.unsupported || []).length}` },
      ]);
      (gr.unsupported || []).forEach((claim) => appendEvents([
        { ts: now(), type: "question", text: `unconfirmed (not in the documents): ${claim.text}` },
      ]));
      setStatus(`answered from ${(res.chunks || []).length} document chunk(s)`, "ok");
    } catch (e) {
      appendEvents([{ ts: now(), type: "question", text: `RAG answer failed: ${e.message}` }]);
    }
    return;
  }
  if (intent.kind === "design") {
    if (intent.description.length < 20) {
      appendEvents([{ ts: now(), type: "question",
        text: "describe the system in a couple of sentences — e.g. «design a service that takes orders, a manager approves them, accounting books the trip»" }]);
      return;
    }
    const designName = state.project || (state.modelName ? `${state.modelName}-v2` : `design-${Date.now().toString(36).slice(-5)}`);
    state.pendingCommand = { kind: "design", description: intent.description, name: designName,
                             label: `synthesise ${designName}` };
    appendEvents([{ ts: now(), type: "question",
      text: `synthesise a new architecture «${designName}» from this description (${intent.description.length} chars)` +
            `${state.project ? "" : " — no project is active, the result will be a new model"}? reply «да» to run` }]);
    return;
  }
  if (intent.kind === "reconstruct") {
    state.pendingCommand = { kind: "reconstruct", repo: intent.repo, label: `reconstruct ${intent.repo}` };
    appendEvents([{ ts: now(), type: "question",
      text: `start a reconstruction of ${intent.repo}${state.project ? " into project " + state.project : ""}? reply «да» to run` }]);
    return;
  }
  if (intent.kind === "aspects") {
    if (!model) {
      appendEvents([{ ts: now(), type: "question", text: "load a model first (Projects tab or Model name + Load)" }]);
      return;
    }
    state.pendingCommand = { kind: "aspects", list: intent.list, model,
                             label: `extend ${model} with aspects: ${intent.list.join(", ")}` };
    appendEvents([{ ts: now(), type: "question",
      text: `extend ${model} with aspects ${intent.list.join(", ")}? reply «да» to run` }]);
    return;
  }
  if (intent.kind === "validate") {
    if (!model) {
      appendEvents([{ ts: now(), type: "question", text: "load a model first" }]);
      return;
    }
    try {
      const res = await api(`/api/models/${encodeURIComponent(model)}/validate`);
      const validation = res.validation || {};
      const violations = res.violations || [];
      appendEvents([{ ts: now(), type: validation.ok ? "status" : "error",
        text: `validation: ${validation.ok ? "ok" : (validation.errors || []).length + " error(s)"}`
              + ` · constraints: ${violations.length} violation(s)` }]);
      recordProjectChat("agent", `validation: ${validation.ok ? "ok" : "errors"}`);
    } catch (e) {
      appendEvents([{ ts: now(), type: "error", text: "validation failed: " + e.message }]);
    }
    return;
  }
  if (intent.kind === "apply") {
    await applyAgentAnswer();
    return;
  }

  if (intent.kind === "add" || intent.kind === "remove") {
    if (!model) {
      appendEvents([{ ts: now(), type: "question", text: "Which model? Set 'Model name' in the Edit model panel." }]);
      return;
    }
    const operations = intent.kind === "add"
      ? [{ op: "add_node", node: { id: intent.id, type: intent.type, parent: intent.parent, evidence: [] } }]
      : [{ op: "remove_node", id: intent.id }];
    appendEvents([{ ts: now(), type: "reasoning", text: `proposed: ${JSON.stringify(operations)}` }]);
    try {
      const res = await api(`/api/models/${encodeURIComponent(model)}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operations, auto_repair: true }),
      });
      appendEvents([{ ts: now(), type: "status", text: `applied \u00b7 nodes: ${(res.model.nodes || []).length} \u00b7 impacted: ${(res.impacted || []).length}` }]);
      recordProjectChat("agent", `applied: nodes ${(res.model.nodes || []).length}, impacted ${(res.impacted || []).length}`);
      state.graph = res.model;
      state.fullGraph = res.model;
      state.focus = null;
      render();
    } catch (e) {
      appendEvents([{ ts: now(), type: "error", text: "edit rejected: " + e.message }]);
    }
    return;
  }

  if (intent.kind === "review" || intent.kind === "fix") {
    if (!model) {
      appendEvents([{ ts: now(), type: "question", text: "Which model? Set 'Model name' in the Edit model panel." }]);
      return;
    }
    try {
      const res = await api(`/api/models/${encodeURIComponent(model)}/${intent.kind === "fix" ? "reconcile" : "review"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apply: intent.kind === "fix" }),
      });
      const issues = res.divergences || res.issues || [];
      appendEvents([{ ts: now(), type: "reasoning", text: `${issues.length} issue(s), ${(res.proposals || []).length} proposal(s)` }]);
      if (intent.kind === "fix" && res.convergence) {
        appendEvents([{ ts: now(), type: "status", text: `convergence ${res.convergence.before} -> ${res.convergence.after}` }]);
      }
      if (intent.kind === "fix" && res.model) {
        state.graph = res.model;
        state.fullGraph = res.model;
        render();
      }
    } catch (e) {
      appendEvents([{ ts: now(), type: "error", text: "review failed: " + e.message }]);
    }
    return;
  }

  try {
    const res = await api("/api/rag/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: currentUser(), query: value }),
    });
    const hits = res.results || [];
    if (!hits.length) {
      appendEvents([
        { ts: now(), type: "reasoning", text: `no documents matched "${value}"` },
        { ts: now(), type: "question", text: "Please upload a document or rephrase your request." },
      ]);
      return;
    }
    state.ragHits = hits;
    appendEvents([{ ts: now(), type: "reasoning", text: `retrieved ${hits.length} marked chunk(s)` }]);
    hits.forEach((h) => appendEvents([{ ts: now(), type: "output", text: `[${h.name}#${h.chunk}] ${h.text}`, attach: h.text }]));
  } catch (e) {
    appendEvents([{ ts: now(), type: "error", text: "agent error: " + e.message }]);
  }
}

function wirePromptInput() {
  const input = $("prompt_input");
  if (!input) return;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendPrompt(input.value);
      return;
    }
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    const before = input.value.slice(0, input.selectionStart);
    const after = input.value.slice(input.selectionEnd);
    if (e.key === "ArrowUp" && before.includes("\n")) return;
    if (e.key === "ArrowDown" && after.includes("\n")) return;
    const history = state.promptHistory;
    if (!history.length) return;
    e.preventDefault();
    if (e.key === "ArrowUp") state.promptIndex = Math.max(0, state.promptIndex - 1);
    else state.promptIndex = Math.min(history.length, state.promptIndex + 1);
    input.value = state.promptIndex >= history.length ? "" : history[state.promptIndex];
    input.selectionStart = input.selectionEnd = input.value.length;
  });
}

function wirePromptResize() {
  const handle = $("prompt-resize");
  const form = $("prompt-form");
  if (!handle || !form) return;
  let resizing = false, startY = 0, startH = 0;
  handle.addEventListener("mousedown", (e) => {
    resizing = true;
    startY = e.clientY;
    startH = form.getBoundingClientRect().height;
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const h = Math.max(30, Math.min(320, startH - (e.clientY - startY)));
    form.style.height = h + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    document.body.style.userSelect = "";
  });
}

function wireCommResize() {
  const handle = $("comm-resize");
  if (!handle) return;
  let resizing = false, startY = 0, startH = 0;
  handle.addEventListener("mousedown", (e) => {
    resizing = true;
    startY = e.clientY;
    startH = $("comm").getBoundingClientRect().height;
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const h = Math.max(90, Math.min(window.innerHeight - 220, startH - (e.clientY - startY)));
    $("comm").style.height = h + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!resizing) return;
    resizing = false;
    document.body.style.userSelect = "";
    render();
  });
}

async function applyEdit(name, operations) {
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operations, auto_repair: true }),
    });
    $("edit-info").textContent = `ok \u00b7 nodes: ${(res.model.nodes || []).length} \u00b7 impacted: ${(res.impacted || []).length}`;
    if (res.model) {
      state.graph = res.model;
      state.fullGraph = res.model;
      state.focus = null;
      render();
    }
  } catch (e) {
    $("edit-info").textContent = "edit failed: " + e.message;
  }
}

async function generateArtifacts() {
  const name = $("gen_model").value.trim();
  if (!name) { $("gen-info").textContent = "model name required"; return; }
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kinds: ["openapi", "ddl", "schema", "skeleton"] }),
    });
    const arts = res.artifacts || {};
    openArtifactsWindow(arts);
    $("gen-info").textContent = Object.keys(arts).length
      ? Object.entries(arts).map(([k, v]) => `${k}: ${v.length} chars`).join("\n")
      : "no artifacts";
  } catch (e) {
    $("gen-info").textContent = "generate failed: " + e.message;
  }
}

async function showCoverageMatrix() {
  const name = $("gen_model").value.trim();
  if (!name) { $("gen-info").textContent = "model name required"; return; }
  try {
    const res = await api(`/api/models/${encodeURIComponent(name)}/coverage-matrix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    openCoverageWindow(res);
    $("gen-info").textContent = `coverage: ${res.covered}/${res.total} (${Math.round((res.ratio || 0) * 100)}%)`;
  } catch (e) {
    $("gen-info").textContent = "coverage failed: " + e.message;
  }
}

function applyCanvasEdit(operations) {
  const name = state.modelName;
  if (!name) {
    setStatus("Load a model first (model name + Load)", "error");
    return Promise.resolve();
  }
  return api(`/api/models/${encodeURIComponent(name)}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operations, auto_repair: true }),
  }).then((res) => {
    state.graph = res.model;
    state.fullGraph = res.model;
    render();
    renderAllAspectWindows();
    setStatus("edit applied", "ok");
  }).catch((e) => setStatus("edit rejected: " + e.message, "error"));
}

async function loadModel() {
  const name = $("model_load").value.trim();
  if (!name) { setStatus("model name required", "error"); return; }
  try {
    const model = await api(`/api/models/${encodeURIComponent(name)}`);
    state.modelName = name;
    await loadLayout(name);
    setGraph(model);
    setStatus("model loaded: " + name, "ok");
  } catch (e) {
    setStatus("load failed: " + e.message, "error");
  }
}

function openAddPopover() {
  if (!state.modelName) { setStatus("Load a model first", "error"); return; }
  const sel = $("pop_type");
  sel.innerHTML = "";
  Object.keys(TYPE_TO_ASPECT).forEach((type) => {
    const opt = document.createElement("option");
    opt.value = type; opt.textContent = type;
    sel.appendChild(opt);
  });
  $("pop_name").value = "";
  const r = $("add-node").getBoundingClientRect();
  const wrap = $("canvas-wrap").getBoundingClientRect();
  $("popover").style.left = Math.max(8, r.left - wrap.left) + "px";
  $("popover").style.top = Math.max(8, r.bottom - wrap.top + 4) + "px";
  $("popover").classList.remove("hidden");
  $("pop_name").focus();
}

function createNodeFromPopover() {
  const name = $("pop_name").value.trim();
  const type = $("pop_type").value;
  if (!name) return;
  const parent = state.focus || state.rootId;
  const id = `${type.toLowerCase()}://${(state.modelName || "app")}/${name.replace(/[^A-Za-z0-9._-]+/g, "-")}`;
  $("popover").classList.add("hidden");
  applyCanvasEdit([{ op: "add_node", node: { id, type, name, parent, evidence: [] } }]);
}

function deleteSelectedNode() {
  if (!state.selected) return;
  const id = state.selected;
  state.selected = null;
  applyCanvasEdit([{ op: "remove_node", id }]);
}

function renameSelectedNode() {
  const name = $("node-rename").value.trim();
  if (!state.selected || !name) return;
  applyCanvasEdit([{ op: "update_node", id: state.selected, patch: { name } }]);
}

function svgEl(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, String(v)));
  return el;
}

// ---------------------------------------------------------------------------
// Diagram tooling for the aspect windows: each aspect gets its own notation.
// ---------------------------------------------------------------------------
const ASPECT_MODES = {
  structure: ["c4", "graph"],
  interface: ["api", "sequence", "graph"],
  data: ["er", "graph"],
  behavior: ["state", "graph"],
  process: ["bpmn", "sequence", "graph"],
  access: ["graph", "table"],
  deployment: ["tree", "graph"],
  quality: ["table", "graph"],
};
const ASPECT_DEFAULT_MODE = {
  structure: "c4", interface: "api", data: "er", behavior: "state",
  process: "bpmn", access: "graph", deployment: "tree", quality: "table",
};

function awinKids(graph) {
  const kids = new Map();
  (graph.nodes || []).forEach((n) => {
    if (!n.parent) return;
    if (!kids.has(n.parent)) kids.set(n.parent, []);
    kids.get(n.parent).push(n);
  });
  return kids;
}

function awinEdges(graph, nodes) {
  const ids = new Set(nodes.map((n) => n.id));
  return (graph.edges || []).filter((e) => ids.has(e.source) && ids.has(e.target));
}

let AWIN_SEQ = 0;
function awinSvg(width, height) {
  const svg = svgEl("svg", { class: "awin-svg", xmlns: NS, width, height, viewBox: `0 0 ${width} ${height}` });
  svg.style.width = width + "px";
  svg.style.height = height + "px";
  const id = "awin-arrow-" + (++AWIN_SEQ);
  const defs = svgEl("defs");
  const marker = svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5,
    markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
  marker.appendChild(svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: "#8b949e" }));
  defs.appendChild(marker);
  svg.appendChild(defs);
  svg.dataset.marker = id;
  return svg;
}

function awinText(svg, x, y, text, cls, anchor) {
  const t = svgEl("text", { x, y, class: cls || "" });
  if (anchor) t.setAttribute("text-anchor", anchor);
  t.textContent = text == null ? "" : String(text);
  svg.appendChild(t);
  return t;
}

function awinArrow(svg, x1, y1, x2, y2, label) {
  svg.appendChild(svgEl("line", { x1, y1, x2, y2, stroke: "#8b949e", "stroke-width": 1.2,
    "marker-end": `url(#${svg.dataset.marker})` }));
  if (label) awinText(svg, (x1 + x2) / 2, (y1 + y2) / 2 - 3, label, "edge-label", "middle");
}

function awinBox(svg, x, y, w, h, color, title, subtitle) {
  svg.appendChild(svgEl("rect", { x, y, width: w, height: h, rx: 8,
    fill: "var(--panel-2, #161b22)", stroke: color, "stroke-width": 1.4 }));
  svg.appendChild(svgEl("rect", { x, y, width: w, height: 22, rx: 8, fill: color, opacity: 0.2 }));
  awinText(svg, x + 10, y + 15, truncate(title, 24), "node-name");
  if (subtitle) awinText(svg, x + w - 10, y + 15, subtitle, "node-type", "end");
  return y + 24;
}

function awinRound(svg, x, y, w, h, color, title, cls) {
  svg.appendChild(svgEl("rect", { x, y, width: w, height: h, rx: h / 2,
    fill: "var(--panel-2, #161b22)", stroke: color, "stroke-width": 1.4, class: cls || "state-node" }));
  awinText(svg, x + 12, y + h / 2 + 4, truncate(title, 22), "node-name");
}

function awinDiamond(svg, cx, cy, w, h, color, title) {
  svg.appendChild(svgEl("polygon", { class: "gateway-node",
    points: `${cx},${cy - h / 2} ${cx + w / 2},${cy} ${cx},${cy + h / 2} ${cx - w / 2},${cy}`,
    fill: "var(--panel-2, #161b22)", stroke: color, "stroke-width": 1.4 }));
  awinText(svg, cx, cy + 4, truncate(title, 6), "node-type", "middle");
}

function awinCylinder(svg, x, y, w, h, color, title) {
  svg.appendChild(svgEl("path", { class: "store-node",
    d: `M${x},${y + 10} A ${w / 2},10 0 0 1 ${x + w},${y + 10} L${x + w},${y + h - 10} ` +
       `A ${w / 2},10 0 0 1 ${x},${y + h - 10} Z`,
    fill: "var(--panel-2, #161b22)", stroke: color, "stroke-width": 1.4 }));
  svg.appendChild(svgEl("ellipse", { cx: x + w / 2, cy: y + 10, rx: w / 2, ry: 10,
    fill: "none", stroke: color, "stroke-width": 1.2 }));
  awinText(svg, x + w / 2, y + h / 2 + 6, truncate(title, 18), "node-name", "middle");
}

// interface -> API listing (interfaces with their operations as rows)
function rApi(aspect, graph, nodes) {
  const kids = awinKids(graph);
  const ifaces = nodes.filter((n) => n.type === "Interface");
  const ops = nodes.filter((n) => n.type === "Operation");
  const orphan = ops.filter((op) => !ifaces.some((f) => f.id === op.parent));
  const BW = 320, ROW = 20, PAD = 20;
  let y = PAD;
  const blocks = ifaces.map((f) => {
    const list = (kids.get(f.id) || []).filter((k) => k.type === "Operation").slice(0, 14);
    const h = 28 + Math.max(1, list.length) * ROW;
    const b = { f, list, y, h };
    y += h + 14;
    return b;
  });
  if (orphan.length) y += 28 + Math.min(orphan.length, 14) * ROW;
  const height = Math.max(140, y + PAD);
  const svg = awinSvg(BW + PAD * 2, height);
  blocks.forEach(({ f, list, y, h }) => {
    const inner = awinBox(svg, PAD, y, BW, h, "#2f81f7", f.name || f.id, "Interface");
    if (!list.length) awinText(svg, PAD + 12, inner + 14, "(no operations)", "node-detail");
    list.forEach((op, i) => {
      awinText(svg, PAD + 12, inner + 14 + i * ROW, "ƒ " + (op.name || op.id), "node-detail");
      awinText(svg, PAD + BW - 12, inner + 14 + i * ROW, "Operation", "node-type", "end");
    });
  });
  if (orphan.length) {
    const inner = awinBox(svg, PAD, y - 28 - Math.min(orphan.length, 14) * ROW + 14, BW,
      28 + Math.min(orphan.length, 14) * ROW, "#8b949e", "loose operations", "Operation");
    orphan.slice(0, 14).forEach((op, i) => {
      awinText(svg, PAD + 12, inner + 14 + i * ROW, "ƒ " + (op.name || op.id), "node-detail");
    });
  }
  return svg;
}

// data -> ER: entities with attribute rows, data stores as cylinders
function rEr(aspect, graph, nodes) {
  const kids = awinKids(graph);
  const entities = nodes.filter((n) => n.type === "DataEntity" || n.type === "SchemaRef" || n.type === "Message");
  const stores = nodes.filter((n) => n.type === "DataStore");
  const BW = 220, ROW = 18, PAD = 24, GAP = 40;
  const pos = new Map();
  const cols = Math.max(1, Math.min(3, entities.length));
  let maxY = PAD;
  entities.forEach((e, i) => {
    const attrs = (kids.get(e.id) || []).filter((k) => k.type === "Attribute").slice(0, 10);
    const h = 30 + Math.max(1, attrs.length) * ROW + 8;
    const c = i % cols, r = Math.floor(i / cols);
    const x = PAD + c * (BW + GAP);
    const y = PAD + r * 0;
    pos.set(e.id, { x, y, w: BW, h, attrs, r, c });
    maxY = Math.max(maxY, y + h + 8);
  });
  // stack rows per column properly
  const colY = new Array(cols).fill(PAD);
  entities.forEach((e) => {
    const p = pos.get(e.id);
    p.y = colY[p.c];
    colY[p.c] += p.h + 26;
    maxY = Math.max(maxY, p.y + p.h);
  });
  const storeY = maxY + 40;
  const height = storeY + (stores.length ? 90 : 0) + PAD;
  const svg = awinSvg(PAD * 2 + cols * (BW + GAP) - GAP, Math.max(150, height));
  entities.forEach((e) => {
    const p = pos.get(e.id);
    const inner = awinBox(svg, p.x, p.y, p.w, p.h, "#2ea043", e.name || e.id, e.type);
    svg.appendChild(svgEl("line", { x1: p.x, y1: inner, x2: p.x + p.w, y2: inner, stroke: "#2ea043", "stroke-width": 1 }));
    if (!p.attrs.length) awinText(svg, p.x + 12, inner + 14, "(no attributes)", "node-detail");
    p.attrs.forEach((a, i) => awinText(svg, p.x + 12, inner + 14 + i * ROW, "· " + (a.name || a.id), "attr-row"));
  });
  const sw = 160;
  stores.forEach((s, i) => {
    const x = PAD + i * (sw + GAP);
    awinCylinder(svg, x, storeY, sw, 70, "#1f6feb", s.name || s.id);
  });
  // relationships between data nodes are drawn Chen-style: two segments with a
  // labelled diamond in between (ER notation)
  awinEdges(graph, nodes).forEach((e) => {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    awinArrow(svg, x1, y1, mx - 12, my, "");
    awinArrow(svg, mx + 12, my, x2, y2, "");
    const diamond = svgEl("polygon", { class: "er-diamond",
      points: `${mx},${my - 10} ${mx + 26},${my} ${mx},${my + 10} ${mx - 26},${my}`,
      fill: "var(--panel-2, #161b22)", stroke: "#2ea043", "stroke-width": 1.2 });
    svg.appendChild(diamond);
    const card = e.cardinality ? ` ${e.cardinality}` : "";
    awinText(svg, mx, my - 14, truncate(String(e.type).replace(/_/g, " ") + card, 18), "edge-label", "middle");
  });
  return svg;
}

// behavior -> state machine view (states, transitions, emitted events)
function rState(aspect, graph, nodes) {
  const kids = awinKids(graph);
  const machines = nodes.filter((n) => n.type === "StateMachine");
  const allStates = nodes.filter((n) => n.type === "State");
  const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  const stateByName = new Map();
  allStates.forEach((s) => stateByName.set(norm(s.name || s.id.split("/").pop()), s));

  const PAD = 22, BW = 560, SH = 22, SW = 150, GX = 18, ROW = 34;
  const blocks = [];
  let y = PAD;
  machines.forEach((m) => {
    const list = kids.get(m.id) || [];
    const states = list.filter((k) => k.type === "State");
    const transitions = list.filter((k) => k.type === "Transition");
    const events = list.filter((k) => k.type === "Event");
    if (!states.length && !transitions.length) return;
    const perRow = Math.max(1, Math.min(3, Math.ceil(Math.sqrt(states.length || 1))));
    const rows = Math.max(1, Math.ceil(states.length / perRow));
    const h = 44 + rows * (SH + ROW) + (events.length ? 24 : 0);
    blocks.push({ m, states, transitions, events, y, h, perRow });
    y += h + 16;
  });
  if (!blocks.length) return rGraph(aspect, graph, nodes);
  const svg = awinSvg(BW + PAD * 2, Math.max(150, y + PAD));

  blocks.forEach(({ m, states, transitions, events, y, h, perRow }) => {
    svg.appendChild(svgEl("rect", { x: PAD, y, width: BW, height: h, rx: 10, fill: "none",
      stroke: "#a371f7", "stroke-width": 1.4, "stroke-dasharray": "5 4", class: "state-machine" }));
    awinText(svg, PAD + 10, y + 18, truncate(m.name || m.id, 34), "node-name");
    awinText(svg, PAD + BW - 10, y + 18, "StateMachine", "node-type", "end");

    const pos = new Map();
    states.forEach((s, i) => {
      const col = i % perRow, row = Math.floor(i / perRow);
      const x = PAD + 26 + col * (SW + GX);
      const sy = y + 34 + row * (SH + ROW);
      pos.set(s.id, { x, y: sy, w: SW, h: SH });
      awinRound(svg, x, sy, SW, SH, "#a371f7", s.name || s.id);
    });

    // conventional entry state gets an initial marker
    const entry = states.find((s) => /^(created|new|started|idle|init|ready)$/i.test(String(s.name || "")));
    if (entry && pos.has(entry.id)) {
      const e = pos.get(entry.id);
      svg.appendChild(svgEl("circle", { cx: e.x - 14, cy: e.y + e.h / 2, r: 5, fill: "#a371f7",
        class: "initial-marker" }));
      awinArrow(svg, e.x - 9, e.y + e.h / 2, e.x - 1, e.y + e.h / 2, "");
    }

    // transitions: `a_to_b` names (what the model carries) or matching states
    transitions.forEach((t) => {
      const label = String(t.name || t.id.split("/").pop());
      const stateById = new Map(states.map((s) => [s.id, s]));
      const resolve = (raw) => {
        if (!raw) return null;
        return stateById.get(raw) || stateByName.get(norm(raw))
          || states.find((s) => norm(s.id.split("/").pop()) === norm(raw)) || null;
      };
      const parts = label.split(/_to_|->|→/i);
      // explicit `from`/`to` (Ф2.14) win over parsing the name
      const from = resolve(t.from) || (parts[0] ? stateByName.get(norm(parts[0])) : null);
      const to = resolve(t.to) || (parts[1] ? stateByName.get(norm(parts[1])) : null);
      const a = from ? pos.get(from.id) : null;
      const b = to ? pos.get(to.id) : null;
      if (!a || !b) return;
      let d, tx, ty;
      if (Math.abs(a.y - b.y) < 2) {
        const right = b.x >= a.x;
        const x1 = right ? a.x + a.w : a.x, x2 = right ? b.x - 5 : b.x + b.w + 5;
        d = `M${x1},${a.y + a.h / 2} L${x2},${b.y + b.h / 2}`;
        tx = (a.x + a.w + b.x) / 2; ty = a.y - 5;
      } else {
        const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
        const bend = Math.max(18, Math.abs(y2 - y1) / 2);
        d = `M${x1},${y1} C${x1},${y1 + bend} ${x2},${y2 - bend} ${x2},${y2 - 5}`;
        tx = (x1 + x2) / 2; ty = (y1 + y2) / 2;
      }
      const arrow = svgEl("path", { d, fill: "none", stroke: "#f0883e", "stroke-width": 1.3,
        "marker-end": `url(#${svg.dataset.marker})` });
      arrow.setAttribute("class", "state-arrow");
      svg.appendChild(arrow);
      awinText(svg, tx, ty, truncate(label.replace(/_/g, " "), 20), "edge-label", "middle");
    });

    events.forEach((ev, i) => {
      const cx = PAD + 44 + i * 130, cy = y + h - 12;
      svg.appendChild(svgEl("circle", { cx, cy, r: 6, fill: "none", stroke: "#f0883e",
        "stroke-width": 1.4, class: "event-node" }));
      awinText(svg, cx + 12, cy + 4, truncate(ev.name || ev.id, 12), "node-detail");
    });
  });
  return svg;
}

function rBpmn(aspect, graph, nodes) {
  const kids = awinKids(graph);
  const processes = nodes.filter((n) => n.type === "Process");
  if (!processes.length) return rGraph(aspect, graph, nodes);
  const PAD = 22, BW = 580, GAP = 34, BH = 22, LANE_W = 132;
  const isStep = (n) => ["Task", "Activity", "Gateway"].includes(n.type);
  const blocks = processes.map((p) => {
    const children = kids.get(p.id) || [];
    const participants = children.filter((k) => k.type === "Participant");
    const lanes = participants.map((pt) => ({
      name: pt.name || pt.id,
      steps: (kids.get(pt.id) || []).filter(isStep),
    }));
    const loose = children.filter((k) => isStep(k) && !participants.some((pt) => k.parent === pt.id));
    if (loose.length || !lanes.length) lanes.push({ name: lanes.length ? "unassigned" : "process", steps: loose, plain: true });
    const steps = children.filter(isStep);
    const ids = new Set(steps.map((k) => k.id));
    const flows = (graph.edges || []).filter((e) => e.type === "flows_to" && ids.has(e.source) && ids.has(e.target));
    return { p, lanes, steps, flows };
  }).filter((b) => b.steps.length || b.lanes.some((l) => l.steps.length));
  if (!blocks.length) return rGraph(aspect, graph, nodes);

  const heights = blocks.map((b) => Math.max(70, b.lanes.reduce((acc, l) => acc + Math.max(1, l.steps.length) * GAP + 24, 26)));
  let y = PAD;
  const svg = awinSvg(BW + PAD * 2, PAD + heights.reduce((a, h) => a + h + 16, 0) + PAD);
  blocks.forEach(({ p, lanes, flows }, bi) => {
    const h = heights[bi];
    svg.appendChild(svgEl("rect", { x: PAD, y, width: BW, height: h, rx: 10, fill: "none",
      stroke: "#f0883e", "stroke-width": 1.4, "stroke-dasharray": "5 4", class: "process-node" }));
    awinText(svg, PAD + 10, y + 18, truncate(p.name || p.id, 34), "node-name");
    awinText(svg, PAD + BW - 10, y + 18, `Process · ${lanes.length} lane(s)`, "node-type", "end");
    const pos = new Map();
    let ly = y + 28;
    lanes.forEach((lane) => {
      const laneH = Math.max(1, lane.steps.length) * GAP + 18;
      svg.appendChild(svgEl("rect", { x: PAD + 8, y: ly, width: LANE_W, height: laneH, rx: 8,
        fill: "none", stroke: "#8b949e", "stroke-width": 1, class: "bpmn-lane" }));
      awinText(svg, PAD + 16, ly + 16, truncate(lane.name, 16), "node-type");
      lane.steps.forEach((t, i) => {
        const cy = ly + 20 + i * GAP;
        if (t.type === "Gateway") {
          awinDiamond(svg, PAD + LANE_W + 60, cy, 30, 22, "#f0883e", t.name || "?");
          pos.set(t.id, { x: PAD + LANE_W + 60, y: cy });
        } else {
          awinRound(svg, PAD + LANE_W + 28, cy - 11, 190, BH, "#f0883e", t.name || t.id, "task-node");
          pos.set(t.id, { x: PAD + LANE_W + 28, y: cy - 11, w: 190, h: BH });
        }
        awinText(svg, PAD + LANE_W + 240, cy + 4, t.type, "node-type");
      });
      ly += laneH + 8;
    });

    const draw = (a, b, implied) => {
      const x1 = a.x + (a.w ? a.w : 15), y1 = a.y + (a.h ? a.h / 2 : 0);
      const x2 = b.x - (b.w ? 0 : 15), y2 = b.y + (b.h ? b.h / 2 : 0);
      const line = svgEl("line", { x1, y1, x2, y2, stroke: implied ? "#8b949e" : "#f0883e",
        "stroke-width": 1.3, "stroke-dasharray": implied ? "4 3" : "",
        "marker-end": `url(#${svg.dataset.marker})` });
      line.setAttribute("class", "bpmn-flow" + (implied ? " implied" : ""));
      svg.appendChild(line);
    };
    if (flows.length) {
      flows.forEach((e) => {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (a && b) draw(a, b, false);
      });
    } else {
      const ordered = lanes.flatMap((l) => l.steps);
      for (let i = 1; i < ordered.length; i++) {
        const a = pos.get(ordered[i - 1].id), b = pos.get(ordered[i].id);
        if (a && b) draw(a, b, true);
      }
    }
    y += h + 16;
  });
  return svg;
}

function rTree(aspect, graph, nodes) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const layerOrder = ["Environment", "Node", "Runtime", "InfraResource"];
  const layers = layerOrder.map((t) => [t, nodes.filter((n) => n.type === t)]).filter(([, l]) => l.length);
  if (!layers.length) return rGraph(aspect, graph, nodes);
  const PAD = 22, BW = 208, BH = 36, GX = 22, LAYER = BH + 52;
  const cols = Math.max(...layers.map(([, l]) => l.length), 1);
  const width = PAD * 2 + cols * (BW + GX) - GX;
  const svg = awinSvg(width, PAD + layers.length * LAYER + PAD);
  const pos = new Map();
  let y = PAD;
  layers.forEach(([type, list]) => {
    svg.appendChild(svgEl("rect", { x: 8, y: y - 16, width: width - 16, height: BH + 34, rx: 10,
      fill: "none", stroke: "#1f6feb", "stroke-width": 1.1, "stroke-dasharray": "6 4",
      class: "deploy-boundary" }));
    awinText(svg, 16, y - 4, `${type} (${list.length})`, "node-type");
    list.forEach((n, i) => {
      const x = PAD + i * (BW + GX);
      const parent = byId.get(n.parent);
      const inner = awinBox(svg, x, y + 8, BW, BH, "#1f6feb", n.name || n.id,
        parent ? truncate(parent.name || parent.id, 14) : "");
      pos.set(n.id, { x: x + BW / 2, y: y + 8, h: BH });
      if (parent && pos.has(parent.id)) {
        const a = pos.get(parent.id), b = pos.get(n.id);
        const line = svgEl("line", { x1: a.x, y1: a.y + a.h, x2: b.x, y2: b.y, stroke: "#1f6feb",
          "stroke-width": 1.1, "marker-end": `url(#${svg.dataset.marker})` });
        line.setAttribute("class", "deploy-link");
        svg.appendChild(line);
      }
    });
    y += LAYER;
  });
  return svg;
}

function rTable(aspect, graph, nodes) {
  const byId = new Map((graph.nodes || []).map((n) => [n.id, n]));
  const rowH = 20, width = 520, height = 40 + nodes.length * rowH + 10;
  const svg = awinSvg(width, height);
  awinText(svg, 12, 18, "element", "node-type");
  awinText(svg, 300, 18, "type", "node-type");
  awinText(svg, 420, 18, "anchor", "node-type");
  nodes.forEach((n, i) => {
    const y = 40 + i * rowH;
    const parent = n.parent ? byId.get(n.parent) : null;
    awinText(svg, 12, y, truncate(n.name || n.id, 42), "node-detail");
    awinText(svg, 300, y, String(n.type), "node-type");
    awinText(svg, 420, y, parent ? truncate(parent.name || parent.id, 16) : "—", "node-type");
  });
  return svg;
}

// interface/process -> sequence diagram derived from call/flow edges (Ф2.10)
const SEQ_EDGE_TYPES = ["calls", "flows_to", "publishes", "subscribes"];

function seqData(graph) {
  const byId = new Map((graph.nodes || []).map((n) => [n.id, n]));
  const ops = new Map();
  (graph.nodes || []).forEach((n) => {
    if (n.type !== "Operation" || !n.parent) return;
    if (!ops.has(n.parent)) ops.set(n.parent, []);
    ops.get(n.parent).push(n.name || n.id);
  });
  const edges = (graph.edges || []).filter((e) => SEQ_EDGE_TYPES.includes(e.type)
    && byId.has(e.source) && byId.has(e.target));
  const labels = new Map();
  edges.forEach((e) => {
    [e.source, e.target].forEach((id) => {
      if (!labels.has(id)) labels.set(id, byId.get(id).name || id);
    });
  });
  // participants that own operations but are not part of an edge are still shown
  ops.forEach((_names, owner) => {
    if (byId.has(owner) && !labels.has(owner) && edges.length) labels.set(owner, byId.get(owner).name || owner);
  });
  return { byId, ops, edges, labels };
}

function rSequence(aspect, graph, nodes) {
  const { byId, ops, edges, labels } = seqData(graph);
  const names = [...labels.entries()].sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  if (!names.length) return rGraph(aspect, graph, nodes);

  const sel = (state.awinSeq && state.awinSeq[aspect]) || { from: "", to: "" };
  const shown = edges.filter((e) => (!sel.from || e.source === sel.from || e.target === sel.from)
    && (!sel.to || e.source === sel.to || e.target === sel.to));
  const label = (edge) => {
    const owner = ops.get(edge.target);
    return owner && owner.length ? owner[0] : String(edge.type);
  };

  const COL = 150, PAD = 24, HEAD = 26, ROW = 6, STEP = 26;
  const width = PAD * 2 + Math.max(names.length, 2) * COL;
  const height = Math.max(160, PAD + 70 + Math.max(shown.length, 1) * STEP + 40);
  const svg = awinSvg(width, height);

  const xOf = new Map();
  names.forEach(([id], i) => {
    const x = PAD + i * COL + COL / 2;
    xOf.set(id, x);
    const box = awinBox(svg, PAD + i * COL + 8, PAD, COL - 16, HEAD, "#2f81f7",
      labels.get(id), (ops.get(id) || []).length ? `${(ops.get(id) || []).length} op` : "");
    svg.appendChild(svgEl("line", { x1: x, y1: box + 4, x2: x, y2: height - 20,
      stroke: "#8b949e", "stroke-width": 1, "stroke-dasharray": "4 4", class: "seq-lifeline" }));
  });

  if (!shown.length) {
    awinText(svg, PAD, PAD + 70 + ROW, "(no call/flow relationships to sequence)", "node-detail");
  }
  shown.forEach((edge, i) => {
    const y = PAD + 70 + ROW + i * STEP;
    const x1 = xOf.get(edge.source), x2 = xOf.get(edge.target);
    if (x1 === undefined || x2 === undefined) return;
    const line = svgEl("line", { x1, y1: y, x2, y2: y, stroke: "#2f81f7", "stroke-width": 1.4,
      "marker-end": `url(#${svg.dataset.marker})` });
    line.setAttribute("class", "seq-msg");
    svg.appendChild(line);
    const t = awinText(svg, (x1 + x2) / 2, y - 4, `${i + 1}. ${label(edge)}`, "edge-label", "middle");
    t.setAttribute("class", "edge-label seq-label");
  });

  if (typeof state.awinSeq !== "object" || state.awinSeq === null) state.awinSeq = {};
  state.awinSeq[aspect] = sel;
  return svg;
}

function rC4(aspect, graph, nodes) {
  const kids = awinKids(graph);
  const containers = nodes.filter((n) => n.type === "Container");
  const placed = new Set();
  const bands = containers.map((c) => {
    const comps = (kids.get(c.id) || []).filter((k) => ["Component", "Module"].includes(k.type));
    comps.forEach((k) => placed.add(k.id));
    return { c, comps };
  }).filter((b) => b.comps.length);
  const loose = nodes.filter((n) => ["Component", "Module", "ExternalSystem", "Actor", "DataStore", "System"].includes(n.type)
    && !placed.has(n.id));
  if (!bands.length && !loose.length) return rGraph(aspect, graph, nodes);

  const PAD = 22, BW = 190, BH = 56, GX = 16, GY = 26, COLS = 3;
  const cols = Math.max(...bands.map((b) => Math.min(b.comps.length, COLS)), Math.min(loose.length, 4), 1);
  const width = PAD * 2 + cols * (BW + GX) - GX;
  let height = PAD;
  const bandHeights = bands.map((b) => Math.max(BH + 46, 48 + Math.ceil(b.comps.length / COLS) * (BH + GY)));
  height += bandHeights.reduce((a, h) => a + h + 16, 0);
  if (loose.length) height += BH + 52;
  const svg = awinSvg(width, height + PAD);

  let y = PAD;
  bands.forEach(({ c, comps }, i) => {
    const h = bandHeights[i];
    svg.appendChild(svgEl("rect", { x: 10, y, width: width - 20, height: h, rx: 12, fill: "none",
      stroke: "#3b82f6", "stroke-width": 1.3, "stroke-dasharray": "6 4", class: "c4-boundary" }));
    awinText(svg, 20, y + 18, `Container: ${truncate(c.name || c.id, 32)}`, "node-name");
    comps.forEach((k, j) => {
      const col = j % COLS, row = Math.floor(j / COLS);
      const bx = 24 + col * (BW + GX), by = y + 30 + row * (BH + GY);
      const inner = awinBox(svg, bx, by, BW, BH, "#3b82f6", k.name || k.id, k.type);
      (kids.get(k.id) || []).slice(0, 3).forEach((child, ci) => {
        awinText(svg, bx + 12, inner + 12 + ci * 12,
          truncate(`· ${child.name || child.id} (${child.type})`, 34), "node-detail");
      });
    });
    y += h + 16;
  });
  if (loose.length) {
    svg.appendChild(svgEl("rect", { x: 10, y, width: width - 20, height: BH + 44, rx: 12, fill: "none",
      stroke: "#8b949e", "stroke-width": 1.1, "stroke-dasharray": "6 4",
      class: "c4-boundary unassigned" }));
    awinText(svg, 20, y + 18, "externals · actors · stores · unassigned", "node-type");
    loose.forEach((k, j) => {
      const bx = 24 + (j % 4) * (BW + GX);
      const inner = awinBox(svg, bx, y + 30, BW, BH, "#8b949e", k.name || k.id, k.type);
      (kids.get(k.id) || []).slice(0, 3).forEach((child, ci) => {
        awinText(svg, bx + 12, inner + 12 + ci * 12,
          truncate(`· ${child.name || child.id} (${child.type})`, 34), "node-detail");
      });
    });
  }
  return svg;
}

const C4_ORDER = ["System", "Container", "Component", "Module", "ExternalSystem", "Actor", "DataStore"];
const ASPECT_RENDERERS = {
  graph: rGraph,
  c4: rC4,
  api: rApi,
  er: rEr,
  state: rState,
  bpmn: rBpmn,
  sequence: rSequence,
  tree: rTree,
  table: rTable,
};

// Generic aspect diagram: elements are grouped into a column per node type (in
// the aspect's declared order, or an explicit order) and linked by aspect edges.
function rGraph(aspect, graph, nodes, orderOverride) {
  const order = orderOverride || ASPECT_TYPES[aspect] || [];
  const rank = (t) => { const i = order.indexOf(t); return i < 0 ? 99 : i; };
  const groups = new Map();
  nodes.forEach((n) => {
    const key = String(n.type);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  });
  const types = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  const kidsOf = new Map();
  (graph.nodes || []).forEach((n) => {
    if (!n.parent) return;
    if (!kidsOf.has(n.parent)) kidsOf.set(n.parent, []);
    kidsOf.get(n.parent).push(n);
  });

  const BW = 200, GX = 48, GY = 22, PAD = 24, HEAD = 26;
  const pos = new Map();
  const heights = new Map();
  let maxBottom = PAD + HEAD;
  types.forEach((type, ci) => {
    const list = groups.get(type).slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
    let y = PAD + HEAD;
    list.forEach((node) => {
      const details = (kidsOf.get(node.id) || []).slice(0, 6);
      const h = 40 + details.length * 14;
      const x = PAD + ci * (BW + GX);
      pos.set(node.id, { x, y, w: BW, h, details });
      heights.set(node.id, h);
      y += h + GY;
    });
    maxBottom = Math.max(maxBottom, y);
  });
  const width = PAD * 2 + types.length * (BW + GX) - GX;
  const height = Math.max(160, maxBottom + PAD - GY);

  const svg = svgEl("svg", {
    class: "awin-svg", xmlns: NS, width, height, viewBox: `0 0 ${width} ${height}`,
  });
  svg.style.width = width + "px";
  svg.style.height = height + "px";

  const defs = svgEl("defs");
  const marker = svgEl("marker", { id: "awin-arrow-" + aspect, viewBox: "0 0 10 10",
    refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
  marker.appendChild(svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: "#8b949e" }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  types.forEach((type, ci) => {
    const x = PAD + ci * (BW + GX);
    const label = svgEl("text", { x, y: PAD + 14, class: "node-type" });
    label.textContent = `${type} (${groups.get(type).length})`;
    svg.appendChild(label);
  });

  const ids = new Set(nodes.map((n) => n.id));
  (graph.edges || []).forEach((edge) => {
    if (!ids.has(edge.source) || !ids.has(edge.target)) return;
    const a = pos.get(edge.source), b = pos.get(edge.target);
    if (!a || !b) return;
    const x1 = a.x + (b.x >= a.x ? a.w : 0), y1 = a.y + a.h / 2;
    const x2 = b.x + (b.x >= a.x ? 0 : b.w), y2 = b.y + b.h / 2;
    svg.appendChild(svgEl("line", { x1, y1, x2, y2, stroke: "#8b949e", "stroke-width": 1.2,
      "marker-end": `url(#awin-arrow-${aspect})` }));
    const t = svgEl("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 3, class: "edge-label", "text-anchor": "middle" });
    t.textContent = String(edge.type || "");
    svg.appendChild(t);
  });

  nodes.forEach((node) => {
    const p = pos.get(node.id);
    if (!p) return;
    const color = ASPECT_COLORS[aspectOf(node)] || "#3b82f6";
    const box = svgEl("rect", { x: p.x, y: p.y, width: p.w, height: p.h, rx: 8,
      fill: "var(--panel-2, #161b22)", stroke: color, "stroke-width": 1.4 });
    svg.appendChild(box);
    const name = svgEl("text", { x: p.x + 10, y: p.y + 18, class: "node-name", fill: color });
    name.textContent = `${node.name || node.id}`;
    svg.appendChild(name);
    const type = svgEl("text", { x: p.x + p.w - 10, y: p.y + 18, class: "node-type", "text-anchor": "end" });
    type.textContent = String(node.type);
    svg.appendChild(type);
    p.details.forEach((child, i) => {
      const row = svgEl("text", { x: p.x + 14, y: p.y + 36 + i * 14, class: "node-detail" });
      row.textContent = truncate(`\u00b7 ${child.name || child.id} (${child.type})`, 34);
      svg.appendChild(row);
    });
  });
  return svg;
}

// sequence view: participant pair filter (Ф2.10)
function buildSeqFilters(aspect, graph) {
  const wrap = document.createElement("div");
  wrap.className = "awin-filters";
  const { labels } = seqData(graph);
  [["from", "from"], ["to", "to"]].forEach(([key, title]) => {
    const sel = document.createElement("select");
    sel.className = "mini awin-seq-" + key;
    sel.title = `sequence: ${title} participant`;
    const any = document.createElement("option");
    any.value = "";
    any.textContent = `${title}: any`;
    sel.appendChild(any);
    [...labels.entries()]
      .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
      .forEach(([id, name]) => {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = String(name).slice(0, 18);
        sel.appendChild(option);
      });
    const current = (state.awinSeq && state.awinSeq[aspect] && state.awinSeq[aspect][key]) || "";
    sel.value = current;
    sel.addEventListener("change", () => {
      if (!state.awinSeq) state.awinSeq = {};
      const pair = state.awinSeq[aspect] || { from: "", to: "" };
      pair[key] = sel.value;
      state.awinSeq[aspect] = pair;
      renderAspectWindow(aspect);
    });
    wrap.appendChild(sel);
  });
  return wrap;
}

function download(text, filename, mime) {
  const blob = new Blob([text], { type: mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return filename;
}

// Ф2.12: every aspect view in its current notation, stacked into one SVG
function exportAllAspects() {
  const graph = state.fullGraph || state.graph;
  if (!graph) { setStatus("nothing to export", "error"); return; }
  const parts = [];
  let y = 0, width = 640, count = 0;
  Object.keys(ASPECT_TYPES || {}).forEach((aspect) => {
    const nodes = (graph.nodes || []).filter((n) => aspectOf(n) === aspect);
    if (!nodes.length) return;
    const mode = (state.awinMode && state.awinMode[aspect]) || ASPECT_DEFAULT_MODE[aspect] || "graph";
    const draw = ASPECT_RENDERERS[mode] || rGraph;
    let svg;
    try { svg = draw(aspect, graph, nodes); } catch (e) { return; }
    const w = Number(svg.getAttribute("width")) || 640;
    const h = Number(svg.getAttribute("height")) || 200;
    parts.push(`<g class="awin-export" data-aspect="${aspect}" transform="translate(0,${y})">`
      + `<text x="10" y="18" class="node-name">${aspect} · ${mode}</text>`
      + `<g transform="translate(0,28)">${svg.innerHTML}</g></g>`);
    y += h + 52;
    width = Math.max(width, w);
    count += 1;
  });
  if (!count) { setStatus("no aspect elements to export", "error"); return; }
  const out = `<svg xmlns="${NS}" width="${width}" height="${y}">${parts.join("")}</svg>`;
  const name = download(out, (state.modelName || "architecture") + ".aspects.svg", "image/svg+xml");
  setStatus(`exported ${count} aspect view(s) → ${name}`, "ok");
}

// Ф2.13: show one whole C4 level, and find a node by name
function applyLevelJump() {
  const raw = $("level-jump") ? $("level-jump").value : "";
  state.levelOnly = raw === "" ? null : Number(raw);
  state.focus = null;
  render();
  setStatus(state.levelOnly === null ? "level: auto (around the focus)" : `level L${state.levelOnly + 1}`, "ok");
}

function findNode() {
  const query = ($("node-search") ? $("node-search").value : "").trim().toLowerCase();
  if (!query) return;
  const graph = state.fullGraph || state.graph;
  const nodes = (graph && graph.nodes) || [];
  const hit = nodes.find((n) => String(n.name || "").toLowerCase().includes(query))
    || nodes.find((n) => String(n.id).toLowerCase().includes(query));
  if (!hit) { setStatus(`node "${query}" not found`, "error"); return; }
  state.levelOnly = null;
  if ($("level-jump")) $("level-jump").value = "";
  // reveal the node even when its aspect is filtered out
  const aspect = aspectOf(hit);
  if (aspect && state.aspects && !state.aspects.has(aspect)) {
    state.aspects.add(aspect);
    buildAspectChips();
  }
  state.focus = hit.parent || null;
  state.selected = hit.id;
  render();
  setStatus(`found ${hit.name || hit.id}`, "ok");
}

function renderAspectWindow(aspect) {
  const win = $("awin-" + aspect);
  if (!win) return;
  const body = win.querySelector(".awin-body");
  const graph = state.fullGraph || state.graph;
  body.innerHTML = "";
  if (!graph || !(graph.nodes || []).length) {
    body.innerHTML = "<div class='muted'>no model loaded</div>";
    return;
  }
  const nodes = (graph.nodes || []).filter((n) => aspectOf(n) === aspect);
  if (!nodes.length) {
    body.innerHTML = "<div class='muted'>no elements</div>";
    return;
  }
  const mode = (state.awinMode && state.awinMode[aspect]) || ASPECT_DEFAULT_MODE[aspect] || "graph";
  const draw = ASPECT_RENDERERS[mode] || rGraph;
  if (mode === "sequence") body.appendChild(buildSeqFilters(aspect, graph));
  try {
    body.appendChild(draw(aspect, graph, nodes));
  } catch (err) {
    body.innerHTML = "<div class='muted'>render failed: " + err.message + "</div>";
  }
}

function exportAspectSvg(aspect) {
  const win = $("awin-" + aspect);
  const svg = win && win.querySelector(".awin-body svg");
  if (!svg) { setStatus("nothing to export for " + aspect, "error"); return; }
  const blob = new Blob([svg.outerHTML], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = (state.modelName || "architecture") + "-" + aspect + ".svg";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  setStatus("exported " + a.download, "ok");
}

function renderAllAspectWindows() {
  document.querySelectorAll("#windows .awin").forEach((w) => renderAspectWindow(w.id.replace("awin-", "")));
}

function addAspectElement(aspect) {
  const win = $("awin-" + aspect);
  if (!win) return;
  const name = win.querySelector(".awin-add input").value.trim();
  const type = win.querySelector(".awin-add select").value;
  if (!name) return;
  const parent = state.focus || state.rootId;
  const id = `${type.toLowerCase()}://${(state.modelName || "app")}/${name.replace(/[^A-Za-z0-9._-]+/g, "-")}`;
  applyCanvasEdit([{ op: "add_node", node: { id, type, name, parent, evidence: [] } }]);
}

function makeDraggable(win) {
  const head = win.querySelector(".awin-head");
  let dragging = false, sx = 0, sy = 0;
  head.addEventListener("mousedown", (e) => {
    dragging = true;
    sx = e.clientX - win.offsetLeft;
    sy = e.clientY - win.offsetTop;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    win.style.left = (e.clientX - sx) + "px";
    win.style.top = (e.clientY - sy) + "px";
  });
  window.addEventListener("mouseup", () => { dragging = false; });
}

function openArtifactsWindow(artifacts) {
  const winId = "awin-artifacts";
  if ($(winId)) $(winId).remove();
  const names = Object.keys(artifacts || {});
  const win = document.createElement("div");
  win.className = "awin wide";
  win.id = winId;
  win.style.left = "40px";
  win.style.top = "40px";
  win.innerHTML = `
    <div class="awin-head"><span>Generated artifacts</span><button class="awin-close" title="close">\u2715</button></div>
    <div class="awin-tabs">${names.map((n, i) => `<button class="tab${i === 0 ? " active" : ""}" data-a="${n}">${n}</button>`).join("")}</div>
    <pre class="awin-pre"></pre>`;
  $("windows").appendChild(win);
  const pre = win.querySelector(".awin-pre");
  const show = (name) => {
    pre.textContent = (artifacts || {})[name] || "";
    win.querySelectorAll(".awin-tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.a === name));
  };
  win.querySelectorAll(".awin-tabs .tab").forEach((b) => b.addEventListener("click", () => show(b.dataset.a)));
  show(names[0] || "");
  win.querySelector(".awin-close").addEventListener("click", () => win.remove());
  makeDraggable(win);
}

function openCoverageWindow(cov) {
  const winId = "awin-coverage";
  if ($(winId)) $(winId).remove();
  const rows = ((cov && cov.matrix) || []).map((r) =>
    `<div class="awin-row"><span>${r.element}</span><span class="${r.covered ? "ok" : "warn"}">${r.covered ? "covered" : "\u2014"}</span></div>`).join("");
  const win = document.createElement("div");
  win.className = "awin";
  win.id = winId;
  win.style.left = "80px";
  win.style.top = "80px";
  win.innerHTML = `
    <div class="awin-head"><span>Coverage ${(cov && cov.covered) || 0}/${(cov && cov.total) || 0}</span><button class="awin-close" title="close">\u2715</button></div>
    <div class="awin-body">${rows || "<div class='muted'>no elements</div>"}</div>`;
  $("windows").appendChild(win);
  win.querySelector(".awin-close").addEventListener("click", () => win.remove());
  makeDraggable(win);
}

function openAspectWindow(aspect) {
  if (!aspect) return;
  const winId = "awin-" + aspect;
  if ($(winId)) return;
  const offset = document.querySelectorAll("#windows .awin").length * 26;
  const win = document.createElement("div");
  win.className = "awin";
  win.id = winId;
  win.style.left = (16 + offset) + "px";
  win.style.top = (16 + offset) + "px";
  win.innerHTML = `
    <div class="awin-head"><span>${aspect}</span>
      <select class="awin-mode" title="view type"></select>
      <button class="awin-min" title="minimize">\u2013</button>
      <button class="awin-dl" title="export this view as SVG">\u2913</button>
      <button class="awin-close" title="close">\u2715</button></div>
    <div class="awin-body"></div>
    <div class="awin-add">
      <input type="text" placeholder="name" />
      <select></select>
      <button class="secondary">Add</button>
    </div>`;
  $("windows").appendChild(win);
  const sel = win.querySelector(".awin-add select");
  (ASPECT_TYPES[aspect] || []).forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  });
  const modeSel = win.querySelector(".awin-mode");
  (ASPECT_MODES[aspect] || ["graph"]).forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    modeSel.appendChild(opt);
  });
  modeSel.value = state.awinMode[aspect] || ASPECT_DEFAULT_MODE[aspect] || "graph";
  modeSel.addEventListener("change", () => {
    state.awinMode[aspect] = modeSel.value;
    renderAspectWindow(aspect);
  });
  win.querySelector(".awin-close").addEventListener("click", () => win.remove());
  win.querySelector(".awin-min").addEventListener("click", () => win.classList.toggle("min"));
  win.querySelector(".awin-dl").addEventListener("click", () => exportAspectSvg(aspect));
  state.lastAwin = aspect;
  win.querySelector(".awin-add button").addEventListener("click", () => addAspectElement(aspect));
  makeDraggable(win);
  renderAspectWindow(aspect);
}

async function exportDocumentation() {
  const model = state.modelName;
  if (!model) { setStatus("load a model first", "error"); return; }
  try {
    const doc = await api(`/api/models/${encodeURIComponent(model)}/documentation`);
    const blob = new Blob([doc.markdown || ""], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${model}.architecture.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus(`architecture documentation for ${model} downloaded (${doc.sections ? doc.sections.length : 0} sections)`, "ok");
  } catch (e) {
    setStatus("documentation failed: " + e.message, "error");
  }
}

function exportGraph() {
  // Always export the complete model: `state.graph` may hold a slice view, and
  // writing it under the model name would save a partial architecture.
  const graph = state.fullGraph || state.graph;
  if (!graph) { setStatus("nothing to export", "error"); return; }
  const sliced = Boolean(state.fullGraph && state.graph !== state.fullGraph);
  const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = (state.modelName || "architecture") + ".archide.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  setStatus(sliced ? "exported " + a.download + " (full model, not the current slice)"
                   : "exported " + a.download, "ok");
}

async function importGraph(file) {
  if (!file) return;
  let graph;
  try {
    graph = JSON.parse(await file.text());
  } catch (e) {
    setStatus("import: invalid JSON", "error");
    return;
  }
  const name = file.name.replace(/\.archide\.json$|\.json$/i, "") || "imported";
  // Never replace a saved model silently: ask before overwriting an existing name.
  let exists = false;
  try { await api(`/api/models/${encodeURIComponent(name)}`); exists = true; } catch (e) { exists = false; }
  if (exists && typeof confirm === "function" && !confirm(`Model "${name}" already exists. Overwrite it?`)) {
    setStatus("import cancelled; model " + name + " was not overwritten", "error");
    $("import_file").value = "";
    return;
  }
  setGraph(graph);
  setStatus("imported " + file.name, "ok");
  try {
    await api(`/api/models/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph }),
    });
    state.modelName = name;
    $("model_load").value = name;
    $("gen_model").value = name;
    setStatus("imported & saved as " + name, "ok");
  } catch (e) {
    setStatus("imported (not saved: " + e.message + ")", "error");
  }
  $("import_file").value = "";
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
  // The aspect chips became the canvas view sub-tabs (C4 + one per aspect): an
  // aspect is a view of the model, not a layer stacked on the C4 canvas.
  syncViewTabs();
  const wa = $("win_aspect");
  if (wa) {
    wa.innerHTML = "";
    Object.keys(ASPECT_TYPES).forEach((aspect) => {
      const opt = document.createElement("option");
      opt.value = aspect; opt.textContent = aspect;
      wa.appendChild(opt);
    });
  }
}

function toggleAspect(aspect) {
  // kept for the command palette: switching to an aspect is switching the view
  setView(aspect === "structure" ? "c4" : aspect);
}

function applyMetamodel(mm) {
  if (!mm || !mm.aspects) return;
  const rebuilt = {};
  Object.entries(mm.aspects).forEach(([name, spec]) => { rebuilt[name] = spec.node_types || []; });
  if (Object.keys(rebuilt).length) {
    ASPECT_TYPES = rebuilt;
    rebuildTypeMap();
    buildViewTabs();
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
  state.fullGraph = state.graph;
  state.focus = null;
  state.selected = null;
  state.treeSel = null;
  buildTree();
  persistUi();
  render();
}

async function applySlice() {
  const scope = $("view_scope").value.trim();
  const info = $("view-info");
  if (!scope) { info.textContent = "enter a scope node id"; return; }
  if (!state.fullGraph) { info.textContent = "no graph loaded"; return; }
  try {
    const res = await api("/api/slice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        graph: state.fullGraph,
        scope,
        depth: Number($("view_depth").value) || 0,
        direction: $("view_dir").value,
      }),
    });
    state.graph = res.slice;
    state.focus = null;
    state.selected = null;
    render();
    info.textContent = `slice: ${res.slice.nodes.length} nodes, ${res.slice.edges.length} edges`;
  } catch (e) {
    info.textContent = "slice failed: " + e.message;
  }
}

function focusSelectedNode() {
  if (!state.selected) return;
  $("view_scope").value = state.selected;
  applySlice();
}

function resetSlice() {
  state.graph = state.fullGraph;
  state.focus = null;
  state.selected = null;
  $("view-info").textContent = "";
  render();
}

// -------------------------------------------------------------------------
// Navigation: level drill-down/up + view camera (pan/zoom), all on one camera.
// Interaction handlers are bound once (wireCanvas) and always act on the live
// viewport stored by drawPane; binding them per render used to accumulate stale
// closures that wrote to detached viewports, which broke pan/zoom.
function applyCam() {
  if (state._viewport && state._cam) {
    state._viewport.setAttribute(
      "transform", `translate(${state._cam.x},${state._cam.y}) scale(${state._cam.k})`);
  }
}

function zoomCanvas(factor, mx, my) {
  const cam = state._cam;
  if (!cam) return;
  const rect = $("canvas-wrap").getBoundingClientRect();
  const px = typeof mx === "number" ? mx : rect.width / 2;
  const py = typeof my === "number" ? my : rect.height / 2;
  const nk = Math.max(0.15, Math.min(4, cam.k * factor));
  cam.x = px - (px - cam.x) * (nk / cam.k);
  cam.y = py - (py - cam.y) * (nk / cam.k);
  cam.k = nk;
  applyCam();
  setStatus("zoom " + Math.round(nk * 100) + "%", "ok");
}

function wireCanvas() {
  const svg = $("canvas");
  let dragging = false, dragged = false, sx = 0, sy = 0;
  svg.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0 || !state._cam) return;
    dragging = true;
    dragged = false;
    sx = ev.clientX - state._cam.x;
    sy = ev.clientY - state._cam.y;
    svg.classList.add("dragging");
  });
  window.addEventListener("mouseup", () => { dragging = false; svg.classList.remove("dragging"); });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging || !state._cam) return;
    if (Math.abs(ev.clientX - (sx + state._cam.x)) > 3 || Math.abs(ev.clientY - (sy + state._cam.y)) > 3) dragged = true;
    state._cam.x = ev.clientX - sx;
    state._cam.y = ev.clientY - sy;
    applyCam();
  });
  // UI-2: a click on empty canvas offers "create here" (nodes stop propagation)
  svg.addEventListener("click", (ev) => {
    if (dragged) { dragged = false; return; }
    if (!state.graph && !state.fullGraph) return;
    const popover = $("popover");
    if (!popover) return;
    const rect = $("canvas-wrap").getBoundingClientRect();
    popover.style.left = Math.min(Math.max(8, ev.clientX - rect.left), rect.width - 240) + "px";
    popover.style.top = Math.min(Math.max(8, ev.clientY - rect.top), rect.height - 140) + "px";
    popover.classList.remove("hidden");
  });
  svg.addEventListener("wheel", (ev) => {
    if (!state._cam) return;
    ev.preventDefault();
    const rect = $("canvas-wrap").getBoundingClientRect();
    zoomCanvas(ev.deltaY < 0 ? 1.12 : 1 / 1.12, ev.clientX - rect.left, ev.clientY - rect.top);
  }, { passive: false });
}

function goHome() {
  // A real home: back to the whole system with every filter reset, so a stale
  // aspect/source filter can never make this look like it did nothing.
  state.focus = null;
  state.selected = null;
  state.aspects = new Set(["structure"]);
  state.view = "c4";
  state.graph = state.fullGraph;      // a Focus slice must not survive "home"
  state.levelsUp = 1;
  state.levelsDown = 2;
  if ($("view_info")) $("view_info").textContent = "";
  if ($("view_scope")) $("view_scope").value = "";
  if ($("view-info")) $("view-info").textContent = "";
  if ($("lvl-up")) $("lvl-up").value = String(state.levelsUp);
  if ($("lvl-down")) $("lvl-down").value = String(state.levelsDown);
  setSourceMode("all");   // re-renders
  buildAspectChips();
  render();
  setStatus("home: whole system, filters reset", "ok");
}

function zoomIn() {
  const kids = state.kids || new Map();
  if (state.selected && (kids.get(state.selected) || []).length) {
    state.focus = state.selected;
    state.selected = null;
    render();
    return;
  }
  setStatus("select a node (⇩ needs a selected parent node)", "error");
}

function zoomOut() {
  const byId = state.byId || new Map();
  const cur = state.focus;
  if (!cur) { setStatus("already at the top level", "error"); return; }
  const node = byId.get(cur);
  state.focus = node && node.parent ? node.parent : null;
  state.selected = null;
  render();
}

function computeView(graph) {
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
  if (!nodes.length) {
    return { nodes, edges, byId, kids, rootId: null, focusId: null, absDepth: new Map() };
  }
  const roots = nodes.filter((n) => !n.parent || !byId.has(n.parent));
  const rootId = roots.length ? roots[0].id : nodes[0].id;
  const focusId = state.focus && byId.has(state.focus) ? state.focus : rootId;
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
  return { nodes, edges, byId, kids, rootId, focusId, absDepth };
}

function drawPane(svg, wrap, emptyEl, aspectsSet, view) {
  svg.innerHTML = "";
  const { edges, byId, kids, focusId, absDepth } = view;
  const mode = state.sourceMode;
  const srcOf = (el) => (el && el.sources && el.sources.length ? el.sources : ["code"]);
  const matchMode = (el) => mode === "all" || srcOf(el).includes(mode);
  const matchAspect = (el) => aspectsSet.has(aspectOf(el));
  // Layered neighbourhood: show `levels up` above the focus and `levels down`
  // below it (rows per absolute level, C4 style) instead of a single level.
  const levelRaw = state.levelOnly;
  const levelView = levelRaw !== null && levelRaw !== undefined && levelRaw !== "";
  const up = levelView ? 0 : Math.max(0, Math.min(4, Number(state.levelsUp) || 0));
  const down = levelView ? 0 : Math.max(0, Math.min(4, Number(state.levelsDown) || 0));
  const focusDepth = absDepth.get(focusId) || 0;
  const topDepth = Math.max(0, focusDepth - up);
  let topId = focusId;
  let guard = 0;
  while (topId && (absDepth.get(topId) || 0) > topDepth && guard++ < 64) {
    const node = byId.get(topId);
    topId = node && node.parent ? node.parent : null;
  }
  if (!topId || !byId.has(topId)) topId = focusId;
  const maxDepth = topDepth + up + down;
  let visNodes = [];
  if (levelView) {
    // Ф2.13: one whole C4 level, wrapped into rows of six
    const wanted = Number(levelRaw);
    visNodes = [...byId.values()].filter((n) => (absDepth.get(n.id) || 0) === wanted
      && matchMode(n) && matchAspect(n));
  } else {
    const walked = [];
    const collect = (id, guard2) => {
      const node = byId.get(id);
      if (!node || guard2 > 48) return;
      if ((absDepth.get(id) || 0) > maxDepth) return;
      if (matchMode(node) && matchAspect(node)) walked.push(node);
      (kids.get(id) || []).forEach((k) => collect(k.id, guard2 + 1));
    };
    collect(topId, 0);
    // At the top level the window starts at the focus itself; drop that box when
    // there are descendants, so the first visible (drillable) nodes are its
    // children — clicking the root box would otherwise look like a no-op.
    visNodes = (topId === focusId && walked.length > 1)
      ? walked.filter((n) => n.id !== topId)
      : walked;
  }

  const NW = 200, NH = 96, GX = 26, GY = 74, PAD = 60, LABEL_W = 140;
  if (!visNodes.length) {
    // Keep the camera/viewport live even when the filters hide everything, so
    // pan/zoom/home never act on a detached element from an earlier render.
    const emptyDefs = document.createElementNS(NS, "defs");
    svg.appendChild(emptyDefs);
    const emptyViewport = document.createElementNS(NS, "g");
    svg.appendChild(emptyViewport);
    state._cam = { x: 0, y: 0, k: 1 };
    if (svg.id === "canvas") { state._viewport = emptyViewport; state._pos = new Map(); }
    if (emptyEl) {
      emptyEl.textContent = "Nothing to show here with the current aspect/source filters — press ⌂ (home) to reset.";
      emptyEl.style.display = "flex";
    }
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";

  const levelLabel = levelView
    ? `${LEVEL_LABELS[Number(levelRaw)] || "Level " + (Number(levelRaw) + 1)} — whole level`
    : (up || down
      ? `${LEVEL_LABELS[topDepth] || "Level " + (topDepth + 1)} … ${LEVEL_LABELS[maxDepth] || "Level " + (maxDepth + 1)}`
      : (LEVEL_LABELS[focusDepth] || `Level ${focusDepth + 1}`));

  // Rows are laid out in visual order: a level is wrapped into several sub-rows so
  // a wide level (hundreds of operations) does not stretch the canvas into a
  // 60 000px strip that the fit-to-view shrinks into an unreadable line.
  let rows;
  if (levelView) {
    const CHUNK = 6;
    const sorted = visNodes.slice().sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
    rows = [];
    for (let i = 0; i < sorted.length; i += CHUNK) rows.push({ depth: null, nodes: sorted.slice(i, i + CHUNK) });
  } else {
    const depths = [...new Set(visNodes.map((n) => absDepth.get(n.id) || 0))].sort((a, b) => a - b);
    const perRowMax = Math.max(2, Math.min(8, Math.ceil(Math.sqrt(visNodes.length)) + 1));
    rows = [];
    // a node's horizontal order follows its parent, so edges go down instead of
    // crossing the whole canvas (alphabetical order scattered them)
    const orderIndex = new Map();
    depths.forEach((d) => {
      const list = visNodes.filter((n) => (absDepth.get(n.id) || 0) === d)
        .sort((a, b) => {
          const pa = orderIndex.has(a.parent) ? orderIndex.get(a.parent) : 1e9;
          const pb = orderIndex.has(b.parent) ? orderIndex.get(b.parent) : 1e9;
          return pa - pb || String(a.name || a.id).localeCompare(String(b.name || b.id));
        });
      let cursor = 0;
      for (let i = 0; i < list.length; i += perRowMax) {
        const chunk = list.slice(i, i + perRowMax);
        rows.push({ depth: d, nodes: chunk });
        chunk.forEach((n, j) => orderIndex.set(n.id, cursor + j));
        cursor += chunk.length;
      }
    });
  }
  const rowTop = (index) => PAD + 30 + index * (NH + GY);
  const rowWidths = rows.map((r) => r.nodes.length * (NW + GX) - GX);
  const maxRowWidth = Math.max(NW, ...rowWidths);
  const pos = new Map();
  rows.forEach((r, index) => {
    const startX = LABEL_W + PAD + Math.max(0, (maxRowWidth - rowWidths[index]) / 2);
    r.nodes.forEach((node, i) => {
      pos.set(node.id, { x: startX + i * (NW + GX), y: rowTop(index), w: NW, h: NH });
    });
  });

  applyLayoutPositions(pos);   // a saved arrangement wins over the computed one

  const totalW = LABEL_W + maxRowWidth + PAD * 2;
  const totalH = rows.length * (NH + GY) - GY + PAD * 2 + 30;

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
  if (!levelView) {
    const seenDepths = new Set();
    rows.forEach((r, index) => {
      if (r.depth === null || seenDepths.has(r.depth)) return;
      seenDepths.add(r.depth);
      const rowLabel = document.createElementNS(NS, "text");
      rowLabel.setAttribute("class", "node-detail");
      rowLabel.setAttribute("x", 12);
      rowLabel.setAttribute("y", rowTop(index) + 20);
      rowLabel.textContent = LEVEL_LABELS[r.depth] || `Level ${r.depth + 1}`;
      viewport.appendChild(rowLabel);
    });
  }

  const visible = new Set(visNodes.map((x) => x.id));
  // edge labels live in their own layer, moved above the nodes once they exist,
  // so a connection stays legible even where it passes behind an element
  let labelSeq = 0;
  const labelLayer = document.createElementNS(NS, "g");
  labelLayer.setAttribute("class", "edge-labels");
  viewport.appendChild(labelLayer);
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
    const t = e.type || "depends_on";
    const acx = a.x + a.w / 2, acy = a.y + a.h / 2;
    const bcx = b.x + b.w / 2, bcy = b.y + b.h / 2;
    const dx = bcx - acx, dy = bcy - acy;
    const gap = 6;
    const from = edgeAnchor(acx, acy, a.w, a.h, dx, dy, gap);
    const to = edgeAnchor(bcx, bcy, b.w, b.h, -dx, -dy, gap);
    const colour = EDGE_COLORS[t] || "#8b949e";
    // a gentle bend keeps the arrow clear of the boxes it passes
    const nx = -(to.y - from.y), ny = to.x - from.x;
    const nl = Math.hypot(nx, ny) || 1;
    const bend = Math.min(26, Math.hypot(to.x - from.x, to.y - from.y) * 0.12);
    const cx = (from.x + to.x) / 2 + (nx / nl) * bend;
    const cy = (from.y + to.y) / 2 + (ny / nl) * bend;
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", `M${from.x},${from.y} Q${cx},${cy} ${to.x},${to.y}`);
    path.setAttribute("class", "edge-line" + (t === "contains" ? " edge-contains" : ""));
    path.setAttribute("stroke", colour);
    path.setAttribute("stroke-width", t === "contains" ? "1.5" : "2.2");
    if (t !== "contains") path.setAttribute("marker-end", `url(#arrow-${t})`);
    const title = document.createElementNS(NS, "title");
    title.textContent = `${e.source} → ${e.target}\n${t}${e.description ? "\n" + e.description : ""}`;
    path.appendChild(title);
    viewport.appendChild(path);
    // C4 edges carry their meaning: label the line at its midpoint
    if (t === "contains" || e.source === e.target) return;
    // stagger along the curve (quadratic Bezier) so labels of parallel edges
    // do not stack on top of each other
    const tp = [0.5, 0.34, 0.66][labelSeq++ % 3];
    const q = (a, b, c) => (1 - tp) * (1 - tp) * a + 2 * (1 - tp) * tp * b + tp * tp * c;
    const lx = q(from.x, cx, to.x);
    const ly = q(from.y, cy, to.y);
    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "edge-label");
    label.setAttribute("x", lx);
    label.setAttribute("y", ly - 4);
    label.setAttribute("text-anchor", "middle");
    label.textContent = truncate(t, 18);
    labelLayer.appendChild(label);
    try {
      const bb = label.getBBox();
      const pill = document.createElementNS(NS, "rect");
      pill.setAttribute("class", "edge-label-pill");
      pill.setAttribute("x", bb.x - 4);
      pill.setAttribute("y", bb.y - 1);
      pill.setAttribute("width", bb.width + 8);
      pill.setAttribute("height", bb.height + 2);
      pill.setAttribute("rx", 3);
      labelLayer.insertBefore(pill, label);
    } catch (err) { /* getBBox is best-effort */ }
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
    const kind = c4Kind(node.type);
    const external = kind === "external";
    const banded = kind === "system" || kind === "container" || kind === "external";
    const g = document.createElementNS(NS, "g");
    g.setAttribute("transform", `translate(${p.x},${p.y})`);
    g.setAttribute("data-node-id", node.id);
    g.setAttribute("data-c4", kind);
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("class", "node-box" + (drillable ? " drillable" : "") + (selected ? " sel-node" : ""));
    rect.setAttribute("width", p.w);
    rect.setAttribute("height", p.h);
    rect.setAttribute("fill", selected ? "#1f2a37" : "#161b22");
    rect.setAttribute("stroke", color);
    rect.setAttribute("stroke-width", String(selected ? 3 : kind === "system" ? 2.5 : neighbor ? 2.5 : 1.6));
    if (external) rect.setAttribute("stroke-dasharray", "7 5");
    g.appendChild(rect);
    if (banded) {
      // C4 look: a tinted header strip carrying the element kind
      const band = document.createElementNS(NS, "rect");
      band.setAttribute("width", p.w);
      band.setAttribute("height", 21);
      band.setAttribute("fill", color);
      band.setAttribute("opacity", "0.18");
      band.setAttribute("rx", 6);
      g.appendChild(band);
    } else {
      const bar = document.createElementNS(NS, "rect");
      bar.setAttribute("width", 4);
      bar.setAttribute("height", p.h);
      bar.setAttribute("fill", color);
      bar.setAttribute("rx", 2);
      g.appendChild(bar);
    }
    if (kind === "deployment") {
      const inner = document.createElementNS(NS, "rect");
      inner.setAttribute("x", 5); inner.setAttribute("y", 5);
      inner.setAttribute("width", p.w - 10); inner.setAttribute("height", p.h - 10);
      inner.setAttribute("fill", "none"); inner.setAttribute("stroke", color);
      inner.setAttribute("stroke-width", "1"); inner.setAttribute("opacity", "0.55");
      inner.setAttribute("rx", 5);
      g.appendChild(inner);
    }
    if (kind === "database") {
      const top = document.createElementNS(NS, "ellipse");
      top.setAttribute("cx", p.w / 2); top.setAttribute("cy", 13);
      top.setAttribute("rx", p.w / 2 - 3); top.setAttribute("ry", 11);
      top.setAttribute("fill", "#0d1117"); top.setAttribute("stroke", color);
      top.setAttribute("stroke-width", "1.4");
      g.appendChild(top);
      const bottom = document.createElementNS(NS, "ellipse");
      bottom.setAttribute("cx", p.w / 2); bottom.setAttribute("cy", p.h - 13);
      bottom.setAttribute("rx", p.w / 2 - 3); bottom.setAttribute("ry", 11);
      bottom.setAttribute("fill", "none"); bottom.setAttribute("stroke", color);
      bottom.setAttribute("stroke-width", "1.2"); bottom.setAttribute("opacity", "0.75");
      g.appendChild(bottom);
    }
    if (kind === "person") {
      // the C4 stick figure
      const head = document.createElementNS(NS, "circle");
      head.setAttribute("cx", 24); head.setAttribute("cy", 30); head.setAttribute("r", 8);
      head.setAttribute("fill", "none"); head.setAttribute("stroke", color);
      head.setAttribute("stroke-width", "2");
      g.appendChild(head);
      [[24, 38, 24, 62], [12, 48, 36, 48], [24, 62, 14, 78], [24, 62, 34, 78]].forEach(([x1, y1, x2, y2]) => {
        const line = document.createElementNS(NS, "line");
        line.setAttribute("x1", x1); line.setAttribute("y1", y1);
        line.setAttribute("x2", x2); line.setAttribute("y2", y2);
        line.setAttribute("stroke", color); line.setAttribute("stroke-width", "2");
        g.appendChild(line);
      });
    }
    const labelX = kind === "person" ? 48 : p.w / 2;
    const anchor = kind === "person" ? "start" : "middle";
    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "node-label");
    label.setAttribute("x", labelX);
    label.setAttribute("y", 32);
    label.setAttribute("text-anchor", anchor);
    label.textContent = truncate(node.name || node.id, 22);
    g.appendChild(label);
    const type = document.createElementNS(NS, "text");
    type.setAttribute("class", "node-type");
    type.setAttribute("x", labelX);
    type.setAttribute("y", 48);
    type.setAttribute("text-anchor", anchor);
    type.textContent = `[${C4_KIND_LABEL[kind] || node.type || "?"}]` + (drillable ? "  ⊕" : "");
    g.appendChild(type);
    // Detail inside the box: a summary of the node's children by type, so the
    // contents are visible without drilling into the next level.
    const childList = kids.get(node.id) || [];
    let ty = 39;
    if (childList.length) {
      const counts = new Map();
      childList.forEach((k) => counts.set(k.type, (counts.get(k.type) || 0) + 1));
      const summary = [...counts.entries()]
        .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
        .slice(0, 3)
        .map(([t, c]) => `${c}× ${t}`)
        .join(" · ");
      const det = document.createElementNS(NS, "text");
      det.setAttribute("class", "node-detail");
      det.setAttribute("x", labelX);
      det.setAttribute("y", 64);
      det.setAttribute("text-anchor", anchor);
      det.textContent = truncate(summary, 34);
      g.appendChild(det);
      ty = 64;
    }
    const src = document.createElementNS(NS, "text");
    src.setAttribute("class", "node-src");
    src.setAttribute("x", labelX);
    src.setAttribute("y", ty + 17);
    src.setAttribute("text-anchor", anchor);
    src.textContent = "source: " + (both ? "code + docs" : sources[0]) + (both ? "" : "  ⚠");
    g.appendChild(src);
    if (node.note) {
      const note = document.createElementNS(NS, "text");
      note.setAttribute("class", "node-note");
      note.setAttribute("x", labelX);
      note.setAttribute("y", ty + 34);
      note.setAttribute("text-anchor", anchor);
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
    g.addEventListener("mousedown", (ev) => startNodeDrag(ev, node, g, p));
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      // a click right after a drag is the tail of that drag; a later one is real
      if (state._dragged && Date.now() - (state._dragEnd || 0) < 350) {
        state._dragged = false;
        return;
      }
      state._dragged = false;
      // UI-2: a click inspects the element; entering the next level is a double click
      state.selected = node.id;
      render();
      openInspector(node.id);
    });
    g.addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      if (!drillable) return;
      state.focus = node.id;
      state.selected = null;
      render();
      closeInspector();
    });
    viewport.appendChild(g);
  });
  viewport.appendChild(labelLayer);  // re-append: labels above the nodes

  const cam = { x: 0, y: 0, k: 1 };
  const r = wrap.getBoundingClientRect();
  const fitK = Math.min(1, Math.min(r.width / totalW, r.height / totalH)) * 0.95;
  cam.k = fitK > 0 ? fitK : 1;
  cam.x = (r.width - totalW * cam.k) / 2;
  cam.y = (r.height - totalH * cam.k) / 2;
  const apply = () => viewport.setAttribute("transform", `translate(${cam.x},${cam.y}) scale(${cam.k})`);
  apply();
  if (svg.id === "canvas") {
    state._cam = cam;
    state._pos = pos;
    state._viewport = viewport;   // pan/zoom handlers (bound once) act on this
  }
}

function render() {
  const crumb = $("breadcrumb");
  crumb.innerHTML = "";
  const empty = $("empty");
  let graph = state.graph;
  const wrap = $("canvas-wrap");
  if (!graph) {
    $("canvas").innerHTML = "";
    empty.textContent = "Run a reconstruction to render the graph.";
    empty.style.display = "flex";
    return;
  }
  if (state.lens) {
    renderLensBar();
    drawLens(state.lens);
    return;
  }
  hideLensBar();
  // An aspect sub-tab is a view of its own: it shows that aspect's elements for
  // the selected C4 entity, never a mix on the C4 canvas (Ф2: the graph is the
  // internal structure, the architect works with C4 and the aspect views).
  if (state.view !== "c4") {
    graph = aspectScopeGraph(graph, state.view, state.treeSel);
    if (!graph.nodes.length) {
      // an empty aspect view must clear the canvas: leaving the previous render
      // on screen made it look like the aspect showed C4 elements
      $("canvas").innerHTML = "";
      const scopeName = state.treeSel && state.byId && state.byId.has(state.treeSel)
        ? state.byId.get(state.treeSel).name : null;
      empty.textContent = `no ${state.view} elements` + (scopeName ? ` for ${scopeName}` : "")
        + " — pick another element in the tree, or switch back to C4.";
      empty.style.display = "flex";
      return;
    }
  }
  const view = computeView(graph);
  state.byId = view.byId;
  state.kids = view.kids;
  state.rootId = view.rootId;
  renderBreadcrumb(view.focusId, view.byId);
  drawPane($("canvas"), wrap, empty, state.aspects, view);

  // UI-2: the inspector replaces the floating node popup; keep its scope in sync
  if (state.selected) {
    const scope = $("view_scope");
    if (scope) scope.value = state.selected;
  }
}

// ---------------------------------------------------------------------------
// UI: canvas view sub-tabs (C4 + one per aspect) and the collapsible C4 tree
// ---------------------------------------------------------------------------
let VIEW_TABS = ["c4"];
const VIEW_ORDER = ["c4", "process", "data", "behavior", "interface", "deployment",
                    "access", "quality", "aggregation", "crosscutting"];
const VIEW_LABELS = { c4: "C4", process: "Processes", data: "Data", behavior: "States",
                      interface: "Interfaces", deployment: "Deployment", access: "Access",
                      quality: "Quality", aggregation: "Aggregation", crosscutting: "Crosscutting" };

function buildViewTabs() {
  const bar = $("canvas-tabs");
  if (!bar) return;
  const known = Object.keys(ASPECT_TYPES).filter((a) => a !== "structure");
  const ordered = [...VIEW_ORDER.filter((v) => v === "c4" || known.includes(v)),
                   ...known.filter((a) => !VIEW_ORDER.includes(a))];
  VIEW_TABS = ordered;
  bar.querySelectorAll(".ctab").forEach((b) => b.remove());
  const anchor = bar.firstChild;
  ordered.forEach((v) => {
    const btn = document.createElement("button");
    btn.className = "ctab" + (state.view === v ? " active" : "");
    btn.dataset.view = v;
    btn.textContent = VIEW_LABELS[v] || v;
    btn.title = v === "c4" ? "C4 view of the whole system"
                           : `the ${v} aspect of the selected element`;
    btn.addEventListener("click", () => setView(v));
    bar.insertBefore(btn, anchor);
  });
  if (!VIEW_TABS.includes(state.view)) state.view = "c4";
}

function syncViewTabs() {
  document.querySelectorAll("#canvas-tabs .ctab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === state.view);
  });
}

function setView(name) {
  if (!VIEW_TABS.includes(name)) return;
  state.view = name;
  state.lens = null;
  if (name === "c4") {
    state.aspects = new Set(["structure"]);
    state.focus = state.treeSel && state.byId && state.byId.has(state.treeSel)
      ? (state.byId.get(state.treeSel).parent || null) : null;
  } else {
    state.aspects = new Set([name]);
    state.focus = null;               // the aspect view is scoped by the tree, not by level
  }
  syncViewTabs();
  render();
  setStatus(name === "c4" ? "view: C4" : `view: ${name}`, "ok");
}

function descendantsOf(byId, rootId) {
  const out = new Set();
  if (!rootId || !byId.has(rootId)) return out;
  const stack = [rootId];
  while (stack.length) {
    const id = stack.pop();
    if (out.has(id)) continue;
    out.add(id);
    (state.kids && state.kids.get(id) || []).forEach((k) => stack.push(k.id));
  }
  return out;
}

function aspectScopeGraph(graph, aspect, scopeId) {
  const nodes = graph.nodes || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const kids = new Map();
  nodes.forEach((n) => {
    if (!n.parent) return;
    if (!kids.has(n.parent)) kids.set(n.parent, []);
    kids.get(n.parent).push(n);
  });
  const saved = state.kids;
  state.kids = kids;
  const scope = scopeId && byId.has(scopeId) ? descendantsOf(byId, scopeId) : null;
  state.kids = saved;
  const kept = nodes.filter((n) => aspectOf(n) === aspect && (!scope || scope.has(n.id)));
  const ids = new Set(kept.map((n) => n.id));
  return { nodes: kept, edges: (graph.edges || []).filter((e) => ids.has(e.source) && ids.has(e.target)) };
}

function buildTree() {
  const body = $("tree-body");
  if (!body) return;
  body.innerHTML = "";
  const graph = state.fullGraph || state.graph;
  if (!graph || !(graph.nodes || []).length) {
    body.innerHTML = '<div class="muted" style="padding:8px">load a model…</div>';
    return;
  }
  const nodes = graph.nodes || [];
  const kids = new Map();
  nodes.forEach((n) => {
    if (!n.parent) return;
    if (!kids.has(n.parent)) kids.set(n.parent, []);
    kids.get(n.parent).push(n);
  });
  const root = nodes.find((n) => !n.parent);
  const add = (node, depth) => {
    if (!node || depth > 3) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tree-item" + (state.treeSel === node.id ? " active" : "");
    btn.dataset.node = node.id;
    btn.style.paddingLeft = `${8 + depth * 12}px`;
    btn.textContent = node.name || node.id;
    btn.title = `${node.id} · ${node.type}`;
    btn.addEventListener("click", () => {
      state.treeSel = node.id;
      if (state.view === "c4") state.focus = node.parent || null;
      buildTree();
      render();
    });
    body.appendChild(btn);
    (kids.get(node.id) || [])
      .slice()
      .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)))
      .slice(0, 80)
      .forEach((k) => add(k, depth + 1));
  };
  add(root, 0);
}

// ---------------------------------------------------------------------------
// UI: canvas layout — drag an element, or let an auto-grouping place everything
// ---------------------------------------------------------------------------
function applyLayoutPositions(pos) {
  const saved = state.layout && state.layout.positions;
  if (!saved) return;
  Object.entries(saved).forEach(([id, p]) => {
    const cur = pos.get(id);
    if (cur && p && typeof p.x === "number" && typeof p.y === "number") {
      pos.set(id, { ...cur, x: p.x, y: p.y });
    }
  });
}

function saveLayout() {
  const model = state.layoutModel;
  if (!model) return;
  clearTimeout(state.layoutTimer);
  state.layoutTimer = setTimeout(() => {
    api(`/api/models/${encodeURIComponent(model)}/layout`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: state.layout.mode || "manual",
                             layout: state.layout.layout || "",
                             positions: state.layout.positions || {} }),
    }).catch(() => {});
  }, 400);
}

async function loadLayout(model) {
  state.layoutModel = model || null;
  state.layout = { mode: "", layout: "", positions: {} };
  if (!model) return;
  try {
    const saved = await api(`/api/models/${encodeURIComponent(model)}/layout`);
    state.layout = { mode: saved.mode || "", layout: saved.layout || "",
                     positions: saved.positions || {} };
  } catch (e) { /* a missing layout is fine */ }
}

function autoLayout() {
  const mode = ($("layout-mode") ? $("layout-mode").value : "c4") || "c4";
  const graph = state.graph;
  if (!graph) { setStatus("load a model first", "error"); return; }
  const view = computeView(graph);
  const vis = view.nodes || [];
  const positions = {};
  if (mode === "grid") {
    const cols = Math.max(2, Math.ceil(Math.sqrt(vis.length)));
    const W = 226, H = 150;
    vis.forEach((n, i) => {
      positions[n.id] = { x: 60 + (i % cols) * W, y: 60 + Math.floor(i / cols) * H };
    });
  } else if (mode === "aspect") {
    const byAspect = new Map();
    vis.forEach((n) => {
      const a = aspectOf(n);
      if (!byAspect.has(a)) byAspect.set(a, []);
      byAspect.get(a).push(n);
    });
    let col = 0;
    [...byAspect.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([, list]) => {
      list.forEach((n, row) => { positions[n.id] = { x: 80 + col * 260, y: 70 + row * 118 }; });
      col += 1;
    });
  } else {
    // the C4 layered arrangement: record where the layout already put them
    (state._pos || new Map()).forEach((p, id) => {
      if (view.byId && view.byId.has(id)) positions[id] = { x: p.x, y: p.y };
    });
  }
  state.layout = { mode: "auto", layout: mode, positions };
  saveLayout();
  render();
  setStatus(`auto layout: ${mode} (${Object.keys(positions).length} elements)`, "ok");
}

function resetLayout() {
  const model = state.layoutModel;
  state.layout = { mode: "", layout: "", positions: {} };
  if (model) api(`/api/models/${encodeURIComponent(model)}/layout`, { method: "DELETE" }).catch(() => {});
  render();
  setStatus("canvas arrangement reset", "ok");
}

function startNodeDrag(ev, node, group, origin) {
  if (ev.button !== 0 || state.lens) return;
  const cam = state._cam;
  if (!cam) return;
  const startX = ev.clientX, startY = ev.clientY;
  let frame = 0;
  const onMove = (e) => {
    const dx = (e.clientX - startX) / cam.k;
    const dy = (e.clientY - startY) / cam.k;
    if (Math.abs(e.clientX - startX) < 3 && Math.abs(e.clientY - startY) < 3) return;
    state._dragged = true;
    state.layout.mode = "manual";
    state.layout.positions[node.id] = { x: origin.x + dx, y: origin.y + dy };
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; render(); });
  };
  const onUp = () => {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    if (state._dragged) {
      state._dragEnd = Date.now();
      render();
      saveLayout();
      setStatus(`moved ${node.name || node.id}`, "ok");
    }
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  ev.preventDefault();
}

// ---------------------------------------------------------------------------
// UI: resizable panels (bottom drawer, left sidebar, C4 tree)
// ---------------------------------------------------------------------------
function wirePanelResize() {
  const drag = (handle, apply, axis) => {
    if (!handle) return;
    handle.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      const start = axis === "y" ? ev.clientY : ev.clientX;
      const onMove = (e) => {
        const now = axis === "y" ? e.clientY : e.clientX;
        apply(now - start);
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        persistUi();
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      ev.preventDefault();
    });
  };
  const drawer = $("events-drawer");
  drag($("drawer-resize"), (delta) => {
    const startH = drawer.getBoundingClientRect().height;
    const next = Math.max(120, Math.min(window.innerHeight - 200, startH - delta));
    drawer.style.height = `${next}px`;
    drawer.style.maxHeight = "none";
  }, "y");
  const sidebar = document.querySelector("aside.sidebar");
  drag($("sidebar-resize"), (delta) => {
    const startW = sidebar.getBoundingClientRect().width;
    const next = Math.max(240, Math.min(760, startW + delta));
    sidebar.style.width = `${next}px`;
    document.querySelector("main.layout").style.gridTemplateColumns =
      `${next}px 1fr${document.querySelector("main.layout").classList.contains("inspector-open") ? " 340px" : ""}`;
  }, "x");
  const tree = $("c4-tree");
  drag($("tree-resize"), (delta) => {
    const startW = tree.getBoundingClientRect().width;
    const next = Math.max(160, Math.min(520, startW + delta));
    tree.style.width = `${next}px`;
  }, "x");
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
