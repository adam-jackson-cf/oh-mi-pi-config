import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { GuardSettings } from '../javascript/node_guard/install.ts';

interface ReporterOptions {
  tier: string;
  report: string;
}

interface VitestProject {
  config: {
    setupFiles: string[];
    sequence: { setupFiles: string };
    env: Record<string, string>;
    cache: boolean;
    fsModuleCache: boolean;
  };
  provide(key: string, value: GuardSettings): void;
}

interface ReportedError {
  message?: string;
}

interface TestResult {
  state: string;
  errors?: ReportedError[];
  note?: string;
}

interface TestCase {
  fullName: string;
  name: string;
  options: { mode: string; fails?: boolean };
  result(): TestResult;
  diagnostic(): { retryCount: number; duration: number } | undefined;
}

interface TestSuite {
  type: string;
  errors(): ReportedError[];
}

interface TestModule extends TestSuite {
  moduleId: string;
  children: { allTests(): Iterable<TestCase>; allSuites(): Iterable<TestSuite> };
}

interface GuardEvent {
  kind: string;
  guards?: string[];
  reason?: string;
}

const ESCAPES = new Map([['&', '&amp;'], ['<', '&lt;'], ['>', '&gt;'], ['"', '&quot;'], ["'", '&apos;']]);
const escape = (value: string | number | undefined) => String(value ?? '').replace(/[&<>"']/g, char => ESCAPES.get(char) ?? char);
const property = (name: string, value: string | number) => `<property name="${escape(name)}" value="${escape(value)}"/>`;
const failure = (tag: string, reason: string, type = '') => `<${tag} message="${escape(reason)}"${type ? ` type="${escape(type)}"` : ''}/>`;
const errorText = (error: ReportedError) => error.message ?? String(error);

function armedGuards(events: GuardEvent[]): Set<string> {
  const armed = events.filter(event => event.kind === 'armed').map(event => new Set(event.guards ?? []));
  return armed.reduce((left, right) => new Set([...left].filter(value => right.has(value))), armed[0] ?? new Set<string>());
}

function outsideRepository(root: string, report: string): boolean {
  const relative = path.relative(root, report);
  return relative !== '' && (relative.startsWith('..' + path.sep) || relative === '..' || path.isAbsolute(relative));
}

class RunRecords {
  readonly records: string[] = [];
  readonly errors: string[] = [];
  failed = 0;

  appendError(file: string, name: string, reason: string): void {
    this.records.push(`<testcase file="${escape(file)}" classname="${escape(file)}" name="${escape(name)}" time="0">${failure('error', reason)}</testcase>`);
    this.errors.push(reason);
  }

  outcome(test: TestCase, result: TestResult): string {
    if (result.state === 'failed') { this.failed++; return failure('failure', (result.errors ?? []).map(errorText).join('\n')); }
    if (result.state === 'skipped') return failure('skipped', result.note ?? test.options.mode);
    if (result.state === 'pending') { this.failed++; return failure('error', 'test did not finish'); }
    return test.options.fails ? failure('skipped', test.name, 'xfail') : '';
  }

  addTest(file: string, test: TestCase): void {
    const outcome = this.outcome(test, test.result());
    const diagnostic = test.diagnostic();
    const retries = diagnostic?.retryCount ?? 0;
    this.records.push(`<testcase file="${escape(file)}" classname="${escape(file)}" name="${escape(test.fullName)}" time="${(diagnostic?.duration ?? 0) / 1000}"><properties>${property('retries', retries)}</properties>${outcome}</testcase>`);
  }

  addModule(file: string, module: TestModule): void {
    for (const test of module.children.allTests()) this.addTest(file, test);
    for (const suite of [module, ...module.children.allSuites()]) {
      for (const error of suite.errors()) this.appendError(file, `guardrails::${suite.type}`, errorText(error));
    }
  }
}

export default class GuardrailsReporter {
  readonly options: ReporterOptions;
  readonly started = performance.now();
  readonly projects = new Set<VitestProject>();
  root = '';
  events = '';
  settings: GuardSettings | undefined;
  environment: Record<string, string> = {};

  constructor(options: ReporterOptions) {
    this.options = options;
  }

  onInit(ctx: { projects: VitestProject[] }): void {
    this.root = process.cwd();
    const { tier, report } = this.options;
    if (!['t1', 't2', 't3'].includes(tier) || !path.isAbsolute(report)) throw new Error('Vitest evidence needs a tier and absolute external report');
    if (!outsideRepository(this.root, report)) throw new Error('Vitest evidence must be outside the target repository');
    const scratch = fs.mkdtempSync(path.join(path.dirname(report), '.vitest-guard-'));
    const home = path.join(scratch, 'home');
    const temporary = path.join(scratch, 'tmp');
    fs.mkdirSync(home);
    fs.mkdirSync(temporary);
    this.events = path.join(scratch, 'events.jsonl');
    fs.writeFileSync(this.events, '');
    this.settings = { tier, events: this.events, protectedHome: os.homedir(), allowed: [scratch] };
    this.environment = {
      HOME: home, USERPROFILE: home, TMPDIR: temporary, TMP: temporary, TEMP: temporary,
      XDG_CONFIG_HOME: path.join(home, '.config'), XDG_DATA_HOME: path.join(home, '.local', 'share'),
      XDG_CACHE_HOME: path.join(home, '.cache'), APPDATA: path.join(home, 'AppData', 'Roaming'),
      LOCALAPPDATA: path.join(home, 'AppData', 'Local'),
    };
    this.configureProjects(ctx.projects);
  }

  configureProjects(projects: VitestProject[]): void {
    const setup = fileURLToPath(new URL('./setup.ts', import.meta.url));
    const settings = this.settings;
    if (!settings) throw new Error('Vitest evidence was not initialised');
    for (const project of projects) {
      if (this.projects.has(project)) continue;
      this.projects.add(project);
      project.config.setupFiles = [setup, ...project.config.setupFiles.filter(file => file !== setup)];
      project.config.sequence.setupFiles = 'list';
      project.config.env = { ...project.config.env, ...this.environment };
      project.config.cache = false;
      project.config.fsModuleCache = false;
      project.provide('guardrails.settings', settings);
    }
  }

  onTestRunStart(specifications: { project: VitestProject }[]): void {
    this.configureProjects(specifications.map(specification => specification.project));
  }

  onTestRunEnd(modules: TestModule[], unhandledErrors: ReportedError[] = []): void {
    const run = new RunRecords();
    for (const module of modules) run.addModule(path.relative(this.root, module.moduleId).split(path.sep).join('/'), module);
    for (const error of unhandledErrors) run.appendError('', 'guardrails::unhandled', errorText(error));
    const events: GuardEvent[] = fs.readFileSync(this.events, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
    const guards = armedGuards(events);
    for (const event of events.filter(entry => entry.kind === 'violation')) run.appendError('', 'guardrails::violation', event.reason ?? '');
    this.writeReport(run, guards);
  }

  writeReport(run: RunRecords, guards: Set<string>): void {
    const exitCode = run.failed || run.errors.length || !run.records.length ? 1 : 0;
    if (exitCode) process.exitCode = 1;
    const wall = (performance.now() - this.started) / 1000;
    const properties: [string, string | number][] = [
      ['guardrails.tier', this.options.tier], ['guardrails.guards', [...guards].join(',')],
      ['guardrails.runtime', `node ${process.version}; vitest`], ['guardrails.revision', process.env['GUARDRAILS_REVISION'] ?? ''],
      ['guardrails.wall_s', wall], ['guardrails.exit_code', exitCode],
    ];
    const xml = `<?xml version="1.0" encoding="UTF-8"?><testsuite tests="${run.records.length}" failures="${run.failed}" errors="${run.errors.length}" time="${wall}"><properties>${properties.map(([key, value]) => property(key, value)).join('')}</properties>${run.records.join('')}</testsuite>\n`;
    fs.writeFileSync(this.options.report, xml);
  }
}
