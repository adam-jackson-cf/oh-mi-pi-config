import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, test } from "node:test";

// The compiled omp binary serves extensions only the @oh-my-pi modules it bundles; any other
// @oh-my-pi path falls back to node_modules source the binary cannot resolve, and the extension
// fails to load. Plain bun resolves those paths, so only this check catches them before merge.
const root = join(import.meta.dir, "..");
const packageDir = join(root, "node_modules/@oh-my-pi/pi-coding-agent");
const RUNTIME_IMPORT = /^\s*(?:import|export)\s+(?!type\s)(?:[^;]*?\sfrom\s+)?["'](@oh-my-pi\/[^"']+)["']/gm;

// omp builds the bundled list with this script; loaded by path so the type check skips its source.
const collector = join(packageDir, "scripts/legacy-pi-virtual-module.ts");
type Collect = (layout: { packageDir: string; repoRoot: string }) => Promise<{ key: string }[]>;

async function bundled(): Promise<Set<string>> {
	const { collectBundledPiEntries }: { collectBundledPiEntries: Collect } = await import(collector);
	const entries = await collectBundledPiEntries({ packageDir, repoRoot: join(root, "no-monorepo") });
	return new Set(entries.map(entry => entry.key));
}

async function runtimeImports(): Promise<string[]> {
	const found: string[] = [];
	for await (const file of new Bun.Glob("agent/extensions/**/*.{ts,tsx}").scan({ cwd: root })) {
		if (file.includes("node_modules")) continue;
		const source = await readFile(join(root, file), "utf8");
		for (const match of source.matchAll(RUNTIME_IMPORT)) found.push(`${file} -> ${match[1]}`);
	}
	return found.toSorted();
}

describe("omp export surface", () => {
	test("the bundled list excludes private modules, so the check below can fail", async () => {
		const keys = await bundled();
		assert.ok(keys.has("@oh-my-pi/pi-coding-agent/judgment"));
		assert.ok(!keys.has("@oh-my-pi/pi-coding-agent/eval/judgment-bridge"));
	});

	test("every runtime @oh-my-pi import in extensions is a module the omp binary bundles", async () => {
		const keys = await bundled();
		const imports = await runtimeImports();
		assert.ok(imports.length > 0);
		assert.deepEqual(imports.filter(line => !keys.has(line.split(" -> ")[1] ?? "")), []);
	});
});
