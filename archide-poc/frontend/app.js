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
  graph: null, fullGraph: null, focus: null, selected: null, byId: null, kids: null, rootId: null,
  sourceMode: "all", aspects: new Set(["structure"]), split: false, aspectB: "interface",
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
    if (snap.meta && snap.meta.model_path) {
      const name = String(snap.meta.model_path).split("/").pop().replace(/\.json$/, "");
      state.modelName = name;
      $("model_load").value = name;
      $("gen_model").value = name;
    }
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
  $("split").addEventListener("change", () => { state.split = $("split").checked; render(); });
  $("aspect-b").addEventListener("change", () => { state.aspectB = $("aspect-b").value; render(); });
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
  const add = text.match(/^add\s+(\w+)\s+(\S+)(?:\s+under\s+(\S+))?$/i);
  if (add) return { kind: "add", type: add[1], id: add[2], parent: add[3] || null };
  const rem = text.match(/^remove\s+(\S+)$/i);
  if (rem) return { kind: "remove", id: rem[1] };
  if (/^review\b/i.test(text)) return { kind: "review" };
  if (/^fix\b/i.test(text)) return { kind: "fix" };
  return { kind: "rag" };
}

async function sendPrompt(text) {
  const value = String(text || "").trim();
  if (!value) return;
  const now = () => Date.now() / 1000;
  appendEvents([{ ts: now(), type: "user", text: value }]);
  state.promptHistory.push(value);
  state.promptIndex = state.promptHistory.length;
  $("prompt_input").value = "";

  const intent = parseIntent(value);
  const model = state.modelName || "";

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

function renderAspectWindow(aspect) {
  const win = $("awin-" + aspect);
  if (!win) return;
  const body = win.querySelector(".awin-body");
  const nodes = ((state.graph && state.graph.nodes) || []).filter((n) => aspectOf(n) === aspect);
  body.innerHTML = nodes.length
    ? nodes.map((n) => `<div class="awin-row"><span>${n.name || n.id}</span><span class="muted">${n.type}</span></div>`).join("")
    : "<div class='muted'>no elements</div>";
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
    <div class="awin-head"><span>${aspect}</span><button class="awin-close" title="close">\u2715</button></div>
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
  win.querySelector(".awin-close").addEventListener("click", () => win.remove());
  win.querySelector(".awin-add button").addEventListener("click", () => addAspectElement(aspect));
  makeDraggable(win);
  renderAspectWindow(aspect);
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

  const ab = $("aspect-b");
  if (ab) {
    ab.innerHTML = "";
    Object.keys(ASPECT_TYPES).forEach((aspect) => {
      const opt = document.createElement("option");
      opt.value = aspect;
      opt.textContent = aspect;
      ab.appendChild(opt);
    });
    ab.value = ASPECT_TYPES[state.aspectB] ? state.aspectB : "structure";
  }
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
  state.fullGraph = state.graph;
  state.focus = null;
  state.selected = null;
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
  const children = (kids.get(focusId) || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const single = children.length === 0 && byId.has(focusId);
  const visNodes = (single ? [byId.get(focusId)] : children).filter((n) => matchMode(n) && matchAspect(n));
  if (!visNodes.length) {
    if (emptyEl) {
      emptyEl.textContent = "No elements of the selected aspects/sources at this level.";
      emptyEl.style.display = "flex";
    }
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";

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

  const cam = { x: 0, y: 0, k: 1 };
  const r = wrap.getBoundingClientRect();
  const fitK = Math.min(1, Math.min(r.width / totalW, r.height / totalH)) * 0.95;
  cam.k = fitK > 0 ? fitK : 1;
  cam.x = (r.width - totalW * cam.k) / 2;
  cam.y = (r.height - totalH * cam.k) / 2;
  const apply = () => viewport.setAttribute("transform", `translate(${cam.x},${cam.y}) scale(${cam.k})`);
  apply();
  if (svg.id === "canvas") { state._cam = cam; state._pos = pos; }

  let dragging = false, sx = 0, sy = 0;
  svg.addEventListener("mousedown", (ev) => {
    dragging = true;
    sx = ev.clientX - cam.x;
    sy = ev.clientY - cam.y;
    svg.classList.add("dragging");
  });
  window.addEventListener("mouseup", () => { dragging = false; svg.classList.remove("dragging"); });
  window.addEventListener("mousemove", (ev) => {
    if (!dragging) return;
    cam.x = ev.clientX - sx;
    cam.y = ev.clientY - sy;
    apply();
  });
  svg.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nk = Math.max(0.2, Math.min(3, cam.k * factor));
    cam.x = mx - (mx - cam.x) * (nk / cam.k);
    cam.y = my - (my - cam.y) * (nk / cam.k);
    cam.k = nk;
    apply();
  }, { passive: false });
}

function render() {
  const crumb = $("breadcrumb");
  crumb.innerHTML = "";
  const empty = $("empty");
  const graph = state.graph;
  const wrap = $("canvas-wrap");
  const split = !!state.split;
  $("canvas2").style.display = split ? "block" : "none";
  wrap.classList.toggle("split", split);
  if (!graph) {
    $("canvas").innerHTML = "";
    $("canvas2").innerHTML = "";
    empty.textContent = "Run a reconstruction to render the graph.";
    empty.style.display = "flex";
    return;
  }
  const view = computeView(graph);
  state.byId = view.byId;
  state.kids = view.kids;
  state.rootId = view.rootId;
  renderBreadcrumb(view.focusId, view.byId);
  drawPane($("canvas"), wrap, empty, state.aspects, view);
  if (split) drawPane($("canvas2"), wrap, null, new Set([state.aspectB]), view);

  const actions = $("node-actions");
  if (actions) {
    if (state.selected && state._pos && state._pos.has(state.selected) && state._cam) {
      const p = state._pos.get(state.selected);
      const rect = wrap.getBoundingClientRect();
      const sx = state._cam.x + p.x * state._cam.k;
      const sy = state._cam.y + p.y * state._cam.k;
      actions.style.left = Math.max(8, Math.min(rect.width - 250, sx)) + "px";
      actions.style.top = Math.max(8, sy - 44) + "px";
      $("node-rename").value = ((state.byId && state.byId.get(state.selected)) || {}).name || "";
      $("view_scope").value = state.selected;
      actions.classList.remove("hidden");
    } else {
      actions.classList.add("hidden");
    }
  }
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
