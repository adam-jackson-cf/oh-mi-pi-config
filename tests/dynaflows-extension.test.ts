import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { harness as createHarness, execute, repoCommand, defaultOrigin, integrity, structure, enforcement, tiers, verification, ciHead, greenCi, mockPrCi, forbidGh, ciGate, ciRunner, originRemote, proveResult, structureResult, railsConfig } from "./agent-rails.test";

// Most cases exercise the co-registered extensions; isolated package cases select dynaflows only.
const harness = (options: Parameters<typeof createHarness>[0] = {}) => createHarness({ extensions: "both", ...options });
const proofs = "skill://dynaflows/references/proofs.md";

describe("dynaflows extension", () => {
	test("blocks new code and test files until every mapped reference is successfully read", async () => {
		const h = harness({ extensions: "dynaflows" });
		try {
			h.dynaflows(true);
			const input = { path: "tests/new.test.ts", content: "test('behaviour', () => {});" };
			const blocked = await h.call("write", input);
			assert.equal(blocked?.block, true);
			for (const url of [integrity, structure, tiers, enforcement, proofs]) assert.ok(blocked?.reason?.includes(url));
			await h.emit("tool_result", { toolName: "read", input: { path: tiers }, isError: true });
			await h.read(integrity, structure);
			assert.ok((await h.call("write", input))?.reason?.includes(tiers));
			await h.read(`${tiers}:1-40:raw`, enforcement, proofs);
			assert.equal(await h.call("write", input), undefined);
		} finally { h.cleanup(); }
	});

	test("dynaflows gates both existing code and new tests on their mapped reads", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			assert.ok((await h.call("edit", { path: "existing.ts" }))?.reason?.includes(integrity));
			await h.read(integrity, structure);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok((await h.call("write", { path: "tests/contract.test.ts" }))?.reason?.includes(tiers));
			await h.read(tiers, enforcement, proofs);
			assert.equal(await h.call("write", { path: "tests/contract.test.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	test("runs the harness path check with all changed paths, surfacing command failures", async () => {
		const h = harness();
		try {
			h.pathFailure("use feature ownership");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure);
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid records leave edit reads off without throwing", async () => {
		const h = harness();
		try {
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.equal(h.notes.length, 0, "a repo without a record is normal, not noteworthy");
			h.rawGate("{broken");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok(h.notes.some(note => note.includes(join(h.recordDir(), "gate.json"))));
			h.rawGate(JSON.stringify({ schemaVersion: 2, checks: { commit: "not an argv array" } }));
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.ok(h.notes.some(note => note.includes("invalid")));
			await h.read(integrity);
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
		} finally { h.cleanup(); }
	});

	for (const dynaflows of [false, true]) {
		test(`handoff needs only the verification read after code edits, never a check run (dynaflows ${dynaflows ? "on" : "off"})`, async () => {
			const h = harness();
			try {
				h.dynaflows(dynaflows);
				h.gate({ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["bun", "ci"] } });
				await h.emit("session_start");
				writeFileSync(join(h.root, "existing.ts"), "pulled change\n");
				writeFileSync(join(h.root, "session.log"), "growing log\n");
				assert.equal(await h.emit("session_stop"), undefined, "tree changes outside this session's edits never block");
				await h.read(integrity, structure);
				await h.call("edit", { path: "existing.ts" });
				const blocked = await h.emit("session_stop");
				if (dynaflows) {
					assert.equal(blocked?.decision, "block");
					assert.ok(blocked?.reason?.includes(verification));
					assert.ok(!blocked?.reason?.includes("bun"), "no repository check is demanded at handoff");
				} else assert.equal(blocked, undefined);
				await h.read(verification);
				writeFileSync(join(h.root, "session.log"), "grew again\n");
				assert.equal(await h.emit("session_stop"), undefined);
			} finally { h.cleanup(); }
		});
	}

	test("discovery tools are never gated by reference reads, before or after compaction", async () => {
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

	test("newFile reads apply only to new code files inside a repository", async () => {
		const h = harness();
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-scratch-")));
		try {
			h.dynaflows(true);
			assert.equal(await h.call("write", { path: join(scratch, "notes.md"), content: "# notes\n" }), undefined,
				"a new file outside any repository needs no reads");
			await h.read(integrity);
			assert.equal(await h.call("write", { path: join(scratch, "scratch.ts"), content: "export {};\n" }), undefined,
				"a new code file outside any repository needs no newFile reads");
			assert.equal(await h.call("write", { path: "notes.md", content: "# notes\n" }), undefined,
				"a new non-code file inside a repository needs no reads");
			const blocked = await h.call("write", { path: "src/new.ts", content: "export {};\n" });
			assert.equal(blocked?.block, true);
			for (const url of [structure]) assert.ok(blocked?.reason?.includes(url));
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("dynaflows commits run the record's checks.commit, then the whole-repo harness checks, without AGENT", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "agent-rails-commit-")));
		const log = join(scratch, "runs.log");
		// Appends its label, argv and AGENT to the log, then exits with $FAIL_<label> (default 0).
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\nlabel=$1; shift\necho "$label $* AGENT=\${AGENT-unset}" >> '${log}'\necho "$label finding: src/x.py" >&2\neval "exit \\\${FAIL_$label:-0}"\n`, { mode: 0o755 });
		const h = harness({ guardrailsCommand: [script, "harness"] });
		const previous = process.env.AGENT;
		process.env.AGENT = "1";
		try {
			await h.read("skill://git-tool");
			const commit = () => h.call("bash", { command: "git commit -m x" });
			const runs = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [];
			h.gate({ schemaVersion: 2, checks: { commit: [script, "commit"] } });
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "dynaflows off: rails run no repo or harness check");
			h.dynaflows(true);
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), ["commit  AGENT=unset", `harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`],
				"the repo command first, then the harness over the whole repo with the record; AGENT never reaches either");
			rmSync(log);
			process.env.FAIL_commit = "3";
			let blocked = await commit();
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`checks.commit (${script} commit) failed in ${h.root} (exit 3)`), blocked?.reason);
			assert.ok(blocked?.reason?.includes("commit finding: src/x.py"), "the agent sees the repo command's output");
			assert.ok(!blocked?.reason?.includes("adopt mode"), "a repo command failure is fixed in the repo");
			assert.deepEqual(runs(), ["commit  AGENT=unset"], "a failed repo command stops before the harness checks");
			rmSync(log);
			delete process.env.FAIL_commit;
			process.env.FAIL_harness = "1";
			blocked = await commit();
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes(`harness checks (guardrails) failed in ${h.root} (exit 1)`), blocked?.reason);
			assert.ok(blocked?.reason?.includes("harness finding: src/x.py"), blocked?.reason);
			assert.ok(blocked?.reason?.includes("checks.commit passed. If a repo checker already enforces a failing step's rules"),
				"a harness-only finding points at adopt mode before the agent reshapes repo code");
			h.gate({ schemaVersion: 2 });
			rmSync(log);
			assert.equal((await commit())?.block, true, "harness checks run even when the record declares no checks.commit");
			assert.deepEqual(runs(), [`harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`]);
			delete process.env.FAIL_harness;
			h.machineDynaflows(false);
			rmSync(log);
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "the machine default off turns enforcement off with dynaflows");
		} finally {
			if (previous === undefined) delete process.env.AGENT; else process.env.AGENT = previous;
			delete process.env.FAIL_commit; delete process.env.FAIL_harness;
			h.cleanup(); rmSync(scratch, { recursive: true, force: true });
		}
	});

	test("dynaflows commits run the harness path check over the paths the commit can take, first", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
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
			h.gate({ schemaVersion: 2, checks: { commit: ["commit-check"] } });
			h.dynaflows(true);
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			writeFileSync(join(h.root, "staged.ts"), "export {};\n");
			await execute("git", ["add", "staged.ts"], { cwd: h.root });
			writeFileSync(join(h.root, "untracked.ts"), "export {};\n");
			assert.equal(await h.call("bash", { command: "git commit -am x" }), undefined);
			assert.deepEqual(order, ["path check", "commit"], "the path check runs before checks.commit");
			assert.deepEqual(checked.sort(), [join(h.root, "existing.ts"), join(h.root, "staged.ts")],
				"staged and unstaged tracked changes are checked; untracked files are not");
			pathFails = true;
			order.length = 0;
			const blocked = await h.call("bash", { command: "git commit -am x" });
			assert.equal(blocked?.block, true);
			assert.ok(blocked?.reason?.includes("path check failed") && blocked?.reason?.includes("bad path"), blocked?.reason);
			assert.deepEqual(order, ["path check"], "a path check failure stops before checks.commit");
		} finally { h.cleanup(); }
	});

	test("owner-only commit refusal belongs only to dynaflows-on repos", async () => {
		const h = harness();
		const owned = "scripts/checks/dependency-audit-exceptions.json";
		const git = (...args: string[]) => execFileSync("git", args, { cwd: h.root });
		try {
			await h.read("skill://git-tool");
			mkdirSync(join(h.root, "scripts/checks"), { recursive: true });
			mkdirSync(join(h.root, "security"), { recursive: true });
			writeFileSync(join(h.root, owned), "[]\n");
			writeFileSync(join(h.root, "security/policy.json"), "{}\n");
			git("add", "-A");
			git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "base");
			h.gate({ schemaVersion: 2, ownerOnlyPaths: [owned, "security/"] });
			const commit = () => h.call("bash", { command: "git commit -m x" });
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			git("add", "existing.ts");
			assert.equal(await commit(), undefined, "a commit that leaves owner-only paths alone passes");
			for (const dynaflows of [false, true]) {
				h.dynaflows(dynaflows);
				writeFileSync(join(h.root, owned), '[{"advisory": "GHSA-x"}]\n');
				git("add", owned);
				const staged = await commit();
				if (dynaflows) {
					assert.equal(staged?.block, true);
					assert.ok(staged?.reason?.includes(`owner-only path ${owned}`), staged?.reason);
					assert.ok(staged?.reason?.includes("raise the change to the owner"), staged?.reason);
					assert.ok(staged?.reason?.includes(join(h.recordDir(), "gate.json")));
				} else assert.equal(staged, undefined);
				git("reset", "-q", owned);
				assert.equal((await commit())?.block, dynaflows ? true : undefined, "unstaged owner-only changes follow the same switch");
				git("checkout", "--", owned);
				assert.equal(await commit(), undefined, "restored: the commit passes");
			}
			writeFileSync(join(h.root, "security/new.json"), "{}\n");
			git("add", "security/new.json");
			const added = await commit();
			assert.ok(added?.reason?.includes("owner-only path security/new.json"), "a directory entry covers new files under it");
			git("rm", "-q", "--cached", "security/new.json");
			rmSync(join(h.root, "security/new.json"));
			git("mv", owned, "scripts/checks/moved.json");
			const moved = await commit();
			assert.ok(moved?.reason?.includes(owned), `moving an owner-only file away is refused: ${moved?.reason}`);
		} finally { h.cleanup(); }
	});

	test("dynaflows commits need no sole-command, message or staging shape; only hook bypass is refused", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
			h.dynaflows(true);
			for (const command of [
				"git add -A && git commit -m x", "git -c core.editor=true commit", "git commit", "git commit -e -m x",
				"git --exec-path=/usr/lib/git-core commit -m x",
			]) assert.equal(await h.call("bash", { command }), undefined, command);
			for (const command of ["git commit --no-verify -m x", "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x"]) {
				assert.ok((await h.call("bash", { command }))?.reason?.includes("pre-commit hook"), command);
			}
		} finally { h.cleanup(); }
	});

	test("the default harness check is guardrails from the harness's own reference project", async () => {
		const h = harness({ guardrailsCommand: "default" });
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			const seen: string[][] = [];
			const exec = h.api.exec.bind(h.api);
			h.api.exec = async (command, args, options) => {
				const [name, rest] = repoCommand(command, args);
				if (name !== "uv") return exec(command, args, options);
				assert.equal(options.cwd, h.root);
				seen.push(rest);
				return { code: 0, killed: false, stdout: "", stderr: "" };
			};
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			const project = resolve(import.meta.dir, "../agent/extensions/dynaflows/engine");
			assert.deepEqual(seen, [["run", "--project", project, "guardrails", "check", "--repo", h.root, "--record", h.recordDir()]]);
		} finally { h.cleanup(); }
	});

	test("a record missing checks.commit, or carrying an unknown field, refuses commits and pushes by name", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			const complete = { schemaVersion: 2, checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: true } };
			for (const [gate, named] of [
				[{ ...complete, ownerOnlyPaths: undefined }, "ownerOnlyPaths"], [{ ...complete, ownerOnlyPaths: ["../outside.json"] }, "../outside.json"],
				[{ ...complete, checks: { ci: ["ci"] } }, "checks"],
				[{ ...complete, checks: undefined }, "checks"], [{ ...complete, unknownField: 3 }, "unknownField"],
				[{ ...complete, checks: { commit: ["true"], lint: ["x"] } }, "checks.lint"],
			] as const) {
				h.rawGate(JSON.stringify(gate));
				for (const command of ["git commit -m x", "git push"]) {
					const refused = await h.call("bash", { command });
					assert.equal(refused?.block, true, `${named}: ${command}`);
					assert.ok(refused?.reason?.includes(named), refused?.reason);
				}
			}
			h.rawGate(JSON.stringify(complete));
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("failed path checks persist at handoff and rerun to observe out-of-tool repairs", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure);
			await h.read(verification);
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


	test("isolates sessions, restores successful branch reads, and forgets compacted reads", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			await h.read(integrity);
			h.setSession("second");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
			h.setSession("first");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			h.branch.push(
				{ type: "message", message: { role: "assistant", content: [
					{ type: "toolCall", id: "restored", name: "read", arguments: { path: integrity } },
				] } },
				{ type: "message", message: { role: "toolResult", toolCallId: "restored", isError: false } },
			);
			await h.emit("session_tree");
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			h.branch.push({ type: "compaction" });
			await h.emit("session_branch");
			assert.equal((await h.call("edit", { path: "existing.ts" }))?.block, true);
		} finally { h.cleanup(); }
	});

	test("$dynaflows sets the record's switch; the machine default can only turn it off; only main prompts toggle it", async () => {
		const h = harness();
		try {
			h.machineDynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "pull latest" }), undefined, "no record: off whatever the machine says");
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record, so dynaflows is already off")), h.notes.join("; "));
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record; starting repository setup")), h.notes.join("; "));
			assert.equal(existsSync(h.recordDir()), false, "the toggle never creates a record");
			h.gate({ schemaVersion: 2 });
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the record says off");
			h.dynaflows(true);
			assert.equal(await h.emit("before_agent_start", { reason: "revive", prompt: "" }), undefined);
			const pointer = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }));
			assert.ok(pointer.includes("skill://dynaflows"), "the record turns it on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "again" }), undefined, "pointer once per session");
			const off = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }));
			assert.ok(off.includes("Dynaflows off") && !off.includes("skill://dynaflows"));
			assert.deepEqual(h.record(), { checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: false }, schemaVersion: 2 },
				"only the switch changes");
			h.setSession("restarted");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "record off outlives the session and beats machine on");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "price $dynaflowsy" }), undefined, "keyword needs a word boundary");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" })).includes("skill://dynaflows"));
			h.setSession("machine-off");
			h.machineDynaflows(false);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "machine off beats record on");
			h.setSession("sub"); h.context.agent.kind = "sub";
			for (const prompt of ["$dynaflows off", "stop $dynaflows", "turn off $dynaflows"]) {
				assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt }), undefined, `subagents cannot toggle dynaflows: ${prompt}`);
			}
			assert.deepEqual(h.record().dynaflows, { enabled: true });
		} finally { h.cleanup(); }
	});

	test("every on and off directive form sets the record's switch", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			const on = ["$dynaflows", "$DYNAFLOWS ON", "  $dynaflows enable  "];
			for (const verb of ["on", "enable", "init", "initialise", "initialize", "start", "setup", "set up", "turn on"]) {
				on.push(`$dynaflows ${verb}`, `${verb} $dynaflows`, `${verb.toUpperCase()}  $Dynaflows`);
			}
			const off = ["$dynaflows off", "$dynaflows disable", "$dynaflows stop", "disable $dynaflows", "stop $dynaflows", "Turn  Off $dynaflows"];
			for (const prompt of on) {
				h.dynaflows(false);
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: true }, prompt);
			}
			for (const prompt of off) {
				h.dynaflows(true);
				const result = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
				assert.ok(result.includes("Dynaflows off"), prompt);
			}
		} finally { h.cleanup(); }
	});

	test("enabling dynaflows starts from the repo's stage", async () => {
		const h = harness();
		try {
			const start = async (prompt: string) => JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
			// No record: setup starts at Step 0, drafting the switch on; nothing is written and no error shows.
			const setup = await start("init $dynaflows");
			assert.ok(setup.includes("Start skill://dynaflows/references/harness-setup.md setup now, from Step 0") && setup.includes("owner/repo"), setup);
			assert.ok(setup.includes('\\"dynaflows\\": {\\"enabled\\": true}'), setup);
			assert.equal(existsSync(h.recordDir()), false, "enabling never creates a record");
			assert.ok(h.notes.some(note => note.includes("starting repository setup")), h.notes.join("; "));
			assert.deepEqual(h.errors, [], "no record is a stage, not an error");
			// A refused record: setup resumes and names the reason.
			h.rawGate('{"schemaVersion":2,"dynaflows":{"enabled":false}}');
			const resume = await start("$dynaflows enable");
			assert.ok(resume.includes("Resume skill://dynaflows/references/harness-setup.md setup now") && resume.includes("checks"), resume);
			h.rawGate("{broken");
			const broken = await start("$dynaflows enable");
			assert.ok(broken.includes("Resume skill://dynaflows/references/harness-setup.md setup now") && broken.includes("is refused: "), broken);
			// A valid record and a directive-only prompt: assess the stage and start it.
			h.gate({ schemaVersion: 2 });
			const kickoff = await start("$dynaflows on");
			assert.ok(kickoff.includes("Dynaflows on for owner/repo") && kickoff.includes("skill://dynaflows"), kickoff);
			assert.ok(kickoff.includes("Report the stage you found and start it") && kickoff.includes("ask the owner for the task"), kickoff);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the kickoff counts as the pointer");
			// A directive beside a task: the prompt's task is routed.
			const route = await start("enable $dynaflows\nfix the login bug");
			assert.ok(route.includes("Dynaflows on. Route this work with skill://dynaflows") && !route.includes("Report the stage"), route);
			// The machine default keeps it off and says so.
			h.machineDynaflows(false);
			const kept = await start("$dynaflows");
			assert.ok(kept.includes("Dynaflows off for owner/repo: the machine default") && kept.includes("Do not apply dynaflows"), kept);
			assert.deepEqual(h.record().dynaflows, { enabled: true });
		} finally { h.cleanup(); }
	});

	test("only a standalone directive line sets the switch; asks inside sentences are left to the agent", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			h.dynaflows(false);
			for (const prompt of ["I want to enable dynaflows", "please enable $dynaflows", "what does `$dynaflows init` do?",
				"don't enable dynaflows", "type enable $dynaflows to start", "$dynaflows on."]) {
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
			}
			for (const prompt of ["enable $dynaflows", "  Turn On   $dynaflows ", "$dynaflows init"]) {
				h.dynaflows(false);
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.record().dynaflows, { enabled: true }, prompt);
			}
			const route = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "enable $dynaflows\nfix the login bug" }));
			assert.ok(route.includes("Route this work with") && !route.includes("Report the stage"), route);
		} finally { h.cleanup(); }
	});

	test("contradictory asks change nothing and tell the agent to ask the owner", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			for (const prompt of ["enable $dynaflows\ndisable $dynaflows", "$dynaflows on\n$dynaflows off"]) {
				const result = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
				assert.ok(result.includes("both enable and disable dynaflows") && result.includes("Ask the owner"), result);
				assert.deepEqual(h.record().dynaflows, { enabled: false }, prompt);
			}
			assert.ok(h.errors.some(error => error.includes("contradictory dynaflows directives")), h.errors.join("; "));
		} finally { h.cleanup(); }
	});

	test("the dynaflows tool sets the switch and returns the next step; subagents cannot use it", async () => {
		const h = harness();
		try {
			const setup = await h.tool("dynaflows", { enabled: true });
			assert.ok(setup.includes("from Step 0") && setup.includes(h.recordDir()), setup);
			h.gate({ schemaVersion: 2 });
			const kickoff = await h.tool("dynaflows", { enabled: true });
			assert.deepEqual(h.record().dynaflows, { enabled: true });
			assert.ok(kickoff.includes("Dynaflows on for owner/repo") && kickoff.includes("Report the stage you found"), kickoff);
			assert.ok((await h.tool("dynaflows", { enabled: false })).includes("Dynaflows off"));
			assert.deepEqual(h.record().dynaflows, { enabled: false });
			h.context.agent.kind = "sub";
			assert.ok((await h.tool("dynaflows", { enabled: true })).includes("only the main agent"));
			assert.deepEqual(h.record().dynaflows, { enabled: false });
		} finally { h.cleanup(); }
	});

	test("a repo without an origin remote has no record, so dynaflows is off", async () => {
		const h = harness();
		try {
			h.dynaflows(true);
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" })).includes("skill://dynaflows"));
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			h.setSession("no-origin");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined);
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("dynaflows off is never silent: one note at session start and one at every commit name the slug and reason", async () => {
		const h = harness();
		try {
			const gate = join(h.recordDir(), "gate.json");
			const cases: [string, () => Promise<void> | void, string][] = [
				["missing record", () => {}, `dynaflows off for owner/repo: no per-repo record at ${gate}`],
				["record off", () => h.gate({ schemaVersion: 2 }), `dynaflows off for owner/repo: the record ${gate} sets dynaflows.enabled false`],
				["machine off", () => { h.dynaflows(true); h.machineDynaflows(false); }, "dynaflows off for owner/repo: the machine default "],
				["non-GitHub origin", async () => {
					h.machineDynaflows(true);
					await execute("git", ["remote", "set-url", "origin", "https://gitlab.com/owner/repo.git"], { cwd: h.root });
				}, "dynaflows off for none: origin https://gitlab.com/owner/repo.git is not a GitHub owner/name URL"],
				["no origin", () => execute("git", ["remote", "remove", "origin"], { cwd: h.root }).then(() => {}), "dynaflows off for none: no origin remote"],
			];
			for (const [name, arrange, text] of cases) {
				await arrange();
				h.notes.length = 0;
				h.setSession(name);
				await h.emit("session_start");
				await h.read("skill://git-tool");
				assert.equal(h.notes.filter(note => note.includes(text)).length, 1, `${name}: ${h.notes.join("; ")}`);
				for (let commit = 0; commit < 2; commit++) assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, name);
				const commits = h.notes.filter(note => note.includes(text) && note.includes("commit checks do not run"));
				assert.equal(commits.length, 2, `${name}: every commit says why: ${h.notes.join("; ")}`);
			}
			h.machineDynaflows(true);
			await execute("git", ["remote", "add", "origin", defaultOrigin], { cwd: h.root });
			h.dynaflows(true);
			h.notes.length = 0;
			h.setSession("on");
			await h.emit("session_start");
			await h.read("skill://git-tool");
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			assert.deepEqual(h.notes.filter(note => note.includes("dynaflows off")), [], "dynaflows on adds no off note");
		} finally { h.cleanup(); }
	});

	test("checks AST mutation on resolve, not its preview", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.gate({ schemaVersion: 2 });
			h.dynaflows(true);
			await h.read(integrity, structure);
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
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		await h.call("edit", { paths: ["existing.ts", "ignored.ts", "deleted.ts"] });
		assert.deepEqual(checked, [[join(h.root, "existing.ts")]]);
		rmSync(join(h.root, "existing.ts"));
		await h.call("edit", { path: "ignored.ts" });
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
		assert.equal(checked.length, 1);
	} finally { h.cleanup(); }
});

test("path checks exclude Git directory internals while checking live files", async () => {
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
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		await h.call("edit", { paths: [internal, live] });
		assert.deepEqual(checked, [[live]], "git directory paths must not reach the path check");
	} finally { h.cleanup(); }
});

test("path checks forget failures for files restored to HEAD", async () => {
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
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		const failure = await h.call("edit", { path });
		assert.ok(failure?.additionalContext?.includes("bad tracked path"));
		assert.deepEqual(checked, [[path]]);
		writeFileSync(path, committed);
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined, "a file restored to HEAD must clear its path check failure");
		assert.deepEqual(checked, [[path]], "a file restored to HEAD must clear its path check failure");
	} finally { h.cleanup(); }
});

test("path checks keep ignored files excluded when a path crosses a symlink", async () => {
	const h = harness();
	const outside = mkdtempSync(join(tmpdir(), "agent-rails-pathcheck-"));
	try {
		writeFileSync(join(h.root, ".gitignore"), "node_modules/\n");
		mkdirSync(join(h.root, "node_modules/pkg"), { recursive: true });
		const ignored = join(h.root, "node_modules/pkg/index.js");
		writeFileSync(ignored, "module.exports = true;\n");
		writeFileSync(join(outside, "file"), "live\n");
		// An escaping symlink retains its lexical path when rails prepares the Git query.
		symlinkSync(outside, join(h.root, "link"), "dir");
		const checked: string[][] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") { checked.push(rest); return { code: 0, killed: false, stdout: "", stderr: "" }; }
			return exec(command, args, options);
		};
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		await h.call("edit", { paths: [ignored, join(h.root, "link/file")] });
		assert.ok(!checked.some(args => args.some(path => path.includes("node_modules/"))),
			"ignored paths must not reach the path check when another path breaks git check-ignore");
	} finally { h.cleanup(); rmSync(outside, { recursive: true, force: true }); }
});

test("dynaflows merge requires prove()'s result file to say pass for the exact head", async () => {
	const h = harness();
	try {
		await execute("git", ["init"], { cwd: h.root });
		await h.read("skill://git-tool");
		const head = "a".repeat(40);
		const command = `env gh pr merge 3 --match-head-commit=${head}`;
		mockPrCi(h, greenCi, head, "3");
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off: no merge guard");
		h.dynaflows(true);
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("--match-head-commit"));
		const missing = await h.call("bash", { command });
		assert.ok(missing?.reason?.includes(`no prove() pass for ${head}`), missing?.reason);
		assert.ok(missing?.reason?.includes(join(".git", "omp-prove", `${head}.result.json`)), missing?.reason);
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
		proveResult(h, head);
		structureResult(h, head);
		assert.equal(await h.call("bash", { command }), undefined);
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${head}` }), undefined);
	} finally { h.cleanup(); }
});

test("dynaflows merge requires a valid exact-head structure result and blocks ready findings", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		proveResult(h);
		mockPrCi(h, greenCi, ciHead, "3");
		const command = `gh pr merge 3 --match-head-commit ${ciHead}`;
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off needs no structure file");
		h.dynaflows(true);
		const missing = await h.call("bash", { command });
		assert.equal(missing?.block, true);
		assert.ok(missing?.reason?.includes(`no valid structure_review() result for ${ciHead}`));
		structureResult(h);
		const path = join(h.root, ".git/omp-structure", `${ciHead}.json`);
		for (const content of [
			"{broken",
			JSON.stringify({ head: "b".repeat(40), halt: false, status: "shadow", findings: [] }),
			JSON.stringify({ head: ciHead, halt: "false", status: "shadow", findings: [] }),
			JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [] }),
		]) {
			writeFileSync(path, content);
			assert.equal((await h.call("bash", { command }))?.block, true);
		}
		const finding = { unit: "src/orders", question: "folder_name_coverage", status: "ready", score: 0.9, threshold: 0.7 };
		writeFileSync(path, JSON.stringify({ head: ciHead, halt: true, status: "blocked", findings: [finding] }));
		const blocked = await h.call("bash", { command });
		assert.equal(blocked?.block, true);
		assert.ok(blocked?.reason?.includes("src/orders: folder_name_coverage"));
		assert.ok(blocked?.reason?.includes("fix them and rerun prove() and structure_review() on the new head"));
		h.dynaflows(false);
		assert.equal(await h.call("bash", { command }), undefined, "dynaflows off ignores halt");
		h.dynaflows(true);
		writeFileSync(path, JSON.stringify({ head: ciHead, halt: false, status: "incomplete",
			findings: [{ ...finding, status: "provisional" }], errors: ["judge offline"] }));
		assert.equal(await h.call("bash", { command }), undefined, "provisional findings and infrastructure errors stay shadow-only");
		structureResult(h);
		assert.equal(await h.call("bash", { command }), undefined);
	} finally { h.cleanup(); }
});

// The generic CI rule (no ci block declared) on a dynaflows merge with prove() passed.
for (const scenario of [
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }], fragment: "test: COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], fragment: "test: IN_PROGRESS" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "FAILURE" }], fragment: "external: FAILURE" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "PENDING" }], fragment: "external: PENDING" },
	{ name: "only skipped", rollup: [{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" }], fragment: ciHead },
	{ name: "empty declared CI", rollup: [], boundary: "gate", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty workflow directory", rollup: [], boundary: "workflows", fragment: `no CI checks reported for ${ciHead}` },
	{ name: "empty no-CI boundary", rollup: [] },
	{ name: "green", rollup: greenCi },
] as const) {
	test(`merge CI rule: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			if ("boundary" in scenario && scenario.boundary === "gate") h.gate({ schemaVersion: 2, checks: { commit: ["true"], ci: ["bun", "ci"] } });
			if ("boundary" in scenario && scenario.boundary === "workflows") mkdirSync(join(h.root, ".github/workflows"), { recursive: true });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			mockPrCi(h, [...scenario.rollup], ciHead, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if ("fragment" in scenario) {
				assert.equal(result?.block, true);
				assert.equal(result?.reason?.includes(scenario.fragment), true, scenario.fragment);
				assert.equal(result?.reason?.includes(ciHead), true, "diagnostic identifies the PR head");
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

// gate.ci: the declared context must be SUCCESS on the exact head, as a commit status or a check run.
const requiredRefusal = (state: string) => `Agent rails: required CI context "gate" is ${state} for ${ciHead} in base-owner/base-repo (runner ci-gate). Submit and wait: python3 ${ciRunner} submit ci-gate base-owner/base-repo ${ciHead}, then python3 ${ciRunner} wait ci-gate base-owner/base-repo ${ciHead} --context gate; see skill://git-tool/references/merge-and-recover.md for failures.`;
for (const scenario of [
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
	// Only the declared context decides: optional checks, red or pending, never block.
	{ name: "success while other checks are red or pending", rollup: [
		{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
		{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" },
		{ __typename: "StatusContext", context: "docs-preview", state: "PENDING" },
	] },
] as const) {
	test(`required context merge: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			await originRemote(h, "git@github.com:base-owner/base-repo.git");
			h.gate({ schemaVersion: 2, ci: ciGate });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			mockPrCi(h, [...scenario.rollup], ciHead, "3", undefined, "https://github.com/base-owner/base-repo/pull/3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if ("state" in scenario) {
				assert.equal(result?.block, true, "the required context must block");
				assert.equal(result?.reason, requiredRefusal(scenario.state));
			} else assert.equal(result, undefined);
		} finally { h.cleanup(); }
	});
}

test("without a ci block every reported check still decides the merge", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
			{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" }], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true);
		assert.ok(result?.reason?.includes(`CI is not green for ${ciHead}: lint: COMPLETED/FAILURE`), result?.reason);
	} finally { h.cleanup(); }
});

test("agents can write records before enabling dynaflows and after disabling it", async () => {
	const h = harness();
	try {
		await h.read(integrity, structure);
		const gate = join(h.recordDir(), "gate.json");
		const draft = JSON.stringify({ schemaVersion: 2, checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: true } });
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		const kickoff = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "enable $dynaflows" }));
		assert.ok(kickoff.includes("from Step 0") && kickoff.includes("Write the record yourself") && kickoff.includes("Never ask the owner to save"), kickoff);
		for (const path of [gate, join(h.recordDir(), "profile.md")]) {
			assert.equal((await h.call("write", { path, content: draft }))?.block, undefined);
		}
		const other = await h.call("write", { path: join(h.records, "repos", "owner", "other", "gate.json"), content: draft });
		assert.equal(other?.block, undefined);
		h.context.agent.kind = "sub";
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		h.context.agent.kind = "main";
		mkdirSync(h.recordDir(), { recursive: true });
		writeFileSync(gate, draft);
		await h.emit("before_agent_start", { reason: "prompt", prompt: "disable $dynaflows" });
		assert.deepEqual(h.record().dynaflows, { enabled: false });
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		h.setSession("other");
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
	} finally { h.cleanup(); }
});

test("plain merges with no ci block and dynaflows off never query GitHub", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2 });
		forbidGh(h);
		assert.equal(await h.call("bash", { command: "gh pr merge 3 --rebase" }), undefined);
	} finally { h.cleanup(); }
});

test("ci-runner commands come from the extension's own location, never a host literal", async () => {
	const source = readFileSync(join(import.meta.dir, "../agent/extensions/dynaflows/index.ts"), "utf8");
	assert.ok(source.includes('new URL("../../../scripts/ci-runner.py", import.meta.url)'), "path is derived from import.meta.url");
	for (const literal of [homedir(), "/Users/", "ssh ", "BatchMode", "--timeout", "ci-gate.py"]) {
		assert.equal(source.includes(literal), false, `rails source must not hold ${literal}`);
	}
	assert.equal(ciRunner, resolve(import.meta.dir, "..", "scripts", "ci-runner.py"));
});

test("a gate with every optional check and a ci block allows publishing", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, ci: ciGate, checks: { commit: ["true"], format: ["ruff", "format"], profile: ["guardrails", "profile-check"] } });
		assert.equal(await h.call("bash", { command: "git push" }), undefined);
	} finally { h.cleanup(); }
});

for (const remote of ["git@github.com:OWNER/REPO.git", "https://github.com/owner/repo.git"]) {
	test(`PR URL repository: ${remote}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			await originRemote(h, remote);
			h.gate({ schemaVersion: 2, ci: ciGate });
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			const url = "https://github.com/other/checkout/pull/3";
			mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], ciHead, url, undefined, url);
			const result = await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` });
			assert.equal(result?.block, true, "cross-repo URL must not use checkout policy");
			assert.ok(result?.reason?.includes("run from a checkout of other/checkout"), result?.reason ?? "missing refusal reason");
		} finally { h.cleanup(); }
	});
}

test("PR URL selector accepts the checkout repository case-insensitively", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		await originRemote(h, "git@github.com:OWNER/REPO.git");
		h.gate({ schemaVersion: 2, ci: ciGate });
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		const url = "https://github.com/owner/repo/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], ciHead, url);
		assert.equal(await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("required context blocks merge despite a prove() pass", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		h.gate({ schemaVersion: 2, ci: ciGate });
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], ciHead, "3");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(result?.block, true, "the required context must block merge despite a prove() pass");
		assert.ok(result?.reason?.includes(`required CI context "gate" is missing for ${ciHead} in owner/repo`), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR query contract: requests the PR URL", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		await originRemote(h);
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, greenCi, ciHead, "3");
		const exec = h.api.exec.bind(h.api);
		let fields: string | undefined;
		h.api.exec = async (command, args, options) => {
			if (command === "gh" && args[1] === "view") fields = args.at(-1);
			return exec(command, args, options);
		};
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` }), undefined);
		assert.equal(fields, "headRefOid,statusCheckRollup,url");
	} finally { h.cleanup(); }
});

test("a merge reports a gh failure while reading the PR", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.dynaflows(true);
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [], ciHead, "3", "not authenticated");
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.ok(result?.reason?.includes("cannot read PR head/CI via gh: not authenticated"), result?.reason);
	} finally { h.cleanup(); }
});

for (const scenario of ["head mismatch", "red CI", "green CI"] as const) {
	test(`merge CI gate: ${scenario}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			proveResult(h);
			structureResult(h);
			const head = scenario === "head mismatch" ? "b".repeat(40) : ciHead;
			mockPrCi(h, scenario === "red CI"
				? [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }] : greenCi, head, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
			if (scenario === "green CI") assert.equal(result, undefined);
			else {
				assert.equal(result?.block, true);
				const fragment = scenario === "head mismatch" ? `PR head is ${head}, not ${ciHead}; prove() the current head` : "test: COMPLETED/FAILURE";
				assert.equal(result?.reason?.includes(fragment), true, fragment);
			}
		} finally { h.cleanup(); }
	});
}

test("dynaflows off: merges have no guard, even with a declared gate.ci", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, ci: ciGate });
		forbidGh(h);
		assert.equal(await h.call("bash", { command: "gh pr merge 3 --merge" }), undefined);
		h.gate({ schemaVersion: 2, ci: ciGate, agentMerge: "disabled" });
		assert.ok((await h.call("bash", { command: "gh pr merge 3 --merge" }))?.reason?.includes("agentMerge is disabled"));
	} finally { h.cleanup(); }
});

for (const scenario of [
	{ name: "missing flag", args: "", fragment: "requires --match-head-commit <40-hex verified SHA>" },
	{ name: "bare variable", args: "--match-head-commit $H", fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "quoted variable", args: '--match-head-commit "$H"', fragment: "received unexpanded shell text `$H`; rails check the literal command, so pass the 40-hex SHA itself" },
	{ name: "non-hex", args: "--match-head-commit nope", fragment: "must be a lowercase 40-hex SHA, got nope" },
	{ name: "missing prove() result", args: `--match-head-commit ${ciHead}`, fragment: `no prove() pass for ${ciHead}` },
] as const) {
	test(`merge diagnostics gate: ${scenario.name}`, async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool");
			h.dynaflows(true);
			mockPrCi(h, greenCi, ciHead, "3");
			const result = await h.call("bash", { command: `gh pr merge 3 ${scenario.args}` });
			assert.equal(result?.block, true);
			assert.equal(result?.reason?.includes(scenario.fragment), true, scenario.fragment);
		} finally { h.cleanup(); }
	});
}

test("path checks canonicalize a symlinked repository root before executing", async () => {
	const h = harness();
	const aliases = mkdtempSync(join(tmpdir(), "agent-rails-alias-"));
	try {
		const alias = join(aliases, "repo");
		symlinkSync(h.root, alias);
		h.context.cwd = alias;
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		const exec = h.api.exec.bind(h.api);
		let checked = false;
		h.api.exec = async (command, args, options) => {
			const [name, rest] = repoCommand(command, args);
			if (name === "path-check") {
				checked = true;
				assert.equal(options.cwd, h.root);
				assert.deepEqual(rest, [join(h.root, "existing.ts")]);
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
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		await h.read(integrity, structure);
		assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path check failed"));
		assert.deepEqual(timeouts, [20000]);
	} finally { h.cleanup(); }
});

test("child handoff requires its own verification read and repaired path checks", async () => {
	const h = harness();
	try {
		h.context.agent.kind = "sub";
		assert.equal(await h.emit("session_stop"), undefined);
		h.pathFailure("bad path");
		h.gate({ schemaVersion: 2, checks: { commit: ["not-run"], ci: ["not-run"] } });
		h.dynaflows(true);
		await h.read(integrity, structure);
		await h.call("edit", { path: join(h.root, "existing.ts") });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.pathFailure();
		assert.ok((await h.emit("session_stop"))?.reason?.includes(verification));
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
	} finally { h.cleanup(); }
});

test("toggle asks ignore mentions without a verb and reject contradictions and nonrepositories", async () => {
	const h = harness();
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "rails-toggle-")));
	try {
		h.gate({ schemaVersion: 2 });
		h.dynaflows(true);
		const record = h.record();
		for (const prompt of ['"$dynaflows"', "$dynaflows status", "$dynaflows on\n$dynaflows off", "init $dynaflows\nstop $dynaflows",
			"$dynaflows enabled", "off $dynaflows", "$dynaflowson"]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		// Prose around a directive makes it a sentence, which the agent judges instead.
		for (const prompt of ["please $dynaflows on", "please enable $dynaflows", "`$dynaflows init`", "$dynaflows on."]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		await h.emit("before_agent_start", { reason: "prompt", prompt: "turn off $dynaflows" });
		assert.deepEqual(h.record().dynaflows, { enabled: false });
		assert.equal(existsSync(join(h.root, ".omp")), false, "the repo tree is never written");
		h.context.cwd = scratch;
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" });
		assert.equal(existsSync(join(scratch, ".omp")), false);
		assert.ok(h.notes.some(note => note.includes("Git project")));
	} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
});

test("linked worktrees resolve the main checkout's record", async () => {
	const h = harness();
	const linked = `${h.root}-linked`;
	try {
		h.gate({ schemaVersion: 2 });
		await execute("git", ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed"], { cwd: h.root });
		await execute("git", ["worktree", "add", "-b", "agent/source", linked], { cwd: h.root });
		h.context.cwd = linked;
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
		assert.deepEqual(h.record().dynaflows, { enabled: true }, "the linked worktree toggles the shared record");
		assert.equal(existsSync(join(linked, ".omp")), false);
		const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: linked })).stdout.trim();
		await h.read("skill://git-tool");
		const blocked = await h.call("bash", { command: `gh pr merge 3 --match-head-commit=${head}` });
		assert.ok(blocked?.reason?.includes(join(realpathSync(h.root), ".git", "omp-prove", `${head}.result.json`)),
			`the result file lives in the shared Git common directory: ${blocked?.reason}`);
	} finally { h.cleanup(); rmSync(linked, { recursive: true, force: true }); }
});

test("Python proof gates and rails accept the same schema fixtures", async () => {
	// Each case sits over a complete dynaflows-off record; a case's undefined value drops the field.
	const record = { checks: { commit: ["true"] }, ownerOnlyPaths: [], dynaflows: { enabled: false } };
	const cases: object[] = [
		{ schemaVersion: 2 }, { schemaVersion: 2, checks: {} }, { schemaVersion: 2, checks: undefined },
		{ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["ci"], format: ["fmt"], profile: ["profile"] }, agentMerge: "allowed", ci: { requiredContext: "gate", runner: "ci-gate" } },
		{ schemaVersion: 2, ci: { requiredContext: "build", runner: "github-actions" } },
		{ schemaVersion: 2, unknownField: 3 }, { schemaVersion: 2, extraField: 1000 }, { schemaVersion: 2, checks: { commit: ["c"], lint: ["l"] } },
		{ schemaVersion: 2, otherField: "github" }, { schemaVersion: 2, otherField: null },
		{ schemaVersion: 2, ci: null }, { schemaVersion: 2, ci: {} }, { schemaVersion: 2, ci: { requiredContext: " ", runner: "ci-gate" } },
		{ schemaVersion: 2, ci: { requiredContext: "gate" } }, { schemaVersion: 2, ci: { requiredContext: "gate", runner: 1 } },
		{ schemaVersion: 2, checks: { format: [] } }, { schemaVersion: 2, checks: { profile: [" "] } }, { schemaVersion: 2, checks: { format: "fmt" } },
		{ schemaVersion: 2, checks: null }, { schemaVersion: 2, checks: { commit: null } },
		{ schemaVersion: 2, checks: { ci: [] } },
		{ schemaVersion: 2, agentMerge: "maybe" },
		{ schemaVersion: true }, { schemaVersion: 3 },
		{ schemaVersion: 2, dynaflows: { enabled: true } }, { schemaVersion: 2, dynaflows: null },
		{ schemaVersion: 2, dynaflows: {} }, { schemaVersion: 2, dynaflows: { enabled: "yes" } },
		{ schemaVersion: 2, ownerOnlyPaths: undefined }, { schemaVersion: 2, ownerOnlyPaths: null }, { schemaVersion: 2, ownerOnlyPaths: "a.json" },
		{ schemaVersion: 2, ownerOnlyPaths: ["scripts/checks/exceptions.json", "security/"] }, { schemaVersion: 2, ownerOnlyPaths: ["/abs.json"] },
		{ schemaVersion: 2, ownerOnlyPaths: ["a/./b"] }, { schemaVersion: 2, ownerOnlyPaths: ["a.json", "a.json"] }, { schemaVersion: 2, ownerOnlyPaths: [1] },
	].map(value => ({ ...record, ...value }));
	const h = harness();
	try {
		await h.read("skill://git-tool");
		const rails: boolean[] = [];
		for (const value of cases) {
			h.rawGate(JSON.stringify(value));
			rails.push(!(await h.call("bash", { command: "git push" }))?.block);
		}
		const modulePath = join(import.meta.dir, "../agent/extensions/dynaflows/engine/workflows.py");
		const script = "import importlib.util,json,sys; spec=importlib.util.spec_from_file_location('workflows',sys.argv[1]); module=importlib.util.module_from_spec(spec); sys.modules['workflows']=module; spec.loader.exec_module(module); print(json.dumps([module.gate_policy(v) is not None for v in json.loads(sys.argv[2])]))";
		const result = await execute("python3", ["-c", script, modulePath, JSON.stringify(cases)]);
		assert.deepEqual(JSON.parse(result.stdout), rails);
	} finally { h.cleanup(); }
});

test("mise commits run the record's checks in a real repository", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool");
		h.gate({ schemaVersion: 2, checks: { commit: ["true"] }, dynaflows: { enabled: true } });
		assert.equal(await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }), undefined);
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] }, dynaflows: { enabled: true } });
		assert.ok((await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }))?.reason?.includes("checks.commit (false) failed"));
	} finally { h.cleanup(); }
});

test("mise directory options resolve the wrapped commit repository", async () => {
	const h = harness();
	const target = harness({ configRoot: h.records, remote: "git@github.com:owner/target.git" });
	try {
		await h.read("skill://git-tool");
		target.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
		target.gate({ schemaVersion: 2, checks: { commit: ["false"] }, dynaflows: { enabled: true } });
		for (const command of [
			`mise x -C '${target.root}' -- git commit -m x`,
			`mise exec --cd='${target.root}' node@20 -- git commit -m x`,
		]) {
			const enforced = await h.call("bash", { command });
			assert.ok(enforced?.reason?.includes(`checks.commit (false) failed in ${target.root}`), `the wrapped repo's record is enforced: ${command}`);
		}
		const unresolved = await h.call("bash", { command: "mise x -C /nonexistent-rails-directory -- git commit -m x" });
		assert.equal(unresolved?.block, true);
		assert.ok(unresolved?.reason?.includes("effective cwd"));
	} finally { h.cleanup(); target.cleanup(); }
});

for (const record of ["absent", "off", "on"] as const) {
	test(`repo-scoped harness process follows dynaflows (${record}) while Git reads always apply`, async () => {
		const h = harness();
		try {
			if (record !== "absent") h.dynaflows(record === "on");
			h.pathFailure("path policy violation");
			const edit = await h.call("write", { path: "tests/new.test.ts" });
			if (record === "on") {
				assert.equal(edit?.block, true);
				for (const url of [integrity, structure, tiers, enforcement]) assert.ok(edit?.reason?.includes(url));
				await h.read(integrity, structure, tiers, enforcement);
				writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
				assert.ok((await h.call("edit", { path: "existing.ts" }))?.additionalContext?.includes("path policy violation"));
				assert.equal((await h.emit("session_stop"))?.decision, "block");
				h.pathFailure();
				assert.ok((await h.emit("session_stop"))?.reason?.includes(verification));
				await h.read(verification);
			} else {
				assert.equal(edit, undefined);
				assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			}
			assert.equal(await h.emit("session_stop"), undefined);
			const git = await h.call("bash", { command: "git push" });
			assert.equal(git?.block, true);
			assert.ok(git?.reason?.includes("skill://git-tool"));
		} finally { h.cleanup(); }
	});
}

test("edit and handoff policy follows each edited repo, not the session cwd", async () => {
	const h = harness();
	const other = harness({ configRoot: h.records, remote: "git@github.com:owner/other.git" });
	try {
		h.dynaflows(true);
		other.dynaflows(false);
		h.pathFailure("enabled repo violation");
		assert.equal(await h.call("edit", { path: join(other.root, "existing.ts") }), undefined);
		assert.equal(await h.emit("session_stop"), undefined, "editing only an off repo does not require a handoff read");
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.reason?.includes(integrity));
		await h.read(integrity, structure);
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.additionalContext?.includes("enabled repo violation"));
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.dynaflows(false);
		assert.equal(await h.emit("session_stop"), undefined, "turning off suppresses both outstanding retries and handoff reads");
		assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
	} finally { h.cleanup(); other.cleanup(); }
});

test("delivery policy references and native reminder scope use the targeted homes", () => {
	const config = z.object({
		dynaflowsRules: z.array(z.string()),
		references: z.object({
			firstEdit: z.array(z.string()), newFile: z.array(z.string()),
			test: z.array(z.string()), verification: z.array(z.string()),
		}),
	}).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
	assert.deepEqual(config.references, {
		firstEdit: [integrity], newFile: [structure],
		test: [tiers, enforcement, proofs], verification: [verification],
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

for (const enabled of [undefined, false, true]) {
	test(`native judged rules follow session dynaflows (${enabled ?? "no record"}) and preserve user exclusions`, async () => {
		const h = harness();
		try {
			if (enabled !== undefined) h.dynaflows(enabled);
			const rules = lookup("ttsr.disabledRules");
			assert.ok(rules);
			rules.override(h.api.pi.settings, ["user-disabled-rule"]);
			const config = z.object({ dynaflowsRules: z.array(z.string()).min(1) }).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
			for (const name of config.dynaflowsRules) {
				assert.ok(existsSync(join(import.meta.dir, "../agent/extensions/dynaflows/rules", `${name}.md`)), `configured native rule exists: ${name}`);
			}
			await h.emit("session_start");
			assert.deepEqual(rules.get(h.api.pi.settings), enabled
				? ["user-disabled-rule"]
				: ["user-disabled-rule", ...config.dynaflowsRules]);
			await h.emit("session_start");
			assert.deepEqual(rules.get(h.api.pi.settings), enabled
				? ["user-disabled-rule"]
				: ["user-disabled-rule", ...config.dynaflowsRules], "restarting the session does not duplicate exclusions");
		} finally { h.cleanup(); }
	});
}
