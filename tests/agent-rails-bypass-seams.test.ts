import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "bun:test";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import { prMerge } from "../agent/extensions/agent-rails/lib/guards.ts";
import { gitActions, loadMapping } from "../agent/extensions/agent-rails/lib/rails-runtime.ts";
import {
	harness, execute, reasonOf, isBlocked, ciHead, ciGate, greenCi, mockPrCi, proveResult, structureResult, railsConfig, wrappedPublishing,
} from "./agent-rails-guards-seams.test";

type Harness = ReturnType<typeof harness>;
const other = "b".repeat(40);

// governed: the repository has a valid agent-rails config; otherwise it is not onboarded and only the fixed guards decide.
async function withHarness(governed: boolean, body: (h: Harness) => Promise<void>): Promise<void> {
	const h = harness();
	try {
		if (governed) h.govern();
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

describe("agent-rails closes routes around the bash rails", () => {
	test("shell carriers that hide git, gh or omp fail closed", () => withHarness(true, async h => {
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

	test("pipelines and background jobs keep the cwd, so cd cannot move a commit out of its repository", () => withHarness(true, async h => {
		h.config({ commands: { commit: ["false"] } });
		for (const command of ["cd / | git commit -m x", "cd /tmp & git commit -m x"]) {
			await assertRefused(h, "bash", { command }, "commands.commit (false) failed");
		}
	}));

	test("git options and variables that retarget the repository, config or hooks are refused", () => withHarness(false, async h => {
		for (const command of [
			"git --git-dir=/x/.git push origin agent/test:main", "git --git-dir /x/.git push", "GIT_DIR=/x/.git git push origin agent/test:main",
			"GIT_WORK_TREE=/x git commit -m x", "git --work-tree=/x commit -m x", "git -c include.path=/tmp/x.cfg commit -m x",
			"git -c includeIf.gitdir:/.path=/tmp/x commit -m x", "git --config-env=include.path=V commit -m x", "GIT_CONFIG_GLOBAL=/tmp/x git commit -m x",
			"GIT_CONFIG_SYSTEM=/tmp/x git commit -m x", "HOME=/tmp/h git commit -m x", "export HOME=/tmp/h", "XDG_CONFIG_HOME=/tmp/x git push",
			"LEFTHOOK_EXCLUDE=pre-commit git commit -m x", "GH_REPO=o/other gh pr merge 3 --squash", "export GH_REPO=o/other",
			"git -c core.hooksPath=/dev/null push", "git -c gpg.program=/tmp/x commit -m x",
		]) await assertRefused(h, "bash", { command });
	}));

	test("persistent Git config, remote and ref writes that steer the rails are refused", () => withHarness(false, async h => {
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

	test("commands that create commits or run git outside git commit and git push are refused", () => withHarness(false, async h => {
		for (const command of [
			"git commit-tree HEAD^{tree} -m x", "git fast-import < stream", "git replay --onto main a..b", "git subtree push --prefix=d origin main",
			"git filter-branch --tree-filter true", "git rebase -x 'git commit --amend --no-verify --no-edit' HEAD~1",
			"git rebase --exec='git push origin HEAD:main' HEAD~1", "git submodule foreach git push origin HEAD:main",
			"git bisect run git commit -n -m x", "git absorb", "git http-push x main",
		]) await assertRefused(h, "bash", { command });
		await assertAllowed(h, "bash", { command: "git rebase -x 'bun test' HEAD~1" });
	}));

	test("the default branch stays protected when push config or the local remote HEAD moves", () => withHarness(true, async h => {
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

	test("gh aliases, extensions, settings writes and API writes outside PR conversation are refused", () => withHarness(false, async h => {
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
		await withHarness(true, async h => {
			for (const command of [
				`gh pr merge 3 --squash --match-head-commit ${ciHead} --match-head-commit ${other}`,
				`gh pr merge --match-head-commit=${ciHead} 3 --squash --match-head-commit=${other}`,
				`gh pr merge --frobnicate 7 --match-head-commit ${ciHead}`, `gh pr merge 3 4 --match-head-commit ${ciHead}`,
				`git push origin agent/test && gh pr merge 3 --squash --match-head-commit ${ciHead}`,
			]) await assertRefused(h, "bash", { command });
		});
	});

	test("proof evidence counts only when a watched prove() or structure_review() call wrote it", () => withHarness(true, async h => {
		mockPrCi(h, greenCi, { head: ciHead, selector: "3" });
		const merge = { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` };
		mkdirSync(join(h.root, ".git/omp-prove"), { recursive: true });
		mkdirSync(join(h.root, ".git/omp-structure"), { recursive: true });
		writeFileSync(join(h.root, ".git/omp-prove", `${ciHead}.result.json`), JSON.stringify({ head: ciHead, status: "pass" }));
		writeFileSync(join(h.root, ".git/omp-structure", `${ciHead}.json`), JSON.stringify({ head: ciHead, halt: false, status: "pass", findings: [] }));
		await assertRefused(h, "bash", merge, "not written by a prove() call the rails saw");
		await assertRefused(h, "write", { path: join(h.root, ".git/omp-prove", `${ciHead}.result.json`), content: "{}" }, "inside Git's internal directory");
		await assertRefused(h, "bash", { command: "mkdir -p .git/omp-prove && echo '{}' > .git/omp-prove/x.result.json" }, "writes near rail state");
		await assertRefused(h, "eval", { language: "py", code: "open('.git/omp-prove/x.result.json', 'w').write('{}')" }, "may not write rail state");
		await assertRefused(h, "eval", { language: "py", code: "prove = lambda *args, **kwargs: None" }, "may not redefine the proof workflow");
		await proveResult(h);
		await structureResult(h);
		await assertAllowed(h, "bash", merge);
	}));

	test("a failing required context blocks the merge even beside a green one of the same name", () => withHarness(true, async h => {
		h.config(ciGate);
		await proveResult(h);
		await structureResult(h);
		mockPrCi(h, [
			{ __typename: "StatusContext", context: "gate", state: "FAILURE" },
			{ __typename: "CheckRun", name: "gate", status: "COMPLETED", conclusion: "SUCCESS" },
		], { head: ciHead, selector: "3", url: "https://github.com/owner/repo/pull/3" });
		await assertRefused(h, "bash", { command: `gh pr merge 3 --rebase --match-head-commit ${ciHead}` }, 'required CI context "gate" is FAILURE');
	}));

	test("agent tools cannot write the review state, live extension, harness config or Git config", () => withHarness(false, async h => {
		const extension = dirname(railsConfig);
		for (const path of [
			h.options.reviewsPath ?? "", join(extension, "rails.yml"), join(extension, "index.ts"),
			join(extension, "../../config.yml"), join(h.root, ".git/hooks/pre-commit"), join(h.root, ".git/config"),
		]) await assertRefused(h, "write", { path, content: "x" });
		await assertRefused(h, "bash", { command: `printf '{}' > '${h.options.reviewsPath ?? ""}'` }, "writes near rail state");
		await assertRefused(h, "bash", { command: "chmod -x .git/hooks/pre-commit" }, "writes near rail state");
	}));

	test("agents never write a repository's agent-rails config, through any tool; reads pass", () => withHarness(false, async h => {
		h.config();
		await assertRefused(h, "write", { path: h.repoConfigPath, content: "x" }, "agents read it but never write it");
		await assertRefused(h, "write", { path: join(h.root, ".agentrails", "other.json"), content: "x" }, "agents read it but never write it");
		await assertRefused(h, "bash", { command: `cp /tmp/x '${h.repoConfigPath}'` }, "writes near rail state");
		await assertRefused(h, "bash", { command: "printf '{}' > .agentrails/config.json" }, "writes near rail state");
		await assertRefused(h, "eval", { language: "py", code: `open('${h.repoConfigPath}', 'w').write('x')` }, "may not write rail state");
		await assertAllowed(h, "bash", { command: "cat .agentrails/config.json" });
		await assertAllowed(h, "read", { path: h.repoConfigPath });
	}));

	test("an owner's config change during a session takes effect on the next action, including its removal", () => withHarness(true, async h => {
		await h.emit("session_start");
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		const saved = h.configText();
		h.config({ commands: { commit: ["false"] } });
		await assertRefused(h, "bash", { command: "git commit -m x" }, "commands.commit (false) failed");
		h.rawConfig(saved);
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		h.unconfigure();
		await assertRefused(h, "bash", { command: "git push origin agent/test" }, "owner/repo is not onboarded (missing)");
	}));

	test("an origin rewrite during a session refuses git actions", () => withHarness(true, async h => {
		await h.emit("session_start");
		await execute("git", ["remote", "set-url", "origin", "https://github.com/owner/other.git"], { cwd: h.root });
		await assertRefused(h, "bash", { command: "git commit -m x" }, "origin remote");
	}));

	test("eval cannot commit, run git or gh, or launch omp", () => withHarness(false, async h => {
		for (const code of [
			"sh(\"git commit --no-verify -m x\")", "import subprocess\nsubprocess.run(['git', 'push', 'origin', 'HEAD:main'])",
			"d = await commit.draft()\nawait d.commit('msg', no_verify=True)", "await (await commit.draft()).commit('m')",
			"await $`gh pr merge 3 --admin`", "require('child_process').execSync('git push')", "!git commit -n -m x",
			"import os; os.system('omp -p hi')",
		]) await assertRefused(h, "eval", { language: "py", code });
		for (const code of ["print(1 + 1)", "sh(\"ls\")", "%load ~/.omp/agent/extensions/agent-rails/lib/rails_kernel.py"]) {
			await assertAllowed(h, "eval", { language: "py", code });
		}
	}));

	test("session start pins the omp settings behind the eval commit, github push, branch-merge and handler-timeout routes", () => withHarness(false, async h => {
		const settings = h.api.pi.settings;
		for (const id of ["commit.enabled", "github.enabled"]) lookup(id)?.override(settings, true);
		lookup("task.isolation.merge")?.override(settings, "branch");
		await h.emit("session_start");
		assert.equal(lookup("commit.enabled")?.get(settings), false);
		assert.equal(lookup("github.enabled")?.get(settings), false);
		assert.equal(lookup("task.isolation.merge")?.get(settings), "patch");
		assert.ok(Number(lookup("extensionHandlers.toolCallTimeoutMs")?.get(settings)) > 600_000);
	}));

	test("commit checks run under a timeout below the pinned tool-call budget", () => withHarness(true, async h => {
		const timeouts: (number | undefined)[] = [];
		const exec = h.api.exec.bind(h.api);
		h.api.exec = async (command, args, options) => {
			if (command === "env" && args.includes("true")) timeouts.push(options.timeout);
			return exec(command, args, options);
		};
		await h.emit("session_start");
		await assertAllowed(h, "bash", { command: "git commit -m x" });
		const budget = Number(lookup("extensionHandlers.toolCallTimeoutMs")?.get(h.api.pi.settings));
		assert.ok(timeouts.length > 0 && timeouts.every(timeout => timeout !== undefined && timeout <= 600_000 && timeout < budget), JSON.stringify(timeouts));
	}));

	test("the harness config disables omp's eval commit prelude", () => {
		const config = Bun.YAML.parse(readFileSync(join(dirname(railsConfig), "../../config.yml"), "utf8"));
		assert.equal(JSON.stringify(config).includes('"commit":{"enabled":false}'), true);
	});
});
// rails.yml gitCommand: the Git and GitHub writes the rails check (extension review, unresolved origin, commit and publishing checks).
test("the rails check every integration git command, and no inspection command or look-alike", async () => {
	const h = harness();
	try {
		const gitWrite = RegExp(loadMapping(railsConfig).gitCommand);
		const checked = async (command: string) => (await gitActions(command, h.root)).some(action => gitWrite.test(action.command));
		for (const command of [
			"git status && git diff", "git log --grep=commit", 'rg "git push"', 'echo "git commit"',
			"echo 'git commits'", "npm run gh-pr-merge", "git switch existing", "git checkout existing",
			"git -C /w status", "git -c color.ui=never log", "gh pr view", "git stash list", "git stash", "git fetch origin", "git fetch origin main",
			"git fetch https://github.com/o/r main", "gh repo view", "gh issue list", "gh run list", "gh release view v1", "git config --get core.hooksPath",
			"git tag", "git tag -l", "git tag --list 'v*'", "git tag -n5", "git tag --contains HEAD", "git branch", "git branch -a",
			"git branch --list", "git worktree list", "gh api repos/o/r/pulls", "gh api -X GET repos/o/r", "gh api --method=HEAD x",
			"gh pr checks 3", "gh pr diff 3",
			"command -v git push", "sudo git status", "env -i git diff", 'sh -c "git log --grep=commit"',
		]) assert.equal(await checked(command), false, command);
		for (const command of [
			"git commit -m 'feat: x'", "cd repo && git push -u origin agent/x", "gh pr merge 7 --rebase",
			"git -C /w commit", "git -c user.name=x commit", "git --no-pager commit", "git status\ngit commit",
			"FOO=1 git push", "git switch -c agent/x", "git switch -C agent/x", "git switch --create=agent/x",
			"git checkout -b agent/x", "git checkout -B agent/x", "git worktree add /w",
			'git -C "/some path" -c core.pager=cat --no-pager push',
			"git status; git push", "true | git push", "(git push)",
			"FOO=x BAR=y gh pr create",
			"git status; git merge x", "false || git rebase x", "git cherry-pick abc", "git reset --hard HEAD~1", "git reset",
			"git tag v1", "git tag -a v1 -m x", "git tag -d v1", "git branch -d old", "git branch -D old", "git branch --delete old",
			"git branch -v -D old", "git worktree remove /w", "gh pr edit 3 --title x", "gh pr close 3",
			'FOO="a b" git push', "FOO='x;y' BAR=z git commit", "git -C'/some path' push",
			"git revert HEAD", "git am patch", "git pull", "git branch -f main HEAD", "git branch -m old new",
			"git branch -M old new", "git stash drop", "git stash clear", "git stash pop", "git restore .", "git clean -fdx", "git fetch origin main:main",
			"git fetch origin +refs/heads/main:refs/heads/main", "gh pr ready 3", "gh pr review 3 --approve", "gh pr comment 3 -b x", "gh pr reopen 3",
			"gh release create v1", "gh workflow run ci.yml", "gh issue create -t x", "gh run rerun 5",
			...wrappedPublishing,
		]) assert.equal(await checked(command), true, command);
	} finally { h.cleanup(); }
});

