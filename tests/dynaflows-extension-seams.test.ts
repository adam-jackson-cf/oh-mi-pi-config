import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { describe, test } from "bun:test";
import { z } from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { harness, execute, reasonOf, isBlocked, repoCommand, defaultOrigin, integrity, structure, enforcement, tiers, verification, ciHead, greenCi, mockPrCi, forbidGh, ciGate, ciRunner, originRemote, proveResult, structureResult, railsConfig, mergeRecover } from "./dynaflows-guards-seams.test";

const proofs = "skill://structured-workflow/references/proofs.md";

describe("dynaflows extension", () => {
	test("blocks new code and test files until every mapped reference is successfully read", async () => {
		const h = harness();
		try {
			h.mode("structured");
			const input = { path: "tests/new.test.ts", content: "test('behaviour', () => {});" };
			const blocked = await h.call("write", input);
			assert.equal(isBlocked(blocked), true);
			for (const url of [integrity, structure, tiers, enforcement, proofs]) assert.ok(reasonOf(blocked).includes(url));
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
			h.mode("structured");
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
			h.mode("structured");
			await h.read(integrity, structure);
			writeFileSync(join(h.root, "another.ts"), "export {};\n");
			const result = await h.call("edit", { paths: ["existing.ts", "another.ts"] });
			assert.ok(result?.additionalContext?.includes(join(h.root, "existing.ts")));
			assert.ok(result?.additionalContext?.includes(join(h.root, "another.ts")));
			assert.ok(result?.additionalContext?.includes("use feature ownership"));
			assert.equal(await h.call("edit", { path: "existing.ts" }, {}, true), undefined);
		} finally { h.cleanup(); }
	});

	test("absent and invalid records ask for no edit reads and do not throw", async () => {
		const h = harness();
		try {
			assert.equal(await h.call("edit", { path: "existing.ts" }), undefined);
			assert.equal(h.notes.length, 0, "a repo without a record is normal, not noteworthy");
			h.mode("structured");
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

	test.each(["minimal", "structured"])("handoff needs only the verification read after code edits, never a check run (dynaflows %s mode)", async name => {
		const mode = name === "structured" ? "structured" : "minimal";
		const h = harness();
		try {
			h.mode(mode);
			h.gate({ schemaVersion: 2, checks: { commit: ["bun", "check"], ci: ["bun", "ci"] } });
			await h.emit("session_start");
			writeFileSync(join(h.root, "existing.ts"), "pulled change\n");
			writeFileSync(join(h.root, "session.log"), "growing log\n");
			assert.equal(await h.emit("session_stop"), undefined, "tree changes outside this session's edits never block");
			await h.read(integrity, structure);
			await h.call("edit", { path: "existing.ts" });
			const blocked = await h.emit("session_stop");
			if (mode === "structured") {
				assert.equal(blocked?.decision, "block");
				assert.ok(reasonOf(blocked).includes(verification));
				assert.ok(!reasonOf(blocked).includes("bun"), "no repository check is demanded at handoff");
			} else assert.equal(blocked, undefined);
			await h.read(verification);
			writeFileSync(join(h.root, "session.log"), "grew again\n");
			assert.equal(await h.emit("session_stop"), undefined);
		} finally { h.cleanup(); }
	});

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
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-guards-scratch-")));
		try {
			h.mode("structured");
			assert.equal(await h.call("write", { path: join(scratch, "notes.md"), content: "# notes\n" }), undefined,
				"a new file outside any repository needs no reads");
			await h.read(integrity);
			assert.equal(await h.call("write", { path: join(scratch, "scratch.ts"), content: "export {};\n" }), undefined,
				"a new code file outside any repository needs no newFile reads");
			assert.equal(await h.call("write", { path: "notes.md", content: "# notes\n" }), undefined,
				"a new non-code file inside a repository needs no reads");
			const blocked = await h.call("write", { path: "src/new.ts", content: "export {};\n" });
			assert.equal(isBlocked(blocked), true);
			for (const url of [structure]) assert.ok(reasonOf(blocked).includes(url));
		} finally { h.cleanup(); rmSync(scratch, { recursive: true, force: true }); }
	});

	test("dynaflows commits run the record's checks.commit, then the whole-repo harness checks, without AGENT", async () => {
		const scratch = realpathSync(mkdtempSync(join(tmpdir(), "dynaflows-guards-commit-")));
		const log = join(scratch, "runs.log");
		const script = join(scratch, "check.sh");
		writeFileSync(script, `#!/bin/sh\nlabel=$1; shift\necho "$label $* AGENT=\${AGENT-unset}" >> '${log}'\necho "$label finding: src/x.py" >&2\neval "exit \\\${FAIL_$label:-0}"\n`, { mode: 0o755 });
		const h = harness({ guardrailsCommand: [script, "harness"] });
		const previous = process.env["AGENT"];
		process.env["AGENT"] = "1";
		try {
			await h.read("skill://git-tool", mergeRecover);
			const commit = () => h.call("bash", { command: "git commit -m x" });
			const runs = () => existsSync(log) ? readFileSync(log, "utf8").trim().split("\n") : [];
			h.gate({ schemaVersion: 2, checks: { commit: [script, "commit"] } });
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "minimal mode: rails run no repo or harness check");
			h.mode("structured");
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), ["commit  AGENT=unset", `harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`],
				"the repo command first, then the harness over the whole repo with the record; AGENT never reaches either");
			rmSync(log);
			process.env["FAIL_commit"] = "3";
			let blocked = await commit();
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes(`checks.commit (${script} commit) failed in ${h.root} (exit 3)`), String(blocked?.reason));
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
			assert.ok(reasonOf(blocked).includes("checks.commit passed. If a repo checker already enforces a failing step's rules"),
				"a harness-only finding points at adopt mode before the agent reshapes repo code");
			h.gate({ schemaVersion: 2 });
			rmSync(log);
			assert.equal((await commit())?.block, true, "harness checks run even when the record declares no checks.commit");
			assert.deepEqual(runs(), [`harness --repo ${h.root} --record ${h.recordDir()} AGENT=unset`]);
			delete process.env["FAIL_harness"];
			h.mode("minimal");
			rmSync(log);
			assert.equal(await commit(), undefined);
			assert.deepEqual(runs(), [], "minimal mode runs no repo or harness check");
		} finally {
			if (previous === undefined) delete process.env["AGENT"]; else process.env["AGENT"] = previous;
			delete process.env["FAIL_commit"]; delete process.env["FAIL_harness"];
			h.cleanup(); rmSync(scratch, { recursive: true, force: true });
		}
	});

	test("dynaflows commits run the harness path check over the paths the commit can take, first", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool", mergeRecover);
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
			h.mode("structured");
			writeFileSync(join(h.root, "existing.ts"), "export const answer = 43;\n");
			writeFileSync(join(h.root, "staged.ts"), "export {};\n");
			await execute("git", ["add", "staged.ts"], { cwd: h.root });
			writeFileSync(join(h.root, "untracked.ts"), "export {};\n");
			assert.equal(await h.call("bash", { command: "git commit -am x" }), undefined);
			assert.deepEqual(order, ["path check", "commit"], "the path check runs before checks.commit");
			assert.deepEqual(checked, ["--event", "staged"],
				"the checker derives added and renamed names from the index, not unstaged or untracked paths");
			pathFails = true;
			order.length = 0;
			const blocked = await h.call("bash", { command: "git commit -am x" });
			assert.equal(isBlocked(blocked), true);
			assert.ok(reasonOf(blocked).includes("path check failed") && reasonOf(blocked).includes("bad path"), String(blocked?.reason));
			assert.deepEqual(order, ["path check"], "a path check failure stops before checks.commit");
		} finally { h.cleanup(); }
	});

	test("dynaflows commits need no sole-command, message or staging shape; only hook bypass is refused", async () => {
		const h = harness();
		try {
			await h.read("skill://git-tool", mergeRecover);
			h.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
			h.mode("structured");
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
			await h.read("skill://git-tool", mergeRecover);
			h.mode("structured");
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
			await h.read("skill://git-tool", mergeRecover);
			h.mode("structured");
			const complete = { schemaVersion: 2, checks: { commit: ["true"] } };
			for (const [gate, named] of [
				[{ ...complete, ownerOnlyPaths: [] }, "ownerOnlyPaths"],
				[{ ...complete, checks: { ci: ["ci"] } }, "checks"],
				[{ ...complete, checks: undefined }, "checks"], [{ ...complete, unknownField: 3 }, "unknownField"],
				[{ ...complete, checks: { commit: ["true"], lint: ["x"] } }, "checks.lint"],
			] as const) {
				h.rawGate(JSON.stringify(gate));
				for (const command of ["git commit -m x", "git push"]) {
					const refused = await h.call("bash", { command });
					assert.equal(isBlocked(refused), true, `${named}: ${command}`);
					assert.ok(reasonOf(refused).includes(named), String(refused?.reason));
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
			h.mode("structured");
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
			h.mode("structured");
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

	test("$dynaflows sets the repo's entry in the mode file; only main prompts change it", async () => {
		const h = harness();
		try {
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "pull latest" }), undefined, "an unlisted repo runs minimal");
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record, so dynaflows is already in minimal mode")), h.notes.join("; "));
			await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows on" });
			assert.ok(h.notes.some(note => note.includes("has no per-repo record; starting repository setup")), h.notes.join("; "));
			assert.equal(existsSync(h.recordDir()), false, "the directive never creates a record");
			assert.equal(h.modeOf(), "structured", "the directive lists the repo even before its record exists");
			h.mode("minimal");
			h.gate({ schemaVersion: 2 });
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the mode file says minimal");
			h.mode("structured");
			assert.equal(await h.emit("before_agent_start", { reason: "revive", prompt: "" }), undefined);
			const pointer = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }));
			assert.ok(pointer.includes("skill://structured-workflow"), "the mode file sets structured mode");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "again" }), undefined, "pointer once per session");
			const off = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows off" }));
			assert.ok(off.includes("Dynaflows minimal mode") && !off.includes("skill://structured-workflow"));
			assert.deepEqual(h.record(), { checks: { commit: ["true"] }, schemaVersion: 2 }, "the record is never written");
			assert.equal(h.modeOf(), "minimal");
			assert.deepEqual(JSON.parse(readFileSync(h.modesPath, "utf8")), { schemaVersion: 1, repos: {} }, "minimal removes the entry");
			h.setSession("restarted");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined, "minimal outlives the session");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "price $dynaflowsy" }), undefined, "keyword needs a word boundary");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows" })).includes("skill://structured-workflow"));
			h.setSession("sub"); h.context.agent.kind = "sub";
			for (const prompt of ["$dynaflows off", "stop $dynaflows", "turn off $dynaflows"]) {
				assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt }), undefined, `subagents cannot change the dynaflows mode: ${prompt}`);
			}
			assert.equal(h.modeOf(), "structured");
		} finally { h.cleanup(); }
	});

	test("every structured and minimal directive form sets the repo's mode", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			const on = ["$dynaflows", "$DYNAFLOWS ON", "  $dynaflows enable  "];
			for (const verb of ["on", "enable", "init", "initialise", "initialize", "start", "setup", "set up", "turn on", "structured"]) {
				on.push(`$dynaflows ${verb}`, `${verb} $dynaflows`, `${verb.toUpperCase()}  $Dynaflows`);
			}
			const off = ["$dynaflows off", "$dynaflows disable", "$dynaflows stop", "disable $dynaflows", "stop $dynaflows", "Turn  Off $dynaflows", "$dynaflows minimal", "Minimal $dynaflows"];
			for (const prompt of on) {
				h.mode("minimal");
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.modeOf(), "structured", prompt);
			}
			for (const prompt of off) {
				h.mode("structured");
				const result = JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
				assert.deepEqual(h.modeOf(), "minimal", prompt);
				assert.ok(result.includes("Dynaflows minimal mode"), prompt);
			}
		} finally { h.cleanup(); }
	});

	test("enabling dynaflows starts from the repo's stage", async () => {
		const h = harness();
		try {
			const start = async (prompt: string) => JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt }));
			const setup = await start("init $dynaflows");
			assert.ok(setup.includes("Start skill://structured-workflow/references/harness-setup.md setup now, from Step 0") && setup.includes("owner/repo"), setup);
			assert.ok(!setup.includes("dynaflows\\\":"), "the record holds no mode");
			assert.equal(existsSync(h.recordDir()), false, "enabling never creates a record");
			assert.ok(h.notes.some(note => note.includes("starting repository setup")), h.notes.join("; "));
			assert.deepEqual(h.errors, [], "no record is a stage, not an error");
			h.rawGate('{"schemaVersion":2,"dynaflows":{"mode":"structured"}}');
			const resume = await start("$dynaflows enable");
			assert.ok(resume.includes("Resume skill://structured-workflow/references/harness-setup.md setup now") && resume.includes("checks"), resume);
			h.rawGate("{broken");
			const broken = await start("$dynaflows enable");
			assert.ok(broken.includes("Resume skill://structured-workflow/references/harness-setup.md setup now") && broken.includes("is refused: "), broken);
			h.gate({ schemaVersion: 2 });
			const kickoff = await start("$dynaflows on");
			assert.ok(kickoff.includes("Dynaflows structured mode for owner/repo") && kickoff.includes("skill://structured-workflow"), kickoff);
			assert.ok(kickoff.includes("Report the stage you found and start it") && kickoff.includes("ask the owner for the task"), kickoff);
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "next" }), undefined, "the kickoff counts as the pointer");
			const route = await start("enable $dynaflows\nfix the login bug");
			assert.ok(route.includes("Dynaflows structured mode. Route this work with skill://structured-workflow") && !route.includes("Report the stage"), route);
			const off = await start("$dynaflows off");
			assert.ok(off.includes("Dynaflows minimal mode") && h.modeOf() === "minimal", off);
		} finally { h.cleanup(); }
	});

	test("only a standalone directive line sets the mode; asks inside sentences are left to the agent", async () => {
		const h = harness();
		try {
			h.gate({ schemaVersion: 2 });
			h.mode("minimal");
			for (const prompt of ["I want to enable dynaflows", "please enable $dynaflows", "what does `$dynaflows init` do?",
				"don't enable dynaflows", "type enable $dynaflows to start", "$dynaflows on."]) {
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.modeOf(), "minimal", prompt);
			}
			for (const prompt of ["enable $dynaflows", "  Turn On   $dynaflows ", "$dynaflows init"]) {
				h.mode("minimal");
				await h.emit("before_agent_start", { reason: "prompt", prompt });
				assert.deepEqual(h.modeOf(), "structured", prompt);
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
				assert.ok(result.includes("both dynaflows minimal and structured mode") && result.includes("Ask the owner"), result);
				assert.deepEqual(h.modeOf(), "minimal", prompt);
			}
			assert.ok(h.errors.some(error => error.includes("contradictory dynaflows directives")), h.errors.join("; "));
		} finally { h.cleanup(); }
	});

	test("the dynaflows tool sets the mode and returns the next step; subagents cannot use it", async () => {
		const h = harness();
		try {
			const setup = await h.tool("dynaflows", { mode: "structured" });
			assert.ok(setup.includes("from Step 0") && setup.includes(h.recordDir()), setup);
			h.gate({ schemaVersion: 2 });
			const kickoff = await h.tool("dynaflows", { mode: "structured" });
			assert.equal(h.modeOf(), "structured");
			assert.ok(kickoff.includes("Dynaflows structured mode for owner/repo") && kickoff.includes("Report the stage you found"), kickoff);
			assert.ok((await h.tool("dynaflows", { mode: "minimal" })).includes("Dynaflows minimal mode"));
			assert.equal(h.modeOf(), "minimal");
			h.context.agent.kind = "sub";
			assert.ok((await h.tool("dynaflows", { mode: "structured" })).includes("only the main agent"));
			assert.equal(h.modeOf(), "minimal");
		} finally { h.cleanup(); }
	});

	test("a repo without an origin remote has no record, so dynaflows runs minimal mode", async () => {
		const h = harness();
		try {
			h.mode("structured");
			assert.ok(JSON.stringify(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" })).includes("skill://structured-workflow"));
			await execute("git", ["remote", "remove", "origin"], { cwd: h.root });
			h.setSession("no-origin");
			assert.equal(await h.emit("before_agent_start", { reason: "prompt", prompt: "work" }), undefined);
			await h.read("skill://git-tool", mergeRecover);
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
		} finally { h.cleanup(); }
	});

	test("minimal mode is never silent: one note at session start and one at every commit name the slug and reason", async () => {
		const h = harness();
		try {
			const gate = join(h.recordDir(), "gate.json");
			const cases: [string, () => Promise<void> | void, string][] = [
				["unlisted", () => {}, `dynaflows minimal mode for owner/repo: the mode file ${h.modesPath} does not set structured`],
				["structured without a record", () => { h.mode("structured"); rmSync(h.recordDir(), { recursive: true }); },
					`dynaflows minimal mode for owner/repo: no per-repo record at ${gate}`],
				["record back, mode off", () => { h.mode("minimal"); }, `dynaflows minimal mode for owner/repo: the mode file ${h.modesPath} does not set structured`],
				["non-GitHub origin", async () => {
					h.mode("structured");
					await execute("git", ["remote", "set-url", "origin", "https://gitlab.com/owner/repo.git"], { cwd: h.root });
				}, "dynaflows minimal mode for none: origin https://gitlab.com/owner/repo.git is not a GitHub owner/name URL"],
				["no origin", () => execute("git", ["remote", "remove", "origin"], { cwd: h.root }).then(() => {}), "dynaflows minimal mode for none: no origin remote"],
			];
			for (const [name, arrange, text] of cases) {
				await arrange();
				h.notes.length = 0;
				h.setSession(name);
				await h.emit("session_start");
				await h.read("skill://git-tool", mergeRecover);
				assert.equal(h.notes.filter(note => note.includes(text)).length, 1, `${name}: ${h.notes.join("; ")}`);
				for (let commit = 0; commit < 2; commit++) assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined, name);
				const commits = h.notes.filter(note => note.includes(text) && note.includes("commit checks do not run"));
				assert.equal(commits.length, 2, `${name}: every commit says why: ${h.notes.join("; ")}`);
			}
			await execute("git", ["remote", "add", "origin", defaultOrigin], { cwd: h.root });
			h.mode("structured");
			h.notes.length = 0;
			h.setSession("on");
			await h.emit("session_start");
			await h.read("skill://git-tool", mergeRecover);
			assert.equal(await h.call("bash", { command: "git commit -m x" }), undefined);
			assert.deepEqual(h.notes.filter(note => note.includes("dynaflows minimal mode")), [], "structured mode adds no minimal-mode note");
		} finally { h.cleanup(); }
	});

	test("checks AST mutation on resolve, not its preview", async () => {
		const h = harness();
		try {
			h.pathFailure("bad path");
			h.gate({ schemaVersion: 2 });
			h.mode("structured");
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
		h.mode("structured");
		await h.read(integrity, structure);
		await h.call("edit", { paths: ["existing.ts", "ignored.ts", "deleted.ts"] });
		assert.deepEqual(checked, [["--event", "edit", join(h.root, "existing.ts")]]);
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
		h.mode("structured");
		await h.read(integrity, structure);
		await h.call("edit", { paths: [internal, live] });
		assert.deepEqual(checked, [["--event", "edit", live]], "git directory paths must not reach the path check");
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
		h.gate({ schemaVersion: 2 });
		h.mode("structured");
		await h.read(integrity, structure);
		assert.equal(await h.call("edit", { path }), undefined);
		assert.deepEqual(checked, []);
		writeFileSync(path, committed);
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
		assert.deepEqual(checked, []);
	} finally { h.cleanup(); }
});

test("path checks keep ignored files excluded when a path crosses a symlink", async () => {
	const h = harness();
	const outside = mkdtempSync(join(tmpdir(), "dynaflows-guards-pathcheck-"));
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
		h.gate({ schemaVersion: 2 });
		h.mode("structured");
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
		await h.read("skill://git-tool", mergeRecover);
		const head = "a".repeat(40);
		const command = `env gh pr merge 3 --match-head-commit=${head}`;
		mockPrCi(h, greenCi, { head, selector: "3" });
		assert.ok(reasonOf(await h.call("bash", { command })).includes("blocked in dynaflows minimal mode"), "minimal mode blocks every merge");
		h.mode("structured");
		assert.ok((await h.call("bash", { command: "gh pr merge 3" }))?.reason?.includes("--match-head-commit"));
		const missing = await h.call("bash", { command });
		assert.ok(reasonOf(missing).includes(`no prove() pass for ${head}`), String(missing?.reason));
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
		proveResult(h, head);
		structureResult(h, head);
		assert.equal(await h.call("bash", { command }), undefined);
		assert.equal(await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${head}` }), undefined);
	} finally { h.cleanup(); }
});

test("dynaflows merge requires a valid exact-head structure result and blocks ready findings", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		proveResult(h);
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const command = `gh pr merge 3 --match-head-commit ${ciHead}`;
		assert.ok(reasonOf(await h.call("bash", { command })).includes("blocked in dynaflows minimal mode"), "minimal mode blocks every merge");
		h.mode("structured");
		const missing = await h.call("bash", { command });
		assert.equal(isBlocked(missing), true);
		assert.ok(reasonOf(missing).includes(`no valid structure_review() result for ${ciHead}`));
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
		assert.equal(isBlocked(blocked), true);
		assert.ok(reasonOf(blocked).includes("src/orders: folder_name_coverage"));
		assert.ok(reasonOf(blocked).includes("fix them and rerun prove() and structure_review() on the new head"));
		h.mode("minimal");
		assert.ok(reasonOf(await h.call("bash", { command })).includes("blocked in dynaflows minimal mode"), "minimal mode blocks the merge before halt is read");
		h.mode("structured");
		writeFileSync(path, JSON.stringify({ head: ciHead, halt: false, status: "incomplete",
			findings: [{ ...finding, status: "provisional" }], errors: ["judge offline"] }));
		assert.equal(await h.call("bash", { command }), undefined, "provisional findings and infrastructure errors stay shadow-only");
		structureResult(h);
		assert.equal(await h.call("bash", { command }), undefined);
	} finally { h.cleanup(); }
});

test.each([
	{ name: "failed CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }], fragment: "test: COMPLETED/FAILURE" },
	{ name: "pending CheckRun", rollup: [{ __typename: "CheckRun", name: "test", status: "IN_PROGRESS", conclusion: null }], fragment: "test: IN_PROGRESS" },
	{ name: "failed StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "FAILURE" }], fragment: "external: FAILURE" },
	{ name: "pending StatusContext", rollup: [{ __typename: "StatusContext", context: "external", state: "PENDING" }], fragment: "external: PENDING" },
	{ name: "only skipped", rollup: [{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" }], fragment: "no SUCCESS check reported" },
	{ name: "empty declared CI", rollup: [], boundary: "gate", fragment: "no CI checks reported for" },
	{ name: "empty workflow directory", rollup: [], boundary: "workflows", fragment: "no CI checks reported for" },
	{ name: "empty no-CI boundary", rollup: [] },
	{ name: "green", rollup: [
		{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "SUCCESS" },
		{ __typename: "CheckRun", name: "optional", status: "COMPLETED", conclusion: "NEUTRAL" },
		{ __typename: "CheckRun", name: "unused", status: "COMPLETED", conclusion: "SKIPPED" },
		{ __typename: "StatusContext", context: "external", state: "SUCCESS" },
	] },
])("merge CI rule: $name", async scenario => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		if ("boundary" in scenario && scenario.boundary === "gate") h.gate({ schemaVersion: 2, checks: { commit: ["true"], ci: ["bun", "ci"] } });
		if ("boundary" in scenario && scenario.boundary === "workflows") mkdirSync(join(h.root, ".github/workflows"), { recursive: true });
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [...scenario.rollup], { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		if ("fragment" in scenario) {
			assert.equal(isBlocked(result), true);
			assert.equal(reasonOf(result).includes(scenario.fragment), true, scenario.fragment);
			assert.equal(reasonOf(result).includes(ciHead), true, "diagnostic identifies the PR head");
		} else assert.equal(result, undefined);
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
		await h.read("skill://git-tool", mergeRecover);
		await originRemote(h, "git@github.com:base-owner/base-repo.git");
		h.gate({ schemaVersion: 2, ci: ciGate });
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [...scenario.rollup], { head: ciHead, selector: "3", url: "https://github.com/base-owner/base-repo/pull/3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		if ("state" in scenario) {
			assert.equal(isBlocked(result), true, "the required context must block");
			assert.equal(result?.reason, requiredRefusal(scenario.state));
		} else assert.equal(result, undefined);
	} finally { h.cleanup(); }
});

test("without a ci block every reported check still decides the merge", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" },
			{ __typename: "CheckRun", name: "lint", status: "COMPLETED", conclusion: "FAILURE" }], { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true);
		assert.ok(reasonOf(result).includes(`CI is not green for ${ciHead}: lint: COMPLETED/FAILURE`), String(result?.reason));
	} finally { h.cleanup(); }
});

test("agents can write records before enabling dynaflows and after disabling it", async () => {
	const h = harness();
	try {
		await h.read(integrity, structure);
		const gate = join(h.recordDir(), "gate.json");
		const draft = JSON.stringify({ schemaVersion: 2, checks: { commit: ["true"] } });
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
		assert.equal(h.modeOf(), "minimal");
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
		h.setSession("other");
		assert.equal((await h.call("write", { path: gate, content: draft }))?.block, undefined);
	} finally { h.cleanup(); }
});

test("minimal-mode merges are blocked without querying GitHub", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.gate({ schemaVersion: 2 });
		forbidGh(h);
		assert.ok(reasonOf(await h.call("bash", { command: "gh pr merge 3 --rebase" })).includes("blocked in dynaflows minimal mode"));
	} finally { h.cleanup(); }
});

test("a gate with every optional check and a ci block allows publishing", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.gate({ schemaVersion: 2, ci: ciGate, checks: { commit: ["true"], format: ["ruff", "format"], profile: ["guardrails", "profile-check"] } });
		assert.equal(await h.call("bash", { command: "git push" }), undefined);
	} finally { h.cleanup(); }
});

test.each(["git@github.com:OWNER/REPO.git", "https://github.com/owner/repo.git"])("PR URL repository: %s", async remote => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		await originRemote(h, remote);
		h.gate({ schemaVersion: 2, ci: ciGate });
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		const url = "https://github.com/other/checkout/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], { head: ciHead, selector: url, url });
		const result = await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true, "cross-repo URL must not use checkout policy");
		assert.ok(reasonOf(result).includes("run from a checkout of other/checkout"), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR URL selector accepts the checkout repository case-insensitively", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		await originRemote(h, "git@github.com:OWNER/REPO.git");
		h.gate({ schemaVersion: 2, ci: ciGate });
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		const url = "https://github.com/owner/repo/pull/3";
		mockPrCi(h, [{ __typename: "StatusContext", context: "gate", state: "SUCCESS" }], { head: ciHead, selector: url });
		assert.equal(await h.call("bash", { command: `gh pr merge ${url} --match-head-commit ${ciHead}` }), undefined);
	} finally { h.cleanup(); }
});

test("required context blocks merge despite a prove() pass", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		h.gate({ schemaVersion: 2, ci: ciGate });
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [{ __typename: "CheckRun", name: "audit-packages", status: "COMPLETED", conclusion: "SUCCESS" }], { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true, "the required context must block merge despite a prove() pass");
		assert.ok(reasonOf(result).includes(`required CI context "gate" is missing for ${ciHead} in owner/repo`), result?.reason ?? "missing refusal reason");
	} finally { h.cleanup(); }
});

test("PR query contract: requests the PR URL", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		await originRemote(h);
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
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
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		mockPrCi(h, [], { head: ciHead, selector: "3", failure: "not authenticated" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.ok(reasonOf(result).includes("cannot read PR head/CI via gh: not authenticated"), String(result?.reason));
	} finally { h.cleanup(); }
});

test.each(["head mismatch", "red CI", "green CI"])("merge CI gate: %s", async scenario => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		proveResult(h);
		structureResult(h);
		const head = scenario === "head mismatch" ? "b".repeat(40) : ciHead;
		mockPrCi(h, scenario === "red CI"
			? [{ __typename: "CheckRun", name: "test", status: "COMPLETED", conclusion: "FAILURE" }] : greenCi, { head, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		if (scenario === "green CI") assert.equal(result, undefined);
		else {
			assert.equal(isBlocked(result), true);
			const fragment = scenario === "head mismatch" ? `PR head is ${head}, not ${ciHead}; prove() the current head` : "test: COMPLETED/FAILURE";
			assert.equal(reasonOf(result).includes(fragment), true, fragment);
		}
	} finally { h.cleanup(); }
});

test("minimal mode blocks merges before the CI guard, even with a declared gate.ci", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.gate({ schemaVersion: 2, ci: ciGate });
		forbidGh(h);
		assert.ok(reasonOf(await h.call("bash", { command: "gh pr merge 3 --merge" })).includes("blocked in dynaflows minimal mode"));
		h.gate({ schemaVersion: 2, ci: ciGate, agentMerge: "disabled" });
		assert.ok((await h.call("bash", { command: "gh pr merge 3 --merge" }))?.reason?.includes("agentMerge is disabled"));
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
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 ${scenario.args}` });
		assert.equal(isBlocked(result), true);
		assert.equal(reasonOf(result).includes(scenario.fragment), true, scenario.fragment);
	} finally { h.cleanup(); }
});

test("merge diagnostics gate: missing prove() result", async () => {
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const result = await h.call("bash", { command: `gh pr merge 3 --match-head-commit ${ciHead}` });
		assert.equal(isBlocked(result), true);
		assert.equal(reasonOf(result).includes(`no prove() pass for ${ciHead}`), true);
	} finally { h.cleanup(); }
});

test("path checks canonicalize a symlinked repository root before executing", async () => {
	const h = harness();
	const aliases = mkdtempSync(join(tmpdir(), "dynaflows-guards-alias-"));
	try {
		const alias = join(aliases, "repo");
		symlinkSync(h.root, alias);
		h.context.cwd = alias;
		h.gate({ schemaVersion: 2 });
		h.mode("structured");
		await h.read(integrity, structure);
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
		h.gate({ schemaVersion: 2 });
		h.mode("structured");
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
		h.mode("structured");
		await h.read(integrity, structure);
		await h.call("edit", { path: join(h.root, "existing.ts") });
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.pathFailure();
		assert.ok((await h.emit("session_stop"))?.reason?.includes(verification));
		await h.read(verification);
		assert.equal(await h.emit("session_stop"), undefined);
	} finally { h.cleanup(); }
});

test("mode directives ignore mentions without a verb and reject contradictions and nonrepositories", async () => {
	const h = harness();
	const scratch = realpathSync(mkdtempSync(join(tmpdir(), "rails-directive-")));
	try {
		h.gate({ schemaVersion: 2 });
		h.mode("structured");
		const record = h.record();
		for (const prompt of ['"$dynaflows"', "$dynaflows status", "$dynaflows on\n$dynaflows off", "init $dynaflows\nstop $dynaflows",
			"$dynaflows enabled", "off $dynaflows", "$dynaflowson"]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		for (const prompt of ["please $dynaflows on", "please enable $dynaflows", "`$dynaflows init`", "$dynaflows on."]) {
			await h.emit("before_agent_start", { reason: "prompt", prompt });
			assert.deepEqual(h.record(), record, prompt);
		}
		await h.emit("before_agent_start", { reason: "prompt", prompt: "turn off $dynaflows" });
		assert.equal(h.modeOf(), "minimal");
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
		assert.equal(h.modeOf(), "structured", "the linked worktree sets the shared origin's mode");
		assert.equal(existsSync(join(linked, ".omp")), false);
		const head = (await execute("git", ["rev-parse", "HEAD"], { cwd: linked })).stdout.trim();
		await h.read("skill://git-tool", mergeRecover);
		const blocked = await h.call("bash", { command: `gh pr merge 3 --match-head-commit=${head}` });
		assert.ok(reasonOf(blocked).includes(join(realpathSync(h.root), ".git", "omp-prove", `${head}.result.json`)),
			`the result file lives in the shared Git common directory: ${blocked?.reason}`);
	} finally { h.cleanup(); rmSync(linked, { recursive: true, force: true }); }
});

test("Python proof gates and rails accept the same schema fixtures", async () => {
	const record = { checks: { commit: ["true"] } };
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
		{ schemaVersion: 2, dynaflows: { mode: "structured" } }, { schemaVersion: 2, dynaflows: { mode: "minimal" } },
		{ schemaVersion: 2, ownerOnlyPaths: [] }, { schemaVersion: 2, ownerOnlyPaths: ["scripts/checks/exceptions.json"] },
	].map(value => ({ ...record, ...value }));
	const h = harness();
	try {
		await h.read("skill://git-tool", mergeRecover);
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
		await h.read("skill://git-tool", mergeRecover);
		h.mode("structured");
		h.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
		assert.equal(await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }), undefined);
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		assert.ok((await h.emit("tool_call", { toolName: "bash", input: { command: "mise exec -- git commit -m x" } }))?.reason?.includes("checks.commit (false) failed"));
	} finally { h.cleanup(); }
});

test("mise directory options resolve the wrapped commit repository", async () => {
	const h = harness();
	const target = harness({ configRoot: h.records, remote: "git@github.com:owner/target.git" });
	try {
		await h.read("skill://git-tool", mergeRecover);
		target.gate({ schemaVersion: 2, checks: { commit: ["true"] } });
		target.mode("structured");
		target.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		for (const command of [
			`mise x -C '${target.root}' -- git commit -m x`,
			`mise exec --cd='${target.root}' node@20 -- git commit -m x`,
		]) {
			const enforced = await h.call("bash", { command });
			assert.ok(reasonOf(enforced).includes(`checks.commit (false) failed in ${target.root}`), `the wrapped repo's record is enforced: ${command}`);
		}
		const unresolved = await h.call("bash", { command: "mise x -C /nonexistent-rails-directory -- git commit -m x" });
		assert.equal(isBlocked(unresolved), true);
		assert.ok(reasonOf(unresolved).includes("effective cwd"));
	} finally { h.cleanup(); target.cleanup(); }
});

test.each(["absent", "minimal", "structured"])("repo-scoped harness process follows the dynaflows mode (%s) while Git reads always apply", async record => {
	const h = harness();
	try {
		if (record !== "absent") h.mode(record === "structured" ? "structured" : "minimal");
		h.pathFailure("path policy violation");
		const edit = await h.call("write", { path: "tests/new.test.ts" });
		if (record === "structured") {
			assert.equal(isBlocked(edit), true);
			for (const url of [integrity, structure, tiers, enforcement]) assert.ok(reasonOf(edit).includes(url));
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
		assert.equal(isBlocked(git), true);
		assert.ok(reasonOf(git).includes("skill://git-tool"));
	} finally { h.cleanup(); }
});

test("edit and handoff policy follows each edited repo, not the session cwd", async () => {
	const h = harness();
	const other = harness({ configRoot: h.records, remote: "git@github.com:owner/other.git" });
	try {
		h.mode("structured");
		other.mode("minimal");
		h.pathFailure("enabled repo violation");
		assert.equal(await h.call("edit", { path: join(other.root, "existing.ts") }), undefined);
		assert.equal(await h.emit("session_stop"), undefined, "editing only an off repo does not require a handoff read");
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.reason?.includes(integrity));
		await h.read(integrity, structure);
		assert.ok((await h.call("edit", { path: join(h.root, "existing.ts") }))?.additionalContext?.includes("enabled repo violation"));
		assert.equal((await h.emit("session_stop"))?.decision, "block");
		h.mode("minimal");
		assert.equal(await h.emit("session_stop"), undefined, "turning off suppresses both outstanding retries and handoff reads");
		assert.equal(await h.call("edit", { path: join(h.root, "existing.ts") }), undefined);
	} finally { h.cleanup(); other.cleanup(); }
});

test.each(["no record", "minimal", "structured"])("native judged rules follow the session dynaflows mode (%s) and preserve user exclusions", async record => {
	const h = harness();
	try {
		if (record !== "no record") h.mode(record === "structured" ? "structured" : "minimal");
		const rules = lookup("ttsr.disabledRules");
		assert.ok(rules);
		rules.override(h.api.pi.settings, ["user-disabled-rule"]);
		const config = z.object({ structuredRules: z.array(z.string()).min(1) }).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
		for (const name of config.structuredRules) {
			assert.ok(existsSync(join(import.meta.dir, "../agent/extensions/dynaflows/rules", `${name}.md`)), `configured native rule exists: ${name}`);
		}
		await h.emit("session_start");
		assert.deepEqual(rules.get(h.api.pi.settings), record === "structured"
			? ["user-disabled-rule"]
			: ["user-disabled-rule", ...config.structuredRules]);
		await h.emit("session_start");
		assert.deepEqual(rules.get(h.api.pi.settings), record === "structured"
			? ["user-disabled-rule"]
			: ["user-disabled-rule", ...config.structuredRules], "restarting the session does not duplicate exclusions");
	} finally { h.cleanup(); }
});

test("native judged rules follow each mode switch through the directive, the dynaflows tool and a new session", async () => {
	const h = harness();
	try {
		h.mode("minimal");
		const rules = lookup("ttsr.disabledRules");
		assert.ok(rules);
		rules.override(h.api.pi.settings, ["user-disabled-rule"]);
		const config = z.object({ structuredRules: z.array(z.string()).min(1) }).parse(Bun.YAML.parse(readFileSync(railsConfig, "utf8")));
		const minimalRules = ["user-disabled-rule", ...config.structuredRules];
		await h.emit("session_start");
		assert.deepEqual(rules.get(h.api.pi.settings), minimalRules);
		await h.emit("before_agent_start", { reason: "prompt", prompt: "enable $dynaflows" });
		assert.deepEqual(rules.get(h.api.pi.settings), ["user-disabled-rule"], "the structured directive turns the judged rules on");
		await h.emit("before_agent_start", { reason: "prompt", prompt: "$dynaflows minimal" });
		assert.deepEqual(rules.get(h.api.pi.settings), minimalRules, "the minimal directive turns them off");
		await h.tool("dynaflows", { mode: "structured" });
		assert.deepEqual(rules.get(h.api.pi.settings), ["user-disabled-rule"], "the dynaflows tool turns them on");
		h.mode("minimal");
		await h.emit("session_start");
		assert.deepEqual(rules.get(h.api.pi.settings), minimalRules, "a new session in minimal mode turns them off");
		h.mode("structured");
		await h.emit("session_start");
		assert.deepEqual(rules.get(h.api.pi.settings), ["user-disabled-rule"], "a new session in structured mode turns them on");
	} finally { h.cleanup(); }
});
