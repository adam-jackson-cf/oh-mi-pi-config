import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char]);
const property = (name, value) => `<property name="${escape(name)}" value="${escape(value)}"/>`;
const failure = (tag, reason, type = '') => `<${tag} message="${escape(reason)}"${type ? ` type="${escape(type)}"` : ''}/>`;
const errors = values => values.map(error => error.message ?? String(error)).join('\n');

export default class GuardrailsReporter {
  constructor(options) {
    this.options = options;
    this.started = performance.now();
    this.projects = new Set();
  }
  onInit(ctx) {
    this.root = process.cwd();
    const { tier, report } = this.options;
    if (!['t1', 't2', 't3'].includes(tier) || !path.isAbsolute(report)) throw new Error('Vitest evidence needs a tier and absolute external report');
    const relative = path.relative(this.root, report);
    if (relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative))) {
      throw new Error('Vitest evidence must be outside the target repository');
    }
    this.scratch = fs.mkdtempSync(path.join(path.dirname(report), '.vitest-guard-'));
    const home = path.join(this.scratch, 'home');
    const temporary = path.join(this.scratch, 'tmp');
    fs.mkdirSync(home);
    fs.mkdirSync(temporary);
    this.events = path.join(this.scratch, 'events.jsonl');
    fs.writeFileSync(this.events, '');
    this.settings = {
      tier, events: this.events, protectedHome: os.homedir(),
      allowed: [this.scratch],
    };
    this.environment = {
      HOME: home, USERPROFILE: home, TMPDIR: temporary, TMP: temporary, TEMP: temporary,
      XDG_CONFIG_HOME: path.join(home, '.config'), XDG_DATA_HOME: path.join(home, '.local', 'share'),
      XDG_CACHE_HOME: path.join(home, '.cache'), APPDATA: path.join(home, 'AppData', 'Roaming'),
      LOCALAPPDATA: path.join(home, 'AppData', 'Local'),
    };
    this.configureProjects(ctx.projects);
  }
  configureProjects(projects) {
    const setup = fileURLToPath(new URL('./guards.mjs', import.meta.url));
    for (const project of projects) {
      if (this.projects.has(project)) continue;
      this.projects.add(project);
      project.config.setupFiles = [setup, ...project.config.setupFiles.filter(file => file !== setup)];
      // Sequential setup ensures user setup cannot run before the native guards.
      project.config.sequence.setupFiles = 'list';
      project.config.env = { ...project.config.env, ...this.environment };
      project.config.cache = false;
      project.config.fsModuleCache = false;
      project.provide('omp.guardrails', this.settings);
    }
  }
  onTestRunStart(specifications) {
    this.configureProjects(specifications.map(specification => specification.project));
  }
  onTestRunEnd(modules, unhandledErrors = []) {
    const records = [];
    const errorRecords = [];
    const appendError = (file, name, reason) => {
      records.push(`<testcase file="${escape(file)}" classname="${escape(file)}" name="${escape(name)}" time="0">${failure('error', reason)}</testcase>`);
      errorRecords.push(reason);
    };
    let failed = 0;
    for (const module of modules) {
      const file = path.relative(this.root, module.moduleId).split(path.sep).join('/');
      for (const test of module.children.allTests()) {
        const result = test.result();
        const diagnostic = test.diagnostic();
        const name = test.fullName;
        let outcome = '';
        if (result.state === 'failed') { failed++; outcome = failure('failure', errors(result.errors)); }
        else if (result.state === 'skipped') outcome = failure('skipped', result.note ?? test.options.mode);
        else if (result.state === 'pending') { failed++; outcome = failure('error', 'test did not finish'); }
        else if (test.options.fails) outcome = failure('skipped', test.name, 'xfail');
        const retries = diagnostic?.retryCount ?? 0;
        records.push(`<testcase file="${escape(file)}" classname="${escape(file)}" name="${escape(name)}" time="${(diagnostic?.duration ?? 0) / 1000}"><properties>${property('retries', retries)}</properties>${outcome}</testcase>`);
      }
      for (const suite of [module, ...module.children.allSuites()]) {
        for (const error of suite.errors()) appendError(file, `guardrails::${suite.type}`, error.message ?? String(error));
      }
    }
    for (const error of unhandledErrors) appendError('', 'guardrails::unhandled', error.message ?? String(error));
    const events = fs.readFileSync(this.events, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
    const armedEvents = events.filter(event => event.kind === 'armed');
    const guards = armedEvents.length ? armedEvents.map(event => new Set(event.guards)).reduce((left, right) => new Set([...left].filter(value => right.has(value)))) : new Set();
    for (const event of events.filter(event => event.kind === 'violation')) appendError('', 'guardrails::violation', event.reason);
    const exitCode = failed || errorRecords.length || !records.length ? 1 : 0;
    if (exitCode) process.exitCode = 1;
    const wall = (performance.now() - this.started) / 1000;
    const properties = {
      'omp.tier': this.options.tier, 'omp.guards': [...guards].join(','),
      'omp.runtime': `node ${process.version}; vitest`, 'omp.revision': process.env.OMP_REVISION ?? '',
      'omp.wall_s': wall, 'omp.exit_code': exitCode,
    };
    const xml = `<?xml version="1.0" encoding="UTF-8"?><testsuite tests="${records.length}" failures="${failed}" errors="${errorRecords.length}" time="${wall}"><properties>${Object.entries(properties).map(([key, value]) => property(key, value)).join('')}</properties>${records.join('')}</testsuite>\n`;
    fs.writeFileSync(this.options.report, xml);
  }
}
