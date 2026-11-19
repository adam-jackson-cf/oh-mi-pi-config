import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";

const integrity = "skill://dynaflows/references/delivery-principles.md";
const structure = "skill://dynaflows/references/structure-and-naming.md";
const enforcement = "skill://dynaflows/references/test-enforcement.md";
const tiers = "skill://test-guidance/references/tiers.md";
const verification = "skill://test-guidance/references/before-commit.md";
const proofs = "skill://dynaflows/references/proofs.md";
const railsConfig = join(import.meta.dir, "../agent/rails.yml");
const ciRunner = resolve(import.meta.dir, "../scripts/ci_runner.py");

test("ci-runner commands come from the extension's own location, never a host literal", async () => {
	const source = readFileSync(join(import.meta.dir, "../agent/extensions/dynaflows/index.ts"), "utf8");
	assert.ok(source.includes('new URL("../../../scripts/ci_runner.py", import.meta.url)'), "path is derived from import.meta.url");
	for (const literal of [homedir(), "/Users/", "ssh ", "BatchMode", "--timeout", "ci_gate.py"]) {
		assert.equal(source.includes(literal), false, `rails source must not hold ${literal}`);
	}
	assert.equal(ciRunner, resolve(import.meta.dir, "..", "scripts", "ci_runner.py"));
});

test("delivery policy references and native reminder scope use the targeted homes", () => {
	const config = z.object({
		dynaflowsRules: z.array(z.string()),
		references: z.object({
			firstEdit: z.array(z.string()), newFile: z.array(z.string()),
			testFile: z.array(z.string()), verification: z.array(z.string()),
		}),
	}).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
	assert.deepEqual(config.references, {
		firstEdit: [integrity], newFile: [structure],
		testFile: [tiers, enforcement, proofs], verification: [verification],
	});
	const directory = join(import.meta.dir, "../agent/extensions/dynaflows/rules");
	const files = [...new Bun.Glob("*.md").scanSync(directory)].map(name => name.slice(0, -3));
	assert.deepEqual([...config.dynaflowsRules].sort(), files.sort());
	for (const name of config.dynaflowsRules) {
		const text = readFileSync(join(directory, `${name}.md`), "utf8");
		const frontmatter = /^---\n([\s\S]*?)\n---/.exec(text);
		assert.ok(frontmatter);
		const rule = z.object({
			agents: z.array(z.string()), repeatMode: z.string(),
		}).parse(Bun.YAML.parse(frontmatter[1]));
		assert.deepEqual(rule.agents, ["main", "task"]);
		assert.equal(rule.repeatMode, "once");
	}
});
