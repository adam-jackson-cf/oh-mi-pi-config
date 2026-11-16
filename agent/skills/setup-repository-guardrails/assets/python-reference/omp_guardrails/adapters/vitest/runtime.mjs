// Native boundaries shared by every Vitest worker. No target-tree writes.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { syncBuiltinESMExports } from 'node:module';
import childProcess from 'node:child_process';
import net from 'node:net';
import tls from 'node:tls';
import dgram from 'node:dgram';
import http from 'node:http';
import https from 'node:https';
import http2 from 'node:http2';
import workerThreads from 'node:worker_threads';

const original = {
  append: fs.appendFileSync.bind(fs),
  realpath: fs.realpathSync.bind(fs),
};

function canonical(value) {
  const input = value instanceof URL ? fileURLToPath(value) : Buffer.isBuffer(value) ? value.toString() : value;
  // Node accepts an already-open numeric descriptor as well as a path.
  if (Number.isInteger(input)) return null;
  let current = path.resolve(input);
  const tail = [];
  for (;;) {
    try { return path.join(original.realpath(current), ...tail.reverse()); }
    catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') throw error;
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(input);
      tail.push(path.basename(current));
      current = parent;
    }
  }
}
const within = (value, root) => value === root || value.startsWith(root + path.sep);
const writing = (flags) => Number.isInteger(flags)
  ? Boolean(flags & (fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_APPEND))
  : /[wa+]/.test(flags ?? 'r');

export function install(settings) {
  const protectedHome = canonical(settings.protectedHome);
  const allowed = settings.allowed.map(canonical);
  const violations = [];
  let recording = false;
  function deny(rule, operation) {
    const reason = `${rule}: blocked ${operation}`;
    violations.push(reason);
    if (!recording) {
      recording = true;
      try { original.append(settings.events, JSON.stringify({ kind: 'violation', reason }) + '\n'); }
      finally { recording = false; }
    }
    throw new Error(reason);
  }
  function protect(value, operation) {
    const target = canonical(value);
    if (target && within(target, protectedHome) && !allowed.some(root => within(target, root))) {
      deny('TEST.user-data-guard', `${operation} ${target}`);
    }
  }
  function patch(owner, name, check) {
    const native = owner[name];
    if (native == null) return;
    owner[name] = function (...args) { check(args); return native.apply(this, args); };
  }
  for (const owner of [fs, fsp]) {
    for (const suffix of owner === fs ? ['', 'Sync'] : ['']) {
      for (const name of ['writeFile', 'appendFile', 'truncate', 'mkdir', 'mkdtemp', 'rm', 'rmdir', 'unlink', 'chmod', 'chown', 'utimes', 'lchmod', 'lchown', 'lutimes']) {
        patch(owner, name + suffix, args => protect(args[0], name));
      }
      for (const name of ['rename', 'link']) {
        patch(owner, name + suffix, args => { protect(args[0], name); protect(args[1], name); });
      }
      for (const name of ['copyFile', 'cp', 'symlink']) {
        patch(owner, name + suffix, args => protect(args[1], name));
      }
      patch(owner, 'open' + suffix, args => { if (writing(args[1])) protect(args[0], 'open'); });
    }
  }
  patch(fs, 'createWriteStream', args => protect(args[0], 'createWriteStream'));
  patch(fs, 'createReadStream', args => { if (args[1]?.flags && writing(args[1].flags)) protect(args[0], 'createReadStream'); });
  if (settings.tier === 't1') {
    const block = operation => () => deny('TEST.fast-tier-isolation', operation);
    for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) patch(childProcess, name, block(`child_process.${name}`));
    // Calling the prototype directly must not bypass the public spawn wrappers.
    patch(childProcess.ChildProcess.prototype, 'spawn', block('ChildProcess.spawn'));
    // Bun's Node compatibility exports keep pre-imported spawnSync bindings even
    // after syncBuiltinESMExports; those bindings delegate to this native boundary.
    if (globalThis.Bun) {
      for (const name of ['spawn', 'spawnSync']) patch(globalThis.Bun, name, block(`Bun.${name}`));
    }
    for (const [owner, names] of [
      [net, ['connect', 'createConnection']], [net.Socket.prototype, ['connect']],
      [net.Server.prototype, ['listen']], [tls, ['connect']],
      [dgram.Socket.prototype, ['connect', 'send', 'bind']],
      [http, ['request', 'get']], [https, ['request', 'get']], [http2, ['connect']],
      [globalThis, ['fetch', 'WebSocket']],
    ]) for (const name of names) patch(owner, name, block(name));
    workerThreads.Worker = function GuardedWorker() {
      deny('TEST.fast-tier-isolation', 'worker_threads.Worker');
    };
  }
  syncBuiltinESMExports();
  original.append(settings.events, JSON.stringify({ kind: 'armed', tier: settings.tier, guards: settings.tier === 't1' ? ['fast-tier', 'user-data'] : ['user-data'] }) + '\n');
  return violations;
}
