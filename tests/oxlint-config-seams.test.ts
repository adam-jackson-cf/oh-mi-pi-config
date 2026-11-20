import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "node:test";
import { z } from "zod";

const repoRoot = fileURLToPath(new URL("..", import.meta.url));

const lintReport = z.object({
	diagnostics: z.array(
		z.object({
			code: z.string().optional(),
			labels: z.array(z.object({ span: z.object({ line: z.number() }) })),
		}),
	),
});

function lintWithRepoConfig(source: string): string[] {
	const dir = mkdtempSync(join(tmpdir(), "oxlint-config-"));
	try {
		const target = join(dir, "assertions.ts");
		writeFileSync(target, source);
		const run = spawnSync(
			"bunx",
			["oxlint", "-c", join(repoRoot, ".oxlintrc.json"), "--format", "json", target],
			{ cwd: repoRoot, encoding: "utf8", timeout: 60000 },
		);
		return lintReport
			.parse(JSON.parse(run.stdout))
			.diagnostics.map((diagnostic) => `${diagnostic.code ?? "parse-error"}:${diagnostic.labels[0]?.span.line}`);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
}

describe("repository oxlint configuration", () => {
	test("bans as and angle-bracket type assertions while allowing const assertions", () => {
		const findings = lintWithRepoConfig(`declare const value: string | number;
export const text = value as string;
export const other = <number>value;
export const fixed = ["a", "b"] as const;
export const checked: string | number = value;
`);
		assert.deepEqual(findings, [
			"typescript(consistent-type-assertions):2",
			"typescript(consistent-type-assertions):3",
		]);
	});
});
