# Freeze contract

## Signature

```python
freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None,
       previous=None, revise=None, max_checks=MAX_PROOFS, project_setup=False)
```

`proofs` are draft records without `sha256`; the return is `(Path, manifest_sha256)`.
An empty list is valid for a PR with no behaviour change; a changed feature-map doc still needs a linked proof.
Store the manifest at `<git-common-dir>/omp-manifests/<slug>.json`, outside tracked
repository files. Freeze rejects every other destination;
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
  from the working tree with `guardrails`' `journeys.mapped_paths`/`resolve_link`.
  An unlinked file is refused with its proof ID. A profile with no `feature_map_path` (absent,
  exempt or placeholder) refuses every proof and points to
  [Project setup](project-setup.md); a declared path that does not exist is refused.
  Zero-proof manifests need no map.
- One rule for every proof: **red at base, green at head**. Freeze overlays each
  proof's files on its pinned parent and requires a failing run there. When the
  record's `profile.md` front matter declares `verification_surface.doctor_marker`,
  red output containing that marker is refused. An absent profile or key skips that check.
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

## Re-freeze for discoveries

Re-freeze a [discovery](proofs.md#map-linked-proofs-and-discoveries) with `previous=(path, digest)`,
passing every earlier proof plus the new ones. Added proof IDs need no `revise`; only they and
revised proofs run red at base. Freeze refuses any other dirty file, so the writer commits or
reverts in-progress product edits before stopping.

## Recovery

- `prove()`'s result file (and its PR comment, when it fits) carries the full manifest so a lost
  manifest can be restored with its recorded digest.
- Re-run `freeze()` in a clean worktree with the same base, setup and proof bytes
  to reproduce a byte-identical manifest digest.
- A writer finding a frozen proof unfixable (for example, formatting) STOPs.
  Main authorises a proof-only change, then re-freezes with `previous=(path, digest)`
  and `revise={id: reason}` before implementation resumes.
