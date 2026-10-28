// ArchIDE browser-level e2e (headless Chrome via puppeteer-core).
//
// Emulates real user actions in the UI. Covers scenarios S1-S5, S7, S8, S10.
// The server is started by tests/run_e2e.sh and exposed via ARCHIDE_TEST_URL.
import puppeteer from 'puppeteer-core';
import path from 'path';
import { existsSync } from 'fs';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import fs from 'fs';
import os from 'os';

const DOWNLOAD_DIR = path.join(os.tmpdir(), 'archide-dl');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');
const REPO = path.join(ROOT, 'tests', 'fixtures', 'sample_repo');
const BASE = process.env.ARCHIDE_TEST_URL || 'http://127.0.0.1:8901';

function resolveChrome() {
  const candidates = [
    process.env.CHROME,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
  ].filter(Boolean);
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    try {
      const p = execSync(`command -v ${name}`, { encoding: 'utf8' }).trim();
      if (p) return p;
    } catch (_) { /* keep looking */ }
  }
  return process.env.CHROME || 'google-chrome';
}
const CHROME = resolveChrome();

const results = [];
function check(name, cond, detail = '') {
  results.push([name, !!cond]);
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name + (detail && !cond ? '  :: ' + detail : ''));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// UI-1: the panels are hidden by default (graph-first) — open them like a user
const openSidebar = async (page) => {
  // wait until the app wired its layout (demo pages initialise after load)
  await page.waitForFunction(() => {
    const layout = document.querySelector('main.layout');
    return !!layout && layout.dataset.wired === '1';
  }, { timeout: 20000 }).catch(() => {});
  const hidden = await page.evaluate(() => {
    const layout = document.querySelector('main.layout');
    return !!layout && layout.classList.contains('sidebar-hidden');
  });
  if (hidden) { await page.click('#sidebar-toggle'); await sleep(250); }
};
const openAgentPane = async (page) => {
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 }).catch(() => {});
  const hidden = await page.evaluate(() => document.getElementById('bpane-agent').classList.contains('hidden'));
  if (hidden) { await page.click('#btab-agent'); await sleep(300); }
};

// the model load/export controls moved into the project pane (UI-6)
const loadModel = async (page, name) => {
  // the controls live in the project pane; come back to whatever pane was open
  const prev = await page.evaluate(() => {
    const active = document.querySelector('.bottom-tabs .tab.active');
    return active ? active.id : 'btab-projects';
  });
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(250);
  await page.$eval('#model_load', (el, v) => { el.value = v; }, name);
  await page.click('#model-load');
  await sleep(600);
  await page.evaluate((id) => document.getElementById(id).click(), prev);
  await sleep(250);
};

const openSetup = async (page) => {
  await openSidebar(page);
  await page.click('#setup-open');
  await sleep(250);
};

async function putConfig(patch) {
  await fetch(`${BASE}/api/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}
const nodeCount = (page) => page.$$eval('#canvas > g > g', (els) => els.length);
const crumbCount = (page) => page.$$eval('#breadcrumb button', (els) => els.length);
const activeChips = (page) => page.$$eval('#canvas-tabs .ctab.active', (els) => els.map((e) => e.dataset.view));
const emptyShown = (page) => page.$eval('#empty', (el) => getComputedStyle(el).display !== 'none');

async function clickChip(page, text) {
  // "structure" is the C4 view; anything else is an aspect sub-tab
  const view = text === 'structure' ? 'c4' : text;
  await page.evaluate((v) => {
    const tab = document.querySelector(`#canvas-tabs .ctab[data-view="${v}"]`);
    if (tab) tab.click();
  }, view);
  await sleep(250);
}

const clickCommTab = (page, re) => page.evaluate((r) => {
  const tab = [...document.querySelectorAll('#comm .tab')].find((t) => new RegExp(r, 'i').test(t.textContent));
  if (tab) tab.click();
}, re);

async function dblClickNode(page, text) {
  const box = await page.evaluate((t) => {
    const gs = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    const node = gs.find((g) => {
      const label = g.querySelector('text.node-label') || g.querySelector('text');
      return label && label.textContent.trim() === t;
    });
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, text);
  if (box) { await page.mouse.click(box.x, box.y, { clickCount: 2 }); }
  await sleep(300);
}

async function clickNode(page, text) {
  const box = await page.evaluate((t) => {
    const gs = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    // match the node label exactly: box text also contains the type/source lines,
    // so a loose `includes` can hit the wrong node now that several levels show
    const node = gs.find((g) => {
      const label = g.querySelector('text.node-label') || g.querySelector('text');
      return label && label.textContent.trim() === t;
    });
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, text);
  if (box) await page.mouse.click(box.x, box.y);
  await sleep(200);
}

async function runDemo(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=1`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  const n0 = await nodeCount(page);
  check('S10 demo renders graph nodes', n0 > 1, `nodes=${n0 - 1}`);
  check('S4 breadcrumb shown', (await crumbCount(page)) >= 1);
  check('S3 source filter buttons present', (await page.$('#src-code')) !== null && (await page.$('#src-docs')) !== null);

  // S4 drill-down: real mouse click on a node that has children (marked with ⊕)
  // enter the next level: double click a drillable node (dispatched through the
  // app handler so the demo's compact canvas does not affect the geometry)
  const drilled = await page.evaluate(() => {
    const groups = [...document.querySelectorAll('#canvas > g > g')]
      .filter((g) => g.querySelector('rect.node-box') && g.textContent.includes('⊕'));
    if (!groups.length) return false;
    groups[0].querySelector('rect.node-box')
      .dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    return true;
  });
  await sleep(300);
  const crumbs = await crumbCount(page);
  const crumbText = await page.$$eval('#breadcrumb button', (els) => els.map((e) => e.textContent));
  check('S4 drill-down grows the breadcrumb', drilled && crumbs >= 2, `drilled=${drilled} crumbs=${crumbs} [${crumbText.join(' > ')}]`);

  // S3 source filter changes the visible set
  const nAll = await nodeCount(page);
  await page.click('#src-docs');
  await sleep(200);
  const nDocs = await nodeCount(page);
  await page.click('#src-code');
  await sleep(200);
  const nCode = await nodeCount(page);
  check('S3 source filter changes the graph', nAll !== nDocs || nAll !== nCode,
    `all=${nAll} docs=${nDocs} code=${nCode}`);
  await page.click('#src-all');

  await page.close();
}

async function runAspects(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=aspects`, { waitUntil: 'load' });
  await openSidebar(page);
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  const tabs = await page.$$eval('#canvas-tabs .ctab', (els) => els.map((e) => e.dataset.view));
  check('Ф2.5 the canvas exposes one sub-tab per aspect, C4 first',
    tabs.length >= 8 && tabs[0] === 'c4' && tabs.includes('data') && tabs.includes('interface'),
    JSON.stringify(tabs));

  let active = await activeChips(page);
  check('Ф2.5 the default view is C4', active.length === 1 && active[0] === 'c4', active.join(','));

  const c4Nodes = await nodeCount(page);
  check('Ф2.5 the C4 view shows the structural elements', c4Nodes >= 1, `nodes=${c4Nodes}`);

  await clickChip(page, 'interface');
  const n3 = await nodeCount(page);
  active = await activeChips(page);
  check('Ф2.6 an aspect opens as its own view, not stacked on C4',
    active[0] === 'interface' && n3 >= 1 && n3 < c4Nodes,
    `c4=${c4Nodes} interface=${n3}; active=${active.join(',')}`);

  await clickChip(page, 'data');
  const n4 = await nodeCount(page);
  active = await activeChips(page);
  check('Ф2.6 switching the sub-tab replaces the view',
    active[0] === 'data' && n4 >= 1 && active.length === 1,
    `data=${n4}; active=${active.join(',')}`);

  await clickChip(page, 'c4');
  const back = await nodeCount(page);
  active = await activeChips(page);
  check('Ф2.6 the C4 sub-tab brings the whole C4 view back',
    active[0] === 'c4' && back === c4Nodes, `back=${back} c4=${c4Nodes}`);

  // the layered view keeps ancestor context, so emptiness is reached by
  // filtering the sources to a set the active aspects have no elements in
  await page.click('#src-docs');
  await sleep(200);
  check('Ф2.5 empty hint when the filters hide everything', await emptyShown(page));
  await page.click('#src-all');
  await sleep(150);

  await page.close();
}

async function runViews(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=aspects`, { waitUntil: 'load' });
  await openSidebar(page);
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  await page.$eval('#view_scope', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, 'component://app/svc');
  await page.select('#view_dir', 'down');
  await page.click('#view-apply');
  await sleep(300);
  const info = await page.$eval('#view-info', (el) => el.textContent);
  check('Ф2.7 slice applied via UI (info shown)', /slice:\s*5 nodes/.test(info), info);

  await page.click('#view-reset');
  await sleep(200);
  const info2 = await page.$eval('#view-info', (el) => el.textContent);
  check('Ф2.7 slice reset clears the view info', info2.trim() === '', info2);

  await page.close();
}

async function runDocs(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);

  const tmp = path.join(os.tmpdir(), 'archide-e2e-reqs.md');
  fs.writeFileSync(tmp, '- REQ-1: system must charge payments\n- REQ-2: refunds must be supported\n');
  await page.$eval('#doc_user', (el) => { el.value = 'e2e-user'; });
  await (await page.$('#doc_file')).uploadFile(tmp);
  await sleep(400);
  await page.click('#doc-confirm');
  await sleep(400);
  const logText = await page.$eval('#activity', (el) => el.textContent);
  check('Ф7.1 document attached in the chat (into RAG)', /indexed .*reqs\.md/i.test(logText), logText.slice(0, 180));

  await page.$eval('#prompt_input', (el) => { el.value = 'refunds'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(500);
  const logText2 = await page.$eval('#activity', (el) => el.textContent);
  check('Ф7.2 RAG question via the chat prompt', /refund/i.test(logText2), logText2.slice(0, 200));

  await page.evaluate(() => {
    const tab = [...document.querySelectorAll('#comm .tab')].find((t) => /answer/i.test(t.textContent));
    if (tab) tab.click();
  });
  await sleep(150);
  await page.$eval('#rag_answer', (el) => { el.value = 'Refunds must be supported. Unicorns fly over rainbows.'; });
  await page.click('#rag-verify');
  await sleep(300);
  const ground = await page.$eval('#rag-ground', (el) => el.textContent);
  check('Ф7.4 grounding verification in UI', /unsupported:\s*1/.test(ground), ground);
  check('Ф7.6 per-claim fidelity shown', /(verbatim|paraphrase|derived|unsupported)/.test(ground), ground);

  await page.close();
}

async function runComm(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);

  const clickTab = (re) => page.evaluate((r) => {
    const tab = [...document.querySelectorAll('#comm .tab')].find((t) => new RegExp(r, 'i').test(t.textContent));
    if (tab) tab.click();
  }, re);

  const tabs = await page.$$eval('#comm .tab', (els) => els.map((e) => e.textContent.trim()));
  check('comm window has tabs (activity/answer)', tabs.length >= 2, JSON.stringify(tabs));
  await clickTab('answer'); await sleep(150);
  check('answer-to-verify form is a tab', await page.$eval('#rag_answer', (el) => el.offsetParent !== null));
  await clickTab('activity'); await sleep(150);
  check('activity tab has a prompt form', await page.$eval('#prompt_input', (el) => el.offsetParent !== null));

  const tmp = path.join(os.tmpdir(), 'archide-e2e-comm.md');
  fs.writeFileSync(tmp, '- REQ-1: refunds must be supported\n');
  await page.$eval('#doc_user', (el) => { el.value = 'comm-user'; });
  await (await page.$('#doc_file')).uploadFile(tmp);
  await sleep(400);
  await page.click('#doc-confirm');
  await sleep(400);

  await page.$eval('#prompt_input', (el) => { el.value = 'refunds'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(500);
  const userEvents = await page.$$eval('#activity .ev.user', (els) => els.length);
  const agentEvents = await page.$$eval('#activity .ev.reasoning, #activity .ev.output', (els) => els.length);
  check('user prompt appears in the log', userEvents >= 1, `user=${userEvents}`);
  check('agent events/reasoning appear in the log', agentEvents >= 1, `agent=${agentEvents}`);

  const attachBtns = await page.$$eval('#activity .attach-btn', (els) => els.length);
  if (attachBtns) await page.evaluate(() => document.querySelector('#activity .attach-btn').click());
  const promptVal = await page.$eval('#prompt_input', (el) => el.value);
  check('RAG result can be attached to the prompt', attachBtns >= 1 && /refund/i.test(promptVal), `btns=${attachBtns} prompt="${promptVal}"`);

  await page.$eval('#prompt_input', (el) => { el.value = 'zzzznomatch'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(500);
  check('agent asks a question when nothing matches', (await page.$$eval('#activity .ev.question', (els) => els.length)) >= 1);

  const panes = await page.evaluate(() => {
    const comm = document.getElementById('comm').getBoundingClientRect();
    const pane = document.getElementById('bpane-agent').getBoundingClientRect();
    return { commH: Math.round(comm.height), paneH: Math.round(pane.height),
             commW: Math.round(comm.width), paneW: Math.round(pane.width) };
  });
  check('the agent panel fills its bottom pane', panes.commH > 120 && Math.abs(panes.commH - panes.paneH) < 160
    && Math.abs(panes.commW - panes.paneW) < 30, JSON.stringify(panes));

  await page.close();
}

async function runPrompt(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);

  const setPrompt = (v) => page.$eval('#prompt_input', (el, val) => { el.value = val; el.dispatchEvent(new Event('input', { bubbles: true })); }, v);
  await setPrompt('first prompt');
  await page.click('#prompt-send'); await sleep(300);
  await setPrompt('second prompt');
  await page.click('#prompt-send'); await sleep(300);

  await page.focus('#prompt_input');
  await page.keyboard.press('ArrowUp'); await sleep(100);
  const v1 = await page.$eval('#prompt_input', (el) => el.value);
  await page.keyboard.press('ArrowUp'); await sleep(100);
  const v2 = await page.$eval('#prompt_input', (el) => el.value);
  await page.keyboard.press('ArrowDown'); await sleep(100);
  const v3 = await page.$eval('#prompt_input', (el) => el.value);
  check('ArrowUp recalls the previous prompt', v1 === 'second prompt', `v1="${v1}"`);
  check('ArrowUp again recalls the older prompt', v2 === 'first prompt', `v2="${v2}"`);
  check('ArrowDown moves forward in history', v3 === 'second prompt', `v3="${v3}"`);

  const h0 = await page.$eval('#prompt-form', (el) => el.getBoundingClientRect().height);
  const box = await page.$eval('#prompt-resize', (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
  await page.mouse.move(box.x, box.y); await page.mouse.down();
  await page.mouse.move(box.x, box.y - 70, { steps: 5 }); await page.mouse.up();
  await sleep(200);
  const h1 = await page.$eval('#prompt-form', (el) => el.getBoundingClientRect().height);
  check('prompt input top border is resizable', h1 > h0 + 20, `h0=${Math.round(h0)} h1=${Math.round(h1)}`);

  await page.close();
}

async function runPreview(browser) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);

  const tmp = path.join(os.tmpdir(), 'archide-e2e-prev.md');
  fs.writeFileSync(tmp, '# Preview\n- REQ-1: hello world\n');
  await page.$eval('#doc_user', (el) => { el.value = 'preview-user'; });
  await (await page.$('#doc_file')).uploadFile(tmp);
  await sleep(500);

  const previewVisible = await page.$eval('#doc-preview', (el) => el.offsetParent !== null);
  const previewText = await page.$eval('#doc-preview', (el) => el.textContent);
  check('document preview shown before upload',
    previewVisible && /prev\.md/.test(previewText) && /REQ-1/.test(previewText), previewText.slice(0, 160));

  const logBefore = await page.$eval('#activity', (el) => el.textContent);
  check('not ingested before confirmation', !/indexed .*prev\.md/i.test(logBefore), logBefore.slice(0, 120));

  await page.click('#doc-confirm');
  await sleep(500);
  const logAfter = await page.$eval('#activity', (el) => el.textContent);
  check('confirm uploads the document to RAG', /indexed .*prev\.md/i.test(logAfter), logAfter.slice(0, 160));
  check('preview hidden after upload', await page.$eval('#doc-preview', (el) => el.offsetParent === null));

  await (await page.$('#doc_file')).uploadFile(tmp);
  await sleep(400);
  await page.click('#doc-cancel');
  await sleep(200);
  check('cancel discards the preview', await page.$eval('#doc-preview', (el) => el.offsetParent === null));

  await page.close();
}

async function runChatEdit(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = {
    nodes: [
      { id: 'sys://ce', type: 'System', parent: null, evidence: ['a'] },
      { id: 'container://ce/c', type: 'Container', parent: 'sys://ce', evidence: ['a'] },
    ],
    edges: [],
  };
  await fetch(`${BASE}/api/models/chatedit`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }),
  });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);

  const send = async (text) => {
    await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
    await page.click('#prompt-send');
    await sleep(500);
  };

  await loadModel(page, 'chatedit');
  await sleep(400);
  await send('add Component component://ce/new under container://ce/c');
  const log = await page.$eval('#activity', (el) => el.textContent);
  check('Ф4.7 chat applies an edit intent', /applied/.test(log), log.slice(-160));
  const stored = await (await fetch(`${BASE}/api/models/chatedit`)).json();
  check('Ф4.7 edit persisted via chat', stored.nodes.some((n) => n.id === 'component://ce/new'), 'node not stored');

  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await openAgentPane(page);
  await send('add Component component://ce/orphan');
  const log2 = await page.$eval('#activity', (el) => el.textContent);
  check('Ф4.7 agent asks a question when context is missing', (await page.$$eval('#activity .ev.question', (els) => els.length)) >= 1, log2.slice(-160));

  await page.close();
}

async function runGenerate(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = {
    nodes: [
      { id: 'sys://gu', type: 'System', parent: null, evidence: ['a'] },
      { id: 'container://gu/c', type: 'Container', parent: 'sys://gu', evidence: ['a'] },
      { id: 'component://gu/svc', type: 'Component', parent: 'container://gu/c', evidence: ['svc.py'] },
      { id: 'iface://gu/svc', type: 'Interface', parent: 'component://gu/svc', evidence: ['svc.py'] },
      { id: 'operation://gu/charge', type: 'Operation', parent: 'iface://gu/svc', evidence: ['svc.py'], signature: 'charge() -> Receipt' },
      { id: 'data://gu/order', type: 'DataEntity', parent: 'component://gu/svc', evidence: ['models.py'] },
      { id: 'attribute://gu/order/id', type: 'Attribute', parent: 'data://gu/order', evidence: ['models.py'], data_type: 'int' },
    ],
    edges: [
      { source: 'component://gu/svc', target: 'iface://gu/svc', type: 'exposes' },
      { source: 'iface://gu/svc', target: 'operation://gu/charge', type: 'has' },
    ],
  };
  await fetch(`${BASE}/api/models/genuitest`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }),
  });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  await page.$eval('#gen_model', (el) => { el.value = 'genuitest'; });
  await page.click('#gen-run');
  await sleep(400);
  const artWin = await page.$('#awin-artifacts');
  const artTxt = artWin ? await page.$eval('#awin-artifacts', (el) => el.textContent) : '';
  check('Ф5.1 artifacts shown in a window', !!artWin && /openapi/i.test(artTxt) && /ddl/i.test(artTxt), artTxt.slice(0, 160));

  await page.click('#gen-coverage');
  await sleep(400);
  const covWin = await page.$('#awin-coverage');
  const covTxt = covWin ? await page.$eval('#awin-coverage', (el) => el.textContent) : '';
  check('Ф5.4 coverage shown in a window', !!covWin && /coverage/i.test(covTxt), covTxt.slice(0, 120));

  await page.close();
}

async function runVisualEdit(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = {
    nodes: [
      { id: 'sys://ve', type: 'System', name: 've', parent: null, evidence: ['a'] },
      { id: 'container://ve/c', type: 'Container', name: 'c', parent: 'sys://ve', evidence: ['a'] },
      { id: 'component://ve/a', type: 'Component', name: 'a', parent: 'container://ve/c', evidence: ['a'] },
    ],
    edges: [],
  };
  await fetch(`${BASE}/api/models/ve`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }),
  });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  await loadModel(page, 've');
  await sleep(400);
  check('visual edit: model loaded on canvas', (await page.$$eval('#canvas > g > g', (els) => els.length)) >= 1);

  await clickNode(page, 'c');
  await page.click('#add-node');
  await sleep(200);
  await page.$eval('#pop_name', (el) => { el.value = 'NewSvc'; });
  await page.select('#pop_type', 'Component');
  await page.click('#pop-create');
  await sleep(400);
  const stored = await (await fetch(`${BASE}/api/models/ve`)).json();
  check('visual edit: node added directly on canvas', stored.nodes.some((n) => n.name === 'NewSvc'), 'not stored');

  await clickNode(page, 'NewSvc');
  await sleep(200);
  await page.click('#node-delete');
  await sleep(400);
  const stored2 = await (await fetch(`${BASE}/api/models/ve`)).json();
  check('visual edit: node deleted directly on canvas', !stored2.nodes.some((n) => n.name === 'NewSvc'), 'still stored');

  await page.select('#win_aspect', 'interface');
  await page.click('#win-open');
  await page.select('#win_aspect', 'data');
  await page.click('#win-open');
  await sleep(300);
  check('specialized aspect windows open simultaneously', (await page.$$eval('#windows .awin', (els) => els.length)) >= 2);

  await page.close();
}

async function runSliceView(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = {
    nodes: [
      { id: 'sys://sv', type: 'System', name: 'sv', parent: null, evidence: ['a'] },
      { id: 'container://sv/c', type: 'Container', name: 'c', parent: 'sys://sv', evidence: ['a'] },
      { id: 'component://sv/a', type: 'Component', name: 'a', parent: 'container://sv/c', evidence: ['a'] },
      { id: 'component://sv/b', type: 'Component', name: 'b', parent: 'container://sv/c', evidence: ['a'] },
    ],
    edges: [],
  };
  await fetch(`${BASE}/api/models/sv`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }),
  });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  await loadModel(page, 'sv');
  await sleep(400);
  await dblClickNode(page, 'c');     // drill into container
  await clickNode(page, 'a');        // select a component
  await sleep(200);
  await page.click('#node-focus');
  await sleep(400);
  const info = await page.$eval('#view-info', (el) => el.textContent);
  check('Ф2.7 slice focused on the selected node (no manual id)', /slice:/.test(info), info);
  await page.click('#view-reset');
  await sleep(200);
  check('Ф2.7 slice reset clears the view', (await page.$eval('#view-info', (el) => el.textContent)).trim() === '');
  await page.close();
}

async function runAspectViews(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://av', type: 'System', name: 'av', parent: null, evidence: [] },
    { id: 'container://av/c', type: 'Container', name: 'svc', parent: 'sys://av', evidence: ['a'] },
    { id: 'iface://av/api', type: 'Interface', name: 'API', parent: 'container://av/c', evidence: ['a'] },
    { id: 'operation://av/api/run', type: 'Operation', name: 'run', parent: 'iface://av/api', evidence: ['a'] },
    { id: 'data://av/Order', type: 'DataEntity', name: 'Order', parent: 'container://av/c', evidence: ['a'] },
    { id: 'attribute://av/Order/id', type: 'Attribute', name: 'id', parent: 'data://av/Order', evidence: ['a'] },
    { id: 'store://av/db', type: 'DataStore', name: 'db', parent: 'container://av/c', evidence: ['a'] },
    { id: 'sm://av/Flow', type: 'StateMachine', name: 'Flow', parent: 'container://av/c', evidence: ['a'] },
    { id: 'state://av/Idle', type: 'State', name: 'Idle', parent: 'sm://av/Flow', evidence: ['a'] },
    { id: 'event://av/status', type: 'Event', name: 'status', parent: 'sm://av/Flow', evidence: ['a'] },
    { id: 'process://av/P', type: 'Process', name: 'P', parent: 'container://av/c', evidence: ['a'] },
    { id: 'task://av/s1', type: 'Task', name: 'step1', parent: 'process://av/P', evidence: ['a'] },
    { id: 'gateway://av/g', type: 'Gateway', name: 'g', parent: 'process://av/P', evidence: ['a'] },
    { id: 'env://av/prod', type: 'Environment', name: 'prod', parent: 'sys://av', evidence: ['a'] },
    { id: 'node://av/host', type: 'Node', name: 'host', parent: 'sys://av', evidence: ['a'] },
    { id: 'runtime://av/docker', type: 'Runtime', name: 'docker', parent: 'node://av/host', evidence: ['a'] },
    { id: 'constraint://av/timeout', type: 'Constraint', name: 'timeout', parent: 'container://av/c', evidence: ['a'] },
    { id: 'role://av/admin', type: 'Role', name: 'admin', parent: 'container://av/c', evidence: ['a'] },
  ], edges: [
    { source: 'container://av/c', target: 'iface://av/api', type: 'exposes' },
    { source: 'iface://av/api', target: 'operation://av/api/run', type: 'has' },
    { source: 'sm://av/Flow', target: 'event://av/status', type: 'emits' },
  ] };
  const seeded = await fetch(`${BASE}/api/models/av`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });
  check('Ф2.8 aspect-views fixture model saved', seeded.ok, `put=${seeded.status}`);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await loadModel(page, 'av');
  await sleep(500);

  const openAspect = async (aspect, mode) => {
    await page.$eval('#win_aspect', (el, a) => { el.value = a; }, aspect);
    await page.click('#win-open');
    await sleep(300);
    if (mode) { await page.select(`#awin-${aspect} .awin-mode`, mode); await sleep(250); }
  };
  const probe = (aspect) => page.evaluate((a) => {
    const win = document.getElementById('awin-' + a);
    const svg = win && win.querySelector('.awin-body svg');
    const q = (sel) => (svg ? svg.querySelectorAll(sel).length : 0);
    return { hasSvg: !!svg, text: svg ? svg.textContent : '', state: q('.state-node'),
      event: q('.event-node'), gateway: q('.gateway-node'), task: q('.task-node'),
      store: q('.store-node'), attr: q('.attr-row'), detail: q('.node-detail') };
  }, aspect);

  await openAspect('interface');
  let p = await probe('interface');
  check('Ф2.8 interface view lists operations', p.hasSvg && p.text.includes('\u0192 run'), JSON.stringify(p).slice(0, 120));

  await openAspect('data');
  p = await probe('data');
  check('Ф2.8 data view shows attribute rows and a store cylinder', p.attr > 0 && p.store > 0,
    JSON.stringify(p).slice(0, 120));

  await openAspect('behavior');
  p = await probe('behavior');
  check('Ф2.8 behavior view draws states and events', p.state > 0 && p.event > 0, JSON.stringify(p).slice(0, 120));

  await openAspect('process');
  p = await probe('process');
  check('Ф2.8 process view draws tasks and a gateway', p.task > 0 && p.gateway > 0, JSON.stringify(p).slice(0, 120));

  await openAspect('deployment');
  p = await probe('deployment');
  const deployLayers = await page.evaluate(() => {
    const svg = document.querySelector('#awin-deployment .awin-body svg');
    return { boundaries: svg ? svg.querySelectorAll('.deploy-boundary').length : 0,
             text: svg ? svg.textContent : '' };
  });
  check('Ф2.8 deployment view renders layered boundaries',
    p.hasSvg && deployLayers.boundaries >= 1 && deployLayers.text.includes('Runtime'),
    JSON.stringify(deployLayers).slice(0, 140));

  await openAspect('quality');
  p = await probe('quality');
  check('Ф2.8 quality view renders a table', p.hasSvg && p.text.includes('anchor'), JSON.stringify(p.text.slice(0, 60)));

  await openAspect('structure');
  p = await probe('structure');
  check('Ф2.8 structure view shows in-box child detail', p.detail > 0, JSON.stringify(p).slice(0, 120));

  await openAspect('behavior', 'graph');
  p = await probe('behavior');
  check('Ф2.8 view-type selector switches the notation',
    p.state === 0 && p.text.includes('StateMachine ('), JSON.stringify(p.text.slice(0, 60)));

  // navigation: zoom buttons, home and level-up must work after re-renders
  await page.evaluate(() => { document.querySelectorAll('#windows .awin').forEach((w) => w.remove()); });
  const scale = () => page.evaluate(() => {
    const t = document.querySelector('#canvas > g:last-of-type').getAttribute('transform');
    const m = /scale\(([\d.]+)\)/.exec(t);
    return m ? +m[1] : null;
  });
  const fit = await scale();
  await page.click('#zoom-in');
  await sleep(250);
  const zoomed = await scale();
  check('Ф2.9 zoom-in changes the view scale', zoomed > fit * 1.05, `${fit} -> ${zoomed}`);
  await page.click('#zoom-reset');
  await sleep(300);
  const home = await scale();
  check('Ф2.9 home fits the whole view again', Math.abs(home - fit) < 0.02, `${fit} vs ${home}`);

  // drill by clicking a drillable node (event goes through the app handler)
  const drilled = await page.evaluate(() => {
    const groups = [...document.querySelectorAll('#canvas > g > g')]
      .filter((g) => g.querySelector('rect.node-box') && g.textContent.includes('⊕'));
    if (!groups.length) return false;
    const box = groups[0].querySelector('rect.node-box');
    box.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    return true;
  });
  await sleep(350);
  const crumbs2 = await crumbCount(page);
  await page.click('#level-out');
  await sleep(350);
  const crumbs1 = await crumbCount(page);
  check('Ф2.9 level-up returns to the parent level', drilled && crumbs2 >= 2 && crumbs1 === crumbs2 - 1,
    `drilled=${drilled} ${crumbs2} -> ${crumbs1}`);

  await page.close();
}

async function runNavLayers(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://nl', type: 'System', name: 'nl', parent: null, evidence: ['a'] },
    { id: 'container://nl/c', type: 'Container', name: 'ctr', parent: 'sys://nl', evidence: ['a'] },
    { id: 'component://nl/a', type: 'Component', name: 'ca', parent: 'container://nl/c', evidence: ['a'] },
    { id: 'component://nl/b', type: 'Component', name: 'cb', parent: 'container://nl/c', evidence: ['a'] },
  ], edges: [] };
  const seeded = await fetch(`${BASE}/api/models/nl`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });
  check('Ф2.9 layers fixture model saved', seeded.ok, `put=${seeded.status}`);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await loadModel(page, 'nl');
  await sleep(500);

  const boxes = () => page.$$eval('#canvas rect.node-box', (els) => els.length);
  check('Ф2.9 level controls exist', (await page.$('#lvl-up')) !== null && (await page.$('#lvl-down')) !== null);
  const deep = await boxes();
  await page.$eval('#lvl-down', (el) => { el.value = '0'; el.dispatchEvent(new Event('change', { bubbles: true })); });
  await sleep(250);
  const shallow = await boxes();
  check('Ф2.9 level controls change how many layers are visible', deep > shallow,
    `down=2 -> ${deep} boxes, down=0 -> ${shallow}`);

  await clickChip(page, 'data');
  await page.click('#zoom-reset');
  await sleep(300);
  const active = await activeChips(page);
  check('Ф2.9 home resets the view to C4', active.length === 1 && active[0] === 'c4', active.join(','));
  const home = await boxes();
  check('Ф2.9 home shows several levels at once', home >= 3, `${home} boxes`);
  await page.close();
}

async function runSequenceView(browser) {
  // Ф2.10 - sequence diagram derived from call/flow edges + operations
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://sq', type: 'System', name: 'sq', parent: null, evidence: ['a'] },
    { id: 'container://sq/c', type: 'Container', name: 'ctr', parent: 'sys://sq', evidence: ['a'] },
    { id: 'component://sq/a', type: 'Component', name: 'A', parent: 'container://sq/c', evidence: ['a'] },
    { id: 'component://sq/b', type: 'Component', name: 'B', parent: 'container://sq/c', evidence: ['a'] },
    { id: 'iface://sq/a', type: 'Interface', name: 'A API', parent: 'component://sq/a', evidence: ['a'] },
    { id: 'operation://sq/a/run', type: 'Operation', name: 'run', parent: 'iface://sq/a', evidence: ['a'] },
    { id: 'iface://sq/b', type: 'Interface', name: 'B API', parent: 'component://sq/b', evidence: ['a'] },
    { id: 'operation://sq/b/load', type: 'Operation', name: 'load', parent: 'iface://sq/b', evidence: ['a'] },
  ], edges: [
    { source: 'component://sq/a', target: 'component://sq/b', type: 'calls' },
  ] };
  const seeded = await fetch(`${BASE}/api/models/sq`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: g }) });
  check('Ф2.10 sequence fixture model saved', seeded.ok, `put=${seeded.status}`);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await loadModel(page, 'sq');
  await sleep(400);
  await page.$eval('#win_aspect', (el) => { el.value = 'interface'; });
  await page.click('#win-open');
  await sleep(300);
  await page.select('#awin-interface .awin-mode', 'sequence');
  await sleep(350);

  const info = await page.evaluate(() => {
    const win = document.getElementById('awin-interface');
    const svg = win.querySelector('.awin-body svg');
    return {
      lifelines: svg ? svg.querySelectorAll('.seq-lifeline').length : 0,
      msgs: svg ? svg.querySelectorAll('.seq-msg').length : 0,
      text: svg ? svg.textContent : '',
      from: !!win.querySelector('.awin-seq-from'),
      to: !!win.querySelector('.awin-seq-to'),
    };
  });
  check('Ф2.10 sequence view draws lifelines and messages',
    info.lifelines >= 2 && info.msgs >= 1 && info.text.includes('1.') && info.from && info.to,
    JSON.stringify(info).slice(0, 200));

  await page.select('#awin-interface .awin-seq-to', 'component://sq/b');
  await sleep(300);
  const filtered = await page.$$eval('#awin-interface .seq-msg', (els) => els.length);
  check('Ф2.10 sequence participant filter narrows the messages',
    filtered >= 1 && filtered <= info.msgs, `${info.msgs} -> ${filtered}`);
  await page.close();
}

async function runNotations(browser) {
  // Ф2.11 - real notations: state arrows, BPMN flows, deployment layers,
  // ER diamonds, C4 container boundaries
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://nt', type: 'System', name: 'nt', parent: null, evidence: ['a'] },
    { id: 'container://nt/c', type: 'Container', name: 'ctr', parent: 'sys://nt', evidence: ['a'] },
    { id: 'component://nt/a', type: 'Component', name: 'A', parent: 'container://nt/c', evidence: ['a'] },
    { id: 'component://nt/b', type: 'Component', name: 'B', parent: 'container://nt/c', evidence: ['a'] },
    { id: 'sm://nt/flow', type: 'StateMachine', name: 'Flow', parent: 'component://nt/a', evidence: ['a'] },
    { id: 'state://nt/created', type: 'State', name: 'Created', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'state://nt/running', type: 'State', name: 'Running', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'state://nt/done', type: 'State', name: 'Completed', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'transition://nt/start', type: 'Transition', name: 'created_to_running', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'transition://nt/finish', type: 'Transition', name: 'running_to_completed', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'transition://nt/explicit', type: 'Transition', name: 't2', from: 'Created', to: 'Completed', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'event://nt/status', type: 'Event', name: 'status', parent: 'sm://nt/flow', evidence: ['a'] },
    { id: 'process://nt/p', type: 'Process', name: 'P', parent: 'container://nt/c', evidence: ['a'] },
    { id: 'activity://nt/s1', type: 'Activity', name: 'Step 1', parent: 'process://nt/p', evidence: ['a'] },
    { id: 'activity://nt/s2', type: 'Activity', name: 'Step 2', parent: 'process://nt/p', evidence: ['a'] },
    { id: 'participant://nt/team', type: 'Participant', name: 'Team A', parent: 'process://nt/p', evidence: ['a'] },
    { id: 'activity://nt/s3', type: 'Activity', name: 'Step 3', parent: 'participant://nt/team', evidence: ['a'] },
    { id: 'env://nt/host', type: 'Environment', name: 'host', parent: 'sys://nt', evidence: ['a'] },
    { id: 'node://nt/agent', type: 'Node', name: 'agent-node', parent: 'sys://nt', evidence: ['a'] },
    { id: 'runtime://nt/docker', type: 'Runtime', name: 'docker', parent: 'node://nt/agent', evidence: ['a'] },
    { id: 'infra://nt/data', type: 'InfraResource', name: 'agent-data', parent: 'node://nt/agent', evidence: ['a'] },
    { id: 'data://nt/Order', type: 'DataEntity', name: 'Order', parent: 'container://nt/c', evidence: ['a'] },
    { id: 'attribute://nt/Order/id', type: 'Attribute', name: 'id', parent: 'data://nt/Order', evidence: ['a'] },
    { id: 'data://nt/Invoice', type: 'DataEntity', name: 'Invoice', parent: 'container://nt/c', evidence: ['a'] },
  ], edges: [
    { source: 'component://nt/a', target: 'component://nt/b', type: 'calls' },
    { source: 'activity://nt/s1', target: 'activity://nt/s2', type: 'flows_to' },
    { source: 'data://nt/Order', target: 'data://nt/Invoice', type: 'flows_to', cardinality: '1..N' },
  ] };
  const seeded = await fetch(`${BASE}/api/models/nt`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: g }) });
  check('Ф2.11 notation fixture model saved', seeded.ok, `put=${seeded.status}`);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await loadModel(page, 'nt');
  await sleep(400);

  const open = async (aspect, mode) => {
    await page.$eval('#win_aspect', (el, a) => { el.value = a; }, aspect);
    await page.click('#win-open');
    await sleep(300);
    if (mode) { await page.select(`#awin-${aspect} .awin-mode`, mode); await sleep(300); }
  };
  const counts = (aspect, selectors) => page.evaluate((a, sels) => {
    const svg = document.querySelector(`#awin-${a} .awin-body svg`);
    const out = {};
    sels.forEach((sel) => { out[sel] = svg ? svg.querySelectorAll(sel).length : 0; });
    return out;
  }, aspect, selectors);

  await open('behavior');
  let c = await counts('behavior', ['.state-arrow', '.state-node', '.initial-marker']);
  check('Ф2.11 state machine draws transitions as arrows',
    c['.state-arrow'] >= 2 && c['.state-node'] >= 3 && c['.initial-marker'] >= 1, JSON.stringify(c));

  // Ф2.14 - explicit from/to on a transition makes its arrow drawable
  check('Ф2.14 an explicit from/to transition is drawn',
    c['.state-arrow'] >= 3, JSON.stringify(c));

  await open('process');
  c = await counts('process', ['.bpmn-flow', '.bpmn-flow.implied', '.task-node']);
  check('Ф2.11 process flows follow the declared flows_to edges',
    c['.bpmn-flow'] >= 1 && c['.bpmn-flow.implied'] === 0 && c['.task-node'] >= 2, JSON.stringify(c));

  // Ф2.14 - a Participant becomes a lane
  c = await counts('process', ['.bpmn-lane']);
  check('Ф2.14 participants are drawn as BPMN lanes', c['.bpmn-lane'] >= 1, JSON.stringify(c));

  await open('deployment');
  c = await counts('deployment', ['.deploy-boundary', '.deploy-link']);
  check('Ф2.11 deployment renders layered boundaries with containment',
    c['.deploy-boundary'] >= 3 && c['.deploy-link'] >= 1, JSON.stringify(c));

  await open('data');
  c = await counts('data', ['.er-diamond']);
  check('Ф2.11 data relationships are drawn as ER diamonds', c['.er-diamond'] >= 1, JSON.stringify(c));

  const erText = await page.evaluate(() => {
    const svg = document.querySelector('#awin-data .awin-body svg');
    return svg ? svg.textContent : '';
  });
  check('Ф2.14 relationship cardinality is shown when the model carries it',
    erText.includes('1..N'), erText.slice(0, 120));

  await open('structure');
  c = await counts('structure', ['.c4-boundary']);
  check('Ф2.11 structure draws container boundaries (C4)', c['.c4-boundary'] >= 1, JSON.stringify(c));

  await page.close();
}

async function runWindowsNav(browser) {
  // Ф2.12 window convenience + Ф2.13 level jump / node search
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://wn', type: 'System', name: 'wn', parent: null, evidence: ['a'] },
    { id: 'container://wn/c', type: 'Container', name: 'ctr', parent: 'sys://wn', evidence: ['a'] },
    { id: 'component://wn/a', type: 'Component', name: 'Alpha', parent: 'container://wn/c', evidence: ['a'] },
    { id: 'component://wn/b', type: 'Component', name: 'Beta', parent: 'container://wn/c', evidence: ['a'] },
    { id: 'iface://wn/a', type: 'Interface', name: 'Alpha API', parent: 'component://wn/a', evidence: ['a'] },
    { id: 'operation://wn/a/run', type: 'Operation', name: 'run', parent: 'iface://wn/a', evidence: ['a'] },
    { id: 'data://wn/Order', type: 'DataEntity', name: 'Order', parent: 'container://wn/c', evidence: ['a'] },
    { id: 'attribute://wn/Order/id', type: 'Attribute', name: 'id', parent: 'data://wn/Order', evidence: ['a'] },
  ], edges: [{ source: 'component://wn/a', target: 'component://wn/b', type: 'calls' }] };
  const seeded = await fetch(`${BASE}/api/models/wn`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: g }) });
  check('Ф2.12 window/nav fixture model saved', seeded.ok, `put=${seeded.status}`);

  fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  const client = await page.createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DOWNLOAD_DIR });
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);
  await loadModel(page, 'wn');
  await sleep(400);

  // view type is remembered per aspect (survives closing the window)
  await page.$eval('#win_aspect', (el) => { el.value = 'interface'; });
  await page.click('#win-open');
  await sleep(300);
  await page.select('#awin-interface .awin-mode', 'sequence');
  await sleep(250);
  await page.click('#awin-interface .awin-close');
  await sleep(200);
  await page.$eval('#win_aspect', (el) => { el.value = 'interface'; });
  await page.click('#win-open');
  await sleep(300);
  const mode = await page.$eval('#awin-interface .awin-mode', (el) => el.value);
  check('Ф2.12 the view type is remembered per aspect', mode === 'sequence', mode);

  // hotkeys cycle the view type
  await page.mouse.move(10, 10);
  await page.keyboard.press('.');
  await sleep(250);
  const afterDot = await page.$eval('#awin-interface .awin-mode', (el) => el.value);
  await page.keyboard.press(',');
  await sleep(250);
  const afterComma = await page.$eval('#awin-interface .awin-mode', (el) => el.value);
  check('Ф2.12 hotkeys switch the view type', afterDot !== 'sequence' && afterComma === 'sequence',
    `${afterDot} -> ${afterComma}`);

  // minimize
  await page.click('#awin-interface .awin-min');
  await sleep(200);
  const minimized = await page.evaluate(() => {
    const win = document.getElementById('awin-interface');
    const body = win.querySelector('.awin-body');
    return { min: win.classList.contains('min'), hidden: getComputedStyle(body).display === 'none' };
  });
  check('Ф2.12 a window can be minimized', minimized.min && minimized.hidden, JSON.stringify(minimized));

  // export every aspect view as one SVG
  await page.click('#export-all');
  await sleep(900);
  const files = fs.readdirSync(DOWNLOAD_DIR).filter((f) => /\.svg$/.test(f));
  const combined = files.length ? fs.readFileSync(path.join(DOWNLOAD_DIR, files[0]), 'utf8') : '';
  const exports = (combined.match(/class="awin-export"/g) || []).length;
  check('Ф2.12 every aspect view exports as one SVG', files.length >= 1 && exports >= 2,
    `files=${files.length} views=${exports}`);

  // level jump: the whole L3 (Component) level
  await page.select('#level-jump', '2');
  await sleep(400);
  const level = await page.evaluate(() => {
    const boxes = [...document.querySelectorAll('#canvas rect.node-box')];
    return { boxes: boxes.length, text: document.querySelector('#canvas').textContent,
             hasContainer: /Container/.test(document.querySelector('#canvas').textContent) };
  });
  check('Ф2.13 level jump shows one whole C4 level',
    level.boxes === 2 && /Alpha/.test(level.text) && /Beta/.test(level.text) && !level.hasContainer,
    JSON.stringify({ boxes: level.boxes, hasContainer: level.hasContainer }));

  // node search reveals + selects a node
  await page.$eval('#node-search', (el) => { el.value = 'Order'; });
  await page.click('#node-find');
  await sleep(400);
  const found = await page.evaluate(() => ({
    selected: document.querySelectorAll('#canvas rect.sel-node').length,
    text: document.querySelector('#canvas').textContent,
  }));
  check('Ф2.13 node search selects and reveals a node',
    found.selected >= 1 && /Order/.test(found.text), JSON.stringify(found).slice(0, 140));
  await page.close();
}

async function runArchitectPanel(browser) {
  // Ф8/ARCHITECT_GUIDE: the workflow operations are available in the UI
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://ap', type: 'System', name: 'ap', parent: null, evidence: ['a'] },
    { id: 'container://ap/c', type: 'Container', name: 'ctr', parent: 'sys://ap', evidence: ['a'] },
    { id: 'component://ap/core', type: 'Component', name: 'Core', parent: 'container://ap/c', evidence: ['a'] },
    { id: 'iface://ap/api', type: 'Interface', name: 'API', parent: 'component://ap/core', evidence: ['a'] },
    { id: 'operation://ap/api/run', type: 'Operation', name: 'run', parent: 'iface://ap/api', evidence: ['a'] },
  ], edges: [{ source: 'component://ap/core', target: 'iface://ap/api', type: 'exposes' }] };
  const seeded = await fetch(`${BASE}/api/models/ap`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: g }) });
  check('Ф8 architect fixture model saved', seeded.ok, `put=${seeded.status}`);

  fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  const client = await page.createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DOWNLOAD_DIR });
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  const controls = await page.evaluate(() => [
    'arch-load', 'arch-validate', 'arch-eval', 'arch-coverage', 'arch-impact',
    'arch-workpackage', 'arch-markers', 'arch-matrix', 'arch-conformance',
    'arch-review', 'arch-reconcile', 'arch-generate', 'arch-codegen', 'arch-diff', 'arch_target', 'arch-handoff',
  ].filter((id) => !document.getElementById(id)));
  check('Ф8 the architect panel exposes every workflow operation', controls.length === 0, controls.join(','));

  await page.$eval('#arch_model', (el) => { el.value = 'ap'; });
  await page.click('#arch-load');
  await sleep(400);
  let out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel loads a model', /loaded ap/.test(out), out.slice(0, 120));

  await page.click('#arch-validate');
  await sleep(400);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel validates the model', /validation/.test(out) && /"ok": true/.test(out), out.slice(0, 140));

  await page.click('#arch-coverage');
  await sleep(400);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel reports coverage', /coverage/.test(out), out.slice(0, 140));

  await page.$eval('#arch_node', (el) => { el.value = 'component://ap/core'; });
  await page.click('#arch-workpackage');
  await sleep(500);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel builds a work package', /criteria|steps|slice/.test(out), out.slice(0, 160));

  await page.click('#arch-markers');
  await sleep(500);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel issues traceability markers', /marker/i.test(out), out.slice(0, 160));

  await page.click('#arch-eval');
  await sleep(500);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel shows per-aspect metrics', /"aspects"/.test(out) && /"score"/.test(out), out.slice(0, 160));

  // Ф8.1 - one archive with the package, artifacts, markers and instructions
  await page.click('#arch-handoff');
  await sleep(1200);
  const bundles = fs.readdirSync(DOWNLOAD_DIR).filter((f) => /\.tar\.gz$/.test(f));
  const size = bundles.length ? fs.statSync(path.join(DOWNLOAD_DIR, bundles[0])).size : 0;
  check('Ф8.1 the panel downloads the handoff package',
    bundles.length >= 1 && size > 500, `files=${bundles.length} size=${size}`);

  // code generation needs a live LLM: in mock mode the panel says so
  await page.click('#arch-codegen');
  await sleep(600);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel offers code generation to an external agent', /live LLM|code generation/.test(out), out.slice(0, 160));

  // graph diff against a second (delivered) model
  await fetch(`${BASE}/api/models/ap2`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: { nodes: [
      { id: 'sys://ap2', type: 'System', name: 'ap2', parent: null, evidence: ['a'] },
      { id: 'component://ap2/x', type: 'Component', name: 'Core', parent: 'sys://ap2', evidence: ['a'] },
    ], edges: [] } }) });
  await page.$eval('#arch_target', (el) => { el.value = 'ap2'; });
  await page.click('#arch-diff');
  await sleep(600);
  out = await page.$eval('#arch-out', (el) => el.textContent);
  check('Ф8 the panel diffs a delivered model against the target',
    /"verdict"/.test(out) && /"missing"/.test(out), out.slice(0, 200));
  await page.close();
}

async function runLayout(browser) {
  // UI-1: graph-first shell — panels hidden by default, setup in a modal,
  // events in a bottom drawer, reports in a modal
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://ly', type: 'System', name: 'ly', parent: null, evidence: ['a'] },
    { id: 'container://ly/c', type: 'Container', name: 'ctr', parent: 'sys://ly', evidence: ['a'] },
    { id: 'component://ly/a', type: 'Component', name: 'Core', parent: 'container://ly/c', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/ly`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });

  const shell = await page.evaluate(() => {
    const layout = document.querySelector('main.layout');
    const sidebar = document.querySelector('aside.sidebar');
    const wrap = document.querySelector('#canvas-wrap').getBoundingClientRect();
    return { hidden: layout.classList.contains('sidebar-hidden'),
             sidebarVisible: sidebar ? getComputedStyle(sidebar).display !== 'none' : false,
             canvasWidth: Math.round(wrap.width), viewport: window.innerWidth };
  });
  check('UI-1 the graph takes the width left by the collapsible tree',
    shell.hidden && !shell.sidebarVisible && shell.canvasWidth > shell.viewport * 0.7, JSON.stringify(shell));

  await page.click('#sidebar-toggle');
  await sleep(250);
  const shown = await page.evaluate(() => {
    const layout = document.querySelector('main.layout');
    const wrap = document.querySelector('#canvas-wrap').getBoundingClientRect();
    return { hidden: layout.classList.contains('sidebar-hidden'), canvasWidth: Math.round(wrap.width) };
  });
  check('UI-1 the panels can be opened and the canvas re-fits',
    !shown.hidden && shown.canvasWidth < shell.canvasWidth, JSON.stringify(shown));

  await page.click('#setup-open');
  await sleep(250);
  const modal = await page.evaluate(() => {
    const m = document.getElementById('setup-modal');
    return { open: !m.classList.contains('hidden'), hasLlm: !!m.querySelector('#base_url'),
             hasBudget: !!m.querySelector('#max_chars'), hasStorage: !!m.querySelector('#volume'),
             hasLaunch: !!m.querySelector('#run') };
  });
  check('UI-1 setup lives in a modal (LLM, context, storage) and has no launch',
    modal.open && modal.hasLlm && modal.hasBudget && modal.hasStorage && !modal.hasLaunch, JSON.stringify(modal));
  await page.click('#setup-close');
  await sleep(250);
  check('UI-1 the setup modal closes', await page.evaluate(() => document.getElementById('setup-modal').classList.contains('hidden')));

  const drawer = await page.evaluate(() => {
    const d = document.getElementById('events-drawer');
    return { has: !!d, hasStatus: !!d.querySelector('#status'), hasLog: !!d.querySelector('#log'),
             open: d.classList.contains('open'), h: Math.round(d.getBoundingClientRect().height) };
  });
  await page.click('#drawer-toggle');
  await sleep(300);
  const collapsed = await page.evaluate(() => Math.round(document.getElementById('events-drawer').getBoundingClientRect().height));
  check('UI-1 the events drawer collapses and expands',
    drawer.has && drawer.hasStatus && drawer.hasLog && drawer.open && collapsed < drawer.h, `${drawer.h} -> ${collapsed}`);
  await page.click('#drawer-toggle');
  await sleep(250);

  await loadModel(page, 'ly');
  await sleep(500);
  await page.click('#report-open');
  await page.waitForFunction(() => /validation:/.test(document.querySelector('#report-body').textContent), { timeout: 20000 }).catch(() => {});
  await sleep(400);
  const report = await page.$eval('#report-body', (el) => el.textContent);
  check('UI-1 the report tab shows validation and per-aspect metrics',
    /validation: ok/.test(report) && /score:/.test(report) && /structure/.test(report), report.slice(0, 160));
  await page.close();
}

async function runInspector(browser) {
  // UI-2: everything about an element opens from the element itself
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://ins', type: 'System', name: 'ins', parent: null, evidence: ['a'] },
    { id: 'container://ins/c', type: 'Container', name: 'ctr', parent: 'sys://ins', evidence: ['a'] },
    { id: 'component://ins/core', type: 'Component', name: 'Core', parent: 'container://ins/c', evidence: ['a'] },
    { id: 'iface://ins/api', type: 'Interface', name: 'API', parent: 'component://ins/core', evidence: ['a'] },
    { id: 'operation://ins/api/run', type: 'Operation', name: 'run', parent: 'iface://ins/api', evidence: ['a'] },
  ], edges: [{ source: 'component://ins/core', target: 'iface://ins/api', type: 'exposes' }] };
  await fetch(`${BASE}/api/models/ins`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'ins');
  await sleep(500);

  await clickNode(page, 'Core');
  const insp = await page.evaluate(() => {
    const layout = document.querySelector('main.layout');
    const box = document.getElementById('inspector');
    return { open: layout.classList.contains('inspector-open'),
             visible: getComputedStyle(box).display !== 'none',
             name: document.getElementById('insp-name').textContent,
             type: document.getElementById('insp-type').textContent,
             facts: document.getElementById('insp-facts').textContent,
             chips: [...document.querySelectorAll('#insp-aspects .chip')].map((c) => c.dataset.aspect) };
  });
  check('UI-2 a click opens the element inspector',
    insp.open && insp.visible && insp.name === 'Core' && insp.type === 'Component' && /component:\/\/ins\/core/.test(insp.facts),
    JSON.stringify(insp).slice(0, 180));
  check('UI-2 the inspector lists the aspects the element takes part in',
    insp.chips.includes('structure') && insp.chips.includes('interface'), insp.chips.join(','));

  // rename through the inspector (transactional edit)
  await page.$eval('#node-rename', (el) => { el.value = 'CoreService'; });
  await page.click('#node-rename-apply');
  await sleep(700);
  const renamed = await (await fetch(`${BASE}/api/models/ins`)).json();
  check('UI-2 rename from the inspector updates the element',
    (renamed.nodes || []).some((n) => n.id === 'component://ins/core' && n.name === 'CoreService'),
    JSON.stringify((renamed.nodes || []).find((n) => n.id === 'component://ins/core')));

  // create a child inside the selected element
  await clickNode(page, 'CoreService');
  await page.$eval('#insp-child-name', (el) => { el.value = 'Cache'; });
  await page.select('#insp-child-type', 'Component');
  await page.click('#insp-create-child');
  await sleep(700);
  const withChild = await (await fetch(`${BASE}/api/models/ins`)).json();
  check('UI-2 create inside adds a child element',
    (withChild.nodes || []).some((n) => n.name === 'Cache' && n.parent === 'component://ins/core'),
    JSON.stringify((withChild.nodes || []).map((n) => n.name)));

  // link the element to another one
  await clickNode(page, 'CoreService');
  await page.select('#insp-link-target', 'iface://ins/api');
  await page.select('#insp-link-type', 'owns');
  await page.click('#insp-link');
  await sleep(700);
  const linked = await (await fetch(`${BASE}/api/models/ins`)).json();
  check('UI-2 link creates a relationship from the element',
    (linked.edges || []).some((e) => e.source === 'component://ins/core' && e.target === 'iface://ins/api' && e.type === 'owns'),
    JSON.stringify((linked.edges || []).length));

  // a click on empty canvas offers to create something unattached: pick a spot
  // that really is empty (the layout depends on the model and the view)
  const spot = await page.evaluate(() => {
    const r = document.getElementById('canvas').getBoundingClientRect();
    const cands = [[0.92, 0.92], [0.1, 0.92], [0.92, 0.2], [0.12, 0.15], [0.5, 0.95]];
    for (const [fx, fy] of cands) {
      const x = r.x + r.width * fx, y = r.y + r.height * fy;
      const el = document.elementFromPoint(x, y);
      if (el && (el.id === 'canvas' || el.tagName.toLowerCase() === 'svg')) {
        return { x: Math.round(x), y: Math.round(y) };
      }
    }
    return { x: Math.round(r.x + r.width * 0.92), y: Math.round(r.y + r.height * 0.92) };
  });
  await page.mouse.click(spot.x, spot.y);
  await sleep(300);
  const popover = await page.evaluate(() => !document.getElementById('popover').classList.contains('hidden'));
  check('UI-2 a click on empty canvas offers to create', popover);
  await page.click('#pop-cancel');
  await sleep(200);

  // delete through the inspector
  await clickNode(page, 'Cache');
  await page.click('#node-delete');
  await sleep(700);
  const afterDelete = await (await fetch(`${BASE}/api/models/ins`)).json();
  check('UI-2 delete removes the element',
    !(afterDelete.nodes || []).some((n) => n.name === 'Cache'), JSON.stringify((afterDelete.nodes || []).map((n) => n.name)));

  // Esc closes the inspector
  await page.keyboard.press('Escape');
  await sleep(300);
  const closed = await page.evaluate(() => !document.querySelector('main.layout').classList.contains('inspector-open'));
  check('UI-2 Esc closes the inspector', closed);
  await page.close();
}

async function runLens(browser) {
  // UI-3: the aspect lens shows one aspect's notation on the whole canvas,
  // opened from an element
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://ln', type: 'System', name: 'ln', parent: null, evidence: ['a'] },
    { id: 'container://ln/c', type: 'Container', name: 'ctr', parent: 'sys://ln', evidence: ['a'] },
    { id: 'component://ln/core', type: 'Component', name: 'Core', parent: 'container://ln/c', evidence: ['a'] },
    { id: 'sm://ln/flow', type: 'StateMachine', name: 'Flow', parent: 'component://ln/core', evidence: ['a'] },
    { id: 'state://ln/draft', type: 'State', name: 'Draft', parent: 'sm://ln/flow', evidence: ['a'] },
    { id: 'state://ln/done', type: 'State', name: 'Done', parent: 'sm://ln/flow', evidence: ['a'] },
    { id: 'transition://ln/go', type: 'Transition', name: 'go', from: 'Draft', to: 'Done', parent: 'sm://ln/flow', evidence: ['a'] },
    { id: 'data://ln/Order', type: 'DataEntity', name: 'Order', parent: 'container://ln/c', evidence: ['a'] },
    { id: 'attribute://ln/Order/id', type: 'Attribute', name: 'id', parent: 'data://ln/Order', evidence: ['a'] },
    { id: 'process://ln/p', type: 'Process', name: 'P', parent: 'container://ln/c', evidence: ['a'] },
    { id: 'activity://ln/s1', type: 'Activity', name: 'Step 1', parent: 'process://ln/p', evidence: ['a'] },
  ], edges: [
    { source: 'component://ln/core', target: 'sm://ln/flow', type: 'owns' },
    { source: 'sm://ln/flow', target: 'event://ln/x', type: 'owns' },
  ].filter((e) => e.target !== 'event://ln/x') };
  await fetch(`${BASE}/api/models/ln`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'ln');
  await sleep(500);

  await clickNode(page, 'Core');
  await page.evaluate(() => {
    const chip = [...document.querySelectorAll('#insp-aspects .chip')].find((c) => c.dataset.aspect === 'behavior');
    if (chip) chip.click();
  });
  await sleep(500);
  const lens = await page.evaluate(() => {
    const bar = document.getElementById('lens-bar');
    const svg = document.getElementById('canvas');
    return { visible: !bar.classList.contains('hidden'),
             aspect: document.getElementById('lens-aspect').textContent,
             modes: [...document.querySelectorAll('#lens-mode option')].map((o) => o.value),
             machines: svg.querySelectorAll('.state-machine').length,
             states: svg.querySelectorAll('.state-node').length,
             arrows: svg.querySelectorAll('.state-arrow').length,
             layered: svg.querySelectorAll('rect.node-box').length };
  });
  check('UI-3 the aspect chip opens the lens with that aspect notation',
    lens.visible && lens.aspect === 'behavior' && lens.machines >= 1 && lens.states >= 2 && lens.layered === 0,
    JSON.stringify(lens).slice(0, 200));
  check('UI-3 the lens offers the view types of the aspect',
    lens.modes.includes('state') && lens.modes.includes('graph'), lens.modes.join(','));

  await page.select('#lens-mode', 'graph');
  await sleep(400);
  const graphMode = await page.evaluate(() => document.querySelectorAll('#canvas .state-machine').length);
  check('UI-3 switching the lens view type redraws the canvas', graphMode === 0, `machines=${graphMode}`);

  await page.click('#lens-back');
  await sleep(400);
  const back = await page.evaluate(() => ({
    bar: document.getElementById('lens-bar').classList.contains('hidden'),
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('UI-3 back returns to the C4 view', back.bar && back.boxes >= 1, JSON.stringify(back));
  await page.close();
}

async function runAgentPanel(browser) {
  // UI-4: the agent lives next to the inspector, knows the selection and can
  // apply its answer as an edit transaction
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://ag', type: 'System', name: 'ag', parent: null, evidence: ['a'] },
    { id: 'container://ag/c', type: 'Container', name: 'ctr', parent: 'sys://ag', evidence: ['a'] },
    { id: 'component://ag/core', type: 'Component', name: 'Core', parent: 'container://ag/c', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/ag`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'ag');
  await sleep(500);

  await page.click('#btab-agent');
  await sleep(400);
  const panel = await page.evaluate(() => {
    const drawer = document.getElementById('events-drawer');
    const pane = document.getElementById('bpane-agent');
    const comm = document.getElementById('comm');
    return { drawerOpen: drawer.classList.contains('open'),
             agentVisible: !pane.classList.contains('hidden'),
             eventsHidden: document.getElementById('bpane-events').classList.contains('hidden'),
             commInside: pane.contains(comm),
             context: document.getElementById('agent-context').textContent };
  });
  check('UI-4 the agent panel opens in the bottom panel next to Events',
    panel.drawerOpen && panel.agentVisible && panel.eventsHidden && panel.commInside,
    JSON.stringify(panel).slice(0, 180));

  await clickNode(page, 'Core');
  await sleep(400);
  const ctx = await page.$eval('#agent-context', (el) => el.textContent);
  check('UI-4 the agent panel shows the current selection as context', /Core/.test(ctx) && /Component/.test(ctx), ctx);

  await page.click('#btab-events');
  await sleep(250);
  const eventsPane = await page.evaluate(() => !document.getElementById('bpane-events').classList.contains('hidden')
    && document.getElementById('bpane-agent').classList.contains('hidden'));
  await page.click('#btab-agent');
  await sleep(250);
  const agentPane = await page.evaluate(() => document.getElementById('bpane-events').classList.contains('hidden')
    && !document.getElementById('bpane-agent').classList.contains('hidden'));
  check('UI-4 the bottom panel switches between Events and the agent', eventsPane && agentPane,
    `events=${eventsPane} agent=${agentPane}`);

  // the agent's answer (a JSON array of operations) applies as a transaction
  const ops = [{ op: 'add_node', node: { id: 'component://ag/cache', type: 'Component', name: 'Cache',
    parent: 'container://ag/c', evidence: [] } }];
  await page.$eval('#rag_answer', (el, v) => { el.value = v; }, JSON.stringify(ops));
  await page.click('#agent-apply');
  await sleep(800);
  const stored = await (await fetch(`${BASE}/api/models/ag`)).json();
  check('UI-4 an agent answer can be applied as an edit transaction',
    (stored.nodes || []).some((n) => n.id === 'component://ag/cache'), JSON.stringify((stored.nodes || []).map((n) => n.id)));

  await page.$eval('#rag_answer', (el) => { el.value = 'not json at all'; });
  await page.click('#agent-apply');
  await sleep(400);
  const status = await page.$eval('#status', (el) => el.textContent);
  check('UI-4 a non-transaction answer is refused with a hint', /not a JSON array/.test(status), status);
  await page.close();
}

async function runPalette(browser) {
  // UI-5: command palette (Ctrl/⌘+K) and the full-screen report
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://pal', type: 'System', name: 'pal', parent: null, evidence: ['a'] },
    { id: 'container://pal/c', type: 'Container', name: 'ctr', parent: 'sys://pal', evidence: ['a'] },
    { id: 'component://pal/core', type: 'Component', name: 'Core', parent: 'container://pal/c', evidence: ['a'] },
    { id: 'sm://pal/flow', type: 'StateMachine', name: 'Flow', parent: 'component://pal/core', evidence: ['a'] },
    { id: 'state://pal/draft', type: 'State', name: 'Draft', parent: 'sm://pal/flow', evidence: ['a'] },
    { id: 'state://pal/done', type: 'State', name: 'Done', parent: 'sm://pal/flow', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/pal`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'pal');
  await sleep(500);

  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(400);
  const opened = await page.evaluate(() => {
    const p = document.getElementById('palette');
    return { open: !p.classList.contains('hidden'),
             items: document.querySelectorAll('#palette-list .palette-item').length,
             focused: document.activeElement && document.activeElement.id };
  });
  check('UI-5 Ctrl+K opens the command palette with the operations',
    opened.open && opened.items >= 12 && opened.focused === 'palette-input', JSON.stringify(opened));

  await page.$eval('#palette-input', (el) => { el.value = 'validate'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await sleep(250);
  const filtered = await page.$$eval('#palette-list .palette-item', (els) => els.map((e) => e.textContent));
  check('UI-5 the palette filters commands by name',
    filtered.length === 1 && /Validate/.test(filtered[0]), JSON.stringify(filtered).slice(0, 160));

  await page.keyboard.press('Enter');
  await sleep(700);
  const validation = await page.$eval('#arch-out', (el) => el.textContent);
  check('UI-5 running a command from the palette works', /"validation"/.test(validation) || /"ok"/.test(validation),
    validation.slice(0, 120));

  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(300);
  await page.$eval('#palette-input', (el) => { el.value = 'aspect lens: behavior'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await sleep(250);
  await page.keyboard.press('Enter');
  await sleep(500);
  const lens = await page.evaluate(() => ({
    bar: !document.getElementById('lens-bar').classList.contains('hidden'),
    machines: document.querySelectorAll('#canvas .state-machine').length,
  }));
  check('UI-5 the palette can open an aspect lens', lens.bar && lens.machines >= 1, JSON.stringify(lens));

  await page.click('#report-open');
  await page.waitForFunction(() => /validation:/.test(document.getElementById('report-body').textContent), { timeout: 20000 }).catch(() => {});
  await sleep(500);
  const report = await page.evaluate(() => ({
    text: document.getElementById('report-body').textContent,
    wide: document.querySelector('#report-modal .modal-card').getBoundingClientRect().width > 800,
  }));
  check('UI-5 the report view is a full-screen summary',
    report.wide && /validation: ok/.test(report.text) && /score:/.test(report.text) && /requirements/.test(report.text),
    report.text.slice(0, 140));
  await page.close();
}

async function runPolish(browser) {
  // UI-6: hotkeys, theme and persisted view state.
  // This page keeps localStorage on purpose (persistence is what we test).
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://pl', type: 'System', name: 'pl', parent: null, evidence: ['a'] },
    { id: 'container://pl/c', type: 'Container', name: 'ctr', parent: 'sys://pl', evidence: ['a'] },
    { id: 'component://pl/core', type: 'Component', name: 'Core', parent: 'container://pl/c', evidence: ['a'] },
    { id: 'sm://pl/flow', type: 'StateMachine', name: 'Flow', parent: 'component://pl/core', evidence: ['a'] },
    { id: 'state://pl/a', type: 'State', name: 'A', parent: 'sm://pl/flow', evidence: ['a'] },
    { id: 'state://pl/b', type: 'State', name: 'B', parent: 'sm://pl/flow', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/pl`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'pl');
  await sleep(500);

  // hotkeys: Space -> agent, R -> setup
  await page.mouse.move(700, 300);
  await page.keyboard.press(' ');
  await sleep(400);
  const agent = await page.evaluate(() => !document.getElementById('bpane-agent').classList.contains('hidden'));
  await page.keyboard.press('Escape');
  await sleep(250);
  // Space focused the chat prompt: leave the field before using another hotkey
  await page.evaluate(() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); });
  await page.mouse.move(700, 300);
  await page.keyboard.press('r');
  await sleep(350);
  const setup = await page.evaluate(() => !document.getElementById('setup-modal').classList.contains('hidden'));
  await page.keyboard.press('Escape');
  await sleep(250);
  check('UI-6 hotkeys open the agent and the setup master', agent && setup, `agent=${agent} setup=${setup}`);

  // theme toggles and persists across a reload
  await page.click('#theme-toggle');
  await sleep(250);
  const light = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  // restoring the saved UI happens after the shell is wired: wait for it
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light', { timeout: 10000 }).catch(() => {});
  const stillLight = await page.evaluate(() => document.documentElement.dataset.theme);
  check('UI-6 the theme toggles and is remembered', light === 'light' && stillLight === 'light',
    `${light} -> ${stillLight}`);
  await page.click('#theme-toggle');
  await sleep(250);

  // the view state (model + lens) survives a reload
  await openSidebar(page);
  await loadModel(page, 'pl');
  await sleep(500);
  await clickNode(page, 'Core');
  await page.evaluate(() => {
    const chip = [...document.querySelectorAll('#insp-aspects .chip')].find((c) => c.dataset.aspect === 'behavior');
    if (chip) chip.click();
  });
  await sleep(500);
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelectorAll('#canvas .state-machine').length > 0, { timeout: 20000 }).catch(() => {});
  const restored = await page.evaluate(() => ({
    lensBar: !document.getElementById('lens-bar').classList.contains('hidden'),
    aspect: document.getElementById('lens-aspect').textContent,
    machines: document.querySelectorAll('#canvas .state-machine').length,
    model: document.getElementById('model_load').value,
  }));
  check('UI-6 the model and the lens are restored after a reload',
    restored.lensBar && restored.aspect === 'behavior' && restored.machines >= 1 && restored.model === 'pl',
    JSON.stringify(restored));

  // A opens the aspect lens for the selected element
  await openSidebar(page);
  await page.click('#lens-back');
  await sleep(400);
  await clickNode(page, 'Core');
  await sleep(300);
  await page.mouse.move(700, 300);
  await page.keyboard.press('a');
  await sleep(500);
  const byKey = await page.evaluate(() => ({
    bar: !document.getElementById('lens-bar').classList.contains('hidden'),
    aspect: document.getElementById('lens-aspect').textContent,
    inspector: document.querySelector('main.layout').classList.contains('inspector-open'),
    status: document.getElementById('status').textContent,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('UI-6 A opens the aspect lens for the selected element', byKey.bar && byKey.aspect.length > 0, JSON.stringify(byKey));
  await page.close();
}

async function runProjects(browser) {
  // Ф10.2: projects are selected and switched from the top bar (and the palette),
  // and the view state is kept per project
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const a = { nodes: [
    { id: 'sys://pa', type: 'System', name: 'pa', parent: null, evidence: ['a'] },
    { id: 'container://pa/c', type: 'Container', name: 'ctr', parent: 'sys://pa', evidence: ['a'] },
    { id: 'component://pa/core', type: 'Component', name: 'Core', parent: 'container://pa/c', evidence: ['a'] },
    { id: 'sm://pa/flow', type: 'StateMachine', name: 'Flow', parent: 'component://pa/core', evidence: ['a'] },
    { id: 'state://pa/x', type: 'State', name: 'X', parent: 'sm://pa/flow', evidence: ['a'] },
    { id: 'state://pa/y', type: 'State', name: 'Y', parent: 'sm://pa/flow', evidence: ['a'] },
  ], edges: [] };
  const b = { nodes: [
    { id: 'system://pb', type: 'System', name: 'pb', parent: null, evidence: ['a'] },
    { id: 'container://pb/c', type: 'Container', name: 'Other', parent: 'system://pb', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/projectA`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: a }) });
  await fetch(`${BASE}/api/models/projectB`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: b }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await page.waitForFunction(() => document.querySelectorAll('#project-select option').length > 0, { timeout: 20000 });

  const options = await page.$$eval('#project-select option', (els) => els.map((e) => e.value));
  check('Ф10.2 the top bar lists the projects', options.includes('projectA') && options.includes('projectB'),
    JSON.stringify(options).slice(0, 160));

  await page.select('#project-select', 'projectA');
  await sleep(900);
  const inA = await page.evaluate(() => ({
    model: document.getElementById('model_load').value,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('Ф10.2 switching a project loads its graph', inA.model === 'projectA' && inA.boxes >= 1, JSON.stringify(inA));

  // per-project view: open a lens in A, switch to B, come back
  await clickNode(page, 'Core');
  await page.evaluate(() => {
    const chip = [...document.querySelectorAll('#insp-aspects .chip')].find((c) => c.dataset.aspect === 'behavior');
    if (chip) chip.click();
  });
  await sleep(700);
  await page.select('#project-select', 'projectB');
  await sleep(900);
  const inB = await page.evaluate(() => ({
    lens: !document.getElementById('lens-bar').classList.contains('hidden'),
    model: document.getElementById('model_load').value,
  }));
  await page.select('#project-select', 'projectA');
  await sleep(1200);
  const backA = await page.evaluate(() => ({
    lens: !document.getElementById('lens-bar').classList.contains('hidden'),
    machines: document.querySelectorAll('#canvas .state-machine').length,
  }));
  check('Ф10.2 the view state is kept per project',
    !inB.lens && inB.model === 'projectB' && backA.lens && backA.machines >= 1,
    `B=${JSON.stringify(inB)} A=${JSON.stringify(backA)}`);

  // creating a project from the top bar
  await page.click('#project-new');
  await sleep(300);
  await page.$eval('#project_name', (el) => { el.value = 'projectC'; });
  await page.click('#project-create');
  await sleep(900);
  const created = await page.evaluate(() => ({
    options: [...document.querySelectorAll('#project-select option')].map((o) => o.value),
    value: document.getElementById('project-select').value,
  }));
  check('Ф10.2 a project can be created from the top bar',
    created.options.includes('projectC') && created.value === 'projectC', JSON.stringify(created).slice(0, 160));

  // and switching from the palette
  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(300);
  await page.$eval('#palette-input', (el) => { el.value = 'switch project: projectB'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await sleep(250);
  await page.keyboard.press('Enter');
  await sleep(900);
  const viaPalette = await page.evaluate(() => document.getElementById('model_load').value);
  check('Ф10.2 the palette can switch projects', viaPalette === 'projectB', viaPalette);
  await page.close();
}

async function runDialogueStore(browser) {
  // Ф6.1: the chat keeps every turn with its provenance (model/run/view/selection)
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' } });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });

  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(300);
  await page.click('#bp_new');
  await sleep(300);
  await page.$eval('#project_name', (el) => { el.value = 'dlg'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#project-create');
  await sleep(900);

  await page.click('#btab-agent');
  await sleep(300);
  await page.$eval('#prompt_input', (el) => { el.value = 'помоги с архитектурой'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(1200);

  let turns = [];
  for (let i = 0; i < 20; i++) {
    const res = await (await fetch(`${BASE}/api/dialogue?user=default&project=dlg&limit=20`)).json();
    turns = res.turns || [];
    if (turns.length) break;
    await sleep(300);
  }
  check('Ф6.1 the chat turn is stored in the dialogue log',
    turns.length >= 1 && /архитектур/i.test(turns[0].text), JSON.stringify(turns).slice(0, 200));
  check('Ф6.1 the stored turn carries its provenance',
    turns[0].provenance && turns[0].provenance.project === 'dlg'
    && turns[0].provenance.view === 'c4', JSON.stringify(turns[0].provenance));

  // Ф6.2: an applied edit is an implicit signal, attributed to the turn
  await page.$eval('#prompt_input', (el) => { el.value = 'add Component component://dlg/extra'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(1500);
  let implicit = { feedback: [] };
  for (let i = 0; i < 20; i++) {
    implicit = await (await fetch(`${BASE}/api/feedback?user=default&limit=20`)).json();
    if ((implicit.feedback || []).some((f) => f.kind === 'implicit')) break;
    await sleep(300);
  }
  const sig = (implicit.feedback || []).find((f) => f.kind === 'implicit');
  check('Ф6.2 an applied edit is recorded as an implicit signal',
    !!sig && sig.signal === 'accepted' && sig.about && sig.about.applied >= 1,
    JSON.stringify(implicit).slice(0, 220));

  const summary = await (await fetch(`${BASE}/api/dialogue/summary?user=default`)).json();
  check('Ф6.1 the dialogue is listed per project',
    (summary.dialogues || []).some((d) => d.project === 'dlg' && d.turns >= 1),
    JSON.stringify(summary).slice(0, 200));
  await page.close();
}

async function runRagAnswer(browser) {
  // Ф7.6: a question in the chat is answered from the personal documents, with
  // citations per sentence and anything the model invented marked unconfirmed
  const http = await import('node:http');
  const stub = http.createServer((req, res) => {
    req.on('data', () => {});
    req.on('end', () => {
      const answer = 'Refunds must be supported within 14 days [1]. Unicorns approve refunds on Mars.';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: answer } }] }));
    });
  });
  await new Promise((r) => stub.listen(0, '127.0.0.1', r));
  const port = stub.address().port;
  await putConfig({ llm: { base_url: `http://127.0.0.1:${port}/v1`, api_key: '', model: 'stub',
                           mode: 'live', timeout: 30 } });
  try {
    await fetch(`${BASE}/api/docs`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: 'default', name: 'refunds.md', kind: 'md',
        content: '# Refunds\n\n- REQ-1: refunds must be supported within 14 days.\n' }) });

    const page = await browser.newPage();
    await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
    page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
    await page.setViewport({ width: 1680, height: 1000 });
    await page.goto(`${BASE}/`, { waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
    await page.click('#btab-agent');
    await sleep(300);
    await page.$eval('#prompt_input', (el) => { el.value = 'ask refunds policy'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.click('#prompt-send');
    await sleep(1500);

    const text = await page.$eval('#activity', (el) => el.textContent);
    check('Ф7.6 the chat answers a question from the documents',
      /refunds must be supported/i.test(text), text.slice(-200));

    // Ф6.2: the answer can be rated, and the rating is attributed
    await page.click('#activity .ev.answer .fb-btn[data-signal="up"]');
    await sleep(900);
    let fb = { feedback: [] };
    for (let i = 0; i < 20; i++) {
      fb = await (await fetch(`${BASE}/api/feedback?user=default&limit=10`)).json();
      if ((fb.feedback || []).length) break;
      await sleep(300);
    }
    const rated = (fb.feedback || []).filter((f) => f.kind === 'explicit' && f.signal === 'up'
      && f.about && f.about.kind === 'rag');
    check('Ф6.2 rating an answer records attributed feedback',
      rated.length >= 1, JSON.stringify(fb).slice(0, 220));
    check('Ф7.6 the answer lists its sources and marks the unconfirmed part',
      /sources:/i.test(text) && /unconfirmed/i.test(text) && /unicorns/i.test(text),
      text.slice(-260));
  } finally {
    await new Promise((r) => stub.close(r));
    await putConfig({ llm: { mode: 'mock' } });
  }
}

async function runSynthesis(browser) {
  // Ф11: synthesise an architecture from a description typed in the chat, get the
  // graph + the architecture documentation, hand the package to a coding agent,
  // then correct the result (visually and through the agent)
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' },
                    context: { compare_docs: false, aspect_passes: false } });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });

  // the synthesis lands in a project, so the result is tracked like any other work
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(300);
  await page.click('#bp_new');
  await sleep(300);
  await page.$eval('#project_name', (el) => { el.value = 'synthcase'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#project-create');
  await sleep(900);

  await page.click('#btab-agent');
  await sleep(300);
  const send = async (text) => {
    const hidden = await page.evaluate(() => document.getElementById('bpane-agent').classList.contains('hidden'));
    if (hidden) { await page.click('#btab-agent'); await sleep(250); }
    await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
    await page.click('#prompt-send');
    await sleep(600);
  };
  const questions = async () => page.evaluate(() => [...document.querySelectorAll('#activity .ev')]
    .filter((e) => e.classList.contains('question')).map((e) => e.textContent));

  await send('design Сервис учёта заявок: сотрудник создаёт заявку, руководитель согласует её, '
           + 'бухгалтерия бронирует поездку; роли: сотрудник, руководитель, бухгалтерия');
  const asked = await questions();
  check('Ф11.1 the chat proposes a synthesis from the description',
    /synthesise|синтез/i.test(asked.slice(-1)[0] || ''), JSON.stringify(asked.slice(-1)));

  const runsBefore = new Set(((await (await fetch(`${BASE}/api/runs`)).json()).runs || []).map((r) => r.id));
  await send('да');
  await page.waitForFunction(() => /Done|Invalid|Error/.test(document.getElementById('status').textContent), { timeout: 150000 });
  // the run records the model it saved: find the run this test just started
  let model = '';
  for (let i = 0; i < 40 && !model; i++) {
    const runs = await (await fetch(`${BASE}/api/runs`)).json();
    const fresh = (runs.runs || []).find((r) => !runsBefore.has(r.id));
    if (fresh && fresh.id) {
      const snap = await (await fetch(`${BASE}/api/runs/${fresh.id}?since=0`)).json();
      const path = (snap.meta || {}).model_path;
      if (path) model = String(path).split('/').pop().replace(/\.json$/, '');
    }
    if (!model) await sleep(500);
  }
  await sleep(600);
  const after = await page.evaluate(() => ({
    status: document.getElementById('status').textContent,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('Ф11.1 the synthesis produces a graph and loads it',
    /Done/.test(after.status) && !!model && after.boxes >= 1, JSON.stringify({ after, model }));
  const graph = await (await fetch(`${BASE}/api/models/${encodeURIComponent(model)}`)).json();
  check('Ф11.1 the synthesised graph is a real architecture model',
    (graph.nodes || []).length >= 3 && (graph.nodes || []).every((n) => (n.evidence || []).length >= 0),
    `nodes=${(graph.nodes || []).length}`);

  // Ф11.3: the architecture document, generated from the model
  const doc = await page.evaluate(async (m) => {
    const r = await fetch(`/api/models/${encodeURIComponent(m)}/documentation`);
    return r.json();
  }, model);
  check('Ф11.3 the synthesised model yields architecture documentation',
    (doc.markdown || '').length > 300 && (doc.sections || []).includes('C4 decomposition')
    && (doc.markdown || '').includes(model),
    `len=${(doc.markdown || '').length} sections=${JSON.stringify(doc.sections)}`);

  // Ф11.4: a package for the coding agent, straight from the synthesised model
  const bundle = await page.evaluate(async (m) => {
    const r = await fetch(`/api/models/${encodeURIComponent(m)}/handoff`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const buf = await r.arrayBuffer();
    return { status: r.status, bytes: buf.byteLength, type: r.headers.get('content-type') || '' };
  }, model);
  check('Ф11.4 the synthesised model yields a handoff package for a coding agent',
    bundle.status === 200 && bundle.bytes > 200 && /gzip/.test(bundle.type), JSON.stringify(bundle));

  // Ф11.5: correct the result — visually (drag) …
  const before = await page.evaluate(() => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')][0];
    const m = gr.getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/);
    return { id: gr.getAttribute('data-node-id'), x: Number(m[1]), y: Number(m[2]) };
  });
  const spot = await page.evaluate((id) => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')].find((x) => x.getAttribute('data-node-id') === id);
    const r = gr.querySelector('rect.node-box').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, before.id);
  await page.mouse.move(spot.x, spot.y);
  await page.mouse.down();
  await page.mouse.move(spot.x + 120, spot.y + 70, { steps: 8 });
  await page.mouse.up();
  // the save is debounced: poll until the arrangement lands in the store
  let moved = { mode: '', positions: {} };
  for (let i = 0; i < 30; i++) {
    moved = await (await fetch(`${BASE}/api/models/${encodeURIComponent(model)}/layout`)).json();
    if (moved.mode === 'manual' && moved.positions[before.id]) break;
    await sleep(300);
  }
  check('Ф11.5 the synthesised architecture can be corrected visually',
    moved.mode === 'manual' && !!moved.positions[before.id], JSON.stringify(moved).slice(0, 160));

  // … and through the agent
  const target = (graph.nodes || []).find((n) => n.type === 'Container') || (graph.nodes || [])[0];
  const nodesBefore = (graph.nodes || []).length;
  await send(`add Component component://${model}/extra under ${target.id}`);
  let grown = { nodes: [] };
  for (let i = 0; i < 30; i++) {
    grown = await (await fetch(`${BASE}/api/models/${encodeURIComponent(model)}`)).json();
    if ((grown.nodes || []).length > nodesBefore) break;
    await sleep(300);
  }
  check('Ф11.5 the synthesised architecture can be corrected through the agent',
    (grown.nodes || []).length === nodesBefore + 1, `${nodesBefore} -> ${(grown.nodes || []).length}`);
  await page.close();
}

async function runCanvasLayout(browser) {
  // UI: canvas sub-tabs, the collapsible C4 tree, drag-to-place elements with a
  // remembered layout, auto-groupings, resizable panels, and home after focus
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' } });
  const g = { nodes: [
    { id: 'system://lay', type: 'System', name: 'Lay', parent: null, evidence: ['a'] },
    { id: 'container://lay/web', type: 'Container', name: 'Web', parent: 'system://lay', evidence: ['a'] },
    { id: 'component://lay/api', type: 'Component', name: 'API', parent: 'container://lay/web', evidence: ['a'] },
    { id: 'component://lay/core', type: 'Component', name: 'Core', parent: 'container://lay/web', evidence: ['a'] },
    { id: 'store://lay/db', type: 'DataStore', name: 'DB', parent: 'system://lay', evidence: ['a'] },
  ], edges: [
    { source: 'component://lay/api', target: 'store://lay/db', type: 'reads' },
  ] };
  await fetch(`${BASE}/api/models/layoutui`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });
  await fetch(`${BASE}/api/models/layoutui/layout`, { method: 'DELETE' }).catch(() => {});

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await loadModel(page, 'layoutui');
  await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 0, { timeout: 20000 });
  await sleep(600);

  // (5) the canvas has view sub-tabs, and the C4 view is the first one
  const tabs = await page.$$eval('#canvas-tabs .ctab', (els) => els.map((e) => e.dataset.view));
  check('UI(5) the canvas exposes view sub-tabs with C4 first',
    tabs.length >= 4 && tabs[0] === 'c4' && tabs.includes('process') && tabs.includes('data'),
    JSON.stringify(tabs));

  // (5) the C4 tree lists the model hierarchy and can be hidden
  const tree = await page.evaluate(() => ({
    visible: !document.getElementById('c4-tree').classList.contains('hidden'),
    items: [...document.querySelectorAll('#c4-tree .tree-item')].map((b) => b.dataset.node),
  }));
  check('UI(5) the C4 entity tree lists the model hierarchy',
    tree.visible && tree.items.includes('container://lay/web') && tree.items.includes('component://lay/api'),
    JSON.stringify(tree).slice(0, 200));

  await page.click('#tree-toggle');
  await sleep(300);
  const hiddenTree = await page.evaluate(() => document.getElementById('c4-tree').classList.contains('hidden'));
  check('UI(5) the C4 tree can be collapsed to free space', hiddenTree === true, String(hiddenTree));
  await page.click('#tree-toggle');
  await sleep(300);

  // (5) an aspect sub-tab shows that aspect only, for the selected entity
  await page.click('#canvas-tabs .ctab[data-view="data"]');
  await sleep(600);
  const dataView = await page.evaluate(() => ({
    active: document.querySelector('#canvas-tabs .ctab[data-view="data"]').classList.contains('active'),
    kinds: [...document.querySelectorAll('#canvas g[data-node-id]')].map((x) => x.getAttribute('data-node-id')),
  }));
  check('UI(5) an aspect sub-tab shows only that aspect',
    dataView.active && dataView.kinds.includes('store://lay/db')
    && !dataView.kinds.includes('component://lay/core'),
    JSON.stringify(dataView).slice(0, 200));
  await page.click('#canvas-tabs .ctab[data-view="c4"]');
  await sleep(500);

  // (2) dragging an element moves it and the position is remembered
  const before = await page.evaluate(() => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')].find((x) => x.getAttribute('data-node-id') === 'component://lay/api');
    const m = gr.getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/);
    return { x: Number(m[1]), y: Number(m[2]) };
  });
  const box = await page.evaluate(() => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')].find((x) => x.getAttribute('data-node-id') === 'component://lay/api');
    const r = gr.querySelector('rect.node-box').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.mouse.move(box.x + 140, box.y + 60, { steps: 8 });
  await page.mouse.up();
  await sleep(900);
  const after = await page.evaluate(() => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')].find((x) => x.getAttribute('data-node-id') === 'component://lay/api');
    const m = gr.getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/);
    return { x: Number(m[1]), y: Number(m[2]) };
  });
  check('UI(2) an element can be dragged on the canvas',
    Math.abs(after.x - before.x) > 20 || Math.abs(after.y - before.y) > 20,
    JSON.stringify({ before, after }));

  const stored = await (await fetch(`${BASE}/api/models/layoutui/layout`)).json();
  check('UI(2) the dragged position is stored for the next open',
    stored.mode === 'manual' && stored.positions['component://lay/api']
    && Math.abs(stored.positions['component://lay/api'].x - after.x) < 2,
    JSON.stringify(stored).slice(0, 200));

  // (3) an auto-grouping rearranges and is remembered as such
  await page.select('#layout-mode', 'grid');
  await sleep(200);
  await page.click('#layout-auto');
  await sleep(1000);
  const auto = await (await fetch(`${BASE}/api/models/layoutui/layout`)).json();
  check('UI(3) an auto-grouping is applied and remembered',
    auto.mode === 'auto' && auto.layout === 'grid' && Object.keys(auto.positions).length >= 3,
    JSON.stringify(auto).slice(0, 220));

  // (4) home returns to the whole system after Focus
  await page.evaluate(() => {
    const gr = [...document.querySelectorAll('#canvas g[data-node-id]')].find((x) => x.getAttribute('data-node-id') === 'container://lay/web');
    gr.querySelector('rect.node-box').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
  await sleep(400);
  const full = await page.$$eval('#canvas rect.node-box', (e) => e.length);
  await page.click('#node-focus');
  await sleep(900);
  const sliced = await page.$$eval('#canvas rect.node-box', (e) => e.length);
  await page.click('#zoom-reset');
  await sleep(900);
  const home = await page.$$eval('#canvas rect.node-box', (e) => e.length);
  check('UI(4) home restores the whole graph after Focus',
    sliced <= full && home === full && home > sliced,
    `full=${full} sliced=${sliced} home=${home}`);

  // (1) the agent button is gone from the top bar (the chat lives in the bottom panel)
  const agentTop = await page.evaluate(() => !!document.querySelector('.topbar #agent-open'));
  check('UI(1) the top bar has no separate Agent button', agentTop === false, String(agentTop));

  // (9) the bottom panel can be resized by dragging its top edge
  const drawerBefore = await page.evaluate(() => document.getElementById('events-drawer').getBoundingClientRect().height);
  const grip = await page.evaluate(() => {
    const r = document.getElementById('drawer-resize').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(grip.x, grip.y);
  await page.mouse.down();
  await page.mouse.move(grip.x, grip.y - 120, { steps: 8 });
  await page.mouse.up();
  await sleep(500);
  const drawerAfter = await page.evaluate(() => document.getElementById('events-drawer').getBoundingClientRect().height);
  check('UI(9) the bottom panel is resizable by its top edge',
    drawerAfter > drawerBefore + 40, `${Math.round(drawerBefore)} -> ${Math.round(drawerAfter)}`);

  // (10) the left sidebar is resizable (it is hidden by default, so open it)
  await page.click('#sidebar-toggle');
  await sleep(400);
  const sideBefore = await page.evaluate(() => document.querySelector('aside.sidebar').getBoundingClientRect().width);
  const edge = await page.evaluate(() => {
    const r = document.getElementById('sidebar-resize').getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(edge.x, edge.y);
  await page.mouse.down();
  await page.mouse.move(edge.x + 90, edge.y, { steps: 8 });
  await page.mouse.up();
  await sleep(500);
  const sideAfter = await page.evaluate(() => document.querySelector('aside.sidebar').getBoundingClientRect().width);
  check('UI(10) the left panel is resizable by its edge',
    sideAfter > sideBefore + 30, `${Math.round(sideBefore)} -> ${Math.round(sideAfter)}`);

  // (6) model load / export live with project management at the bottom
  const moved = await page.evaluate(() => ({
    inProjects: !!document.querySelector('#bpane-projects #model_load') && !!document.querySelector('#bpane-projects #export'),
    inToolbar: !!document.querySelector('.edit-toolbar #model_load'),
  }));
  check('UI(6) model load/export live in the project pane',
    moved.inProjects && !moved.inToolbar, JSON.stringify(moved));
  await page.close();
}

async function runC4Notation(browser) {
  // C4 notation: each view draws its own kinds — the C4 view the structural
  // elements, an aspect sub-tab its own shapes (Ф2: aspects are views, not layers)
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' } });
  const g = { nodes: [
    { id: 'system://c4', type: 'System', name: 'C4 System', parent: null, evidence: ['a'] },
    { id: 'system://c4/billing', type: 'System', name: 'Billing', parent: 'system://c4', evidence: ['a'] },
    { id: 'container://c4/web', type: 'Container', name: 'Web App', parent: 'system://c4', evidence: ['a'] },
    { id: 'component://c4/api', type: 'Component', name: 'API', parent: 'container://c4/web', evidence: ['a'] },
    { id: 'component://c4/core', type: 'Component', name: 'Core', parent: 'container://c4/web', evidence: ['a'] },
    { id: 'store://c4/db', type: 'DataStore', name: 'Orders DB', parent: 'system://c4', evidence: ['a'] },
    { id: 'role://c4/user', type: 'Role', name: 'Customer', parent: 'system://c4', evidence: ['a'] },
    { id: 'ext://c4/mail', type: 'ExternalSystem', name: 'Mail', parent: 'system://c4', evidence: ['a'] },
    { id: 'node://c4/prod', type: 'Node', name: 'prod', parent: 'system://c4', evidence: ['a'] },
  ], edges: [
    { source: 'component://c4/api', target: 'component://c4/core', type: 'depends_on' },
    { source: 'component://c4/api', target: 'store://c4/db', type: 'reads' },
  ] };
  await fetch(`${BASE}/api/models/c4nota`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });
  await fetch(`${BASE}/api/models/c4nota/layout`, { method: 'DELETE' }).catch(() => {});

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await loadModel(page, 'c4nota');
  await page.$eval('#lvl-down', (el) => { el.value = '4'; el.dispatchEvent(new Event('change', { bubbles: true })); });
  await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length >= 3, { timeout: 20000 });
  await sleep(400);

  // the C4 view: structural kinds with C4 labels
  const c4 = await page.evaluate(() => ({
    kinds: [...document.querySelectorAll('#canvas g[data-node-id]')].map((x) => x.getAttribute('data-c4')),
    labels: [...document.querySelectorAll('#canvas g[data-node-id] text')].map((t) => t.textContent),
    edges: document.querySelectorAll('#canvas path.edge-line').length,
    arrows: document.querySelectorAll('#canvas path.edge-line[marker-end]').length,
    widths: [...document.querySelectorAll('#canvas path.edge-line')].map((p) => Number(p.getAttribute('stroke-width') || 0)),
    labelsE: [...document.querySelectorAll('#canvas text.edge-label')].map((t) => t.textContent),
  }));
  const c4dash = await page.evaluate(() => ({
    dashed: [...document.querySelectorAll('#canvas g[data-c4="external"] rect.node-box')]
      .some((r) => (r.getAttribute('stroke-dasharray') || '') !== ''),
  }));
  check('C4(5) the C4 view draws the structural kinds, external systems dashed',
    c4.kinds.includes('system') && c4.kinds.includes('container') && c4.kinds.includes('component')
    && c4.kinds.includes('external') && c4dash.dashed
    && !c4.kinds.includes('database') && !c4.kinds.includes('person'),
    JSON.stringify({ kinds: c4.kinds, dashed: c4dash.dashed }));
  check('C4(5) C4 element labels name the kind in brackets',
    c4.labels.some((t) => /\[\s*Software System\s*\]/i.test(t))
    && c4.labels.some((t) => /\[\s*Container\s*\]/i.test(t))
    && c4.labels.some((t) => /\[\s*Component\s*\]/i.test(t)),
    JSON.stringify(c4.labels.slice(0, 10)));
  check('C4 edges are clearly visible (thick, arrowed, labelled)',
    c4.edges >= 1 && c4.arrows >= 1 && Math.min(...c4.widths) >= 2
    && c4.labelsE.some((t) => /depends_on|reads/.test(t)),
    JSON.stringify({ edges: c4.edges, arrows: c4.arrows, widths: c4.widths, labels: c4.labelsE }));

  // an edge must leave the source box at its border, not from its centre
  const anchored = await page.evaluate(() => {
    const rects = {};
    document.querySelectorAll('#canvas g[data-node-id]').forEach((gr) => {
      const r = gr.querySelector('rect.node-box');
      if (!r) return;
      const m = gr.getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/);
      const w = Number(r.getAttribute('width')), h = Number(r.getAttribute('height'));
      rects[gr.getAttribute('data-node-id')] = { cx: Number(m[1]) + w / 2, cy: Number(m[2]) + h / 2 };
    });
    const out = [];
    document.querySelectorAll('#canvas path.edge-line[marker-end]').forEach((p) => {
      const m = (p.getAttribute('d') || '').match(/^M\s*([-\d.]+),([-\d.]+)/);
      if (!m) return;
      const x = Number(m[1]), y = Number(m[2]);
      const near = Object.entries(rects).find(([, r]) => Math.abs(x - r.cx) <= 220 && Math.abs(y - r.cy) <= 110);
      if (near) out.push({ start: [Math.round(x), Math.round(y)], cx: Math.round(near[1].cx), cy: Math.round(near[1].cy) });
    });
    return out;
  });
  check('C4 edges start on the box border, not inside the element',
    anchored.length > 0 && anchored.every((e) => Math.abs(e.start[0] - e.cx) > 6 || Math.abs(e.start[1] - e.cy) > 6),
    JSON.stringify(anchored));

  // the data sub-tab draws its own notation (a cylinder)
  await clickChip(page, 'data');
  await sleep(600);
  const data = await page.evaluate(() => ({
    kinds: [...document.querySelectorAll('#canvas g[data-node-id]')].map((x) => x.getAttribute('data-c4')),
    cylinders: document.querySelectorAll('#canvas g[data-c4="database"] ellipse').length,
    labels: [...document.querySelectorAll('#canvas g[data-node-id] text')].map((t) => t.textContent),
  }));
  check('C4(5) the data view draws data elements as cylinders',
    data.kinds.includes('database') && data.cylinders >= 2
    && data.labels.some((t) => /\[\s*Database\s*\]/i.test(t)) && !data.kinds.includes('component'),
    JSON.stringify({ kinds: data.kinds, cyl: data.cylinders }));

  // an aspect the model has nothing for clears the canvas (no stale render)
  await clickChip(page, 'process');
  await sleep(600);
  const emptyView = await page.evaluate(() => ({
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
    hint: document.getElementById('empty').textContent,
    shown: getComputedStyle(document.getElementById('empty')).display !== 'none',
  }));
  check('C4(5) an aspect with no elements clears the canvas and explains it',
    emptyView.boxes === 0 && emptyView.shown && /process/.test(emptyView.hint),
    JSON.stringify(emptyView).slice(0, 160));

  // the deployment sub-tab draws deployment nodes with a double border
  await clickChip(page, 'deployment');
  await sleep(600);
  const dep = await page.evaluate(() => ({
    kinds: [...document.querySelectorAll('#canvas g[data-node-id]')].map((x) => x.getAttribute('data-c4')),
    inner: document.querySelectorAll('#canvas g[data-c4="deployment"] rect').length,
  }));
  check('C4(5) the deployment view draws deployment nodes',
    dep.kinds.includes('deployment') && dep.inner >= 2 && !dep.kinds.includes('component'),
    JSON.stringify(dep));

  // the access sub-tab: a role is a person glyph
  await clickChip(page, 'access');
  await sleep(600);
  const acc = await page.evaluate(() => ({
    kinds: [...document.querySelectorAll('#canvas g[data-node-id]')].map((x) => x.getAttribute('data-c4')),
    persons: document.querySelectorAll('#canvas g[data-c4="person"] circle').length,
  }));
  check('C4(5) the access view draws a role as a person',
    acc.kinds.includes('person') && acc.persons >= 1, JSON.stringify(acc));
  await page.close();
}

async function runReviewFixes(browser) {
  // regressions for the code review: creating a project switches to it, and the
  // "extend aspects" chat command passes the project's repository (not the model name)
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' },
                    context: { compare_docs: false, aspect_passes: false } });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });

  const seed = { nodes: [
    { id: 'system://seed', type: 'System', name: 'seed', parent: null, evidence: ['s'] },
    { id: 'container://seed/c', type: 'Container', name: 'C', parent: 'system://seed', evidence: ['s'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/seedfix`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: seed }) });

  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await loadModel(page, 'seedfix');
  await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 0, { timeout: 20000 });

  // fix(6): a new project becomes the active project and the canvas is cleared
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(300);
  await page.click('#bp_new');
  await sleep(300);
  await page.$eval('#project_name', (el) => { el.value = 'switchfix'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#project-create');
  await sleep(1200);
  const switched = await page.evaluate(() => ({
    project: document.getElementById('project-select').value,
    model: document.getElementById('model_load').value,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('fix(6) creating a project switches to it and clears the previous graph',
    switched.project === 'switchfix' && switched.model === '' && switched.boxes === 0,
    JSON.stringify(switched));

  // fix(3): "extend aspects" resumes from the project's repository
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(300);
  await page.$eval('#bp_repo', (el, v) => { el.value = v; }, REPO);
  await page.click('#bp_run');
  await page.waitForFunction(() => /Done|Invalid|Error/.test(document.getElementById('status').textContent), { timeout: 120000 });
  await page.evaluate(() => document.getElementById('btab-agent').click());
  await sleep(400);
  await page.$eval('#prompt_input', (el) => { el.value = 'add aspect data'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(600);
  const asked = await page.$eval('#activity', (el) => el.textContent);
  await page.$eval('#prompt_input', (el) => { el.value = 'да'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await page.waitForFunction(() => /Done|Invalid|Error/.test(document.getElementById('status').textContent), { timeout: 120000 });
  const outcome = await page.evaluate(() => ({
    status: document.getElementById('status').textContent,
    events: document.getElementById('activity').textContent,
  }));
  check('fix(3) the aspect chat command resumes from the project repository',
    /asked|reply|\u00ab/.test(asked) && /Done/.test(outcome.status) && !/no repository/.test(outcome.events),
    JSON.stringify({ asked: asked.slice(-80), status: outcome.status }));
  await page.close();
}

async function runWalkthrough(browser) {
  // One end-to-end pass through the product, carrying state from step to step:
  // project -> reconstruction -> inspect + lens -> validation -> report -> handoff.
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' },
                    context: { compare_docs: false, aspect_passes: false } });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });

  // 1. a project owns the run
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(400);
  await page.click('#bp_new');
  await sleep(300);
  await page.$eval('#project_name', (el) => { el.value = 'walkthrough'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#project-create');
  await sleep(900);
  const project = await page.evaluate(() => document.getElementById('bp_project').value);
  check('W1 a walkthrough project is created from the Projects pane', project === 'walkthrough', project);

  // 2. reconstruct the repository into it (mock profile, no LLM cost)
  await page.$eval('#bp_repo', (el, v) => { el.value = v; }, REPO);
  await page.click('#bp_run');
  await page.waitForFunction(() => /Done|Invalid|Error/.test(document.getElementById('status').textContent), { timeout: 120000 });
  const run = await page.evaluate(() => ({
    status: document.getElementById('status').textContent,
    model: document.getElementById('model_load').value,
    project: document.getElementById('project-select').value,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  // the model is named after the repository; the project stays the run owner
  check('W2 the walkthrough reconstruction finishes and loads the graph',
    /Done/.test(run.status) && !!run.model && run.project === 'walkthrough' && run.boxes >= 1,
    JSON.stringify(run));

  // 3. inspect an element and open its aspect lens
  const picked = await page.evaluate(() => {
    const g = [...document.querySelectorAll('#canvas > g > g')].find((x) => x.querySelector('rect.node-box'));
    if (!g) return null;
    g.querySelector('rect.node-box').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    return g.textContent.trim().slice(0, 40);
  });
  await sleep(500);
  const chip = await page.evaluate(() => {
    const c = document.querySelector('#insp-aspects .chip');
    if (!c) return null;
    c.click();
    return c.dataset.aspect;
  });
  await sleep(800);
  const lens = await page.evaluate(() => {
    const bar = document.getElementById('lens-bar');
    return { bar: !!bar && !bar.classList.contains('hidden'), mode: !!document.getElementById('lens-mode') };
  });
  check('W3 an element opens in the inspector and its aspect lens',
    !!picked && !!chip && lens.bar, JSON.stringify({ picked, chip, lens }));
  await page.evaluate(() => document.getElementById('lens-back').click());
  await sleep(400);

  // 4. validation
  await page.evaluate(() => document.getElementById('btab-validation').click());
  await sleep(300);
  await page.evaluate(() => document.getElementById('val-check').click());
  await sleep(1400);
  const val = await page.evaluate(() => document.getElementById('val-summary').textContent.trim());
  check('W4 the validation pane reports on the walkthrough model',
    val.length > 0 && !/no checks yet/.test(val), val.slice(0, 120));

  // 5. report
  await page.click('#report-open');
  await page.waitForFunction(() => /validation:/.test(document.getElementById('report-body').textContent), { timeout: 25000 }).catch(() => {});
  const report = await page.evaluate(() => document.getElementById('report-body').textContent);
  check('W5 the report summarises the walkthrough model',
    /validation:/.test(report) && report.length > 60, report.replace(/\s+/g, ' ').slice(0, 120));
  await page.click('#report-close');
  await sleep(300);

  // 6. handoff bundle for an external coding agent
  const handoff = await page.evaluate(async () => {
    const model = document.getElementById('model_load').value;
    const r = await fetch(`/api/models/${encodeURIComponent(model)}/handoff`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const buf = await r.arrayBuffer();
    return { status: r.status, bytes: buf.byteLength, type: r.headers.get('content-type') || '' };
  });
  check('W6 the walkthrough model yields a handoff bundle',
    handoff.status === 200 && handoff.bytes > 200 && /gzip/.test(handoff.type), JSON.stringify(handoff));
  await page.close();
}

async function runTemplates(browser) {
  // Ф8.5: a starter model is created from a template in a couple of steps
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' } });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await sleep(600);

  await page.click('body');
  await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
  await sleep(400);
  await page.waitForSelector('#palette:not(.hidden)', { timeout: 8000 });
  await page.$eval('#palette-input', (el) => { el.value = 'from template'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.keyboard.press('Enter');
  await page.waitForSelector('#tpl-modal:not(.hidden)', { timeout: 8000 });
  const opts = await page.$$eval('#tpl-select option', (els) => els.map((e) => e.value));
  check('Ф8.5 the palette opens a template wizard with the catalog', opts.includes('service_db'), JSON.stringify(opts));

  await page.select('#tpl-select', 'service_db');
  await page.$eval('#tpl-name', (el) => { el.value = 'tpl-demo'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#tpl-create');
  await page.waitForFunction(() => document.getElementById('model_load')?.value === 'tpl-demo', { timeout: 20000 });
  await sleep(900);
  const after = await page.evaluate(() => ({
    model: document.getElementById('model_load').value,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  const apiModel = await (await fetch(`${BASE}/api/models/tpl-demo`)).json();
  check('Ф8.5 a starter model is created from a template and loaded',
    after.model === 'tpl-demo' && (apiModel.nodes || []).length >= 10 && after.boxes >= 1,
    JSON.stringify({ after, nodes: (apiModel.nodes || []).length }));
  await page.close();
}

async function runBottomPanel(browser) {
  // Ф10.3: the bottom panel hosts Events | Agent | Projects
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://bp', type: 'System', name: 'bp', parent: null, evidence: ['a'] },
    { id: 'container://bp/c', type: 'Container', name: 'ctr', parent: 'sys://bp', evidence: ['a'] },
    { id: 'component://bp/core', type: 'Component', name: 'Core', parent: 'container://bp/c', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/bottomA`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });

  const tabs = await page.evaluate(() => ['btab-events', 'btab-agent', 'btab-projects']
    .filter((id) => !document.getElementById(id)));
  check('Ф10.3 the bottom panel has Events | Agent | Projects tabs', tabs.length === 0, tabs.join(','));

  await page.click('#btab-agent');
  await sleep(350);
  const agent = await page.evaluate(() => {
    const pane = document.getElementById('bpane-agent');
    return { visible: !pane.classList.contains('hidden'),
             commInside: pane.contains(document.getElementById('comm')),
             eventsHidden: document.getElementById('bpane-events').classList.contains('hidden') };
  });
  check('Ф10.3 the agent chat lives in the bottom panel', agent.visible && agent.commInside && agent.eventsHidden,
    JSON.stringify(agent));

  await page.$eval('#prompt_input', (el) => { el.value = 'review the model'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.click('#prompt-send');
  await sleep(600);
  const chatWorks = await page.evaluate(() => ({
    events: document.querySelectorAll('#activity .ev').length,
    status: document.getElementById('status').textContent,
  }));
  check('Ф10.3 the chat works from the bottom panel', chatWorks.events >= 1, JSON.stringify(chatWorks).slice(0, 140));

  await page.click('#btab-projects');
  await sleep(400);
  const projects = await page.evaluate(() => ({
    options: [...document.querySelectorAll('#bp_project option')].map((o) => o.value),
    agents: document.querySelectorAll('#bp_agent option').length,
    info: document.getElementById('bp_info').textContent,
  }));
  check('Ф10.3 the Projects tab lists the projects and the agents',
    projects.options.includes('bottomA') && projects.agents >= 1, JSON.stringify(projects).slice(0, 180));

  await page.select('#bp_project', 'bottomA');
  await page.click('#bp_open');
  await sleep(900);
  const opened = await page.evaluate(() => ({
    model: document.getElementById('model_load').value,
    boxes: document.querySelectorAll('#canvas rect.node-box').length,
  }));
  check('Ф10.3 a project can be opened from the Projects tab', opened.model === 'bottomA' && opened.boxes >= 1,
    JSON.stringify(opened));

  // Ф10.4 - the reconstruction starts here, not in Setup
  await page.$eval('#bp_repo', (el, v) => { el.value = v; }, REPO);
  await page.click('#bp_run');
  await sleep(1500);
  const started = await page.evaluate(() => document.getElementById('bp_info').textContent);
  check('Ф10.3 a reconstruction can be started from the Projects tab', /run .* started/.test(started), started.slice(0, 140));

  // Ф10.4 - the setup master holds settings only; the launch lives with the project
  await page.click('#setup-open');
  await sleep(300);
  const setup = await page.evaluate(() => {
    const m = document.getElementById('setup-modal');
    return { hasLaunch: !!m.querySelector('#run'), hasRepo: !!m.querySelector('#repo'),
             hasLlm: !!m.querySelector('#base_url'), hasAgent: !!m.querySelector('#agent'),
             text: m.textContent.replace(/\s+/g, ' ').slice(0, 120) };
  });
  check('Ф10.4 the setup master has no reconstruction launch',
    !setup.hasLaunch && !setup.hasRepo && setup.hasLlm && setup.hasAgent, JSON.stringify(setup).slice(0, 200));
  await page.click('#setup-close');
  await sleep(200);

  // the default agent configured there is used by the Projects run
  await page.click('#setup-open');
  await sleep(250);
  const agentValue = await page.evaluate(() => {
    const select = document.getElementById('agent');
    if (select.options.length > 1) select.value = select.options[1].value;
    return select.value;
  });
  await page.click('#setup-close');
  await sleep(200);
  await page.click('#btab-projects');
  await sleep(400);
  const usedAgent = await page.$eval('#bp_agent', (el) => el.value);
  check('Ф10.4 the default agent is used by the Projects run', usedAgent === agentValue,
    `setup=${agentValue} projects=${usedAgent}`);
  await page.close();
}

async function runChatCommands(browser) {
  // Ф10.5: the chat can start a reconstruction, extend aspects and validate —
  // long/structural commands are proposed first and run on confirmation
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://cc', type: 'System', name: 'cc', parent: null, evidence: ['a'] },
    { id: 'container://cc/c', type: 'Container', name: 'ctr', parent: 'sys://cc', evidence: ['a'] },
    { id: 'component://cc/core', type: 'Component', name: 'Core', parent: 'container://cc/c', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/chatCmd`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph: g }) });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'chatCmd');
  await sleep(500);
  await openAgentPane(page);

  const runCount = async () => ((await (await fetch(`${BASE}/api/runs`)).json()).runs || []).length;
  const send = async (text) => {
    // a chat command may switch the bottom panel to Events: come back to the chat
    const hidden = await page.evaluate(() => document.getElementById('bpane-agent').classList.contains('hidden'));
    if (hidden) { await page.click('#btab-agent'); await sleep(250); }
    await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
    await page.click('#prompt-send');
    await sleep(600);
  };
  const questions = () => page.$$eval('#activity .ev.question', (els) => els.map((e) => e.textContent));

  const before = await runCount();
  await send(`reconstruct ${REPO}`);
  const asked = await questions();
  const afterProposal = await runCount();
  check('Ф10.5 the chat proposes a reconstruction and asks to confirm',
    asked.some((t) => /reply .*да/i.test(t)) && afterProposal === before,
    `questions=${asked.length} runs=${before}->${afterProposal}`);

  await send('да');
  await page.waitForFunction((n) => true, { timeout: 1000 }).catch(() => {});
  await sleep(1200);
  const afterConfirm = await runCount();
  const events = await page.evaluate(() => document.getElementById('log').textContent
    + document.getElementById('activity').textContent);
  check('Ф10.5 confirming in the chat starts the reconstruction',
    afterConfirm > before && /run .* started|Running|Done/i.test(events),
    `runs=${afterConfirm} events=${events.slice(-80)}`);

  // cancelling a proposal must not start anything
  const beforeCancel = await runCount();
  await send(`reconstruct ${REPO}`);
  await send('нет');
  await sleep(600);
  check('Ф10.5 a cancel reply drops the proposal', (await runCount()) === beforeCancel,
    `runs=${await runCount()} (was ${beforeCancel})`);

  // extending aspects is proposed with the aspect list; resuming needs the
  // project's repository, so with no project it is refused clearly instead of
  // starting a run that cannot work (the success path is covered by fix(3))
  const beforeAspects = await runCount();
  await send('add aspect behavior');
  const aspectQuestion = (await questions()).slice(-1)[0] || '';
  await send('да');
  await sleep(1200);
  const aspectEvents = await page.evaluate(() => document.getElementById('activity').textContent);
  check('Ф10.5 the chat proposes an aspect pass and refuses without a repository',
    /behavior/i.test(aspectQuestion) && (await runCount()) === beforeAspects
    && /no repository/.test(aspectEvents),
    `question="${aspectQuestion.slice(0, 60)}" runs=${await runCount()} (was ${beforeAspects}) events=${aspectEvents.slice(-90)}`);

  // validating from the chat reports the formal result
  await send('проверь модель');
  const activity = await page.$eval('#activity', (el) => el.textContent);
  check('Ф10.5 the chat can validate the model', /validation:/.test(activity), activity.slice(-120));
  await page.close();
}

async function runValidationPanel(browser) {
  // Ф10.6: an explicit validation surface — check, agent review, propose, apply
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const g = { nodes: [
    { id: 'sys://vp', type: 'System', name: 'vp', parent: null, evidence: ['a'] },
    { id: 'container://vp/c', type: 'Container', name: 'ctr', parent: 'sys://vp', evidence: ['a'] },
    { id: 'component://vp/a', type: 'Component', name: 'A', parent: 'container://vp/c', evidence: ['a'] },
    { id: 'component://vp/b', type: 'Component', name: 'B', parent: 'container://vp/c', evidence: [] },
  ], edges: [{ source: 'component://vp/a', target: 'component://vp/b', type: 'calls' }],
    constraints: [{ kind: 'forbid_edge', edge_type: 'calls', from: 'Component', to: 'Component' }] };
  const seeded = await fetch(`${BASE}/api/models/valPanel`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: g }) });
  check('Ф10.6 validation fixture model saved', seeded.ok, `put=${seeded.status}`);

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 20000 });
  await openSidebar(page);
  await loadModel(page, 'valPanel');
  await sleep(500);

  await page.click('#btab-validation');
  await sleep(350);
  await page.click('#val-check');
  await sleep(700);
  const checked = await page.evaluate(() => ({
    visible: !document.getElementById('bpane-validation').classList.contains('hidden'),
    summary: document.getElementById('val-summary').textContent,
    history: document.getElementById('val-history').textContent,
  }));
  check('Ф10.6 the Validation tab reports constraints and keeps a history',
    checked.visible && /constraint violations: 1/.test(checked.summary) && /check:/.test(checked.history),
    JSON.stringify(checked).slice(0, 200));

  await page.click('#val-agent');
  await sleep(800);
  const reviewed = await page.$eval('#val-summary', (el) => el.textContent);
  check('Ф10.6 the panel asks the agent to review the model',
    /issue\(s\)/.test(reviewed) && /proposed fix/.test(reviewed), reviewed.slice(0, 180));

  await page.click('#val-apply');
  await sleep(1200);
  const applied = await page.$eval('#val-summary', (el) => el.textContent);
  const saved = await (await fetch(`${BASE}/api/models/valPanel`)).json();
  const appliedHistory = await page.$eval('#val-history', (el) => el.textContent);
  check('Ф10.6 the panel applies fixes as a transaction',
    (saved.edges || []).length === 0 && /apply:/.test(appliedHistory) && /validation:/.test(applied),
    `edges=${(saved.edges || []).length} summary=${applied.slice(0, 80)} history=${appliedHistory.slice(0, 80)}`);

  // the inspector offers the same surface for the current model
  await page.evaluate(() => document.querySelectorAll('#windows .awin').forEach((w) => w.remove()));
  const point = await page.evaluate(() => {
    const groups = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    const node = groups.find((g) => {
      const label = g.querySelector('text.node-label') || g.querySelector('text');
      return label && label.textContent.trim() === 'A';
    });
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  if (point) { await page.mouse.click(point.x, point.y); await sleep(400); }
  await page.click('#insp-validate');
  await sleep(900);
  const fromInspector = await page.evaluate(() => ({
    tab: document.getElementById('btab-validation').classList.contains('active'),
    summary: document.getElementById('val-summary').textContent,
  }));
  check('Ф10.6 the inspector opens the validation panel',
    fromInspector.tab && /validation:/.test(fromInspector.summary), JSON.stringify(fromInspector).slice(0, 160));
  await page.close();
}

async function runExportImport(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = { nodes: [
    { id: 'sys://ei', type: 'System', name: 'ei', parent: null, evidence: ['a'] },
    { id: 'container://ei/c', type: 'Container', name: 'c', parent: 'sys://ei', evidence: ['a'] },
  ], edges: [] };
  await fetch(`${BASE}/api/models/ei`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }) });

  fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  const client = await page.createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DOWNLOAD_DIR });
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  await loadModel(page, 'ei');
  await sleep(400);
  await page.click('#export');
  await sleep(600);
  const files = fs.readdirSync(DOWNLOAD_DIR);
  check('export downloads the graph as a file', files.some((f) => /\.json$/.test(f)), JSON.stringify(files));
  const exportPath0 = path.join(DOWNLOAD_DIR, files.find((f) => /\.json$/.test(f)));

  // exporting while a slice view is active must still write the FULL model
  fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
  await page.$eval('#view_scope', (el) => { el.value = 'container://ei/c'; });
  await page.$eval('#view_dir', (el) => { el.value = 'down'; });
  await page.$eval('#view_depth', (el) => { el.value = '0'; });
  await page.click('#view-apply');
  await sleep(400);
  await page.click('#export');
  await sleep(700);
  const sfiles = fs.readdirSync(DOWNLOAD_DIR).filter((f) => /\.json$/.test(f));
  const dumped = sfiles.length ? JSON.parse(fs.readFileSync(path.join(DOWNLOAD_DIR, sfiles[0]), 'utf8')) : {};
  check('export writes the full model even in slice view',
    (dumped.nodes || []).length === seed.nodes.length, JSON.stringify((dumped.nodes || []).length));
  await page.click('#view-reset');
  await sleep(300);
  const exportPath = exportPath0;

  // import the exported file back under a new name
  const importName = path.join(os.tmpdir(), 'ei-alpha.archide.json');
  fs.copyFileSync(exportPath, importName);
  await page.$eval('#model_load', (el) => { el.value = ''; });
  await (await page.$('#import_file')).uploadFile(importName);
  await sleep(600);
  const loaded = await (await fetch(`${BASE}/api/models/ei-alpha`)).json();
  check('import loads the graph and saves it as a model', (loaded.nodes || []).some((n) => n.id === 'container://ei/c'), JSON.stringify(loaded).slice(0, 120));

  // importing over an existing model name must ask before overwriting
  let dialogMsg = null;
  page.on('dialog', async (d) => { dialogMsg = d.message(); await d.dismiss(); });
  const collideFile = path.join(os.tmpdir(), 'ei.archide.json');
  const collideGraph = JSON.parse(fs.readFileSync(exportPath, 'utf8'));
  collideGraph.nodes.push({ id: 'component://ei/extra', type: 'Component', name: 'extra',
    parent: 'container://ei/c', evidence: ['x'] });
  fs.writeFileSync(collideFile, JSON.stringify(collideGraph));
  await page.$eval('#model_load', (el) => { el.value = ''; });
  await (await page.$('#import_file')).uploadFile(collideFile);
  await sleep(700);
  check('import asks before overwriting an existing model', !!dialogMsg, String(dialogMsg).slice(0, 120));
  const eiAfter = await (await fetch(`${BASE}/api/models/ei`)).json();
  check('dismissed import leaves the existing model intact',
    (eiAfter.nodes || []).some((n) => n.id === 'container://ei/c')
      && !(eiAfter.nodes || []).some((n) => n.id === 'component://ei/extra'),
    JSON.stringify((eiAfter.nodes || []).map((n) => n.id)).slice(0, 140));

  await page.close();
}

async function runLive(browser) {
  await putConfig({
    llm: { mode: 'mock', model: 'test-model' },
    agent: { runner: 'local' },
    context: { compare_docs: false, max_chars: 20000, compress_at: 0.8, strategy: 'extract' },
  });

  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  // wait until the app has initialized (agent list populated)
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  await openSidebar(page);

  const agentTitles = await page.$$eval('#agent option', (els) => els.map((e) => e.title));
  check('Ф1.1 agent manifest limits shown', agentTitles.some((t) => /limits:/.test(t)), JSON.stringify(agentTitles).slice(0, 140));

  // S1 - settings live in the master; the launch lives in the Projects tab (Ф10.4)
  await openSetup(page);
  await page.select('#mode', 'mock');
  await page.select('#runner', 'local');
  await page.click('#setup-close');
  await sleep(250);

  // S2 - start the reconstruction from the bottom Projects tab
  await page.click('#btab-projects');
  await sleep(300);
  await page.$eval('#bp_repo', (el, v) => { el.value = v; }, REPO);
  await page.click('#bp_run');
  await page.click('#btab-events');
  await sleep(300);
  try {
    await page.waitForFunction(
      () => /done|failed|error|cancelled/i.test(document.getElementById('status').textContent),
      { timeout: 60000 },
    );
  } catch (_) {
    const s = await page.$eval('#status', (el) => el.textContent);
    const l = await page.$eval('#log', (el) => el.textContent);
    console.log(`DEBUG status="${s}" log="${l.slice(0, 200)}"`);
  }
  const statusText = await page.$eval('#status', (el) => el.textContent);

  const nodes = await nodeCount(page);
  const validation = await page.$eval('#validation', (el) => el.textContent);
  const events = await page.$$eval('#activity .ev', (els) => els.length);

  check('S2 run finished in UI', /Done/.test(statusText), statusText);
  check('S2 graph rendered in UI', nodes >= 1, `nodes=${nodes - 1}`);
  check('S8 validation panel reports a valid model', /nodes:/.test(validation) && !validation.includes('✕'), validation.slice(0, 80));
  check('S5 activity feed has events', events > 0, `events=${events}`);
  check('S7 context report present', /context:/i.test(await page.$eval('#log', (el) => el.textContent)));
  check('Ф2.1 model hash shown in log', /model:/i.test(await page.$eval('#log', (el) => el.textContent)));
  check('Ф2.4 coverage shown in validation', /coverage:/i.test(await page.$eval('#validation', (el) => el.textContent)));

  await page.close();
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--no-proxy-server'],
  });
  try {
    console.log(`Browser e2e against ${BASE}`);
    await runDemo(browser);
    await runAspects(browser);
    await runViews(browser);
    await runDocs(browser);
    await runComm(browser);
    await runPrompt(browser);
    await runPreview(browser);
    await runVisualEdit(browser);
    await runChatEdit(browser);
    await runGenerate(browser);
    await runSliceView(browser);
    await runExportImport(browser);
    await runAspectViews(browser);
    await runSequenceView(browser);
    await runNotations(browser);
    await runWindowsNav(browser);
    await runArchitectPanel(browser);
    await runLayout(browser);
    await runInspector(browser);
    await runLens(browser);
    await runAgentPanel(browser);
    await runPalette(browser);
    await runPolish(browser);
    await runProjects(browser);
    await runTemplates(browser);
    await runWalkthrough(browser);
    await runReviewFixes(browser);
    await runC4Notation(browser);
    await runCanvasLayout(browser);
    await runSynthesis(browser);
    await runRagAnswer(browser);
    await runDialogueStore(browser);
    await runBottomPanel(browser);
    await runChatCommands(browser);
    await runValidationPanel(browser);
    await runNavLayers(browser);
    await runLive(browser);
  } finally {
    await browser.close();
  }
  const failed = results.filter(([, ok]) => !ok).length;
  console.log(`\nBrowser: ${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
