// Screenshots of the EXAMPLE project (ApprovalHub) for docs/EXAMPLE_GUIDE.md.
//   ARCHIDE_URL=http://127.0.0.1:8000 node capture_example.mjs
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', '..', 'docs', 'screens');
const BASE = process.env.ARCHIDE_URL || 'http://127.0.0.1:8000';
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const MODEL = process.env.MODEL || 'ApprovalHub';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
  args: ['--no-sandbox', '--no-proxy-server', '--disable-dev-shm-usage'] });
const page = await browser.newPage();
await page.setCacheEnabled(false);
await page.setViewport({ width: 1680, height: 1000 });
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));

const shot = async (name) => { await sleep(500); await page.screenshot({ path: path.join(OUT, name + '.png') }); console.log('shot', name); };
const shotEl = async (name, sel) => {
  await sleep(500);
  const el = await page.$(sel);
  if (!el) { console.log('missing element', name, sel); return; }
  await el.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('shot', name);
};

await page.goto(`${BASE}/`, { waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 30000 });
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(400);
await page.$eval('#model_load', (el, v) => { el.value = v; }, MODEL);
await page.click('#model-load');
await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 1, { timeout: 30000 });
await sleep(1200);

// C4 (все уровни): раскрываем дерево и показываем схему целиком
await shot('26-example-c4');

// дерево сущностей — видно четыре уровня C4
await shotEl('27-example-tree', '#c4-tree');

// аспект данных по выбранному элементу
await page.evaluate(() => { const b = document.querySelector('#canvas-tabs .ctab[data-view="data"]'); if (b) b.click(); });
await sleep(1200);
await shot('28-example-aspect');

// диаграммы всех аспектов: подзакладка показывает свой вид для выбранного элемента
const ASPECT_SHOTS = [
  ['30-example-states', 'behavior'],
  ['31-example-process', 'process'],
  ['32-example-data', 'data'],
  ['33-example-interfaces', 'interface'],
  ['34-example-deployment', 'deployment'],
  ['35-example-access', 'access'],
  ['36-example-quality', 'quality'],
];
// выбираем эле��ент с наибольшим числом аспектов (обычно контейнер сервиса)
await page.evaluate(() => {
  const nodes = [...document.querySelectorAll('#canvas > g > g')].filter((g) => g.querySelector('rect.node-box'));
  if (nodes.length) nodes[0].querySelector('rect.node-box').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
});
await sleep(600);
for (const [name, view] of ASPECT_SHOTS) {
  const ok = await page.evaluate((v) => {
    const b = document.querySelector(`#canvas-tabs .ctab[data-view="${v}"]`);
    if (!b) return false;
    b.click();
    return true;
  }, view);
  if (!ok) { console.log('warning: нет подзакладки', view); continue; }
  await sleep(1100);
  await shot(name);
}
await page.evaluate(() => { const b = document.querySelector('#canvas-tabs .ctab[data-view="c4"]'); if (b) b.click(); });
await sleep(600);

// отчёт по модели
await page.evaluate(() => document.getElementById('report-open').click());
await page.waitForFunction(() => (document.getElementById('report-body').textContent || '').length > 40, { timeout: 25000 }).catch(() => {});
await shot('29-example-report');

console.log('done; screenshots in', OUT);
await browser.close();
