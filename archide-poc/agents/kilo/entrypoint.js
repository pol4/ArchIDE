// Kilo Code CLI sandbox entrypoint.
//
// 1. downloads the repo tarball, prompt and constraints from the ArchIDE sandbox,
// 2. points the Kilo CLI at the ArchIDE LLM gateway (its own token; the real key
//    stays on the host),
// 3. runs `kilo run ... --auto --format json` in the repo, streaming activity
//    events (reasoning / output / logs) back to the host,
// 4. uploads the reconstructed graph back.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const base = (process.env.ARCHIDE_SANDBOX_BASE || '').replace(/\/+$/, '');
const job = process.env.ARCHIDE_JOB_ID;
const token = process.env.ARCHIDE_JOB_TOKEN;
const output = process.env.ARCHIDE_OUTPUT || '/work/archide/graph.json';

const dataDir = process.env.ARCHIDE_DATA || '';
const agentDir = process.env.ARCHIDE_AGENT_DIR || '';
const sharedDir = process.env.ARCHIDE_SHARED_DIR || '';

// Use the shared volume for caches/sessions so they persist across runs and are
// shared with the other agents.
function prepareStorage() {
  [dataDir, agentDir, sharedDir].forEach((d) => { if (d) fs.mkdirSync(d, { recursive: true }); });
  if (dataDir) {
    process.env.npm_config_cache = path.join(dataDir, 'npm-cache');
    process.env.XDG_DATA_HOME = path.join(dataDir, 'xdg-data');
    process.env.XDG_CACHE_HOME = path.join(dataDir, 'xdg-cache');
    return postEvent('status', `shared storage ready at ${dataDir}`);
  }
  return Promise.resolve();
}

function saveArtifact(graph, repoName) {
  if (!agentDir) return;
  try {
    const dir = path.join(agentDir, 'graphs');
    fs.mkdirSync(dir, { recursive: true });
    const safe = String(repoName || 'repo').replace(/[^a-zA-Z0-9._-]+/g, '-');
    const target = path.join(dir, `${safe}.json`);
    fs.writeFileSync(target, JSON.stringify(graph));
    postEvent('log', `saved graph to shared storage: ${target}`);
  } catch (err) {
    postEvent('log', `could not save graph to shared storage: ${err.message}`);
  }
}

async function get(pathname) {
  const res = await fetch(`${base}${pathname}?token=${token}`);
  if (!res.ok) throw new Error(`${pathname} -> HTTP ${res.status}`);
  return res;
}

function postEvent(type, text) {
  return fetch(`${base}/sandbox/event/${job}?token=${token}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, text: String(text) }),
  }).catch(() => {});
}

function postResult(payload) {
  return fetch(`${base}/sandbox/result/${job}?token=${token}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

function extractJson(text) {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1) throw new Error('no JSON object found in CLI output');
  return JSON.parse(text.slice(start, end + 1));
}

function writeKiloConfig() {
  const model = process.env.LLM_MODEL || '';
  const provider = process.env.KILO_PROVIDER || 'archide';
  const dir = path.join(process.env.HOME || '/root', '.config', 'kilo');
  fs.mkdirSync(dir, { recursive: true });
  const config = {
    $schema: 'https://app.kilo.ai/config.json',
    provider: {
      [provider]: {
        name: 'ArchIDE Gateway',
        npm: '@ai-sdk/openai-compatible',
        options: {
          baseURL: process.env.LLM_BASE_URL,
          headers: { 'X-OpenAI-Api-Key': process.env.LLM_API_KEY },
        },
        models: model ? { [model]: { name: model } } : {},
      },
    },
    permission: { bash: 'allow', edit: 'allow', webfetch: 'deny' },
  };
  fs.writeFileSync(path.join(dir, 'kilo.jsonc'), JSON.stringify(config, null, 2));
}

function handleLine(line) {
  line = line.trim();
  if (!line) return;
  let event;
  try {
    event = JSON.parse(line);
  } catch (_) {
    return postEvent('log', line);
  }
  const part = event.part || {};
  if (part.type === 'text' && part.text) postEvent('output', part.text);
  else if ((part.type === 'reasoning' || part.type === 'thinking') && part.text) postEvent('reasoning', part.text);
  else if (event.type) postEvent('log', event.type);
}

function runKilo(args, cwd) {
  return new Promise((resolve) => {
    const child = spawn('kilo', args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let buffer = '';
    let all = '';
    child.stdout.on('data', (data) => {
      const chunk = data.toString();
      all += chunk;
      buffer += chunk;
      let idx;
      while ((idx = buffer.indexOf('\n')) >= 0) {
        handleLine(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 1);
      }
    });
    child.stderr.on('data', (data) => postEvent('log', data.toString()));
    child.on('close', () => {
      if (buffer.trim()) handleLine(buffer);
      resolve(all);
    });
    child.on('error', (err) => {
      postEvent('error', `failed to start kilo: ${err.message}`);
      resolve(all);
    });
  });
}

(async () => {
  try {
    const repoDir = '/work/repo';
    fs.mkdirSync(repoDir, { recursive: true });

    await prepareStorage();
    await postEvent('status', 'downloading repository');
    const tarPath = '/work/repo.tar.gz';
    fs.writeFileSync(tarPath, Buffer.from(await (await get(`/sandbox/repo/${job}`)).arrayBuffer()));
    execFileSync('tar', ['-xzf', tarPath, '-C', repoDir]);

    const meta = await (await get(`/sandbox/meta/${job}`)).json();
    writeKiloConfig();

    const kiloModel = process.env.KILO_MODEL || `archide/${process.env.LLM_MODEL || ''}`;
    fs.mkdirSync(path.dirname(output), { recursive: true });

    await postEvent('status', `running kilo (model ${kiloModel})`);
    const stdout = await runKilo(
      ['run', meta.prompt, '--dir', repoDir, '--model', kiloModel, '--auto', '--format', 'json'],
      repoDir,
    );

    const graph = fs.existsSync(output)
      ? JSON.parse(fs.readFileSync(output, 'utf8'))
      : extractJson(stdout);

    saveArtifact(graph, meta.repo_name);
    await postResult({ graph });
    console.log('ok');
  } catch (err) {
    await postResult({ error: String((err && err.message) || err) });
    console.error('error:', (err && err.stack) || err);
    process.exit(1);
  }
})();
