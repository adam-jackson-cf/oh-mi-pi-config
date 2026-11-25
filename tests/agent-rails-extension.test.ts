import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";

const railsConfig = join(import.meta.dir, "../agent/extensions/agent-rails/rails.yml");
const ciRunner = resolve(import.meta.dir, "../scripts/ci_runner.py");

test("ci-runner commands come from the extension's own location, never a host literal", async () => {
	const source = readFileSync(join(import.meta.dir, "../agent/extensions/agent-rails/index.ts"), "utf8");
	assert.ok(source.includes('resolve(import.meta.dir, "../../../scripts/ci_runner.py")'), "path is derived from import.meta.dir");
	for (const literal of [homedir(), "/Users/", "ssh ", "BatchMode", "--timeout", "ci_gate.py"]) {
		assert.equal(source.includes(literal), false, `rails source must not hold ${literal}`);
	}
	assert.equal(ciRunner, resolve(import.meta.dir, "..", "scripts", "ci_runner.py"));
});

test("rails.yml forces no skill reads, and every native rule always loads as self-contained guidance", () => {
	const mapping = z.record(z.string(), z.unknown()).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
	for (const key of ["references", "testGlobs", "codeGlobs"]) assert.equal(key in mapping, false, `rails.yml has no ${key}`);
	const directory = join(import.meta.dir, "../agent/extensions/agent-rails/rules");
	const files = [...new Bun.Glob("*.md").scanSync(directory)].map(name => name.slice(0, -3));
	assert.ok(files.length > 0);
	for (const name of files) {
		const text = readFileSync(join(directory, `${name}.md`), "utf8");
		const frontmatter = /^---\n([\s\S]*?)\n---/.exec(text);
		assert.ok(frontmatter);
		const rule = z.object({
			agents: z.array(z.string()), repeatMode: z.string(), question: z.string().min(1),
		}).parse(Bun.YAML.parse(frontmatter[1] ?? ""));
		assert.deepEqual(rule.agents, ["main", "task"]);
		assert.equal(rule.repeatMode, "once");
		const body = text.slice(frontmatter[0].length);
		assert.equal(body.includes("skill://"), false, `${name} carries its guidance instead of pointing at a skill`);
		assert.ok(body.split("\n").length <= 60, `${name} stays short`);
	}
});

test("external dependency map names only repo paths that exist", () => {
	const root = join(import.meta.dir, "..");
	const map = readFileSync(join(root, "agent/extensions/agent-rails/EXTERNAL-DEPENDENCIES.md"), "utf8");
	const locations = map.split("\n").filter(line => line.startsWith("| ") && !line.startsWith("| ---") && !line.startsWith("| Dependency"))
		.flatMap(line => [...(line.split(" | ")[1] ?? "").matchAll(/`([^`]+)`/g)].map(match => match[1] ?? ""))
		.filter(path => path.includes("/") || path.includes("."))
		.filter(path => !path.startsWith("~") && !path.startsWith("<"));
	assert.ok(locations.length > 40, "map lists repo paths");
	for (const path of locations) {
		const found = path.includes("*") ? [...new Bun.Glob(path).scanSync(root)].length > 0 : existsSync(join(root, path));
		assert.ok(found, `${path} is missing`);
	}
});

test("instructions the rails enforce or the owner dropped are gone; the unenforced ones stay as instructions", () => {
	const root = join(import.meta.dir, "..");
	const rules = readFileSync(join(root, "agent/RULES.md"), "utf8");
	for (const removed of ["bypass git hooks", "merge when CI is green", "in-scope questions", "HANDOFF", "Resolve councils"]) {
		assert.equal(rules.includes(removed), false, `agent/RULES.md still says ${removed}`);
	}
	for (const kept of ["## Security", "## Ask first", "## Code discovery order"]) assert.ok(rules.includes(kept), kept);
	const policy = readFileSync(join(root, "agent/AGENTS.md"), "utf8");
	for (const removed of ["Device rollout:", "Git: read", "device_rollout.py"]) assert.equal(policy.includes(removed), false, removed);
	for (const kept of ["Never run Git in or copy into the NUC's", "Restart OMP sessions on each device"]) assert.ok(policy.includes(kept), kept);
	const limits = readFileSync(join(root, "agent/extensions/agent-rails/AGENTS.md"), "utf8").split("## Known limits")[1] ?? "";
	for (const unenforced of ["no Git in the NUC's", "restart sessions after rail changes"]) {
		assert.ok(limits.includes(unenforced), `the agent-rails known limits list ${unenforced} as not enforced`);
	}
	assert.equal(existsSync(join(root, ".omp/AGENTS.md")), false, "repo facts live in the agent-rails config docs and schema");
});
