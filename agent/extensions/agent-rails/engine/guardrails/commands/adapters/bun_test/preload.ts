import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as runner from 'bun:test';
import { install, type RuntimeGuards } from '../vitest/runtime.ts';
import { GUARDED_MODULES } from './guarded-modules.ts';

interface PreloadSettings {
  mode: string;
  tier: string;
  journal: string;
}

interface SuiteRecord {
  title: string;
  each: boolean;
  skipped: boolean;
}

interface CaseRecord {
  kind: 'case';
  file: string;
  suites: string[];
  suiteEach: boolean[];
  title: string;
  each: boolean;
  skipped: boolean;
  failing: boolean;
  only: boolean;
}

type TestOutcome = void | Promise<void>;
type TestBody = (...values: unknown[]) => TestOutcome;

const original = {
  append: fs.appendFileSync.bind(fs),
  mkdtemp: fs.mkdtempSync.bind(fs),
  mkdir: fs.mkdirSync.bind(fs),
  homedir: os.homedir.bind(os),
};
const FACTORIES = new Set(['each', 'if', 'skipIf', 'todoIf', 'failingIf']);
const FUNCTION_MEMBERS = new Set(['apply', 'bind', 'call', 'toString']);
const HOOKS = ['beforeAll', 'beforeEach', 'afterAll', 'afterEach'];
const SKIPPING = new Set(['skip', 'todo', 'skipIf:true', 'todoIf:true', 'if:false']);
const BUN_PROCESS_AND_NETWORK = ['$', 'connect', 'listen', 'serve', 'udpSocket'];
const BUN_FILE_WRITES = ['write', 'writer', 'delete', 'unlink'];
const functionPrototype = Function.prototype;

function callWith<Receiver>(target: Function, receiver: Receiver, args: unknown[]) {
  return Function.prototype.apply.call(target, receiver, args);
}

function memberOf<Holder>(holder: Holder, property: string | symbol) {
  for (let current = holder; current !== null && current !== undefined; current = Object.getPrototypeOf(current)) {
    const descriptor = Object.getOwnPropertyDescriptor(current, property);
    if (descriptor) return descriptor.get ? descriptor.get.call(holder) : descriptor.value;
  }
  return undefined;
}

function replaceMember<Owner>(owner: Owner, name: string, value: Function): void {
  const existing = Object.getOwnPropertyDescriptor(owner, name);
  Object.defineProperty(owner, name, existing ? { value } : { value, writable: true, enumerable: true, configurable: true });
}

function row(values: unknown[]): string {
  try {
    return JSON.stringify(values.filter(value => !(value instanceof Object && functionPrototype.isPrototypeOf(value))));
  } catch {
    return values.map(String).join(',');
  }
}

function filePath(args: unknown[], index: number): string | URL | null {
  const destination = args[index];
  if (destination instanceof URL || destination === String(destination)) return destination;
  const name = destination instanceof Object && 'name' in destination ? destination.name : undefined;
  return name === String(name) ? name : null;
}

function guardBun(guards: RuntimeGuards): void {
  const check = (args: unknown[], operation: string) => {
    const target = filePath(args, 0);
    if (target !== null) guards.protect([target], 0, operation);
  };
  guards.patch(Bun, 'write', args => check(args, 'Bun.write'));
  const file = Bun.file;
  replaceMember(Bun, 'file', function guardedFile<Receiver>(this: Receiver, ...args: unknown[]) {
    const handle = callWith(file, this, args);
    for (const name of BUN_FILE_WRITES) {
      const native = memberOf(handle, name);
      if (!functionPrototype.isPrototypeOf(native)) continue;
      Object.defineProperty(handle, name, {
        value: (...values: unknown[]) => {
          check([handle], 'Bun.file().' + name);
          return callWith(native, handle, values);
        },
      });
    }
    return handle;
  });
  if (guards.tier !== 't1') return;
  for (const name of BUN_PROCESS_AND_NETWORK) replaceMember(Bun, name, () => guards.deny('TEST.fast-tier-isolation', 'Bun.' + name));
}

class Recorder {
  readonly settings: PreloadSettings;
  readonly root = process.cwd();
  readonly suites: SuiteRecord[] = [];

  constructor(settings: PreloadSettings) {
    this.settings = settings;
  }

  record<Entry>(event: Entry): void {
    original.append(this.settings.journal, JSON.stringify(event) + '\n');
  }

  currentFile(): string {
    return path.relative(this.root, Bun.main).split(path.sep).join('/');
  }

  body(test: CaseRecord, callback: TestBody): TestBody {
    if (this.settings.mode === 'collect') {
      return test.failing ? () => { throw new Error('guardrails collect-only'); } : () => undefined;
    }
    const record = (values: unknown[]) => this.record({ kind: 'attempt', file: test.file, suites: test.suites, title: test.title, row: test.each ? row(values) : '' });
    const wrapped = function <Receiver>(this: Receiver, ...values: unknown[]) {
      record(values);
      return callback.apply(this, values);
    };
    Object.defineProperty(wrapped, 'length', { value: callback.length });
    return wrapped;
  }

  registerTest<Receiver>(target: Function, self: Receiver, args: TestBody[], trail: string[]) {
    const test: CaseRecord = {
      kind: 'case',
      file: this.currentFile(),
      suites: this.suites.map(suite => suite.title),
      suiteEach: this.suites.map(suite => suite.each),
      title: String(args[0]),
      each: trail.includes('each()'),
      skipped: trail.some(step => SKIPPING.has(step)) || this.suites.some(suite => suite.skipped),
      failing: trail.includes('failing') || trail.includes('failingIf:true'),
      only: trail.includes('only'),
    };
    this.record(test);
    const replaced = args.map((value, index) => (index > 0 && functionPrototype.isPrototypeOf(value) ? this.body(test, value) : value));
    return callWith(target, self, replaced);
  }

  registerSuite<Receiver>(target: Function, self: Receiver, args: TestBody[], trail: string[]) {
    const suite = {
      title: String(args[0]),
      each: trail.includes('each()'),
      skipped: trail.some(step => SKIPPING.has(step)) || this.suites.some(parent => parent.skipped),
    };
    const suites = this.suites;
    const replaced = args.map((value, index) => {
      if (index === 0 || !functionPrototype.isPrototypeOf(value)) return value;
      return function <Inner>(this: Inner, ...values: unknown[]) {
        suites.push(suite);
        try { return value.apply(this, values); } finally { suites.pop(); }
      };
    });
    return callWith(target, self, replaced);
  }

  registrar<Target extends Function>(target: Target, kind: string, trail: string[], owner?: Function): Target {
    return new Proxy(target, {
      get: (object, property) => {
        const value = memberOf(object, property);
        if (!functionPrototype.isPrototypeOf(value) || property !== String(property) || FUNCTION_MEMBERS.has(property)) return value;
        return this.registrar(value, kind, [...trail, property], object);
      },
      apply: (object, self, args) => {
        const receiver = owner ?? self;
        const last = trail.at(-1) ?? '';
        if (FACTORIES.has(last)) {
          const step = last === 'each' ? 'each()' : last + ':' + Boolean(args[0]);
          return this.registrar(callWith(object, receiver, args), kind, [...trail.slice(0, -1), step]);
        }
        return kind === 'suite' ? this.registerSuite(object, receiver, args, trail) : this.registerTest(object, receiver, args, trail);
      },
    });
  }
}

function replaceRunner(recorder: Recorder, settings: PreloadSettings): void {
  const registrars = {
    test: recorder.registrar(runner.test, 'case', []),
    it: recorder.registrar(runner.it, 'case', []),
    xtest: recorder.registrar(runner.test, 'case', ['skip']),
    xit: recorder.registrar(runner.it, 'case', ['skip']),
    describe: recorder.registrar(runner.describe, 'suite', []),
    xdescribe: recorder.registrar(runner.describe, 'suite', ['skip']),
  };
  const noHooks = settings.mode === 'collect' ? Object.fromEntries(HOOKS.map(hook => [hook, () => undefined])) : {};
  const replaced = { ...runner, ...registrars, ...noHooks };
  runner.mock.module('bun:test', () => ({ ...replaced, default: { ...replaced } }));
  for (const name of [...Object.keys(registrars), ...HOOKS]) {
    if (name in globalThis) replaceMember(globalThis, name, memberOf(replaced, name));
  }
}

function redirectHome(scratch: string): void {
  const home = path.join(scratch, 'home');
  const temporary = path.join(scratch, 'tmp');
  original.mkdir(home);
  original.mkdir(temporary);
  Object.assign(process.env, {
    HOME: home, USERPROFILE: home, TMPDIR: temporary, TMP: temporary, TEMP: temporary,
    XDG_CONFIG_HOME: path.join(home, '.config'), XDG_DATA_HOME: path.join(home, '.local', 'share'),
    XDG_CACHE_HOME: path.join(home, '.cache'), XDG_STATE_HOME: path.join(home, '.local', 'state'),
    APPDATA: path.join(home, 'AppData', 'Roaming'), LOCALAPPDATA: path.join(home, 'AppData', 'Local'),
  });
  const redirected = { ...os, homedir: () => home, tmpdir: () => temporary };
  for (const name of ['node:os', 'os']) runner.mock.module(name, () => ({ ...redirected, default: redirected }));
}

function armRun(recorder: Recorder, settings: PreloadSettings): void {
  const scratch = original.mkdtemp(path.join(path.dirname(settings.journal), '.bun-guard-'));
  const protectedHome = original.homedir();
  redirectHome(scratch);
  recorder.record({ kind: 'run', tier: settings.tier, runtime: 'bun ' + Bun.version, revision: process.env['OMP_REVISION'] ?? '' });
  const violations = install({ tier: settings.tier, events: settings.journal, protectedHome, allowed: [scratch] }, guardBun);
  for (const [name, guarded] of Object.entries(GUARDED_MODULES)) {
    for (const specifier of [name, 'node:' + name]) runner.mock.module(specifier, () => ({ ...guarded, default: guarded }));
  }
  let reported = 0;
  runner.afterEach(() => {
    if (violations.length <= reported) return;
    const fresh = violations.slice(reported);
    reported = violations.length;
    throw new Error(fresh.join('\n'));
  });
}

export function start(settings: PreloadSettings): void {
  const recorder = new Recorder(settings);
  replaceRunner(recorder, settings);
  if (settings.mode !== 'collect') armRun(recorder, settings);
}
