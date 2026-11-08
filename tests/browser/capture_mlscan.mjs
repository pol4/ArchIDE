// Screenshots of the MLScan reconstruction for the documentation comparison.
//   ARCHIDE_URL=http://127.0.0.1:8000 node capture_mlscan.mjs
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', '..', 'docs', 'screens');
const BASE = process.env.ARCHIDE_URL || 'http://127.0.0.1:8000';
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const MODEL = process.env.MODEL || 'MLScan';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
  args: ['--no-sandbox', '--no-proxy-server', '--disable-dev-shm-usage'] });
const page = await browser.newPage();
await page.setCacheEnabled(false);
await page.setViewport({ width: 1680, height: 1000 });
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));

const shot = async (n) => { await sleep(600); await page.screenshot({ path: path.join(OUT, n + '.png') }); console.log('shot', n); };
const shotEl = async (n, sel) => {
  await sleep(600);
  const el = await page.$(sel);
  if (!el) { console.log('missing', n, sel); return; }
  await el.screenshot({ path: path.join(OUT, n + '.png') });
  console.log('shot', n);
};

await page.goto(`${BASE}/`, { waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 30000 });
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(400);
await page.$eval('#model_load', (el, v) => { el.value = v; }, MODEL);
await page.click('#model-load');
await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 1, { timeout: 40000 });
await sleep(1500);
await shot('38-mlscan-c4');

// дерево сущностей: плоскости и их сервисы
await shotEl('39-mlscan-tree', '#c4-tree');

// группировка по узлам развёртывания
const ok = await page.evaluate(() => {
  const sel = document.getElementById('group-mode');
  if (!sel) return false;
  sel.value = 'deployment';
  sel.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
});
if (ok) { await sleep(1400); await shot('40-mlscan-deployment'); }

// аспект: интерфейсы (в документации это слой API)
await page.evaluate(() => {
  const sel = document.getElementById('group-mode');
  if (sel) { sel.value = 'containers'; sel.dispatchEvent(new Event('change', { bubbles: true })); }
  const b = document.querySelector('#canvas-tabs .ctab[data-view="interface"]');
  if (b) b.click();
});
await sleep(1400);
await shot('41-mlscan-interfaces');

console.log('done');
await browser.close();
