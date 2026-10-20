import * as fs from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

const fail = (code, message) => { throw Object.assign(new Error(message), { code }); };
const BEGIN = Buffer.from('<!-- project-memory:begin v1 -->', 'utf8');
const END = Buffer.from('<!-- project-memory:end -->', 'utf8');
const BLOCK = Buffer.from([
  '<!-- project-memory:begin v1 -->',
  '## Project Memory',
  '',
  'Portable memory lives in `memory/` (see `memory/README.md`).',
  '',
  'Before non-trivial work, inspect the project and populate only verified context; do not invent facts.',
  '<!-- project-memory:end -->',
].join('\n'), 'utf8');
const validConfig = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === 2 && value.schemaVersion === 1
  && ['active', 'paused'].includes(value.maintenance);

async function rootPath(root) {
  if (typeof root !== 'string' || !path.isAbsolute(root)) fail('INVALID', 'Root must be an absolute path');
  const canonical = await fs.realpath(root);
  if (canonical !== path.resolve(root) || !(await fs.stat(canonical)).isDirectory()) fail('UNSAFE_PATH', 'Root must be a canonical directory');
  return canonical;
}

async function configFile(root) {
  const memory = path.join(root, 'memory');
  const memoryStat = await fs.lstat(memory).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
  if (!memoryStat) fail('NOT_INITIALIZED', 'Project memory is not initialized');
  if (memoryStat.isSymbolicLink() || !memoryStat.isDirectory()) fail('UNSAFE_PATH', 'Memory path must be a real directory');
  const file = path.join(memory, 'config.json');
  const stat = await fs.lstat(file).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
  if (!stat) fail('NOT_INITIALIZED', 'Project memory config is missing');
  if (stat.isSymbolicLink() || !stat.isFile() || stat.nlink !== 1) fail('UNSAFE_PATH', 'Memory config must be a regular, unlinked file');
  return file;
}

async function readConfig(file) {
  const before = await fs.lstat(file);
  if (before.isSymbolicLink() || !before.isFile() || before.nlink !== 1 || before.size > 4096) fail('UNSAFE_PATH', 'Memory config is unsafe or oversized');
  const handle = await fs.open(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0));
  try {
    const opened = await handle.stat();
    if (opened.dev !== before.dev || opened.ino !== before.ino || opened.nlink !== 1 || opened.size > 4096) fail('UNSAFE_PATH', 'Memory config changed while opening');
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== bytes.length) fail('CONFLICT', 'Memory config changed while reading');
    let config;
    try { config = JSON.parse(bytes.toString('utf8')); }
    catch { fail('CORRUPT', 'Memory config is malformed'); }
    if (!validConfig(config)) fail('CORRUPT', 'Memory config has an invalid schema');
    return { config, bytes };
  } finally {
    await handle.close();
  }
}

async function acquire(root, timeoutMs) {
  const lockPath = path.join(root, 'memory', '.core.lock');
  const token = randomUUID();
  const started = Date.now();
  while (true) {
    try {
      await fs.mkdir(lockPath);
      const ownerPath = path.join(lockPath, 'owner.json');
      await fs.writeFile(ownerPath, JSON.stringify({ token, pid: process.pid }), { flag: 'wx', mode: 0o600 });
      return async () => {
        const owner = JSON.parse(await fs.readFile(ownerPath, 'utf8'));
        if (owner.token !== token) fail('LOCK_OWNER', 'Lock ownership changed; lock retained');
        await fs.unlink(ownerPath);
        await fs.rmdir(lockPath);
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (Date.now() - started >= timeoutMs) fail('LOCK_TIMEOUT', 'Memory lock is held; manual owner investigation required');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  }
}

export async function getMemoryStatus(projectRoot) {
  const root = await rootPath(projectRoot);
  const file = await configFile(root);
  const { config } = await readConfig(file);
  return { maintenance: config.maintenance };
}

async function setMaintenance(projectRoot, maintenance, { lockTimeoutMs = 1000 } = {}) {
  if (!Number.isInteger(lockTimeoutMs) || lockTimeoutMs < 0 || lockTimeoutMs > 10000) fail('INVALID', 'Lock timeout out of bounds');
  const root = await rootPath(projectRoot);
  const file = await configFile(root);
  const release = await acquire(root, lockTimeoutMs);
  let temporary;
  try {
    const { config, bytes } = await readConfig(file);
    if (config.maintenance === maintenance) return { ok: true, changed: false, maintenance };
    const current = await fs.readFile(file);
    if (!current.equals(bytes)) fail('CONFLICT', 'Memory config changed; reread before updating');
    const updated = Buffer.from(`${JSON.stringify({ schemaVersion: 1, maintenance }, null, 2)}\n`, 'utf8');
    temporary = path.join(path.dirname(file), `.config-${randomUUID()}.tmp`);
    const handle = await fs.open(temporary, 'wx', 0o600);
    try { await handle.writeFile(updated); await handle.sync(); }
    finally { await handle.close(); }
    await fs.rename(temporary, file);
    temporary = undefined;
    const verified = await readConfig(file);
    if (!verified.bytes.equals(updated)) fail('VERIFY_FAILED', 'Memory config readback mismatch');
    return { ok: true, changed: true, maintenance };
  } finally {
    if (temporary) await fs.unlink(temporary).catch(() => {});
    await release();
  }
}

export function pauseMemory(projectRoot, options) { return setMaintenance(projectRoot, 'paused', options); }
export function resumeMemory(projectRoot, options) { return setMaintenance(projectRoot, 'active', options); }

export async function uninstallIntegration(projectRoot, { lockTimeoutMs = 1000 } = {}) {
  if (!Number.isInteger(lockTimeoutMs) || lockTimeoutMs < 0 || lockTimeoutMs > 10000) fail('INVALID', 'Lock timeout out of bounds');
  const root = await rootPath(projectRoot);
  await configFile(root); // Require an initialized project; never infer a target from the caller's cwd.
  const recovery = path.join(root, '.project-memory-init.json');
  if (await fs.lstat(recovery).then(() => true, error => error.code === 'ENOENT' ? false : Promise.reject(error))) {
    fail('CONFLICT', 'Initialization recovery is pending; inspect it before removing integration');
  }

  const agentsPath = path.join(root, 'AGENTS.md');
  let agentsStat = await fs.lstat(agentsPath).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
  if (!agentsStat) return { ok: true, changed: false, preserved: ['memory/'] };
  if (agentsStat.isSymbolicLink() || !agentsStat.isFile() || agentsStat.nlink !== 1) fail('UNSAFE_PATH', 'AGENTS.md must be a regular, unlinked file');
  const release = await acquire(root, lockTimeoutMs);
  let temporary;
  try {
    agentsStat = await fs.lstat(agentsPath);
    if (agentsStat.isSymbolicLink() || !agentsStat.isFile() || agentsStat.nlink !== 1) fail('UNSAFE_PATH', 'AGENTS.md must be a regular, unlinked file');
    const before = await fs.readFile(agentsPath);
    const start = before.indexOf(BEGIN);
    const end = before.indexOf(END);
    if (start === -1 && end === -1) return { ok: true, changed: false, preserved: ['memory/'] };
    if (start === -1 || end === -1 || end < start || before.indexOf(BEGIN, start + BEGIN.length) !== -1 || before.indexOf(END, end + END.length) !== -1) {
      fail('CONFLICT', 'AGENTS.md contains malformed or duplicate Project Memory markers');
    }
    const endAfter = end + END.length;
    if (!before.subarray(start, endAfter).equals(BLOCK) || !before.subarray(endAfter).equals(Buffer.from('\n'))) {
      fail('CONFLICT', 'Owned Project Memory block or trailing AGENTS.md bytes changed; removal refused');
    }
    const prefix = before.subarray(0, start);
    const separatorLength = prefix.length === 0 ? 0 : prefix.at(-1) === 0x0a ? 1 : 2;
    if (separatorLength === 2 && !(prefix.at(-1) === 0x0a && prefix.at(-2) === 0x0a)) {
      fail('CONFLICT', 'Owned Project Memory separator is malformed; removal refused');
    }
    const remaining = prefix.subarray(0, prefix.length - separatorLength);
    const current = await fs.readFile(agentsPath);
    if (!current.equals(before)) fail('CONFLICT', 'AGENTS.md changed during uninstall; no bytes were removed');
    temporary = path.join(root, `.project-memory-uninstall-${randomUUID()}.tmp`);
    const handle = await fs.open(temporary, 'wx', 0o600);
    try { await handle.writeFile(remaining); await handle.sync(); }
    finally { await handle.close(); }
    await fs.rename(temporary, agentsPath);
    temporary = undefined;
    const verified = await fs.readFile(agentsPath);
    if (!verified.equals(remaining)) fail('VERIFY_FAILED', 'AGENTS.md readback mismatch after uninstall');
    return { ok: true, changed: true, preserved: ['memory/'] };
  } finally {
    if (temporary) await fs.unlink(temporary).catch(() => {});
    await release();
  }
}
