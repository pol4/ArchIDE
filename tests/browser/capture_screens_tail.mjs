// Slow screenshots for ARCHITECT_GUIDE_UI.md: live code generation and the
// acceptance diff (they need a real LLM call, so they live apart).
import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const BASE = process.env.ARCHIDE_URL || 'http://127.0.0.1:8200';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = process.env.OUT_DIR || path.resolve(HERE, '../../docs/screens');
const CHROME = process.env.CHROME || '/usr/bin/google-chrome';
const W = 1440, H = 900;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const AREA = { full: undefined, sidebar: { x: 0, y: 0, width: 344, height: H }, stage: { x: 344, y: 0, width: W - 344, height: H } };

fs.mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
  protocolTimeout: 900000, args: ['--no-sandbox', '--hide-scrollbars', '--no-proxy-server'] });
const page = await browser.newPage();
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
await page.setViewport({ width: W, height: H });
await page.goto(BASE + '/', { waitUntil: 'load' });
await page.waitForFunction(() => document.querySelector('main.layout')?.dataset.wired === '1', { timeout: 25000 });

const shot = async (name, area = 'sidebar') => {
  await sleep(350);
  await page.screenshot({ path: path.join(OUT, name + '.png'), clip: AREA[area] });
  console.log('shot', name, new Date().toISOString());
};
const click = async (selector) => {
  await page.evaluate((sel) => { const el = document.querySelector(sel); if (el) el.click(); }, selector);
  await sleep(300);
};
const showSidebar = async () => {
  const hidden = await page.evaluate(() => document.querySelector('main.layout').classList.contains('sidebar-hidden'));
  if (hidden) { await page.click('#sidebar-toggle'); await sleep(350); }
};

await showSidebar();
await page.evaluate(() => document.getElementById('arch_model').scrollIntoView({ block: 'start' }));
await page.$eval('#arch_model', (el) => { el.value = 'TripApproval'; });
await click('#arch-load');
await page.$eval('#arch_node', (el) => { el.value = 'component://trip/api'; });
await page.$eval('#arch_target', (el) => { el.value = 'triprepo'; });

// 14 - live code generation by the external agent
await click('#arch-codegen');
await page.waitForFunction(() => /"repo"|failed/.test(document.getElementById('arch-out').textContent), { timeout: 600000 });
await sleep(700);
await shot('14-codegen');

// 15 - acceptance: the delivered repository against the target model
await click('#arch-diff');
await page.waitForFunction(() => /"verdict"|failed/.test(document.getElementById('arch-out').textContent), { timeout: 90000 });
await sleep(700);
await shot('15-acceptance-diff');

console.log('tail done');
await browser.close();
