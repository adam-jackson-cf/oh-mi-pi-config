import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "bun:test";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { prMerge } from "../agent/extensions/dynaflows/lib/guards.ts";
import {
	harness, execute, reasonOf, isBlocked, ciHead, ciGate, greenCi, mockPrCi, mergeRecover, proveResult, structureResult, railsConfig,
} from "./dynaflows-guards-seams.test";

type Harness = ReturnType<typeof harness>;
const other = "b".repeat(40);

async function withHarness(mode: "minimal" | "structured" | undefined, body: (h: Harness) => Promise<void>): Promise<void> {
	const h = harness();
	try {
		if (mode) h.mode(mode);
		await h.read("skill://git-tool", mergeRecover);
		await body(h);
	} finally { h.cleanup(); }
}

async function assertRefused(h: Harness, tool: string, input: Record<string, string>, fragment = "Agent rails"): Promise<void> {
	const result = await h.call(tool, input);
	assert.equal(isBlocked(result), true, `${tool} ${JSON.stringify(input)} was allowed`);
	assert.ok(reasonOf(result).includes(fragment), `${JSON.stringify(input)}: ${reasonOf(result)}`);
}

async function assertAllowed(h: Harness, tool: string, input: Record<string, string>): Promise<void> {
	const result = await h.call(tool, input);
	assert.ok(!isBlocked(result), `${tool} ${JSON.stringify(input)}: ${reasonOf(result)}`);
}

describe("dynaflows closes routes around the bash rails", () => {
	test("shell carriers that hide git, gh or omp fail closed", () => withHarness(undefined, async h => {
		writeFileSync(join(h.root, "p.sh"), "git push origin HEAD:main\n");
		for (const command of [
			"python3 -c \"import subprocess;subprocess.run(['git','commit','-n','-m','x'])\"",
			"echo 'git commit -n -m x' | sh", "sh <<< 'git commit -n'", "g=git; $g commit -n -m x", "$(echo git) commit -n",
			"find . -maxdepth 0 -exec git commit -n -m x \\;", "caffeinate -i git commit -n", "f() { \"$@\"; }; f git commit -n",
			"printf -- --no-verify | xargs git commit -m x", "bash <(echo 'git push origin HEAD:main')",
			"eval \"$(printf 'git push origin HEAD:main')\"", "node -e \"require('child_process').execSync('git push origin HEAD:main')\"",
			"omp -p --no-extensions 'commit everything'", "ssh nuc 'git push origin HEAD:main'", "script -q /dev/null git push origin HEAD:main",
			"source ./p.sh", ". ./p.sh", "bash p.sh", "python3 - <<'EOF'\nimport os; os.system('git push')\nEOF",
			"curl -H \"Authorization: Bearer $(gh auth token)\" https://api.github.com/repos/o/r/pulls/3/merge -X PUT",
			"curl -H \"Authorization: token $GITHUB_TOKEN\" -X PUT https://api.github.com/repos/o/r/pulls/3/merge",
		]) await assertRefused(h, "bash", { command });
		for (const command of [
			"python3 -m pytest -k thing", "bun test tests/x.test.ts -t 'blocks git push'", "npm run gh-pr-merge", "rg 'git push' src",
			"find . -name '*.ts'", "echo hi | sh", "omp --version", "omp config get worktree.base", "HOME=/tmp/h npm test",
		]) await assertAllowed(h, "bash", { command });
	}));

	test("pipelines and background jobs keep the cwd, so cd cannot move a commit out of its repository", () => withHarness("structured", async h => {
		h.gate({ schemaVersion: 2, checks: { commit: ["false"] } });
		for (const command of ["cd / | git commit -m x", "cd /tmp & git commit -m x"]) {
			await assertRefused(h, "bash", { command }, "checks.commit (false) failed");
		}
	}));

	test("git options and variables that retarget the repository, config or hooks are refused", () => withHarness(undefined, async h => {
		for (const command of [
			"git --git-dir=/x/.git push origin agent/test:main", "git --git-dir /x/.git push", "GIT_DIR=/x/.git git push origin agent/test:main",
			"GIT_WORK_TREE=/x git commit -m x", "git --work-tree=/x commit -m x", "git -c include.path=/tmp/x.cfg commit -m x",
			"git -c includeIf.gitdir:/.path=/tmp/x commit -m x", "git --config-env=include.path=V commit -m x", "GIT_CONFIG_GLOBAL=/tmp/x git commit -m x",
			"GIT_CONFIG_SYSTEM=/tmp/x git commit -m x", "HOME=/tmp/h git commit -m x", "export HOME=/tmp/h", "XDG_CONFIG_HOME=/tmp/x git push",
			"LEFTHOOK_EXCLUDE=pre-commit git commit -m x", "GH_REPO=o/other gh pr merge 3 --squash", "export GH_REPO=o/other",
			"git -c core.hooksPath=/dev/null push", "git -c gpg.program=/tmp/x commit -m x",
		]) await assertRefused(h, "bash", { command });
	}));

	test("persistent Git config, remote and ref writes that steer the rails are refused", () => withHarness(undefined, async h => {
		for (const command of [
			"git config core.hooksPath /dev/null", "git config --global core.hooksPath x", "git config alias.pm 'push origin HEAD:main'",
			"git config remote.pushDefault evil", "git config include.path /tmp/x", "git config --unset core.hooksPath", "git config set core.hooksPath x",
			"git config -e", "git config --add remote.origin.push HEAD:main", "git config branch.agent/test.pushRemote evil",
			"git config url.evil.pushInsteadOf origin", "git remote set-url origin https://github.com/a/b.git", "git remote add evil x",
			"git remote rename origin up", "git remote remove origin", "git remote set-head origin nuc-config",
			"git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/zzz", "git update-ref refs/heads/main abc",
		]) await assertRefused(h, "bash", { command });
		for (const command of [
			"git config user.name x", "git config --get core.hooksPath", "git config core.hooksPath", "git config --list", "git remote -v",
			"git remote set-head origin --auto", "git symbolic-ref --short HEAD",
		]) await assertAllowed(h, "bash", { command });
	}));

	test("commands that create commits or run git outside git commit and git push are refused", () => withHarness(undefined, async h => {
		for (const command of [
			"git commit-tree HEAD^{tree} -m x", "git fast-import < stream", "git replay --onto main a..b", "git subtree push --prefix=d origin main",
			"git filter-branch --tree-filter true", "git rebase -x 'git commit --amend --no-verify --no-edit' HEAD~1",
			"git rebase --exec='git push origin HEAD:main' HEAD~1", "git submodule foreach git push origin HEAD:main",
			"git bisect run git commit -n -m x", "git absorb", "git http-push x main",
		]) await assertRefused(h, "bash", { command });
		await assertAllowed(h, "bash", { command: "git rebase -x 'bun test' HEAD~1" });
	}));

	test("the default branch stays protected when push config or the local remote HEAD moves", () => withHarness(undefined, async h => {
		await assertAllowed(h, "bash", { command: "git push origin agent/test" });
		await execute("git", ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/zzz"], { cwd: h.root });
		await assertRefused(h, "bash", { command: "git push origin HEAD:main" }, "default branch main");
		await execute("git", ["remote", "add", "evil", "git@github.com:owner/repo.git"], { cwd: h.root });
		await execute("git", ["config", "remote.pushDefault", "evil"], { cwd: h.root });
		await execute("git", ["config", "remote.evil.push", "refs/heads/agent/test:refs/heads/main"], { cwd: h.root });
		await assertRefused(h, "bash", { command: "git push" }, "cannot resolve the default branch of remote evil");
		await execute("git", ["config", "--unset", "remote.pushDefault"], { cwd: h.root });
		await execute("git", ["config", "remote.origin.mirror", "true"], { cwd: h.root });
		await assertRefused(h, "bash", { command: "git push origin" }, "default branch");
	}));

	test("gh aliases, extensions, settings writes and API writes outside PR conversation are refused", () => withHarness(undefined, async h => {
		for (const command of [
			"gh alias set mm 'pr merge'", "gh mm 3", "gh extension install x/y", "gh repo set-default o/r", "gh repo edit --default-branch x",
			"gh secret set X", "gh auth token", "gh api 'repos/o/r/pulls/1/merge#' -X PUT", "gh api -X PUT repos/o/r/contents/x -f branch=main",
			"gh api repos/o/r/statuses/abc -f state=success -f context=gate", "gh api -X POST repos/o/r/check-runs -f name=gate",
			"gh api repos/o/r/merge-upstream -f branch=main", "gh api -X PUT /repos/o/r/pulls/3//merge", "gh api -X PUT repos/o/r/pulls/1%2Fmerge",
			"E=repos/o/r/pulls/1/merge; gh api -X PUT \"$E\"", "gh api graphql -F query=@m.graphql", "gh api graphql --input body.json",
			"gh api graphql -f query='mutation { enqueuePullRequest(input: {}) { clientMutationId } }'",
			"gh api -X PATCH repos/o/r -f default_branch=x", "gh api -X PUT repos/o/r/branches/main/protection",
		]) await assertRefused(h, "bash", { command });
		for (const command of [
			"gh api repos/o/r/issues/3/comments -f body=hi", "gh api repos/o/r/pulls/3", "gh pr view 3", "gh run view 5",
			"gh api graphql -f query='mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }' -f id=x",
		]) await assertAllowed(h, "bash", { command });
	}));

	test("gh pr merge is parsed with gh's own flags: one head, one PR, and alone in its command", async () => {
		assert.deepEqual(prMerge(["pr", "merge", "-ds", "7", "--match-head-commit", ciHead]).selectors, ["7"]);
		assert.deepEqual(prMerge(["pr", "merge", "--disable-auto", "7", "--match-head-commit", ciHead]).selectors, ["7"]);
		assert.deepEqual(prMerge(["pr", "merge", "-t", "subject", "7"]).selectors, ["7"]);
		await withHarness("structured", async h => {
			for (const command of [
				`gh pr merge 3 --squash --match-head-commit ${ciHead} --match-head-commit ${other}`,
				`gh pr merge --match-head-commit=${ciHead} 3 --squash --match-head-commit=${other}`,
				`gh pr merge --frobnicate 7 --match-head-commit ${ciHead}`, `gh pr merge 3 4 --match-head-commit ${ciHead}`,
				`git push origin agent/test && gh pr merge 3 --squash --match-head-commit ${ciHead}`,
			]) await assertRefused(h, "bash", { command });
		});
	});

	test("proof evidence counts only when a watched prove() or structure_review() call wrote it", () => withHarness("structured", async h => {
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const merge = { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` };
		mkdirSync(join(h.root, ".git/omp-prove"), { recursive: true });
		mkdirSync(join(h.root, ".git/omp-structure"), { recursive: true });
		writeFileSync(join(h.root, ".git/omp-prove", `${ciHead}.result.json`), JSON.stringify({ head: ciHead, status: "pass" }));
		writeFileSync(join(h.root, ".git/omp-structure", `${ciHead}.json`), JSON.stringify({ head: ciHead, halt: false, status: "shadow", findings: [] }));
		await assertRefused(h, "bash", merge, "not written by a prove() call the rails saw");
		await assertRefused(h, "write", { path: join(h.root, ".git/omp-prove", `${ciHead}.result.json`), content: "{}" }, "inside Git's internal directory");
		await assertRefused(h, "bash", { command: "mkdir -p .git/omp-prove && echo '{}' > .git/omp-prove/x.result.json" }, "writes near rail state");
		await assertRefused(h, "eval", { language: "py", code: "open('.git/omp-prove/x.result.json', 'w').write('{}')" }, "may not write rail state");
		await assertRefused(h, "eval", { language: "py", code: "prove = lambda *args, **kwargs: None" }, "may not redefine the proof workflow");
		await proveResult(h);
		await structureResult(h);
		await assertAllowed(h, "bash", merge);
	}));

	test("a failing required context blocks the merge even beside a green one of the same name", () => withHarness("structured", async h => {
		h.gate({ schemaVersion: 2, checks: { commit: ["true"] }, ci: ciGate });
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [
			{ __typename: "StatusContext", context: "gate", state: "FAILURE" },
			{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "SUCCESS" },
		], { head: ciHead, selector: "3", url: "https://github.com/owner/repo/pull/3" });
		await assertRefused(h, "bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` }, 'required CI context "gate" is FAILURE');
	}));

	test("agent tools cannot write the records, mode file, review state, live extension, harness config or Git config", () => withHarness(undefined, async h => {
		const extension = dirname(railsConfig);
		for (const path of [
			join(h.recordDir(), "gate.json"), h.options.modesPath ?? "", h.options.reviewsPath ?? "", join(extension, "modes.json"), join(extension, "rails.yml"), join(extension, "index.ts"),
			join(extension, "../../config.yml"), join(h.root, ".git/hooks/pre-commit"), join(h.root, ".git/config"),
		]) await assertRefused(h, "write", { path, content: "x" });
		await assertRefused(h, "bash", { command: `printf '{"schemaVersion":1,"repos":{}}' > '${h.options.modesPath ?? ""}'` }, "writes near rail state");
		await assertRefused(h, "bash", { command: "printf '{}' > agent/extensions/dynaflows/modes.json" }, "writes near rail state");
		await assertRefused(h, "bash", { command: `printf '{}' > '${h.options.reviewsPath ?? ""}'` }, "writes near rail state");
		await assertRefused(h, "bash", { command: "chmod -x .git/hooks/pre-commit" }, "writes near rail state");
		await assertRefused(h, "bash", { command: `cp /tmp/x '${join(h.records, "repos/owner/repo/gate.json")}'` }, "writes near rail state");
	}));

	test("record, mode file and origin changes during a session refuse every git and gh action", () => withHarness("structured", async h => {
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		const gate = join(h.recordDir(), "gate.json");
		const saved = readFileSync(gate, "utf8");
		writeFileSync(gate, JSON.stringify({ schemaVersion: 2, checks: { commit: ["true"] }, dynaflows: { mode: "minimal" } }));
		await assertRefused(h, "bash", { command: "git commit -m x" }, "changed during this session");
		writeFileSync(gate, saved);
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		h.mode("minimal");
		await assertRefused(h, "bash", { command: "git push origin agent/test" }, "changed during this session");
	}));

	test("an origin rewrite during a session refuses git actions", () => withHarness("structured", async h => {
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		await execute("git", ["remote", "set-url", "origin", "https://github.com/owner/other.git"], { cwd: h.root });
		await assertRefused(h, "bash", { command: "git commit -m x" }, "origin remote");
	}));

	test("eval cannot commit, run git or gh, or launch omp", () => withHarness(undefined, async h => {
		for (const code of [
			"sh(\"git commit --no-verify -m x\")", "import subprocess\nsubprocess.run(['git', 'push', 'origin', 'HEAD:main'])",
			"d = await commit.draft()\nawait d.commit('msg', no_verify=True)", "await (await commit.draft()).commit('m')",
			"await $`gh pr merge 3 --admin`", "require('child_process').execSync('git push')", "!git commit -n -m x",
			"import os; os.system('omp -p hi')",
		]) await assertRefused(h, "eval", { language: "py", code });
		for (const code of ["print(1 + 1)", "sh(\"ls\")", "%load ~/.omp/agent/extensions/dynaflows/engine/workflows.py"]) {
			await assertAllowed(h, "eval", { language: "py", code });
		}
	}));

	test("session start pins the omp settings behind the eval commit, github push, branch-merge and handler-timeout routes", () => withHarness(undefined, async h => {
		const settings = h.api.pi.settings;
		for (const id of ["commit.enabled", "github.enabled"]) lookup(id)?.override(settings, true);
		lookup("task.isolation.merge")?.override(settings, "branch");
		await h.emit("session_start");
		assert.equal(lookup("commit.enabled")?.get(settings), false);
		assert.equal(lookup("github.enabled")?.get(settings), false);
		assert.equal(lookup("task.isolation.merge")?.get(settings), "patch");
		assert.ok(Number(lookup("extensionHandlers.toolCallTimeoutMs")?.get(settings)) > 600_000);
	}));

	test("commit checks run under a timeout below the pinned tool-call budget", () => withHarness("structured", async h => {
		const timeouts: (number | undefined)[] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			if (command === "env" && args.includes("true")) timeouts.push(options.timeout);
			return exec(command, args, options);
		};
		await h.emit("session_start");
		await h.read("skill://git-tool", mergeRecover);
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		const budget = Number(lookup("extensionHandlers.toolCallTimeoutMs")?.get(h.api.pi.settings));
		assert.ok(timeouts.length > 0 && timeouts.every(timeout => timeout !== undefined && timeout <= 600_000 && timeout < budget), JSON.stringify(timeouts));
	}));

	test("the harness config disables omp's eval commit prelude", () => {
		const config = Bun.YAML.parse(readFileSync(join(dirname(railsConfig), "../../config.yml"), "utf8"));
		assert.equal(JSON.stringify(config).includes('"commit":{"enabled":false}'), true);
	});
});
