// Installed first in every project's setupFiles, before user setup and test modules.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { install } from './runtime.mjs';

const require = createRequire(path.join(process.cwd(), 'package.json'));
const vitestPackage = require.resolve('vitest/package.json');
const vitestEntry = require(vitestPackage).exports['.'].import.default;
const { inject, afterEach } = await import(pathToFileURL(path.resolve(path.dirname(vitestPackage), vitestEntry)).href);
const settings = inject('omp.guardrails');
if (!settings) throw new Error('TEST.failure-propagation: missing external guard settings');
const violations = install(settings);
// A caught guard exception still fails the case. The external event journal additionally
// covers swallowed beforeAll/afterAll errors and setup-file errors, independent of hook order.
afterEach(() => {
  if (violations.length) throw new Error(violations.join('\n'));
});
