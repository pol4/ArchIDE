// ArchIDE browser-level e2e (headless Chrome via puppeteer-core).
//
// Emulates real user actions in the UI. Covers scenarios S1-S5, S7, S8, S10.
// The server is started by tests/run_e2e.sh and exposed via ARCHIDE_TEST_URL.
import puppeteer from 'puppeteer-core';
import path from 'path';
import { existsSync } from 'fs';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

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
const nodeCount = (page) => page.$$eval('#canvas g', (els) => els.length);
const crumbCount = (page) => page.$$eval('#breadcrumb button', (els) => els.length);

async function runDemo(browser) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1000 });
  await page.goto(`${BASE}/?demo=1`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelectorAll('#canvas g').length > 1, { timeout: 15000 });

  const n0 = await nodeCount(page);
  check('S10 demo renders graph nodes', n0 > 1, `nodes=${n0 - 1}`);
  check('S4 breadcrumb shown', (await crumbCount(page)) >= 1);
  check('S3 source filter buttons present', (await page.$('#src-code')) !== null && (await page.$('#src-docs')) !== null);

  // S4 drill-down: real mouse click on a node that has children (marked with ⊕)
  const box = await page.evaluate(() => {
    const gs = [...document.querySelectorAll('#canvas g')].filter((g) => g.querySelector('rect.node-box'));
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

async function runLive(browser) {
  await putConfig({
    llm: { mode: 'mock', model: 'test-model' },
    agent: { runner: 'local' },
    context: { compare_docs: false, max_chars: 20000, compress_at: 0.8, strategy: 'extract' },
  });

  const page = await browser.newPage();
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
  check('S2 graph rendered in UI', nodes > 1, `nodes=${nodes - 1}`);
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
    await runLive(browser);
  } finally {
    await browser.close();
  }
  const failed = results.filter(([, ok]) => !ok).length;
  console.log(`\nBrowser: ${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
