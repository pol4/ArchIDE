// Screenshots for the synthesis scenario (Ф11) — taken against a REAL run.
//
// Unlike `capture_screens.mjs` this script never touches the LLM mode: it expects
// the model produced by a live synthesis run to already exist, and only drives the
// UI (chat, canvas, project pane) to capture it.
//
//   ARCHIDE_URL=http://127.0.0.1:8000 MODEL=TripApproval node capture_synthesis.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', '..', 'docs', 'screens');
const BASE = process.env.ARCHIDE_URL || 'http://127.0.0.1:8000';
const MODEL = process.env.MODEL || 'TripApproval';
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DESCRIPTION = process.env.DESCRIPTION
  || 'Нужна система согласования командировок. Сотрудник создаёт заявку на командировку '
   + '(город, даты, цель, оценка стоимости) и отправляет её на согласование. Руководитель '
   + 'согласует или отклоняет с комментарием. После согласования бухгалтерия бронирует билеты '
   + 'и гостиницу и фиксирует фактические расходы.';

fs.mkdirSync(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--no-proxy-server', '--disable-dev-shm-usage'],
});
const page = await browser.newPage();
await page.setCacheEnabled(false);
await page.setViewport({ width: 1680, height: 1000 });
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));

const shot = async (name) => {
  await sleep(400);
  await page.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('shot', name);
};
const shotEl = async (name, selector) => {
  await sleep(400);
  const el = await page.$(selector);
  if (!el) { console.log('missing element for shot', name, selector); return; }
  await el.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('shot', name);
};
const send = async (text) => {
  const hidden = await page.evaluate(() => document.getElementById('bpane-agent').classList.contains('hidden'));
  if (hidden) { await page.click('#btab-agent'); await sleep(300); }
  await page.$eval('#prompt_input', (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, text);
  await page.click('#prompt-send');
  await sleep(800);
};

await page.goto(`${BASE}/`, { waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 30000 });
await sleep(800);

// 22 — the task statement in the chat, with the agent asking to confirm
await page.evaluate(() => document.getElementById('btab-agent').click());
await sleep(400);
await send('design ' + DESCRIPTION);
await sleep(600);
await shotEl('22-synthesis-chat', '#events-drawer');

// 23 — the architecture the model designed from it
await page.evaluate(() => document.getElementById('btab-projects').click());
await sleep(300);
await page.$eval('#model_load', (el, v) => { el.value = v; }, MODEL);
await page.click('#model-load');
await page.waitForFunction(() => document.querySelectorAll('#canvas rect.node-box').length > 1, { timeout: 40000 });
await sleep(1200);
await shot('23-synthesis-graph');

// 24 — the project pane with the documentation / export actions
await shotEl('24-documentation', '#events-drawer');

// 25 — an aspect sub-tab over the same synthesised model
await page.evaluate(() => {
  const btn = document.querySelector('#canvas-tabs .ctab[data-view="data"]');
  if (btn) btn.click();
});
await sleep(1200);
await shot('25-synthesis-aspect');

console.log('done; screenshots in', OUT);
await browser.close();
