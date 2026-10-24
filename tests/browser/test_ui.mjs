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

async function putConfig(patch) {
  await fetch(`${BASE}/api/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}
const nodeCount = (page) => page.$$eval('#canvas > g > g', (els) => els.length);
const crumbCount = (page) => page.$$eval('#breadcrumb button', (els) => els.length);
const activeChips = (page) => page.$$eval('#legend .chip.active', (els) => els.map((e) => e.textContent.trim()));
const emptyShown = (page) => page.$eval('#empty', (el) => getComputedStyle(el).display !== 'none');

async function clickChip(page, text) {
  await page.evaluate((t) => {
    const chip = [...document.querySelectorAll('#legend .chip')].find((c) => c.textContent.trim() === t);
    if (chip) chip.click();
  }, text);
  await sleep(150);
}

const clickCommTab = (page, re) => page.evaluate((r) => {
  const tab = [...document.querySelectorAll('#comm .tab')].find((t) => new RegExp(r, 'i').test(t.textContent));
  if (tab) tab.click();
}, re);

async function clickNode(page, text) {
  const box = await page.evaluate((t) => {
    const gs = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    const node = gs.find((g) => g.textContent.includes(t));
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, text);
  if (box) await page.mouse.click(box.x, box.y);
  await sleep(200);
}

async function runDemo(browser) {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=1`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  const n0 = await nodeCount(page);
  check('S10 demo renders graph nodes', n0 > 1, `nodes=${n0 - 1}`);
  check('S4 breadcrumb shown', (await crumbCount(page)) >= 1);
  check('S3 source filter buttons present', (await page.$('#src-code')) !== null && (await page.$('#src-docs')) !== null);

  // S4 drill-down: real mouse click on a node that has children (marked with ⊕)
  const box = await page.evaluate(() => {
    const gs = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    const target = gs.find((g) => g.textContent.includes('⊕'));
    if (!target) return null;
    const r = target.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  let drilled = false;
  if (box) { await page.mouse.click(box.x, box.y); drilled = true; }
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
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=aspects`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  const chips = await page.$$eval('#legend .chip', (els) => els.length);
  check('Ф2.5 aspect chips present', chips >= 8, `chips=${chips}`);

  let active = await activeChips(page);
  check('Ф2.5 default aspect = structure', active.length === 1 && active[0] === 'structure', active.join(','));

  await clickChip(page, 'structure');
  active = await activeChips(page);
  check('Ф2.5 structure is locked (for navigation)', active.includes('structure'));

  await clickNode(page, 'API');
  const n1 = await nodeCount(page);
  check('Ф2.5 drill shows the structural child', n1 >= 1, `nodes=${n1 - 1}`);

  await clickChip(page, 'aggregation');
  const n2 = await nodeCount(page);
  active = await activeChips(page);
  check('Ф2.6 multi-aspect adds elements simultaneously',
    n2 > n1 && active.includes('aggregation') && active.includes('structure'),
    `nodes ${n1 - 1}->${n2 - 1}; active=${active.join(',')}`);

  await clickNode(page, 'Service');
  check('Ф2.5 empty hint when no elements of the selected aspect', await emptyShown(page));

  await clickChip(page, 'interface');
  const n3 = await nodeCount(page);
  await clickChip(page, 'data');
  const n4 = await nodeCount(page);
  active = await activeChips(page);
  check('Ф2.6 several aspects visible at once',
    n3 >= 1 && n4 > n3 && active.includes('interface') && active.includes('data'),
    `nodes ${n3 - 1}->${n4 - 1}; active=${active.join(',')}`);

  await page.close();
}

async function runViews(browser) {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=aspects`, { waitUntil: 'load' });
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

async function runSplit(browser) {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=aspects`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#canvas > g > g').length > 0, { timeout: 15000 });

  await clickNode(page, 'API');
  await page.select('#aspect-b', 'aggregation');
  await page.click('#split');
  await sleep(300);
  const bVisible = await page.$eval('#canvas2', (el) => getComputedStyle(el).display !== 'none');
  const aNodes = await page.$$eval('#canvas > g > g', (els) => els.length);
  const bNodes = await page.$$eval('#canvas2 > g > g', (els) => els.length);
  check('Ф2.6b split shows two panes', bVisible && aNodes >= 1, `a=${aNodes} b=${bNodes}`);
  check('Ф2.6b pane B shows its own aspect', bNodes >= 1, `b=${bNodes}`);

  await page.click('#split');
  await sleep(200);
  const bHidden = await page.$eval('#canvas2', (el) => getComputedStyle(el).display === 'none');
  check('Ф2.6b split toggles off', bHidden);

  await page.close();
}

async function runDocs(browser) {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

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
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

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

  const h0 = await page.$eval('#comm', (el) => el.getBoundingClientRect().height);
  const box = await page.$eval('#comm-resize', (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
  await page.mouse.move(box.x, box.y); await page.mouse.down();
  await page.mouse.move(box.x, box.y - 60, { steps: 5 }); await page.mouse.up();
  await sleep(200);
  const h1 = await page.$eval('#comm', (el) => el.getBoundingClientRect().height);
  check('comm window top border is resizable', h1 > h0 + 20, `h0=${Math.round(h0)} h1=${Math.round(h1)}`);

  await page.close();
}

async function runPrompt(browser) {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

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
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

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

async function runEdit(browser) {
  await putConfig({ llm: { mode: 'mock' }, agent: { runner: 'local' }, context: { compare_docs: false } });
  const seed = {
    nodes: [
      { id: 'sys://ui', type: 'System', parent: null, evidence: ['a'] },
      { id: 'container://ui/c', type: 'Container', parent: 'sys://ui', evidence: ['a'] },
    ],
    edges: [],
  };
  await fetch(`${BASE}/api/models/uitest`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ graph: seed }),
  });

  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

  await page.$eval('#edit_model', (el) => { el.value = 'uitest'; });
  await page.$eval('#edit_node_id', (el) => { el.value = 'component://ui/new'; });
  await page.$eval('#edit_node_type', (el) => { el.value = 'Component'; });
  await page.$eval('#edit_node_parent', (el) => { el.value = 'container://ui/c'; });
  await page.click('#edit-add');
  await sleep(400);
  const info = await page.$eval('#edit-info', (el) => el.textContent);
  check('Ф4.6 add node via UI', /ok/.test(info) && /nodes:\s*3/.test(info), info);

  await page.$eval('#edit_remove_id', (el) => { el.value = 'component://ui/new'; });
  await page.click('#edit-remove');
  await sleep(400);
  const info2 = await page.$eval('#edit-info', (el) => el.textContent);
  check('Ф4.6 remove node via UI', /ok/.test(info2) && /nodes:\s*2/.test(info2), info2);

  await fetch(`${BASE}/api/metamodel/aspects`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'compliance', node_types: ['Policy', 'Control'], edge_types: ['governs'] }),
  });
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });
  const chips = await page.$$eval('#legend .chip', (els) => els.map((e) => e.textContent.trim()));
  check('Ф4.8 custom aspect appears in UI', chips.includes('compliance'), JSON.stringify(chips));

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
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

  const send = async (text) => {
    await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
    await page.click('#prompt-send');
    await sleep(500);
  };

  await page.$eval('#edit_model', (el) => { el.value = 'chatedit'; });
  await send('add Component component://ce/new under container://ce/c');
  const log = await page.$eval('#activity', (el) => el.textContent);
  check('Ф4.7 chat applies an edit intent', /applied/.test(log), log.slice(-160));
  const stored = await (await fetch(`${BASE}/api/models/chatedit`)).json();
  check('Ф4.7 edit persisted via chat', stored.nodes.some((n) => n.id === 'component://ce/new'), 'node not stored');

  await page.$eval('#edit_model', (el) => { el.value = ''; });
  await send('add Component component://ce/orphan');
  const log2 = await page.$eval('#activity', (el) => el.textContent);
  check('Ф4.7 agent asks a question when context is missing', (await page.$$eval('#activity .ev.question', (els) => els.length)) >= 1, log2.slice(-160));

  await page.close();
}

async function runLive(browser) {
  await putConfig({
    llm: { mode: 'mock', model: 'test-model' },
    agent: { runner: 'local' },
    context: { compare_docs: false, max_chars: 20000, compress_at: 0.8, strategy: 'extract' },
  });

  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/`, { waitUntil: 'load' });
  // wait until the app has initialized (agent list populated)
  await page.waitForFunction(() => document.querySelectorAll('#agent option').length > 0, { timeout: 15000 });

  const agentTitles = await page.$$eval('#agent option', (els) => els.map((e) => e.title));
  check('Ф1.1 agent manifest limits shown', agentTitles.some((t) => /limits:/.test(t)), JSON.stringify(agentTitles).slice(0, 140));

  // S1 - fill settings like a user
  await page.$eval('#repo', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, REPO);
  await page.select('#mode', 'mock');
  await page.select('#runner', 'local');

  // S2 - run reconstruction
  await page.click('#run');
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
    await runSplit(browser);
    await runDocs(browser);
    await runComm(browser);
    await runPrompt(browser);
    await runPreview(browser);
    await runEdit(browser);
    await runChatEdit(browser);
    await runLive(browser);
  } finally {
    await browser.close();
  }
  const failed = results.filter(([, ok]) => !ok).length;
  console.log(`\nBrowser: ${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
