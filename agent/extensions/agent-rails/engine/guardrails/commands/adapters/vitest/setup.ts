import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { install, type GuardSettings } from '../javascript/node_guard/install.ts';

interface VitestHooks {
  inject(key: string): GuardSettings | undefined;
  afterEach(hook: () => void): void;
}

const require = createRequire(path.join(process.cwd(), 'package.json'));
const vitestPackage = require.resolve('vitest/package.json');
const vitestEntry: string = require(vitestPackage).exports['.'].import.default;
const { inject, afterEach }: VitestHooks = await import(pathToFileURL(path.resolve(path.dirname(vitestPackage), vitestEntry)).href);
const settings = inject('guardrails.settings');
if (!settings) throw new Error('TEST.failure-propagation: missing external guard settings');
const violations = install(settings);
afterEach(() => {
  if (violations.length) throw new Error(violations.join('\n'));
});
