import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { machineName, storePath, withStore } from "../agent/extensions/agent-rails/lib/decision-store.ts";
import { harness, execute, reasonOf, isBlocked, repoCommand, ciHead, greenCi, mockPrCi, forbidGh, askAutonomy, ciGate, ciRunner, harnessConfig, type Input, originRemote, proveResult, structureResult, railsConfig, watchedProofCall } from "./agent-rails-guards-seams.test";

function assertRefused(result: Parameters<typeof reasonOf>[0], fragment: string): void {
	assert.equal(isBlocked(result), true, fragment);
	assert.ok(reasonOf(result).includes(fragment), reasonOf(result));
}

// A git on PATH whose origin remote read fails as the file `how` says (exit128, kill), else runs the real git.
async function withFailingOriginRead(body: (fail: (how: "exit128" | "kill" | "pass") => void) => Promise<void>): Promise<void> {
	const real = execFileSync("sh", ["-c", "command -v git"], { encoding: "utf8" }).trim();
	const dir = realpathSync(mkdtempSync(join(tmpdir(), "failing-git-")));
	const how = join(dir, "how");
	writeFileSync(how, "pass");
	writeFileSync(join(dir, "git"), [
		"#!/bin/sh",
		`case " $* " in *" remote get-url origin "*)`,
		`  case "$(cat '${how}')" in`,
		"    exit128) echo 'fatal: unable to read config file: Resource temporarily unavailable' >&2; exit 128 ;;",
		"    kill) kill -KILL $$ ;;",
		"  esac ;;",
		"esac",
		`exec '${real}' "$@"`,
		"",
	].join("\n"));
	chmodSync(join(dir, "git"), 0o755);
	const previous = process.env["PATH"];
	process.env["PATH"] = `${dir}:${previous ?? ""}`;
	try { await body(value => writeFileSync(how, value)); }
	finally {
		process.env["PATH"] = previous;
		rmSync(dir, { recursive: true, force: true });
	}
}

describe("agent-rails extension", () => {
	test("edits and new code or test files need no skill reads in a governed repository", async () => {
		const h = harness();
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-guards-scratch-")));
		try {
			h.govern();
			for (const [tool, input] of [
				["write", { path: "tests/new.test.ts", content: "test('behaviour', () => {});" }], ["write", { path: "src/new.ts", content: "export {};\n" }],
				["edit", { path: "existing.ts" }], ["write", { path: join(scratch, "scratch.ts"), content: "export {};\n" }],
			] as const) assert.equal(await h.call(tool, input), undefined, `${tool} ${JSON.stringify(input)}`);
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("runs the harness path check with all changed paths, surfacing command failures", async () => {
		const h = harness();
		try {
			h.pathFailure("use feature ownership");
			h.config();
			h.govern();
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid configs refuse edits with the onboarding text, and do not throw", async () => {
		const h = harness();
		try {
			assert.ok(reasonOf(await h.call("edit", { path: "existing.ts" })).includes("owner/repo is not onboarded (missing)"));
			h.rawConfig("{broken");
			assert.ok(reasonOf(await h.call("edit", { path: "existing.ts" })).includes("owner/repo is not onboarded (invalid: "));
			h.rawConfig(JSON.stringify({ ...harnessConfig("owner/repo"), commands: { commit: "not an argv array" } }));
			const invalid = reasonOf(await h.call("edit", { path: "existing.ts" }));
			assert.ok(invalid.includes("commands.commit: must be a non-empty argv array"), invalid);
			assert.deepEqual(h.notes, [], "the refusal carries the problems; nothing else is noted");
			h.govern();
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("handoff after code edits in a governed repository needs no read and runs no check", async () => {
		const h = harness();
		try {
			const ran: string[] = [];
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				if (repoCommand(command, args)[0] === "bun") ran.push(args.join(" "));
				return exec(command, args, options);
			};
			h.config({ commands: { commit: ["bun", "check"], ci: ["bun", "ci"] } });
			await h.emit("session_start");
			writeFileSync(join(h.root, "existing.ts"), "pulled change\n");
			writeFileSync(join(h.root, "session.log"), "growing log\n");
			assert.equal(await h.emit("session_stop"), undefined, "tree changes outside this session's edits never block");
			await h.call("edit", { path: "existing.ts" });
			writeFileSync(join(h.root, "session.log"), "grew again\n");
			assert.equal(await h.emit("session_stop"), undefined);
			assert.deepEqual(ran, [], "no repository check runs at handoff");
		} finally { h.cleanup(); }
	});

	test("discovery tools are never gated, before or after compaction", async () => {
		const h = harness();
		try {
			for (const tool of ["find", "grep", "glob", "ast_grep"]) {
				assert.equal(await h.call(tool, {}), undefined);
				assert.equal(await h.call(tool, { path: h.root }), undefined);
			}
			await h.emit("session_compact");
			assert.equal(await h.call("grep", { path: h.root }), undefined);
			assert.equal(await h.call("glob", { path: "**/*.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("agent-rails commits run the config's commands.commit, then the whole-repo harness checks, without AGENT", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-guards-commit-")));
		const log = join(scratch, "runs.log");
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\nlabel=$1; shift\necho "$label $* AGENT=\${AGENT-unset}" >> '${log}'\necho "$label finding: src/x.py" >&2\neval "exit \\\${FAIL_$label:-0}"\n`, { mode: 0o755 });
		const h = harness({ guardrailsCommand: [script, "harness"] });
		const previous = process.env["AGENT"];
		process.env["AGENT"] = "1";
		try {
			const commit = () => h.call("bash", { command: "git commit -m x" });
			const runs = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [];
			assert.ok(reasonOf(await commit()).includes("owner/repo is not onboarded (missing)"));
			assert.deepEqual(runs(), [], "an unconfigured repository's commit is refused before any check runs");
			h.config({ commands: { commit: [script, "commit"] } });
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), ["commit  AGENT=unset", `harness --repo ${h.root} AGENT=unset`],
				"the repo command first, then the harness over the whole repo (its config); AGENT never reaches either");
			rmSync(log);
			process.env["FAIL_commit"] = "3";
			let blocked = await commit();
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes(`commands.commit (${script} commit) failed in ${h.root} (exit 3)`), String(blocked?.reason));
			assert.ok(reasonOf(blocked).includes("commit finding: src/x.py"), "the agent sees the repo command's output");
			assert.ok(!reasonOf(blocked).includes("adopt mode"), "a repo command failure is fixed in the repo");
			assert.deepEqual(runs(), ["commit  AGENT=unset"], "a failed repo command stops before the harness checks");
			rmSync(log);
			delete process.env["FAIL_commit"];
			process.env["FAIL_harness"] = "1";
			blocked = await commit();
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes(`harness checks (guardrails) failed in ${h.root} (exit 1)`), String(blocked?.reason));
			assert.ok(reasonOf(blocked).includes("harness finding: src/x.py"), String(blocked?.reason));
			assert.ok(reasonOf(blocked).includes("Checks come from .agentrails/config.json commands; the owner changes them through the setup-repo-proofs writer.") &&
				reasonOf(blocked).includes("do not reshape repo code to the harness copy"), "a harness-only finding points at the config's writer, not at reshaping repo code");
			h.config();
			rmSync(log);
			assert.equal((await commit())?.block, true, "harness checks run even when commands.commit checks nothing");
			assert.deepEqual(runs(), [`harness --repo ${h.root} AGENT=unset`]);
			delete process.env["FAIL_harness"];
			h.unconfigure();
			rmSync(log);
			assert.ok(reasonOf(await commit()).includes("owner/repo is not onboarded (missing)"));
			assert.deepEqual(runs(), [], "a removed config runs no repo or harness check");
		} finally {
			if (previous === undefined) delete process.env["AGENT"]; else process.env["AGENT"] = previous;
			delete process.env["FAIL_commit"]; delete process.env["FAIL_harness"];
			h.cleanup(); rmSync(scratch, { recursive: true, force: true });
		}
	});

	test("agent-rails commits run the harness path check over the paths the commit can take, first", async () => {
		const h = harness();
		try {
			writeFileSync(join(h.root, "tracked.ts"), "export {};\n");
			await execute("git", ["add", "existing.ts", "tracked.ts"], { cwd: h.root });
			await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], { cwd: h.root });
			const order: string[] = [];
			let checked: string[] = [];
			let pathFails = false;
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name, rest] = repoCommand(command, args);
				if (name === "path-check") {
					order.push("path check"); checked = rest;
					return { code: pathFails ? 1 : 0, killed: false, stdout: "", stderr: pathFails ? "bad path" : "" };
				}
				if (name === "commit-check") { order.push("commit"); return { code: 0, killed: false, stdout: "", stderr: "" }; }
				return exec(command, args, options);
			};
			h.config({ commands: { commit: ["commit-check"] } });
			h.govern();
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			writeFileSync(join(h.root, "staged.ts"), "export {};\n");
			await execute("git", ["add", "existing.ts", "staged.ts"], { cwd: h.root });
			writeFileSync(join(h.root, "untracked.ts"), "export {};\n");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			assert.deepEqual(order, ["path check", "commit"], "the path check runs before checks.commit");
			assert.deepEqual(checked, ["--event", "staged"],
				"the checker derives added and renamed names from the index, not unstaged or untracked paths");
			pathFails = true;
			order.length = 0;
			const blocked = await h.call("bash", { command: "git commit -m x" });
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes("path check failed") && reasonOf(blocked).includes("bad path"), String(blocked?.reason));
			assert.deepEqual(order, ["path check"], "a path check failure stops before checks.commit");
		} finally { h.cleanup(); }
	});

	test("a commit refused with dependency-audit findings carries the triage evidence fields and the judgments reference", async () => {
		const h = harness();
		try {
			let output = "src/x.py: lint finding";
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name] = repoCommand(command, args);
				if (name === "commit-check") return { code: 1, killed: false, stdout: output, stderr: "" };
				return exec(command, args, options);
			};
			h.config({ commands: { commit: ["commit-check"] } });
			h.govern();
			let reason = reasonOf(await h.call("bash", { command: "git commit -m x" }));
			assert.ok(reason.includes("src/x.py: lint finding") && !reason.includes("triage_advisories"), reason);
			output = "SECURITY.dependency-audit uv.lock:urllib3@2.0.0:GHSA-xxxx fixed in 2.0.7";
			reason = reasonOf(await h.call("bash", { command: "git commit -m x" }));
			for (const field of ["dependency_audit_findings(repo)", "triage_advisories(found, evidence)", "advisory_text", "advisory_source",
				"scope_source", "code_search", "fix_options", "blocked_fix", "constraint_source"]) {
				assert.ok(reason.includes(field), `${field}: ${reason}`);
			}
			const reference = reason.match(/See (\S+)#dependency-advisories\./)?.[1];
			assert.ok(reference && existsSync(reference), reason);
			assert.ok(readFileSync(reference, "utf8").includes("## Dependency advisories"));
		} finally { h.cleanup(); }
	});

	test("agent-rails commits need no message shape, but must be the call's only change over a fully staged index", async () => {
		const h = harness();
		try {
			h.config({ commands: { commit: ["true"] } });
			h.govern();
			assert.ok((await h.call("bash", { command: "git add -A && git commit -m x" }))?.reason?.includes("in a command of its own"));
			for (const command of [
				"git -c core.editor=true commit", "git commit", "git commit -e -m x", "git status && git commit -m x", "cd . && git commit -m x",
			]) assert.equal(await h.call("bash", { command }), undefined, command);
			for (const command of ["echo bad > src.ts && git commit -m x", "git commit -m x; git push"]) {
				assert.ok(reasonOf(await h.call("bash", { command })).includes("as its own bash command"), command);
			}
			await execute("git", ["add", "existing.ts"], { cwd: h.root });
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 0;\n");
			assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes("unstaged changes"));
			assert.ok(reasonOf(await h.call("bash", { command: "git commit -am x" })).includes("staged index only"), "structured commits commit the staged index the checks saw");
			assert.ok(reasonOf(await h.call("bash", { command: "git --exec-path=/usr/lib/git-core commit -m x" })).includes("points git at another"));
			for (const command of ["git commit --no-verify -m x", "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x"]) {
				assert.ok((await h.call("bash", { command }))?.reason?.includes("pre-commit hook"), command);
			}
		} finally { h.cleanup(); }
	});

	test("the default harness check is guardrails from the harness's own reference project", async () => {
		const h = harness({ guardrailsCommand: "default" });
		try {
			h.govern();
			const seen: string[][] = [];
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name, rest] = repoCommand(command, args);
				if (name !== "uv" || rest.includes("config-check")) return exec(command, args, options);
				assert.equal(options.cwd, h.root);
				seen.push(rest);
				return { code: 0, killed: false, stdout: "", stderr: "" };
			};
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			const project = resolve(import.meta.dir, "../agent/extensions/agent-rails/engine");
			assert.deepEqual(seen, [["run", "--project", project, "guardrails", "check", "--repo", h.root]]);
		} finally { h.cleanup(); }
	});

	test("a config missing commands.commit, or carrying an unknown field, refuses commits and pushes by name", async () => {
		const h = harness();
		try {
			h.govern();
			const complete = harnessConfig("owner/repo");
			for (const [config, named] of [
				[{ ...complete, commands: { ci: ["ci"] } }, "commands.commit: is missing"],
				[{ ...complete, commands: undefined }, "commands: is missing"], [{ ...complete, unknownField: 3 }, "unknownField: is not a config section"],
				[{ ...complete, commands: { commit: ["true"], lint: ["x"] } }, "commands.lint: is not a config key"],
			] as const) {
				h.rawConfig(JSON.stringify(config));
				for (const command of ["git commit -m x", "git push"]) {
					const refused = await h.call("bash", { command });
					assert.equal(isBlocked(refused), true, `${named}: ${command}`);
					assert.ok(reasonOf(refused).includes(named), String(refused?.reason));
				}
			}
			h.rawConfig(JSON.stringify(complete));
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("failed path checks persist at handoff and rerun to observe out-of-tool repairs", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.config();
			h.govern();
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			h.pathFailure();
			assert.equal(await h.emit("session_stop"), undefined);
			h.pathFailure("bad existing.ts path");
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
			rmSync(join(h.root, "existing.ts"));
			assert.equal(await h.emit("session_stop"), undefined, "deleted failed paths are not passed back to the checker");
		} finally { h.cleanup(); }
	});


	test("the agentrails_config writer sees this session's skill reads, restored on a branch rebuild and forgotten after compaction", async () => {
		const h = harness();
		try {
			h.govern();
			const unread = "read skill://test-guidance in this session before recording tests";
			const record = () => h.tool("agentrails_config", { section: "tests", value: {}, scope: { kind: "whole" } });
			assert.ok((await record()).includes(unread));
			await h.read("skill://test-guidance");
			assert.ok(!(await record()).includes(unread), "a successful read counts");
			h.setSession("second");
			assert.ok((await record()).includes(unread), "another session has its own reads");
			h.branch.push(
				{ type: "message", message: { role: "assistant", content: [
					{ type: "toolCall", id: "restored", name: "read", arguments: { path: "skill://test-guidance" } },
				] } },
				{ type: "message", message: { role: "toolResult", toolCallId: "restored", isError: false } },
			);
			await h.emit("session_tree");
			assert.ok(!(await record()).includes(unread), "a read on the rebuilt branch is restored");
			h.branch.push({ type: "compaction" });
			await h.emit("session_branch");
			assert.ok((await record()).includes(unread), "compaction forgets reads");
		} finally { h.cleanup(); }
	});

	test("a $agentrails line is ordinary prompt text, and the agentrails tool only opts into rollout or acknowledges a review", async () => {
		const h = harness();
		try {
			h.govern();
			for (const prompt of ["$agentrails on", "$agentrails off", "enable $agentrails\nfix the login bug", "work"]) {
				assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt }), undefined, prompt);
			}
			assert.deepEqual(JSON.parse(h.configText()), harnessConfig("owner/repo"), "the config is never written");
			assert.equal(await h.tool("agentrails", {}), "Agent rails: pass rollout or acknowledge.");
			assert.equal(await h.tool("agentrails", { acknowledge: true }), "Agent rails: no extension change is waiting for acknowledgement.");
			h.context.agent.kind = "sub";
			assert.equal(await h.tool("agentrails", { acknowledge: true }), "Agent rails: only the main agent can call the agentrails tool.");
		} finally { h.cleanup(); }
	});

	test("a repo without an origin remote runs the fixed guards only, whatever its config", async () => {
		const h = harness();
		try {
			h.config({ commands: { commit: ["false"] } });
			assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes("commands.commit (false) failed"));
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			h.setSession("no-origin");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, "no commit checks outside GitHub repositories");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined, "no onboarding refusal or reference reads");
		} finally { h.cleanup(); }
	});

	test.each(["exit128", "kill"] as const)("an origin remote read that fails (%s) refuses commits and pushes in a governed repo; edits are noted", async how => {
		const h = harness();
		try {
			h.govern();
			await withFailingOriginRead(async fail => {
				fail(how);
				const unresolved = "could not resolve this repository's agent-rails config: git remote get-url origin in";
				for (const command of ["git commit -m x", "git push origin HEAD:agent/feature"]) {
					const refused = await h.call("bash", { command });
					assert.equal(isBlocked(refused), true, command);
					assert.ok(reasonOf(refused).includes(unresolved) && reasonOf(refused).endsWith("; retry."), reasonOf(refused));
				}
				h.notes.length = 0;
				assert.equal(await h.call("edit", { path: "existing.ts" }), undefined, "an edit writes nothing through git: notified, not refused");
				assert.ok(h.notes.some(note => note.includes(unresolved)), h.notes.join("; "));

				h.notes.length = 0;
				h.setSession(`start-${how}`);
				await h.emit("session_start");
				assert.ok(h.notes.some(note => note.includes("at session start") && note.includes("refused until the owner restarts omp")), h.notes.join("; "));
				fail("pass");
				assertRefused(await h.call("bash", { command: "git commit -m x" }), "could not be read at session start");

				await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
				h.setSession(`no-origin-${how}`);
				await h.emit("session_start");
				assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, "a repo with no origin remote runs the fixed guards only");
			});
		} finally { h.cleanup(); }
	});

	test("checks AST mutation on resolve, not its preview", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.config();
			h.govern();
			assert.equal(await h.call("ast_edit", { paths: ["existing.ts"] }, {
				applied: false, files: ["existing.ts"], totalReplacements: 1,
			}), undefined);
			assert.equal(await h.emit("session_stop"), undefined);
			const result = await h.call("write", { path: "xd://resolve", content: "Apply the rewrite" }, {
				applied: true, files: ["existing.ts"],
			});
			assert.ok(result?.additionalContext?.includes("bad path"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
		} finally { h.cleanup(); }
	});
});

test("path checks forget deleted and ignored failures without skipping live repository paths", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		writeFileSync(join(h.root, ".gitignore"), "ignored.ts\n");
		writeFileSync(join(h.root, "ignored.ts"), "scratch");
		let checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad live path" }; }
			return exec(command, args, options);
		};
		h.config();
		h.govern();
		await h.call("edit", { paths: ["existing.ts", "ignored.ts", "deleted.ts"] });
		assert.deepEqual(checked, [["--event", "edit", join(h.root, "existing.ts")]]);
		rmSync(join(h.root, "existing.ts"));
		await h.call("edit", { path: "ignored.ts" });
		assert.equal(await h.emit("session_stop"), undefined);
		assert.equal(checked.length, 1);
	} finally { h.cleanup(); }
});

test("edit tools never write Git directory internals, and live files still reach the path check", async () => {
	const h = harness();
	try {
		const internal = join(h.root, ".git/info/exclude");
		const live = join(h.root, "live.ts");
		writeFileSync(internal, "# session edit\n");
		writeFileSync(live, "export const live = true;\n");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.config();
		h.govern();
		assert.ok(reasonOf(await h.call("edit", { paths: [internal, live] })).includes("inside Git's internal directory"));
		await h.call("edit", { paths: [live] });
		assert.deepEqual(checked, [["--event", "edit", live]]);
	} finally { h.cleanup(); }
});

test("path checks ignore content edits to existing tracked names", async () => {
	const h = harness();
	try {
		const path = join(h.root, "existing.ts");
		const committed = readFileSync(path, "utf8");
		await execute("git", ["add", "--", "existing.ts"], { cwd: h.root });
		await execute("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "tracked fixture"], { cwd: h.root });
		writeFileSync(path, "export const answer = 43;\n");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 1, killed: false, stdout: "", stderr: "bad tracked path" }; }
			return exec(command, args, options);
		};
		h.config();
		h.govern();
		assert.equal(await h.call("edit", { path }), undefined);
		assert.deepEqual(checked, []);
		writeFileSync(path, committed);
		assert.equal(await h.emit("session_stop"), undefined);
		assert.deepEqual(checked, []);
	} finally { h.cleanup(); }
});

test("path checks keep ignored files excluded when a path crosses a symlink", async () => {
	const h = harness();
	const outside = mkdtempSync(join(tmpdir(), "agent-rails-guards-pathcheck-"));
	try {
		writeFileSync(join(h.root, ".gitignore"), "node_modules/\n");
		mkdirSync(join(h.root, "node_modules/pkg"), { recursive: true });
		const ignored = join(h.root, "node_modules/pkg/index.js");
		writeFileSync(ignored, "module.exports = true;\n");
		writeFileSync(join(outside, "file"), "live\n");
		symlinkSync(outside, join(h.root, "link"), "dir");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.config();
		h.govern();
		await h.call("edit", { paths: [ignored, join(h.root, "link/file")] });
		assert.ok(!checked.some(args => args.some(path => path.includes("node_modules/"))),
			"ignored paths must not reach the path check when another path breaks git check-ignore");
	} finally { h.cleanup(); rmSync(outside, { recursive: true, force: true }); }
});

test("agent-rails merge requires prove()'s result file to say pass for the exact head", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		const head = "a".repeat(40);
		const command = `env gh pr merge 3 --rebase --match-head-commit=${head}`;
		mockPrCi(h, greenCi, { head, selector: "3" });
		assert.ok(reasonOf(await h.call("bash", { command })).includes("owner/repo is not onboarded (missing)"), "an unconfigured repository refuses every merge");
		h.govern();
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("--match-head-commit"));
		const missing = await h.call("bash", { command });
		assert.ok(reasonOf(missing).includes(`no prove() pass for ${head}`), String(missing?.reason));
		const proofsReference = reasonOf(missing).match(/then merge \((\S+proofs-and-merge\.md)\)/)?.[1];
		assert.ok(proofsReference && existsSync(proofsReference), "the refusal cites the proofs and merge reference by path");
		assert.ok(reasonOf(missing).includes(join(".git", "omp-prove", `${head}.result.json`)), String(missing?.reason));
		const result = join(h.root, ".git/omp-prove", `${head}.result.json`);
		mkdirSync(dirname(result), { recursive: true });
		const cases: [string, string][] = [
			["malformed", "{broken"],
			["failed", JSON.stringify({ status: "fail", head })],
			["wrong head", JSON.stringify({ status: "pass", head: "b".repeat(40) })],
			["no head", JSON.stringify({ status: "pass" })],
		];
		for (const [name, content] of cases) {
			writeFileSync(result, content);
			assert.ok((await h.call("bash", { command }))?.reason?.includes(`no prove() pass for ${head}`), name);
		}
		await proveResult(h, head);
		await structureResult(h, head);
		assert.equal(await h.call("bash", { command }), undefined);
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${head}` }), undefined);
	} finally { h.cleanup(); }
});

test("agent-rails merge requires a valid exact-head structure result and blocks every enforced finding, never a shadow one", async () => {
	const h = harness();
	try {
		await proveResult(h);
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const command = `gh pr merge 3 --rebase --match-head-commit ${ciHead}`;
		assert.ok(reasonOf(await h.call("bash", { command })).includes("owner/repo is not onboarded (missing)"), "an unconfigured repository refuses every merge");
		h.govern();
		const missing = await h.call("bash", { command });
		assert.equal(isBlocked(missing), true);
		assert.ok(reasonOf(missing).includes(`no valid structure_review() result for ${ciHead}`));
		await structureResult(h);
		const path = join(h.root, ".git/omp-structure", `${ciHead}.json`);
		const watched = (content: string) => watchedProofCall(h, () => writeFileSync(path, content), "await structure_review(repo, parent, head)");
		writeFileSync(path, readFileSync(path, "utf8") + "\n");
		assert.ok(reasonOf(await h.call("bash", { command })).includes("not written by a structure_review() call the rails saw"), "a result written outside a watched structure_review() call is not evidence");
		for (const content of [
			"{broken",
			JSON.stringify({ head: "b".repeat(40), halt: false, status: "pass", findings: [] }),
			JSON.stringify({ head: ciHead, halt: "false", status: "pass", findings: [] }),
			JSON.stringify({ head: ciHead, halt: false, status: "shadow", findings: [] }),
			JSON.stringify({ head: ciHead, halt: true, status: "blocked", findings: [{ item: "u", question: "q", mode: "enforced", status: "ready", score: 0.9, threshold: 0.7 }] }),
			JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [{ item: "u", question: "q", mode: "enforced", score: 0.9, threshold: 0.7 }] }),
			JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [{ item: "u", question: "q", score: 0.9, threshold: 0.7 }] }),
			JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [{ item: "u", question: "q", mode: "advisory", score: 0.9, threshold: 0.7 }] }),
			JSON.stringify({ head: ciHead, halt: false, status: "blocked", findings: [] }),
		]) {
			await watched(content);
			assert.equal((await h.call("bash", { command }))?.block, true);
		}
		const finding = { item: "src/orders/report.py", question: "consumer_placement", mode: "enforced", score: 0.9, threshold: 0.7 };
		const shadow = { item: "src/orders/models/order.py", question: "technical_kind_grouping", mode: "shadow", score: 0.95, threshold: 0.7 };
		await watched(JSON.stringify({ head: ciHead, halt: true, status: "blocked", findings: [finding, shadow] }));
		const blocked = await h.call("bash", { command });
		assert.equal(isBlocked(blocked), true);
		assert.ok(reasonOf(blocked).includes("src/orders/report.py: consumer_placement"));
		assert.ok(!reasonOf(blocked).includes("technical_kind_grouping"), "a shadow finding is never a merge block");
		assert.ok(reasonOf(blocked).includes("fix them and rerun prove() and structure_review() on the new head"));
		h.unconfigure();
		assert.ok(reasonOf(await h.call("bash", { command })).includes("owner/repo is not onboarded (missing)"), "a removed config refuses the merge before halt is read");
		h.govern();
		await watched(JSON.stringify({ head: ciHead, halt: false, status: "incomplete",
			findings: [], errors: ["judge offline"] }));
		assert.ok(reasonOf(await h.call("bash", { command })).includes("is incomplete (judge offline)"), "an incomplete review never admits a merge");
		await watched(JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [shadow] }));
		assert.equal(await h.call("bash", { command }), undefined, "shadow findings alone admit the merge");
		const scored = { probability: 0.1, score: 0.1, outcome: "no", digest: "d" };
		await watched(JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [],
			pairs: { "a.py::x<>b.py::y": scored }, kind_files: { "app/models/a.py": scored }, consumers: {} }));
		assert.equal(await h.call("bash", { command }), undefined, "a result carrying its scored pairs and files admits the merge");
		await structureResult(h);
		assert.equal(await h.call("bash", { command }), undefined);
	} finally { h.cleanup(); }
});

test.each([
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }] },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "PENDING" }] },
	{ name: "no checks", rollup: [] },
	{ name: "green", rollup: greenCi },
])("ci.enabled false: a structured merge needs no CI status ($name)", async scenario => {
	const h = harness();
	try {
		h.config({ ci: { enabled: false } });
		h.govern();
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [...scenario.rollup], { head: ciHead, selector: "3" });
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

const requiredRefusal = (state: string) => `Agent rails: required CI context "gate" is ${state} for ${ciHead} in base-owner/base-repo (runner ci-gate). Submit and wait: python3 ${ciRunner} submit ci-gate base-owner/base-repo ${ciHead}, then python3 ${ciRunner} wait ci-gate base-owner/base-repo ${ciHead} --context gate; see skill://git-tool/references/merge-and-recover.md for failures.`;
test.each([
	{ name: "missing among other green checks", rollup: [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], state: "missing" },
	{ name: "missing with no checks", rollup: [], state: "missing" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "gate", state: "PENDING" }], state: "PENDING" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "gate", state: "FAILURE" }], state: "FAILURE" },
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "FAILURE" }], state: "COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "QUEUED", conclusion: null }], state: "QUEUED/" },
	{ name: "success as StatusContext", rollup: [
		{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
		{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" },
	] },
	{ name: "success as CheckRun", rollup: [{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "SUCCESS" }] },
	{ name: "success while other checks are red or pending", rollup: [
		{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
		{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" },
		{ __typename: "StatusContext", context: "docs-preview", state: "PENDING" },
	] },
])("required context merge: $name", async scenario => {
	const h = harness();
	try {
		await originRemote(h, "git@github.com:base-owner/base-repo.git");
		h.config(ciGate);
		h.govern();
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [...scenario.rollup], { head: ciHead, selector: "3", url: "https://github.com/base-owner/base-repo/pull/3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		if ("state" in scenario) {
			assert.equal(isBlocked(result), true, "the required context must block");
			assert.equal(result?.reason, requiredRefusal(scenario.state));
		} else assert.equal(result, undefined);
	} finally { h.cleanup(); }
});

test("agent tools never write a repository's agent-rails config; reads pass", async () => {
	const h = harness();
	try {
		h.config();
		const text = h.configText();
		const foreign = join(h.machine, "elsewhere", ".agentrails", "config.json");
		for (const path of [h.repoConfigPath, ".agentrails/config.json", join(h.root, ".agentrails", "draft.json"), foreign]) {
			const calls: [string, Input][] = [["write", { path, content: "{}" }], ["edit", { paths: [path] }], ["ast_edit", { paths: [path] }]];
			for (const [tool, input] of calls) {
				const refused = await h.call(tool, input);
				assert.equal(isBlocked(refused), true, `${tool} ${path}`);
				assert.ok(reasonOf(refused).includes("agent-rails config (.agentrails/); agents read it but never write it"), reasonOf(refused));
			}
		}
		h.context.agent.kind = "sub";
		assert.equal(isBlocked(await h.call("write", { path: h.repoConfigPath, content: "{}" })), true, "subagents never write it either");
		h.context.agent.kind = "main";
		for (const command of ["cp /tmp/x .agentrails/config.json", "echo '{}' > .agentrails/config.json", "sed -i '' s/ask/allowed/ .agentrails/config.json",
			"rm -rf .agentrails", `python3 -c "open('${h.repoConfigPath}', 'w').write('{}')"`, "mv /tmp/config.json ~/repo/.agentrails/"]) {
			const refused = await h.call("bash", { command });
			assert.equal(isBlocked(refused), true, command);
			assert.ok(reasonOf(refused).includes("an agent-rails config"), `${command}: ${reasonOf(refused)}`);
		}
		const evalWrite = await h.call("eval", { code: `open('${h.repoConfigPath}', 'w').write('{}')` });
		assert.ok(reasonOf(evalWrite).includes("eval may not write rail state"), reasonOf(evalWrite));
		for (const command of ["cat .agentrails/config.json", "jq .autonomy .agentrails/config.json", "ls .agentrails"]) {
			assert.equal(await h.call("bash", { command }), undefined, command);
		}
		assert.equal(await h.call("read", { path: h.repoConfigPath }), undefined, "agents read the config");
		assert.equal(h.configText(), text);
	} finally { h.cleanup(); }
});

test("merges in a repository that is not onboarded, or is onboarding, are refused without querying GitHub", async () => {
	const h = harness();
	try {
		forbidGh(h);
		const merge = `gh pr merge 3 --rebase --match-head-commit ${ciHead}`;
		assert.ok(reasonOf(await h.call("bash", { command: merge })).includes("owner/repo is not onboarded (missing)"));
		await h.onboard();
		assert.ok(reasonOf(await h.call("bash", { command: merge })).includes("the owner merges the bootstrap PR"));
	} finally { h.cleanup(); }
});

test("a gate with every optional check and a ci block allows publishing", async () => {
	const h = harness();
	try {
		h.config({ ...ciGate, commands: { commit: ["true"], ci: ["make", "ci"], format: ["ruff", "format"] } });
		assert.equal(await h.call("bash", { command: "git push" }), undefined);
	} finally { h.cleanup(); }
});

test.each(["git@github.com:OWNER/REPO.git", "https://github.com/owner/repo.git"])("PR URL repository: %s", async remote => {
	const h = harness();
	try {
		await originRemote(h, remote);
		h.config(ciGate);
		h.govern();
		await proveResult(h);
		await structureResult(h);
		const url = "https://github.com/other/checkout/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], { head: ciHead, selector: url, url });
		const result = await h.call("bash", { command: `gh pr merge ${url} --rebase --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true, "cross-repo URL must not use checkout policy");
		assert.ok(reasonOf(result).includes("run from a checkout of other/checkout"), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR URL selector accepts the checkout repository case-insensitively", async () => {
	const h = harness();
	try {
		await originRemote(h, "git@github.com:OWNER/REPO.git");
		h.config(ciGate);
		h.govern();
		await proveResult(h);
		await structureResult(h);
		const url = "https://github.com/owner/repo/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], { head: ciHead, selector: url });
		assert.equal(await h.call("bash", { command: `gh pr merge ${url} --rebase --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("required context blocks merge despite a prove() pass", async () => {
	const h = harness();
	try {
		h.govern();
		h.config(ciGate);
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true, "the required context must block merge despite a prove() pass");
		assert.ok(reasonOf(result).includes(`required CI context "gate" is missing for ${ciHead} in owner/repo`), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR query contract: requests the PR URL", async () => {
	const h = harness();
	try {
		await originRemote(h);
		h.govern();
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const exec = h.api.exec.bind(h.api);
		let fields: string | undefined;
		h.api.exec = async (command, args, options) => {
			if (command === "gh" && args[0] === "pr" && args[1] === "view") fields ??= args.at(-1);
			return exec(command, args, options);
		};
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` }), undefined);
		assert.equal(fields, "number,headRefOid,baseRefName,headRefName,state,statusCheckRollup,url");
	} finally { h.cleanup(); }
});

test("a merge reports a gh failure while reading the PR", async () => {
	const h = harness();
	try {
		h.govern();
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [], { head: ciHead, selector: "3", failure: "not authenticated" });
		const result = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		assert.ok(reasonOf(result).includes("cannot read PR head/CI via gh: not authenticated"), String(result?.reason));
	} finally { h.cleanup(); }
});

test.each(["head mismatch", "red CI", "green CI"])("merge CI gate: %s", async scenario => {
	const h = harness();
	try {
		h.config({ ci: { enabled: true, runner: "github-actions", requiredContext: "test" } });
		h.govern();
		await proveResult(h);
		await structureResult(h);
		const head = scenario === "head mismatch" ? "b".repeat(40) : ciHead;
		mockPrCi(h, scenario === "red CI"
			? [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }] : greenCi, { head, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		if (scenario === "green CI") assert.equal(result, undefined);
		else {
			assert.equal(isBlocked(result), true);
			const fragment = scenario === "head mismatch" ? `PR head is ${head}, not ${ciHead}; prove() the current head` : 'required CI context "test" is COMPLETED/FAILURE';
			assert.equal(reasonOf(result).includes(fragment), true, fragment);
		}
	} finally { h.cleanup(); }
});

test("an invalid config refuses merges before the CI guard and autonomy", async () => {
	const h = harness();
	try {
		forbidGh(h);
		for (const config of [{ ...harnessConfig("owner/repo", ciGate), mergePolicies: "none" }, { ...harnessConfig("owner/repo", { ...ciGate, ...askAutonomy }), extra: 1 }]) {
			h.rawConfig(JSON.stringify(config));
			assert.ok(reasonOf(await h.call("bash", { command: `gh pr merge 3 --merge --match-head-commit ${ciHead}` })).includes("owner/repo is not onboarded (invalid: "));
		}
	} finally { h.cleanup(); }
});

test("autonomy.merge ask refuses a structured merge until the owner approves that PR number, once", async () => {
	const h = harness();
	try {
		h.config({ ...ciGate, autonomy: { merge: "ask" } });
		h.govern();
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], { head: ciHead, selector: "agent/test" });
		const command = `gh pr merge agent/test --rebase --match-head-commit ${ciHead}`;
		const refused = reasonOf(await h.call("bash", { command }));
		assert.ok(refused.includes("sets autonomy.merge to ask, so merging PR #3 needs the owner's approval"), refused);
		assert.ok(refused.includes("`$approve autonomy:merge:3`"), refused);
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$approve autonomy:merge:4" });
		assert.ok(reasonOf(await h.call("bash", { command })).includes("autonomy:merge:3"), "another PR's approval does not cover this one");
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$approve autonomy:merge:3" });
		assert.equal(await h.call("bash", { command }), undefined);
		assert.ok(reasonOf(await h.call("bash", { command })).includes("autonomy:merge:3"), "the merge consumed the approval");
	} finally { h.cleanup(); }
});

test.each([
	{ name: "missing flag", args: "", fragment: "requires --match-head-commit <40-hex verified SHA>" },
	{ name: "bare variable", args: "--match-head-commit $H", fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "quoted variable", args: '--match-head-commit "$H"', fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "non-hex", args: "--match-head-commit nope", fragment: "must be a lowercase 40-hex SHA, got nope" },
])("merge diagnostics gate: $name", async scenario => {
	const h = harness();
	try {
		h.govern();
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 ${scenario.args}` });
		assert.equal(isBlocked(result), true);
		assert.equal(reasonOf(result).includes(scenario.fragment), true, scenario.fragment);
	} finally { h.cleanup(); }
});

test("merge diagnostics gate: missing prove() result", async () => {
	const h = harness();
	try {
		h.govern();
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true);
		assert.equal(reasonOf(result).includes(`no prove() pass for ${ciHead}`), true);
	} finally { h.cleanup(); }
});

test("path checks canonicalize a symlinked repository root before executing", async () => {
	const h = harness();
	const aliases = mkdtempSync(join(tmpdir(), "agent-rails-guards-alias-"));
	try {
		const alias = join(aliases, "repo");
		symlinkSync(h.root, alias);
		h.context.cwd = alias;
		h.config();
		h.govern();
		const exec = h.api.exec.bind(h.api);
		let checked = false;
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") {
				checked = true;
				assert.equal(options.cwd, h.root);
				assert.deepEqual(rest, ["--event", "edit", join(h.root, "existing.ts")]);
				return { code: 0, killed: false, stdout: "", stderr: "" };
			}
			return exec(command, args, options);
		};
		assert.equal(await h.call("edit", { path: join(alias, "existing.ts") }), undefined);
		assert.equal(checked, true);
	} finally { h.cleanup(); rmSync(aliases, { recursive: true, force: true }); }
});

test("path check runs under the one fixed rails timeout; a killed checker is surfaced, not thrown", async () => {
	const h = harness();
	try {
		const timeouts: (number | undefined)[] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name] = repoCommand(command, args);
			if (name === "path-check") { timeouts.push(options.timeout); return { code: 1, killed: true, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.config();
		h.govern();
		assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
		assert.deepEqual(timeouts, [20000]);
	} finally { h.cleanup(); }
});

test("child handoff requires its own repaired path checks", async () => {
	const h = harness();
	try {
		h.context.agent.kind = "sub";
		assert.equal(await h.emit("session_stop"), undefined);
		h.pathFailure("bad path");
		h.config({ commands: { commit: ["not-run"], ci: ["not-run"] } });
		h.govern();
		await h.call("edit", { path: join(h.root, "existing.ts") });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.pathFailure();
		assert.equal(await h.emit("session_stop"), undefined);
	} finally { h.cleanup(); }
});

test("linked worktrees resolve the main checkout's config", async () => {
	const h = harness();
	const linked = `${h.root}-linked`;
	try {
		h.config();
		await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed"], { cwd: h.root });
		await execute("git", ["worktree", "add", "-b", "agent/source", linked], { cwd: h.root });
		h.context.cwd = linked;
		assert.equal(existsSync(join(linked, ".agentrails")), false, "the linked worktree holds no config of its own");
		const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: linked })).stdout.trim();
		mockPrCi({ ...h, root: linked }, greenCi, { head, selector: "3" });
		const blocked = await h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit=${head}` });
		assert.ok(reasonOf(blocked).includes(join(realpathSync(h.root), ".git", "omp-prove", `${head}.result.json`)),
			`the result file lives in the shared Git common directory: ${blocked?.reason}`);
	} finally { h.cleanup(); rmSync(linked, { recursive: true, force: true }); }
});

test("mise commits run the config's commit command in a real repository", async () => {
	const h = harness();
	try {
		h.govern();
		h.config({ commands: { commit: ["true"] } });
		assert.equal(await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }), undefined);
		h.config({ commands: { commit: ["false"] } });
		assert.ok((await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }))?.reason?.includes("commands.commit (false) failed"));
	} finally { h.cleanup(); }
});

test("mise directory options resolve the wrapped commit repository", async () => {
	const h = harness();
	const target = harness({ configRoot: h.configDir, remote: "git@github.com:owner/target.git" });
	try {
		target.config({ commands: { commit: ["true"] } });
		target.govern();
		target.config({ commands: { commit: ["false"] } });
		for (const command of [
			`mise x -C '${target.root}' -- git commit -m x`,
			`mise exec --cd='${target.root}' node@20 -- git commit -m x`,
		]) {
			const enforced = await h.call("bash", { command });
			assert.ok(reasonOf(enforced).includes(`commands.commit (false) failed in ${target.root}`), `the wrapped repo's config is enforced: ${command}`);
		}
		const unresolved = await h.call("bash", { command: "mise x -C /nonexistent-rails-directory -- git commit -m x" });
		assert.equal(isBlocked(unresolved), true);
		assert.ok(reasonOf(unresolved).includes("effective cwd"));
	} finally { h.cleanup(); target.cleanup(); }
});

test.each(["outside", "governed"])("repo-scoped harness process runs only in a governed repository (%s)", async scope => {
	const h = harness(scope === "outside" ? { remote: "https://gitlab.com/owner/repo.git" } : {});
	try {
		if (scope === "governed") h.govern();
		h.pathFailure("path policy violation");
		const edit = await h.call("write", { path: "tests/new.test.ts" });
		if (scope === "governed") {
			assert.equal(isBlocked(edit), false, "a governed repository needs no reads before a new test file");
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path policy violation"));
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			h.pathFailure();
		} else {
			assert.equal(edit, undefined);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
		}
		assert.equal(await h.emit("session_stop"), undefined);
	} finally { h.cleanup(); }
});

test("edit and handoff policy follows each edited repo, not the session cwd", async () => {
	const h = harness();
	const other = harness({ configRoot: h.configDir, remote: "https://gitlab.com/owner/other.git" });
	try {
		h.govern();
		h.pathFailure("enabled repo violation");
		assert.equal(await h.call("edit", { path: join(other.root, "existing.ts") }), undefined);
		assert.equal(await h.emit("session_stop"), undefined, "editing only an ungoverned repo runs no path check");
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.additionalContext?.includes("enabled repo violation"));
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.unconfigure();
		assert.equal(await h.emit("session_stop"), undefined, "a removed config suppresses outstanding retries");
		assert.ok(reasonOf(await h.call("edit", { path: join(h.root, "existing.ts") })).includes("owner/repo is not onboarded (missing)"));
	} finally { h.cleanup(); other.cleanup(); }
});

test("native rules always load: session start never changes the user's ttsr.disabledRules", async () => {
	for (const governed of [false, true]) {
		const h = harness();
		try {
			if (governed) h.govern();
			const rules = lookup("ttsr.disabledRules");
			assert.ok(rules);
			rules.override(h.api.pi.settings, ["user-disabled-rule"]);
			await h.emit("session_start");
			assert.deepEqual(rules.get(h.api.pi.settings), ["user-disabled-rule"], `governed: ${governed}`);
			const mapping = z.record(z.string(), z.unknown()).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
			assert.equal("structuredRules" in mapping, false, "rails.yml lists no rules to switch");
		} finally { h.cleanup(); }
	}
});

test("structured merges refuse --admin before reading the PR", async () => {
	const h = harness();
	try {
		h.govern();
		forbidGh(h);
		for (const flag of ["--admin", "--admin=true"]) {
			const refused = await h.call("bash", { command: `gh pr merge 3 --rebase ${flag} --match-head-commit ${ciHead}` });
			assert.ok(reasonOf(refused).includes("--admin bypasses"), flag);
		}
	} finally { h.cleanup(); }
});

test("without a matching merge policy a merge must rebase and carry proof", async () => {
	const h = harness();
	try {
		h.govern();
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		for (const flags of ["", "--merge", "--squash", "-s", "--rebase --merge"]) {
			const refused = await h.call("bash", { command: `gh pr merge 3 ${flags} --match-head-commit ${ciHead}` });
			assert.ok(reasonOf(refused).includes("the default policy merges with --rebase"), flags);
		}
		assert.ok(reasonOf(await h.call("bash", { command: `gh pr merge 3 -r --match-head-commit ${ciHead}` })).includes("no prove() pass"));
		await proveResult(h);
		await structureResult(h);
		assert.equal(await h.call("bash", { command: `gh pr merge 3 -r --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("gates.commitChecks and gates.pathCheck switch the commit checks and the path checks off one by one", async () => {
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "rails-gates-")));
	const log = join(scratch, "runs.log");
	const script = join(scratch, "check.sh");
	writeFileSync(script, `#!/bin/sh\necho "$1" >> '${log}'\nexit 1\n`, { mode: 0o755 });
	const h = harness({ guardrailsCommand: [script, "harness"] });
	try {
		const runs = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [];
		const pathChecks: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") pathChecks.push(rest);
			return exec(command, args, options);
		};
		h.pathFailure("bad path");
		h.govern();
		h.config({ commands: { commit: [script, "commit"] } });
		assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes("staged path check failed"), "every gate is on by default");
		h.config({ commands: { commit: [script, "commit"] }, gates: { pathCheck: false } });
		assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes(`commands.commit (${script} commit) failed`));
		assert.deepEqual(runs(), ["commit"]);
		pathChecks.length = 0;
		writeFileSync(join(h.root, "fresh.ts"), "export {};\n");
		assert.equal(await h.call("edit", { path: "fresh.ts" }), undefined, "gates.pathCheck false runs no post-edit path check");
		assert.ok(!reasonOf(await h.emit("session_stop")).includes("path check"), "nor a handoff path check");
		assert.deepEqual(pathChecks, [], "no path check ran");
		h.config({ commands: { commit: [script, "commit"] }, gates: { pathCheck: false, commitChecks: false } });
		rmSync(log);
		assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, "gates.commitChecks false runs neither commands.commit nor the harness checks");
		assert.deepEqual(runs(), []);
		h.config({ commands: { commit: ["true"] }, gates: { commitChecks: false } });
		assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes("staged path check failed"), "the staged path check follows gates.pathCheck alone");
		assert.ok(reasonOf(await h.call("bash", { command: "git commit -a -m x" })).includes("commit the staged index only"), "commit-scope stays fixed");
	} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
});

test("gates.prove and gates.structureReview decide which proof a proof merge policy needs", async () => {
	const h = harness();
	try {
		h.govern();
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const merge = () => h.call("bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` });
		assert.ok(reasonOf(await merge()).includes("no prove() pass"));
		h.config({ gates: { prove: false } });
		assert.ok(reasonOf(await merge()).includes("no valid structure_review() result"), "gates.prove false skips only prove()");
		h.config({ gates: { prove: false, structureReview: false } });
		assert.equal(await merge(), undefined, "with both off a proof policy needs no proof");
		h.config({ gates: { structureReview: false } });
		assert.ok(reasonOf(await merge()).includes("no prove() pass"));
		await proveResult(h);
		assert.equal(await merge(), undefined, "gates.structureReview false skips only structure_review()");
	} finally { h.cleanup(); }
});

test("a config merge policy sets the method and whether proof is needed for its base and head prefix", async () => {
	const h = harness();
	try {
		h.govern();
		h.config({
			ci: { enabled: true, runner: "github-actions", requiredContext: "test" },
			mergePolicies: [{ base: "nuc-config", headPrefix: "agent/nuc-sync-", method: "merge", proof: false }],
		});
		mockPrCi(h, greenCi, { head: ciHead, selector: "3", base: "nuc-config", headRef: "agent/nuc-sync-2026-11-21" });
		const sync = (flags: string) => h.call("bash", { command: `gh pr merge 3 ${flags} --match-head-commit ${ciHead}` });
		assert.equal(await sync("--merge"), undefined, "the sync policy needs no prove() or structure_review() result");
		assert.ok(reasonOf(await sync("--rebase")).includes("mergePolicies {base: nuc-config, headPrefix: agent/nuc-sync-} merges with --merge"));
		assert.ok(reasonOf(await sync("--merge --admin")).includes("--admin bypasses"));
		mockPrCi(h, [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }],
			{ head: ciHead, selector: "3", base: "nuc-config", headRef: "agent/nuc-sync-2026-11-21" });
		assert.ok(reasonOf(await sync("--merge")).includes('required CI context "test" is COMPLETED/FAILURE'), "a policy never waives CI");
		mockPrCi(h, greenCi, { head: ciHead, selector: "3", base: "nuc-config", headRef: "agent/feature" });
		assert.ok(reasonOf(await sync("--merge")).includes("the default policy merges with --rebase"), "another head prefix gets the default");
		assert.ok(reasonOf(await sync("--rebase")).includes("no prove() pass"));
	} finally { h.cleanup(); }
});

type LedgerHarness = ReturnType<typeof harness>;
type LedgerRow = {
	ts: string; session: string; repo: string | null; tool: string; kind: string; code: string;
	reason: string | null; calls: number | null; refusals: number | null; machine: string;
};

function ledger(h: LedgerHarness): LedgerRow[] {
	const path = storePath(h.machine);
	if (!existsSync(path)) return [];
	return withStore(db => db.query<LedgerRow, []>(
		"SELECT ts, session, repo, tool, kind, code, reason, calls, refusals, machine FROM rail_events ORDER BY id",
	).all(), path);
}

function brief(entries: LedgerRow[]): string[] {
	return entries.map(entry => `${entry.kind} ${entry.code} ${entry.tool}${entry.kind === "resolved" ? ` calls=${entry.calls} refusals=${entry.refusals}` : ""}`);
}

describe("rails ledger", () => {
	test("a refusal writes a coded block row and a later passing call on the same target resolves it", async () => {
		const h = harness();
		try {
			assert.equal(isBlocked(await h.call("edit", { path: "existing.ts" })), true);
			assert.equal(isBlocked(await h.call("edit", { path: "existing.ts" })), true);
			await h.call("bash", { command: "ls" });
			const [first] = ledger(h);
			assert.ok(first);
			assert.equal(first.session, "first");
			assert.equal(first.repo, "owner/repo");
			assert.equal(first.machine, machineName());
			assert.ok(first.reason?.startsWith("Agent rails: owner/repo is not onboarded (missing)."), first.reason ?? "");
			h.govern();
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.deepEqual(brief(ledger(h)), ["block onboarding-required edit", "block onboarding-required edit", "resolved onboarding-required edit calls=3 refusals=2"],
				"an unrelated passing call resolves nothing; the episode counts both refusals and the calls to the pass");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.equal(ledger(h).length, 3, "passing calls with no open episode write nothing");
		} finally { h.cleanup(); }
	});

	test("Git refusals carry their own codes and resolve on the same Git verb", async () => {
		const h = harness();
		try {
			h.govern();
			assert.equal(isBlocked(await h.call("bash", { command: "git commit -a -m x" })), true);
			assert.equal(isBlocked(await h.call("bash", { command: "git push origin main" })), true);
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			const override = await h.call("bash", { command: "gh pr create -R other/repo" });
			assert.ok(reasonOf(override).startsWith("Agent rails: policy could not be evaluated: Error: gh remote repository overrides"), "the text is unchanged");
			assert.equal(isBlocked(await h.call("bash", { command: "echo 'unterminated" })), true);
			assert.deepEqual(brief(ledger(h)), ["block commit-scope bash", "block push-default-branch bash", "resolved commit-scope bash calls=2 refusals=1",
				"block gh-repo-override bash", "block shell-unparseable bash"]);
			assert.equal(ledger(h)[0]?.repo, "owner/repo");
		} finally { h.cleanup(); }
	});

	test("ledger rows never carry command output bodies or hook-skip values", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-ledger-")));
		const script = join(scratch, "check.sh");
		writeFileSync(script, "#!/bin/sh\necho 'COMMIT-BODY finding in src/x.py'\nexit 3\n", { mode: 0o755 });
		const h = harness();
		try {
			h.config({ commands: { commit: [script] } });
			h.govern();
			const blocked = await h.call("bash", { command: "git commit -m x" });
			assert.ok(reasonOf(blocked).includes("COMMIT-BODY"), "the agent still sees the output");
			h.pathFailure("STAGED-BODY naming finding");
			assert.ok(reasonOf(await h.call("bash", { command: "git commit -m x" })).includes("STAGED-BODY"));
			assert.equal(isBlocked(await h.call("bash", { command: "SKIP=SKIP-VALUE git commit -m x" })), true);
			const entries = ledger(h);
			assert.deepEqual(entries.map(entry => entry.code), ["commit-checks", "staged-path-check", "hook-env"]);
			const summaries = [
				`Agent rails: commands.commit (${script}) failed in ${h.root} (exit 3)`,
				`Agent rails: staged path check failed in ${h.root}`,
				"Agent rails: pre-commit hook environment mutation is forbidden: SKIP",
			];
			entries.forEach((entry, index) => {
				const reason = entry.reason ?? "";
				assert.ok(reason.length <= 200, `reasons are capped at 200 characters: ${reason}`);
				assert.ok((summaries[index] ?? "").startsWith(reason.replace(/…$/, "")), `the reason is the summary: ${reason}`);
			});
			const stored = JSON.stringify(entries);
			for (const body of ["COMMIT-BODY", "STAGED-BODY", "SKIP-VALUE"]) assert.equal(stored.includes(body), false, body);
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("post-edit path-check failures are logged without their findings and resolve when the path check passes", async () => {
		const h = harness();
		try {
			h.govern();
			h.pathFailure("FINDING-BODY use feature ownership");
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { path: "another.ts" });
			assert.ok(result?.additionalContext?.includes("FINDING-BODY"));
			h.pathFailure();
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			const entries = ledger(h);
			assert.deepEqual(brief(entries), ["context path-check edit", "resolved path-check edit calls=1 refusals=1"]);
			assert.equal(entries[0]?.reason, `Agent rails: path check failed in ${h.root}`);
		} finally { h.cleanup(); }
	});

	test("a refused handoff resolves when a later handoff passes", async () => {
		const h = harness();
		try {
			h.govern();
			h.pathFailure("bad path");
			await h.call("edit", { path: "existing.ts" });
			assert.equal((await h.emit("session_stop"))?.decision, "block");
			h.pathFailure();
			assert.equal(await h.emit("session_stop"), undefined);
			assert.deepEqual(brief(ledger(h)), ["context path-check edit", "block handoff-path-check session_stop",
				"resolved path-check edit calls=0 refusals=1", "resolved handoff-path-check session_stop calls=0 refusals=1"]);
		} finally { h.cleanup(); }
	});

	test("a ledger write failure is noted and never changes the decision", async () => {
		const h = harness();
		try {
			writeFileSync(join(h.machine, "agent-rails"), "not a directory\n");
			const blocked = await h.call("bash", { command: "git commit -m x" });
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes("owner/repo is not onboarded (missing)"));
			assert.ok(h.notes.some(note => note.startsWith(`Agent rails: cannot write the rails ledger ${storePath(h.machine)}:`)), h.notes.join("\n"));
		} finally { h.cleanup(); }
	});
});
