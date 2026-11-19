// Bun test preload for the guardrails Bun adapter, loaded with `bun test --preload <entry>`.
// The entry beside the external report calls start(); nothing is written into the target tree.
//
// - Every mode replaces `bun:test` (and the runner globals) with recording registrars, so the
//   journal knows each test's suites, title and modifiers (skip, todo, failing, each).
// - collect: test bodies and hooks become no-ops; `bun test` then lists what the selector picks
//   without running any test code beyond module top level.
// - run: the tier's guards are armed (process, network and real-home writes; see
//   ../vitest/runtime.mjs, whose Node boundaries Bun implements, plus Bun's own APIs here),
//   HOME and the temporary directory are redirected, and every attempt of a test body is
//   journalled, so a retry to green shows in the report.
import childProcess from 'node:child_process';
import dgram from 'node:dgram';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import http from 'node:http';
import http2 from 'node:http2';
import https from 'node:https';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import tls from 'node:tls';
import workerThreads from 'node:worker_threads';
import * as runner from 'bun:test';
import { install } from '../vitest/runtime.mjs';

const original = {
  append: fs.appendFileSync.bind(fs),
  mkdtemp: fs.mkdtempSync.bind(fs),
  mkdir: fs.mkdirSync.bind(fs),
  homedir: os.homedir.bind(os),
};
// Registrar properties that return another registrar once called.
const FACTORIES = new Set(['each', 'if', 'skipIf', 'todoIf', 'failingIf']);
const FUNCTION_MEMBERS = new Set(['apply', 'bind', 'call', 'toString']);
const HOOKS = ['beforeAll', 'beforeEach', 'afterAll', 'afterEach'];
const SKIPPING = new Set(['skip', 'todo', 'skipIf:true', 'todoIf:true', 'if:false']);
// Bun binds named imports of built-in modules at load, so the guarded objects replace the modules.
const GUARDED_MODULES = {
  child_process: childProcess, dgram, fs, 'fs/promises': fsp, http, http2, https, net, tls,
  worker_threads: workerThreads,
};
// Bun's own process and network entry points, beside the Node ones runtime.mjs guards.
const BUN_PROCESS_AND_NETWORK = ['$', 'connect', 'listen', 'serve', 'udpSocket'];
const BUN_FILE_WRITES = ['write', 'writer', 'delete', 'unlink'];

// Calls a runner function with the receiver Bun bound it to.
function callWith(target, receiver, args) {
  return Function.prototype.apply.call(target, receiver, args);
}

function row(values) {
  try {
    return JSON.stringify(values.filter(value => !(value instanceof Function)));
  } catch {
    return values.map(String).join(',');
  }
}

// A Bun.write destination: a path string, a URL, or a BunFile (its name); descriptors are not paths.
function filePath(destination) {
  if (destination instanceof URL || destination?.constructor === String) return destination;
  if (destination?.name?.constructor === String) return destination.name;
  return null;
}

// Bun.write and BunFile writers reach the file system without node:fs.
function guardBun({ deny, protect, patch, tier }) {
  const check = (destination, operation) => {
    const target = filePath(destination);
    if (target !== null) protect(target, operation);
  };
  patch(Bun, 'write', args => check(args[0], 'Bun.write'));
  const file = Bun.file;
  Bun.file = function guardedFile(...args) {
    const handle = file.apply(this, args);
    for (const name of BUN_FILE_WRITES) {
      const native = handle[name];
      if (!(native instanceof Function)) continue;
      Object.defineProperty(handle, name, {
        value: (...values) => {
          check(handle, 'Bun.file().' + name);
          return native.apply(handle, values);
        },
      });
    }
    return handle;
  };
  if (tier === 't1') {
    for (const name of BUN_PROCESS_AND_NETWORK) {
      Bun[name] = () => deny('TEST.fast-tier-isolation', 'Bun.' + name);
    }
  }
}

export function start(settings) {
  const root = process.cwd();
  const record = event => original.append(settings.journal, JSON.stringify(event) + '\n');
  const suites = [];
  const currentFile = () => path.relative(root, Bun.main).split(path.sep).join('/');

  function body(test, callback) {
    if (settings.mode === 'collect') {
      // An expected failure must fail to be listed as passing; nothing else runs.
      return test.failing ? () => { throw new Error('guardrails collect-only'); } : () => {};
    }
    const wrapped = function (...values) {
      record({ kind: 'attempt', file: test.file, suites: test.suites, title: test.title, row: test.each ? row(values) : '' });
      return callback.apply(this, values);
    };
    // Bun reads the declared arity to decide whether a test takes a done callback.
    Object.defineProperty(wrapped, 'length', { value: callback.length });
    return wrapped;
  }

  function registerTest(target, self, args, trail) {
    const test = {
      kind: 'case',
      file: currentFile(),
      suites: suites.map(suite => suite.title),
      suiteEach: suites.map(suite => suite.each),
      title: String(args[0]),
      each: trail.includes('each()'),
      skipped: trail.some(step => SKIPPING.has(step)) || suites.some(suite => suite.skipped),
      failing: trail.includes('failing') || trail.includes('failingIf:true'),
      only: trail.includes('only'),
    };
    record(test);
    const replaced = args.map((value, index) => (index > 0 && value instanceof Function ? body(test, value) : value));
    return callWith(target, self, replaced);
  }

  function registerSuite(target, self, args, trail) {
    const suite = {
      title: String(args[0]),
      each: trail.includes('each()'),
      skipped: trail.some(step => SKIPPING.has(step)) || suites.some(parent => parent.skipped),
    };
    const replaced = args.map((value, index) => {
      if (index === 0 || !(value instanceof Function)) return value;
      return function (...values) {
        suites.push(suite);
        try { return value.apply(this, values); } finally { suites.pop(); }
      };
    });
    return callWith(target, self, replaced);
  }

  // `owner` is the unwrapped object a member was read from: Bun's registrars need it as `this`.
  function registrar(target, kind, trail, owner) {
    return new Proxy(target, {
      get(object, property) {
        const value = object[property];
        if (!(value instanceof Function) || property.constructor !== String || FUNCTION_MEMBERS.has(property)) return value;
        return registrar(value, kind, [...trail, property], object);
      },
      apply(object, self, args) {
        const receiver = owner ?? self;
        const last = trail.at(-1);
        if (FACTORIES.has(last)) {
          const step = last === 'each' ? 'each()' : last + ':' + Boolean(args[0]);
          return registrar(callWith(object, receiver, args), kind, [...trail.slice(0, -1), step]);
        }
        return (kind === 'suite' ? registerSuite : registerTest)(object, receiver, args, trail);
      },
    });
  }

  const registrars = {
    test: registrar(runner.test, 'case', []),
    it: registrar(runner.it, 'case', []),
    xtest: registrar(runner.test, 'case', ['skip']),
    xit: registrar(runner.it, 'case', ['skip']),
    describe: registrar(runner.describe, 'suite', []),
    xdescribe: registrar(runner.describe, 'suite', ['skip']),
  };
  const replaced = { ...runner, ...registrars };
  if (settings.mode === 'collect') {
    for (const hook of HOOKS) replaced[hook] = () => {};
  }
  runner.mock.module('bun:test', () => ({ ...replaced, default: { ...replaced } }));
  for (const name of [...Object.keys(registrars), ...HOOKS]) {
    if (name in globalThis) globalThis[name] = replaced[name];
  }
  if (settings.mode === 'collect') return;

  const scratch = original.mkdtemp(path.join(path.dirname(settings.journal), '.bun-guard-'));
  const home = path.join(scratch, 'home');
  const temporary = path.join(scratch, 'tmp');
  original.mkdir(home);
  original.mkdir(temporary);
  const protectedHome = original.homedir();
  Object.assign(process.env, {
    HOME: home, USERPROFILE: home, TMPDIR: temporary, TMP: temporary, TEMP: temporary,
    XDG_CONFIG_HOME: path.join(home, '.config'), XDG_DATA_HOME: path.join(home, '.local', 'share'),
    XDG_CACHE_HOME: path.join(home, '.cache'), XDG_STATE_HOME: path.join(home, '.local', 'state'),
    APPDATA: path.join(home, 'AppData', 'Roaming'), LOCALAPPDATA: path.join(home, 'AppData', 'Local'),
  });
  // Bun reads the home directory once at start-up, so the redirect replaces the module.
  const redirected = { ...os, homedir: () => home, tmpdir: () => temporary };
  for (const name of ['node:os', 'os']) runner.mock.module(name, () => ({ ...redirected, default: redirected }));
  record({ kind: 'run', tier: settings.tier, runtime: 'bun ' + Bun.version, revision: process.env.OMP_REVISION ?? '' });
  const violations = install({ tier: settings.tier, events: settings.journal, protectedHome, allowed: [scratch] }, guardBun);
  for (const [name, guarded] of Object.entries(GUARDED_MODULES)) {
    for (const specifier of [name, 'node:' + name]) runner.mock.module(specifier, () => ({ ...guarded, default: guarded }));
  }
  let reported = 0;
  // A caught guard error still fails the test that caused it.
  runner.afterEach(() => {
    if (violations.length > reported) {
      const fresh = violations.slice(reported);
      reported = violations.length;
      throw new Error(fresh.join('\n'));
    }
  });
}
