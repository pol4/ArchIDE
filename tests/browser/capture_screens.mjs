// Captures the screenshots used by docs/ARCHITECT_GUIDE_UI.md (graph-first shell).
//
//   cd tests/browser && ARCHIDE_URL=http://127.0.0.1:8200 node capture_screens.mjs
//
// Output: docs/screens/*.png  (slow steps live in capture_screens_tail.mjs)
import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const BASE = process.env.ARCHIDE_URL || 'http://127.0.0.1:8200';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = process.env.OUT_DIR || path.resolve(HERE, '../../docs/screens');
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const SAMPLE = process.env.SAMPLE_REPO || path.resolve(HERE, '../fixtures/sample_repo');
const MODEL = process.env.MODEL || 'ArchIDE';

const W = 1440, H = 900;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const AREA = {
  full: undefined,
  sidebar: { x: 0, y: 0, width: 344, height: H },
  stage: { x: 344, y: 0, width: W - 344, height: H },
  column: { x: W - 364, y: 0, width: 364, height: H },
};

fs.mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
  protocolTimeout: 900000, args: ['--no-sandbox', '--hide-scrollbars', '--no-proxy-server'] });
const page = await browser.newPage();
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
await page.setViewport({ width: W, height: H });
await page.goto(BASE + '/', { waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 25000 });
await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
await page.reload({ waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 25000 });

const click = async (selector) => {
  const ok = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    el.click();
    return true;
  }, selector);
  await sleep(300);
  if (!ok) console.log('missing selector', selector);
};

const shot = async (name, area = 'full') => {
  await sleep(350);
  await page.screenshot({ path: path.join(OUT, name + '.png'), clip: AREA[area] });
  console.log('shot', name);
};
const shotEl = async (name, selector) => {
  await sleep(350);
  const el = await page.$(selector);
  if (!el) { console.log('missing element for shot', name, selector); return; }
  await el.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('shot', name);
};
const showSidebar = async () => {
  const hidden = await page.evaluate(() => document.querySelector('main.layout').classList.contains('sidebar-hidden'));
  if (hidden) { await page.click('#sidebar-toggle'); await sleep(350); }
};
const hideSidebar = async () => {
  const hidden = await page.evaluate(() => document.querySelector('main.layout').classList.contains('sidebar-hidden'));
  if (!hidden) { await page.click('#sidebar-toggle'); await sleep(350); }
};
const loadModel = async (name) => {
  await showSidebar();
  // the model controls live in the project pane now
  await page.evaluate(() => document.getElementById('btab-projects').click());
  await sleep(250);
  await page.$eval('#model_load', (el, v) => { el.value = v; }, name);
  await click('#model-load');
  await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 0, { timeout: 25000 });
  await sleep(600);
};
const clickNode = async (label) => {
  const point = await page.evaluate((t) => {
    const groups = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
    const node = groups.find((g) => {
      const l = g.querySelector('text.node-label') || g.querySelector('text');
      return l && l.textContent.trim() === t;
    });
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, label);
  if (point) { await page.mouse.click(point.x, point.y); await sleep(400); }
  return !!point;
};

// 01 - the graph-first shell with a reconstructed model
await loadModel(MODEL);
await hideSidebar();
await page.evaluate(() => { const d = document.getElementById('events-drawer'); if (d) d.classList.remove('open'); });
await sleep(300);
await shot('01-overview', 'full');

// 02 - element inspector: properties, aspect chips, actions
await showSidebar();
await loadModel(MODEL);
await hideSidebar();
await clickNode('Run Lifecycle') || await clickNode('Runner');
await shot('02-inspector', 'column');

// 03 - aspect lens (the whole canvas in one aspect's notation).
// select an element that really carries the `behavior` aspect: node search
// auto-enables the aspect and focuses the node (Ф2.13)
// the reconstructed `ArchIDE` model carries no behaviour elements, so the lens
// shots use `TripApproval`, which has a state machine (restored right after)
const LENS_MODEL = process.env.LENS_MODEL || 'TripApproval';
await loadModel(LENS_MODEL);
const behaviorName = await page.evaluate(async () => {
  const name = document.getElementById('model_load').value;
  const model = await (await fetch(`/api/models/${encodeURIComponent(name)}`)).json();
  const nodes = model.nodes || [];
  const pick = nodes.find((n) => n.type === 'StateMachine') || nodes.find((n) => n.type === 'State');
  return pick ? pick.name : '';
});
if (behaviorName) {
  // search reveals the element (its aspect is enabled), then a real canvas click
  // selects it and fills the inspector — that is what opens the aspect chips
  await page.$eval('#node-search', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, behaviorName);
  await click('#node-find');
  await sleep(900);
  await clickNode(behaviorName);
  await sleep(600);
}
const chipClicked = await page.evaluate(() => {
  const chip = [...document.querySelectorAll('#insp-aspects .chip')].find((c) => c.dataset.aspect === 'behavior');
  if (chip) chip.click();
  return !!chip;
});
if (!chipClicked) console.log('warning: no behavior aspect chip for', behaviorName);
await sleep(800);
await shot('03-lens-state', 'stage');
await page.select('#lens-mode', 'graph');
await sleep(600);
await shot('04-lens-graph', 'stage');
await click('#lens-back');
await sleep(400);
await loadModel(MODEL);
await hideSidebar();

// 05 - command palette
await page.keyboard.down('Control'); await page.keyboard.press('k'); await page.keyboard.up('Control');
await sleep(400);
await shot('05-palette', 'full');
await page.keyboard.press('Escape');
await sleep(300);

// 06 - the agent chat in the bottom pane, with the selection context
await click('#btab-agent');
await sleep(700);
await shotEl('06-agent-context', '#events-drawer');
await click('#btab-events');
await sleep(300);
await click('#insp-close');
await sleep(300);

// 07 - report view (validation + metrics + coverage)
await click('#report-open');
await page.waitForFunction(() => /validation:/.test(document.getElementById('report-body').textContent), { timeout: 25000 }).catch(() => {});
await sleep(600);
await shot('07-report', 'full');
await click('#report-close');
await sleep(300);

// 08 - setup master (repository, LLM, context, storage)
await click('#setup-open');
await sleep(400);
await shot('08-setup', 'full');
await click('#setup-close');
await sleep(300);

// 09 - panels on demand (architect scenario operations)
await showSidebar();
await page.evaluate(() => document.getElementById('arch_model').scrollIntoView({ block: 'start' }));
await page.$eval('#arch_model', (el, v) => { el.value = v; }, 'TripApproval');
await click('#arch-load');
await sleep(700);
await page.$eval('#arch_node', (el) => { el.value = 'component://trip/api'; });
await click('#arch-workpackage');
await sleep(900);
await shot('09-panels', 'sidebar');

// 10 - whole C4 level
await hideSidebar();
await page.select('#level-jump', '2');
await sleep(700);
await shot('10-level', 'stage');
await page.select('#level-jump', '');
await sleep(500);

// 11 - node search
await page.$eval('#node-search', (el) => { el.value = 'Trip'; });
await click('#node-find');
await sleep(600);
await shot('11-search', 'stage');

// 12 - light theme
await click('#theme-toggle');
await sleep(500);
await shot('12-theme-light', 'full');
await click('#theme-toggle');
await sleep(300);

// 13 - reconstruction runs from the Projects pane (mock profile: no LLM cost)
await click('#setup-open');
await sleep(300);
await page.$eval('#mode', (el) => { el.value = 'mock'; });
await page.$eval('#runner', (el) => { el.value = 'local'; });
await page.$eval('#compare_docs', (el) => { el.checked = false; });
await click('#setup-close');
await sleep(300);
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(400);
await page.$eval('#bp_repo', (el, v) => { el.value = v; }, SAMPLE);
await page.evaluate(() => document.getElementById('bp_run').click());
await page.waitForFunction(() => /Done|Invalid|Error/.test(document.getElementById('status').textContent), { timeout: 180000 });
await sleep(600);
await shotEl('13-reconstruct', '#events-drawer');

// 16 - the Projects pane (projects own the graph, the runs and the chat)
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(500);
await shotEl('16-projects', '#events-drawer');

// 17 - a new model from a template (scaffolding, no LLM)
await click('#bp_tpl');
await sleep(600);
await shot('17-template', 'full');
await click('#tpl-close');
await sleep(300);

// 19 - the collapsible C4 entity tree next to the canvas
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(300);
await shot('19-tree', 'full');

// 20 - an aspect sub-tab: the canvas shows one aspect of the selected element
await page.evaluate(() => document.getElementById('c4-tree').classList.remove('hidden'));
await sleep(200);
await page.evaluate(() => {
  const btn = document.querySelector('#canvas-tabs .ctab[data-view="data"]');
  if (btn) btn.click();
});
await sleep(900);
await shot('20-views', 'full');
await page.evaluate(() => {
  const btn = document.querySelector('#canvas-tabs .ctab[data-view="c4"]');
  if (btn) btn.click();
});
await sleep(600);

// 21 - an automatic arrangement of the canvas
await page.select('#layout-mode', 'grid');
await sleep(200);
await click('#layout-auto');
await sleep(1200);
await shot('21-layout', 'full');

// 22-23 - synthesis: describe the system in the chat, get the architecture
await page.evaluate(() => document.getElementById('btab-agent').click());
await sleep(400);
const chatSend = async (text) => {
  await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
  await page.click('#prompt-send');
  await sleep(700);
};
await chatSend('design Сервис учёта заявок: сотрудник создаёт заявку, руководитель согласует её, '
             + 'бухгалтерия бронирует поездку; роли: сотрудник, руководитель, бухгалтерия; '
             + 'данные: заявка, сотрудник, согласование; внешняя система: 1С');
await sleep(500);
await shotEl('22-synthesis-chat', '#events-drawer');
await chatSend('да');
for (let i = 0; i < 40; i++) {
  await sleep(700);
  const st = await page.evaluate(() => document.getElementById('status').textContent);
  if (/Done|Invalid|Failed/.test(st)) break;
}
await sleep(1200);
await shot('23-synthesis-graph', 'full');
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(400);
await shotEl('24-documentation', '#events-drawer');

// 18 - the Validation pane (formal check + agent review)
await page.evaluate(() => document.getElementById('btab-validation').click());
await sleep(300);
await page.evaluate(() => document.getElementById('val-check').click());
await sleep(1200);
await shotEl('18-validation', '#events-drawer');

// restore the live profile through the UI
await click('#setup-open');
await sleep(300);
await page.$eval('#mode', (el) => { el.value = 'live'; });
await page.$eval('#runner', (el) => { el.value = 'docker'; });
await click('#setup-close');
await sleep(500);
console.log('done; screenshots in', OUT);
await browser.close();
