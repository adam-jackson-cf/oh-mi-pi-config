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

## External bot PRs

Missing author ledgers may identify Dependabot only as `external`: GitHub must
report the Dependabot app as PR author, the PR head must match the checked-out
SHA on a `dependabot/` branch, and every commit in a nonempty `parent..head` must have
`dependabot[bot]` as GitHub author with verified signature. Any unavailable or
failed check leaves authorship unknown. Existing ledgers always take precedence.

Main checks out the Dependabot branch in a dedicated worktree, freezes a
`preserve` proof running the full repository gate with `setup`, and runs
`prove(..., target_ref=<fetched default>)`, followed by normal CI and rebase-merge.
Never re-apply the bump as an agent PR. Agent fixups pushed to a Dependabot branch
are written through rails, creating a ledger and restoring normal author rules.

## Proof manifest

```python
freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None,
       previous=None, revise=None, max_checks=MAX_PROOFS)
```

`proofs` are draft records without `sha256`; the return is `(Path, manifest_sha256)`.
Store the manifest at `<git-common-dir>/omp-manifests/<slug>.json`, outside tracked
repository files. Freeze rejects destinations under resolved `$TMPDIR` or `/tmp`;
`previous=` may still read a manifest from a temporary location.
`MAX_PROOFS = 16` is independent of `MAX_AGENTS = 16`. Both freeze and prove accept
a positive integer `max_checks` (not bool), applied to current/previous manifests.
The entire draft and top-level inputs are validated before hashing or execution.
When `.omp/gate.json` declares `checks.format`, freeze runs that formatter in the
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
      "kind": "defect",
      "cmd": ["pnpm", "vitest", "run", "src/auth/redirect.test.ts"],
      "files": ["src/auth/redirect.test.ts"],
      "expectRed": "expected '/home' to be '/login'",
      "parent": "<40-hex pinned by freeze>",
      "sha256": {"src/auth/redirect.test.ts": "<sha256 written by freeze>"}
    }
  ]
}
```

- IDs are unique; criteria and argv are nonblank. `expectRed` is only for `defect`.
- `files` use canonical POSIX repository-relative paths. Reject empty/dot/parent
  components, repeated separators, backslashes/drive forms, leading/trailing
  slash, NUL and `.git` components rather than normalising them. Symlinks, ignored
  files and clean-filter/EOL byte changes are rejected at actual roots; lexical
  validation does not depend on host temporary-directory contents.
- Only listed files may be dirty. Freeze whole files, including shared fixtures.
  Each proof is overlaid alone, never with another proof's files.
- Inspect each argv token literally, including the value after its first `=`;
  strip one leading `./` for comparison. Every token naming an existing regular
  repository file, even an unchanged tracked runner, must be in that proof's
  `files`. Absolute references into source/execution trees are rejected; external
  absolute executables such as `sys.executable` remain allowed. There is no shell,
  language, pytest-node-ID or selector parsing.
- `defect`: baseline overlay completes nonzero containing `expectRed`; a
  truncated-file negative control must not contain it. Use asserted actual versus
  expected output, not a test name or generic error. The text must be absent in
  both green head outputs. `preserve`: baseline passes. `new-surface`: baseline
  does not pass; this is not proof of API absence. Infrastructure failure is
  never valid red evidence.
  A proof whose files are only documentation is green at the baseline by construction
  (freeze overlays the new text), so declare it `preserve`; it checks head wording only.
- Each detached scratch tree starts fresh at its pinned commit and runs setup
  once, on first use. After checking that setup did not change tracked bytes,
  record its untracked and ignored entries (including collapsed directories).
  Before and after every execution, reset tracked files and remove untracked
  and ignored entries absent from that baseline. This includes both head runs,
  baseline, negative control and earlier-commit runs. Setup outputs survive;
  residue within baseline directories (e.g. `node_modules/.cache` or `target/`)
  intentionally persists. Setup or baseline-recording failure is infrastructure
  failure. Never clean the source checkout. Commands must defeat result caches,
  e.g. `go test -count=1`.
- Optional `journey: true` marks a scripted journey proof. Freeze runs it 3 times
  at head and requires identical pass/fail each time; otherwise it refuses with
  `journey proof not stable over 3 clean runs`.
- When `.omp/test-profile.md` front-matter declares
  `verification_surface.doctor_marker`, freeze refuses an `expectRed` proof whose
  red output contains that marker: the red came from the environment doctor, not
  the behaviour. An absent profile or key skips this check.
- Each proof's parent is always pinned, defaulting to manifest base. A regression
  added during review must explicitly name the defective implementation commit
  as its parent. Prove requires strict parent/head ancestry and parents at/after
  base. A passing defect/new-surface baseline returns to Plan: inspect the
  objective/proof and chosen parent; report already-satisfied work or use a valid
  preserve proof where appropriate. Never weaken a proof to manufacture red.
- `base=None` means HEAD for a new lineage, but inherits the previous base during
  re-freeze; omitted setup inherits previous setup. With `previous=(path, digest)`,
  slug, resolved base and setup are immutable. A new base/setup needs a new
  lineage. Unchanged records retain evidence without rerunning. Changed/removed
  records require nonblank `revise={id: reason}` and record
  `{id, oldSha256, reason}`; the digest covers the entire old record.

### Recovery

- Main's verification comment embeds the full manifest in `<details>` so a lost
  manifest can be restored with its recorded digest.
- Re-run `freeze()` in a clean worktree with the same base, setup and proof bytes
  to reproduce a byte-identical manifest digest.
- A writer finding a frozen proof unfixable (for example, formatting) STOPs.
  Main authorises a proof-only change, then re-freezes with `previous=(path, digest)`
  and `revise={id: reason}` before implementation resumes.

## prove

```python
prove(repo, parent, head, manifest_path, manifest_sha256, objective, *,
      target_ref, max_checks=MAX_PROOFS, verifier_model=None, objective_revision=None)
```

Main calls this in Python eval. `repo` is the dedicated source worktree on a named
branch at `head`, even when main's session is in the primary checkout. Resolve the
actual fetched PR target and require `parent == git merge-base(target_ref, head)`
with strict ancestry, not `HEAD~1`. The helper checks consistency; git-tool owns
confirming actual GitHub target metadata. Repository-specific target exceptions
live in that repository's project context (`.omp/AGENTS.md`).

Completed attempts append atomically to
`<git-common-dir>/omp-prove/attempts/<head>.json` as
`{objective, manifestSha256, status}` records. If the latest attempt for that head
failed and the objective changes, pass a nonblank `objective_revision=<reason>`.
Otherwise supplying it is an input error. An accepted revision appears in the
result as `objectiveRevision: {previous, reason}` and in the verifier brief.
Never put waivers such as "not a finding" in an objective; sequence dependencies
so their decisions land before proving dependent work.

Head gate schema is validated before scratch trees/setup. Missing prerequisites
route to SKILL's owner-approved project setup; an invalid parent gate is a visible
`gateDiagnostics` entry and does not prevent a valid head repair. When the gate
declares `checks.profile`, prove runs it first and blocks on a nonzero exit. Prove
runs no other repository suites; hook/CI eligibility is main's responsibility
under git-tool.

Drift precheck: prove collects paths deleted or renamed in
`git diff --name-status -M parent..head` and public module-level symbols a file's
diff removes without re-declaring them in that same file (Python `def`/`class`,
TS/JS `export` declarations and lists, Go exported `func`/`type`/`const`/`var`, Rust
`pub` items). It runs `git grep -n -F` for each at head over tracked docs (Markdown,
MDX, reStructuredText) and code comments, including files the change also edits;
only change-record/ADR directories are skipped. Each hit becomes a `brief_checks` item the
verifier must address. Hits do not block prove by themselves; the verifier brief
has no separate stale-docs hunt.

Prove validates manifest/file hashes, executes every head proof twice, repeats
isolated baseline checks by kind and checks earlier commits touching proof files
once each with that commit's own bytes. Earlier proofs must already be green;
committing a red proof before its greening fix is forbidden. Partial proofs,
tracked mutation and changed frozen head bytes fail. Every execution uses the
setup-preserving lifecycle above. Launch, timeout, IO, setup and cleanup errors are
attributed infrastructure failures (`ok: null`, error/output/location), not failed
assertions. Previously collected evidence is retained; deterministic failure
prevents verifier dispatch. Cleanup failure prevents pass.

### Gate changes

`gateChanges` unions parent/head references: `.omp/gate.json`, workflows, hooks,
lefthook/pre-commit roots and gate argv file tokens. For each directly referenced
regular text file, one generic `re.findall(r"[\w./-]+", text)` scan discovers
existing canonical in-root regular file tokens. It does not recurse or parse a
language; computed/quoted paths or paths containing spaces inside text are not a
complete dependency graph. Decode/IO problems are diagnostics. No outside-root
file is read. The writer lists discovered paths/effects in optional **Gate changes**
per `skill://git-tool/references/pr-description.md`, updating omissions found by
prove. Discovery alone does not block; unauthorized weakening may.

### Verifier and authorship

Prove creates the sole `agent='verifier'` dispatch with objective, labelled source,
fetched target, full parent/head range, pinned per-proof trees, manifest, recorded
authors, deterministic evidence and `BLOCKING_RULE` from `templates/workflows.py`.
Apply that constant, not a second prose blocker list. Each run's brief output is
limited to its last 4096 characters, prefixed with an explicit truncation/full-evidence-path
marker when needed; full output remains in the supplied `deterministic.json` during verification.
The read-only verifier drives criteria live, with exact command/observed/required
output; no edits or agent spawns. Criteria entries cover every manifest proof ID;
the yield schema restricts `proofId` to those IDs and unknown IDs still block as a
backstop. Put other observations in `blocking` or `followUps`. Multiple entries
for a known ID are allowed, and any fail blocks.

Rails records repository-file edit-tool editors and committers, excluding local
workflow/proposal metadata. It does not discover arbitrary bash-written authors.
Supported tool-runner launchers are `mise exec`/`mise x` (with `--` before the
command), `pnpm exec`/`pnpm dlx`, `npx`, `bunx`, `uv run` and `poetry run`.
`uv run` and `poetry run` honour `--` as the end of launcher options, including
unknown options and their values. Without that separator, ambiguous launcher
options with a later `git`/`gh` word block rather than silently skipping checks;
put the Git command first after options or use `--`. Rails recursively scans
`$(...)` and backtick command substitutions, including inside double quotes,
without evaluating their output. Alias and shell function definitions whose bodies
contain a `git` or `gh` word fail closed: persistent-shell invocations cannot be
resolved safely. Definitions without those words remain allowed.
Provenance persists per real worktree/full branch ref, not session lineage:
`<git-common-dir>/omp-authors/<sha256(worktree + "\0" + branch)>.json`, UTF-8 input,
with `{schemaVersion:1, worktree, branch, authors:[{family, model, agent}]}`.
`worktree` is realpath; `branch` is `refs/heads/...`; authors are sorted/deduped.
Rails owns atomic writes and model/family resolution. Editing/committing sessions
must run this version of agent-rails to record provenance; prove does not create
or backfill a ledger from commit metadata. Detached spikes and other branches do
not authorize this change. Prove reads the ledger into result/brief. A missing
ledger fails closed unless the [External bot PRs](#external-bot-prs) checks pass.
Empty, malformed, unsorted or unreadable ledgers always fail closed as authorship
unknown; do not fabricate one to unblock verification.

Immediately before dispatch prove atomically writes
`<git-common-dir>/omp-prove/verifier-context.json` with
`{schemaVersion:1, worktree, branch, head, parent}`, then removes it in `finally`.
Rails enforces verifier family independence for every prove dispatch, regardless
of the dynaflows switch. It uses that source ledger and checks current branch/head;
malformed or stale context blocks with a rerun-prove explanation. For missing ledgers, `parent` pins
the external bot commit range. Without context a standalone verifier uses its
spawning session's scope and the PR base merge-base for that range. There is no
lock or pid protocol.

Default `verifier_model=None` preserves configured routing: the `verifier` agent
takes its model from the `verifier` entry in `modelRoles`, and prove omits a model
keyword. When recorded authors share that role's family, pass an eligible
cross-family model from `modelRoles` as `verifier_model`; this is
still a verifier, never a task. Rails requires a different family from every
recorded author. Unavailable models or no remaining approved family block with
the actual reason; do not substitute models or change routing config.

Prove compares source status/HEAD before/after: a difference means
`source checkout changed during verification`, not proof the verifier caused it
or a sandbox guarantee. Rerun after concurrent changes. Output includes `status`,
`deterministic`, `verifier`, `authors`, `blocking`, `followUps`, `verifierModel`
(the requested override or null), and resolved source `worktree`/`branch` once
authorship validation succeeds.
Only a final pass including cleanup atomically writes the existing schema-1
exact-head marker. Prove then posts the `agent-verified` commit status
(`state=success`, description `prove() pass`) on head to the origin repository
through `gh api repos/OWNER/REPO/statuses/<head>`. A failed post removes the
marker and returns `fail` with blocking `infrastructure: posting agent-verified
status: ...`. Resolve/revoke the head's old marker before validating other
inputs. Marker format/merge consumption is internal, not an orchestration API.
See `skill://git-tool/references/verify-loop.md` for every-new-SHA policy and fixes.

## Documentation-only exemption

```python
exempt_docs_only(repo, parent, head, *, target_ref)
```

Uses the same whole-PR merge-base/strict-ancestry check, head-first revocation and
atomic pass marker. Returns its Path; rejects empty diffs or the first ineligible
path with `WorkflowError`. This is a conservative path eligibility rule, not
inference of runtime use. Casefold all comparisons. Allow only root `README.md`,
`CHANGELOG.md`, `CONTRIBUTING.md`, `LICENSE`, `LICENSE.md`, `LICENSE.txt`; below
`docs/`, allow `.md`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.svg`, `.gif`.
Exclude any segment `skills`, `agents`, `rules`, `prompts`, `commands`, `.agents`,
`.claude`, `.omp`, `.github`; exclude basenames `SKILL.md`, `CLAUDE.md`, `CODEX.md`,
`GEMINI.md`, `AGENTS.md`, `AGENTS.override.md` and names ending `instructions.md`.
Check additions, deletions and both rename sides. Main must choose prove for known
runtime-loaded content even in an otherwise eligible location. This exempts the
verifier, never required CI; each new head needs fresh exemption evidence.
It posts no status: after exact-head CI is green, main posts `agent-verified` with
`gh api repos/OWNER/REPO/statuses/<head> -f state=success -f context=agent-verified`.

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
Prove fails closed with JSON-safe evidence and rail/spawn errors, preserving
follow-ups and cleaning trees/context; no automatic fix loop or fallback pass.
Do not catch a cap/contract error and continue as if verified. Main owns changes,
CI and follow-up routing under the canonical stage and Git contracts.
