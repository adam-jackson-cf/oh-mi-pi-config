# Proofs and merge

What `freeze()`, `prove()` and `structure_review()` require, and how a merge uses their results.
Load them in an eval cell: `%load <extension>/engine/workflows.py`. Policy comes from the repo's
[config](../docs/config.md), `.agentrails/config.json` in its main checkout, never from the repo tree.

## Binding test policy

- The config's `tests` section holds the binding test settings; the [Tests](../docs/config.md#tests) section
  defines its keys. Agents never write the config.
- A proof is an outcome test or a deterministic lever journey, linked from a feature-map document
  under `tests.verification_surface.feature_map_path`. Live paid or LLM calls and T3 exploration are
  never proofs. An exempt surface (`{"none": true}`) declares no feature map, so it freezes no proofs.
- Every proof fails at base and passes at head. Assert a broken or newly requested outcome, never an
  infrastructure failure, and never weaken a proof to make it red.

## Freeze

```python
freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None, previous=None, revise=None,
       max_checks=MAX_PROOFS)  # -> (manifest path, manifest sha256)
```

- `manifest_path` must be `<git-common-dir>/omp-manifests/<slug>.json`. `MAX_PROOFS` is 16.
- Each draft proof is `{id, criterion, cmd, files, parent?, journey?}`; freeze writes `sha256`.
  An empty list is valid for a PR with no behaviour change.
- Every proof file must be linked (a relative Markdown link) from a feature-map document. With no
  `feature_map_path` every proof is refused; zero-proof manifests need no map.
- With `commands.format` declared, freeze runs it first and lists the files it changed; hashes cover
  the formatted bytes.
- Red at base: each proof's files are overlaid alone on its pinned parent, and the run must fail.
  Output holding `tests.verification_surface.doctor_marker` is refused as red evidence.
- `files` are canonical POSIX repo-relative paths. Every argv token naming a repo file must be in
  that proof's `files`. Only listed files and feature-map documents may be dirty.
- Scratch trees start fresh at the pinned commit and run `setup` once. Commands must defeat result
  caches, for example `go test -count=1`.
- `journey: true` makes prove run that proof 3 times instead of twice; every clean run must pass.
- A parent defaults to the manifest base. A regression found later names the defective commit.

### Re-freeze

- A discovery (user-facing behaviour the plan did not name) gets a map entry and an outcome test,
  then a re-freeze with `previous=(path, digest)` and every earlier proof. New IDs need no `revise`.
- A changed or removed proof needs `revise={id: reason}`. Slug, base and setup never change
  within one lineage. Implementation never edits frozen bytes.
- Before the PR opens, every user-facing behaviour the branch adds or changes has a map entry and an
  outcome test.

## Prove

```python
proof = prove(
    repo, parent, head, (manifest_path, digest), objective, target_ref=target_ref
)
```

`parent` must be `git merge-base <target_ref> <head>`, a strict ancestor of head. `repo` is the
worktree on its branch at head. Prove checks, in order:

1. CI: with `ci.enabled`, `ci.requiredContext` is `success` on the exact head.
2. New-path checks over `parent..head`: `rename-residue`, `path-check`, `structure`,
   `responsibility-overlap` and `prefix-folder`. Every finding blocks, even with no proofs.
3. Map coverage: every feature-map document changed in the range links a frozen proof.
4. Frozen proofs: hashes match, head bytes are unchanged, and each proof passes twice at head
   (`journey: true`: 3 times).

Launch, timeout, IO, setup and cleanup errors are infrastructure failures (`ok: null`), not red
assertions; they never count as a pass. The result goes to
`<git-common-dir>/omp-prove/<head>.result.json` and is posted on the PR at head. It carries the
full manifest, so a lost manifest can be restored.

## Structure review

```python
structure = await structure_review(repo, parent, head, target_ref=target_ref)
```

- Run it after prove, on the same range. It judges one proposed file or name pair per question,
  from `judgments/structure-naming`: `same_thing`, `technical_kind_grouping` and
  `consumer_placement`. Proposers read Git objects only and never run repo code.
- Each question runs at its effective mode ([judgments](judgments.md#effective-mode)). An enforced
  finding sets `halt: true` and `status: blocked`; a shadow finding never blocks.
- An infrastructure error leaves `status: incomplete`, which blocks the merge. Fix the cause.
- An exception with rule `structure-naming/<question>` drops that question's proposals for its path.
- The result goes to `<git-common-dir>/omp-structure/<head>.json` and one PR comment.

## Exact-head evidence

- Call `prove()` and `structure_review()` each in its own eval cell, in the omp process that will
  merge. The rails trust a result file only when they watched the cell that wrote it.
- A result is bound to its head. A push, rebase or merge of the target needs both on the new head.
- Never write or edit a result file by hand.

## Merge

The first `mergePolicies` entry matching the PR's base and head prefix gives the method and
`proof`; no match means `{method: "rebase", proof: true}`.

| `proof` | `gates.prove` | `gates.structureReview` | The merge needs |
| --- | --- | --- | --- |
| false | any | any | CI only |
| true | true | true | A prove pass, then structure `halt: false` and no errors |
| true | true | false | A prove pass |
| true | false | true | A structure review with no enforced finding |
| true | false | false | CI only |

CI follows `ci`: with `ci.enabled` the required context must be green on the exact head; with it
off no CI status is needed. No policy waives CI, and `--admin` is always refused.
`skill://git-tool` owns the merge mechanics and recovery.
