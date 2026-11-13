# Pattern contracts

Stage ownership is canonical in [SKILL.md](../SKILL.md#six-stage-contract).
Git authority and CI/PR/merge mechanics belong to `skill://git-tool`.

## Spike recipe

Main reads `skill://git-tool`, resolves the primary remote/default and fetches it
before creating `git worktree add --detach <absolute-scratch-path> <fetched-ref>`.
Each question has a pass/fail predicate, timebox and declared side effects; writes
to shared services require owner approval. Main gives a non-isolated task absolute
scratch paths and the smallest bounded experiment on the real surface. Prototype
races are useful only when built results decide; use innovation-council only when
no prototype can settle the question. The task returns findings, commands, actual
outputs and failures. Main saves `local://spike-<slug>.md` before removing the tree
with `git worktree remove --force <absolute-scratch-path>`. No spike code is reused.

## Proof manifest

```python
freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None,
       previous=None, revise=None, max_checks=MAX_PROOFS, project_setup=False)
```

`proofs` are draft records without `sha256`; the return is `(Path, manifest_sha256)`.
An empty list is valid: a PR with no behaviour change (refactor, docs, project setup
without new controls) freezes no proofs, and `prove()` then checks CI green only, unless the
PR changes a feature-map doc (which then needs a linked proof).
Store the manifest at `<git-common-dir>/omp-manifests/<slug>.json`, outside tracked
repository files. Freeze rejects destinations under resolved `$TMPDIR` or `/tmp`;
`previous=` may still read a manifest from a temporary location.
`MAX_PROOFS = 16` is independent of `MAX_AGENTS = 16`. Both freeze and prove accept
a positive integer `max_checks` (not bool), applied to current/previous manifests.
The entire draft and top-level inputs are validated before hashing or execution.
`project_setup=True` only tolerates an absent record, for the setup PR that drafts it; without a
record there is no feature map, so that freeze accepts only an empty manifest.
When the record's `gate.json` declares `checks.format`, freeze runs that formatter in the
worktree before hashing. A nonzero exit fails freeze. The result lists files the
formatter changed (from `git status`) so the writer commits them; hashes cover the
formatted bytes.

Generated frozen output (not a draft input):

```json
{
  "schemaVersion": 1,
  "slug": "fix-login-redirect",
  "base": "<40-hex resolved baseline>",
  "proofs": [
    {
      "id": "P1",
      "criterion": "Expired session redirects to /login",
      "cmd": ["pnpm", "vitest", "run", "src/auth/redirect.test.ts"],
      "files": ["src/auth/redirect.test.ts"],
      "parent": "<40-hex pinned by freeze>",
      "sha256": {"src/auth/redirect.test.ts": "<sha256 written by freeze>"}
    }
  ]
}
```

- **Mapped proofs.** Every proof file must be linked (a relative Markdown link) from a
  feature-map doc under the record profile's `verification_surface.feature_map_path`, read
  from the working tree with `omp_guardrails`' `journeys.mapped_paths`/`resolve_link`.
  An unlinked file is refused with its proof ID. A profile with no `feature_map_path` (absent,
  exempt or placeholder) refuses every proof and points to
  [Project setup](playbooks/project-setup.md); a declared path that does not exist is refused.
  Zero-proof manifests need no map.
- One rule for every proof: **red at base, green at head**. Freeze overlays each
  proof's files on its pinned parent and requires a failing run there. When the
  record's `profile.md` front matter declares `verification_surface.doctor_marker`,
  red output containing that marker is refused: the red came from the environment
  doctor, not the behaviour. An absent profile or key skips that check.
  Infrastructure failure (launch, timeout, IO, setup) is never valid red evidence.
  A proof that passes at base returns to Plan: the behaviour already exists or the
  proof does not encode the criterion. Never weaken a proof to manufacture red.
- IDs are unique; criteria and argv are nonblank.
- `files` use canonical POSIX repository-relative paths. Reject empty/dot/parent
  components, repeated separators, backslashes/drive forms, leading/trailing
  slash, NUL and `.git` components rather than normalising them. Symlinks, ignored
  files and clean-filter/EOL byte changes are rejected at actual roots; lexical
  validation does not depend on host temporary-directory contents.
- Only listed files and feature-map docs may be dirty. Map docs are not hashed: implementation
  may refine them. Freeze whole files, including shared fixtures.
  Each proof is overlaid alone, never with another proof's files.
- Inspect each argv token literally, including the value after its first `=`;
  strip one leading `./` for comparison. Every token naming an existing regular
  repository file, even an unchanged tracked runner, must be in that proof's
  `files`. Absolute references into source/execution trees are rejected; external
  absolute executables such as `sys.executable` remain allowed. There is no shell,
  language, pytest-node-ID or selector parsing.
- Each detached scratch tree starts fresh at its pinned commit and runs setup
  once, on first use. After checking that setup did not change tracked bytes,
  record its untracked and ignored entries (including collapsed directories).
  Before and after every execution, reset tracked files and remove untracked
  and ignored entries absent from that baseline. Setup outputs survive;
  residue within baseline directories (e.g. `node_modules/.cache` or `target/`)
  intentionally persists. Setup or baseline-recording failure is infrastructure
  failure. Never clean the source checkout. Commands must defeat result caches,
  e.g. `go test -count=1`.
- Optional `journey: true` marks a scripted journey proof. Prove runs it 3 times
  on the head instead of twice, and blocks unless every clean run passes
  (`journey proof passed <n> of 3 clean head runs`). Freeze does not run a
  stability loop, so proof tasks do not rerun journeys by hand.
- Each proof's parent is pinned, defaulting to manifest base. A regression found after
  implementation names the defective implementation commit as its parent. Prove requires
  each parent to be a strict ancestor of head at or after base.
- `base=None` means HEAD for a new lineage, but inherits the previous base during
  re-freeze; omitted setup inherits previous setup. With `previous=(path, digest)`,
  slug, resolved base and setup are immutable. A new base/setup needs a new
  lineage. Unchanged records retain evidence without rerunning. Changed/removed
  records require nonblank `revise={id: reason}` and record
  `{id, oldSha256, reason}`; the digest covers the entire old record.

### Discoveries and the map sweep

User-facing behaviour found during implementation, or by the map sweep before the PR, gets a
feature-map entry and an outcome test written uncommitted, then main re-freezes with
`previous=(path, digest)`, passing every earlier proof plus the new ones. Added proof IDs need no
`revise`; only they (and revised proofs) run red at base at that freeze. Freeze refuses any other
dirty file, so the writer commits or reverts in-progress product edits before stopping.
The map sweep lists each behaviour with a concrete source path and adds entries and
tests only; it emits no review findings.

### Recovery

- `prove()`'s result file (and its PR comment, when it fits) carries the full manifest so a lost
  manifest can be restored with its recorded digest.
- Re-run `freeze()` in a clean worktree with the same base, setup and proof bytes
  to reproduce a byte-identical manifest digest.
- A writer finding a frozen proof unfixable (for example, formatting) STOPs.
  Main authorises a proof-only change, then re-freezes with `previous=(path, digest)`
  and `revise={id: reason}` before implementation resumes.

## prove

```python
prove(repo, parent, head, manifest_path, manifest_sha256, objective, *,
      target_ref, max_checks=MAX_PROOFS)
```

Main calls this at the PR event, against the PR's current head. `repo` is the dedicated
source worktree on a named branch at `head`, even when main's session is in the primary
checkout. Resolve the actual fetched PR target and require
`parent == git merge-base(target_ref, head)` with strict ancestry, not `HEAD~1`. The helper
checks consistency; git-tool owns confirming actual GitHub target metadata.

Prove checks only:

1. CI green on head: the record's `ci.requiredContext` (a commit status or check run, whoever
   posts it) must be success on the exact head before any proof runs.
2. Map coverage: every feature-map doc under `verification_surface.feature_map_path` changed in
   `parent..head` (`git diff --name-only`, deletions and the profile's map index files excluded)
   must link at least one proof file in the manifest. Otherwise prove blocks and names the docs.
   A record with no feature map skips this check.
3. The frozen proofs: manifest and file hashes match, the head bytes are unchanged, and every
   proof passes at head (twice; `journey: true` proofs 3 times, all of which must pass).

With an empty manifest and no changed map doc prove checks CI green only. It runs no
repository suites, no harness
checks and no `checks.profile`: rails ran the harness checks at each commit and CI ran the
repo's gates. Red at base was shown at freeze; prove does not rerun base. Partial proofs,
tracked mutation and changed frozen head bytes fail. Launch, timeout, IO, setup and cleanup
errors are attributed infrastructure failures (`ok: null`, error/output/location), not failed
assertions. Cleanup failure prevents pass.

A rebase, merge of the target or other branch update needs nothing extra: call prove on the
new head. The proofs are frozen bytes, so no re-freeze is needed unless a proof file changed or a
discovery added a proof.

Output includes `status`, `deterministic`, `blocking`, `head`, `objective`, `manifestSha256`
and the loaded `manifest`. Prove writes that result to
`<git-common-dir>/omp-prove/<head>.result.json` and posts it as a PR comment on the one open PR
at head (`prComment` holds the comment URL). Long proof outputs are tail-truncated in the
comment only; the file keeps them whole. A pass whose result cannot be written or posted
becomes `fail` with blocking `infrastructure: publishing prove result: ...`. The result file
with `status: pass` for the exact head is the one pass signal: with dynaflows on, rails'
`gh pr merge` guard reads it and requires CI green on that head. Never write or edit a result
file by hand.

## Rails command parsing

Rails inspect `git`/`gh` words inside supported tool-runner launchers: `mise exec`/`mise x`
(with `--` before the command), `pnpm exec`/`pnpm dlx`, `npx`, `bunx`, `uv run` and
`poetry run`. `uv run` and `poetry run` honour `--` as the end of launcher options. Without
that separator, ambiguous launcher options with a later `git`/`gh` word block rather than
silently skipping checks; put the Git command first after options or use `--`. Rails
recursively scan `$(...)` and backtick command substitutions, including inside double quotes,
without evaluating their output. Alias and shell function definitions whose bodies contain a
`git` or `gh` word fail closed.

## blind_label

```python
blind_label(batch_files, rubric_path, labels, labellers=("scout", "reviewer"))
```

Frozen JSON batches are a list or `{cases: [...]}` with unique nonblank string
case IDs. Labels must be a nonempty list/tuple of nonblank strings. The labellers
argument is exactly scout and reviewer, in either order, supplied as a list/tuple.
They receive separate blind read-only sessions containing only batch/rubric and
quote deciding evidence. This promises read-only sessions, not a cross-family
guarantee or code verification. Two jobs per batch, at most eight batches/16
agents; excess raises, no implicit waves.

JSON-safe output uses lists: `agreed:[{batch,id,label}]`,
`disputed:[{batch,id,votes}]`, `failed:[{batch,labeller,error}]`. Agreed/disputed
records follow batch/case input order; `batch` is a zero-based index. `votes` maps
labeller names to their `{id,label,evidence,rationale}` answer records.
Only input IDs appear. Missing/duplicate answers remain disputed; unknown answer
IDs invalidate the labeller's batch. Failed/off-schema results are not votes.
The orchestrator settles disputes; a human confirms every label before writing.
The helper itself writes no labels.

## Failure handling

Freeze raises attributed `WorkflowError` for broken contracts/infrastructure.
Prove fails closed with JSON-safe evidence, cleaning its scratch trees; no automatic fix loop
or fallback pass. Do not catch a cap/contract error and continue as if verified. Main owns
changes, CI and follow-up routing under the canonical stage and Git contracts.
