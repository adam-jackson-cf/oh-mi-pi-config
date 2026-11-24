# Prove contract

## Signature

```python
prove(repo, parent, head, frozen, objective, *, target_ref, max_checks=MAX_PROOFS)
```

`frozen` is `freeze()`'s result, the `(manifest_path, manifest_sha256)` pair.

Call it as [Verify](step-5-verify-workflow.md) states. It requires
`parent == git merge-base(target_ref, head)` with strict ancestry; it cannot authenticate GitHub target metadata.

Prove checks four things:

1. CI green on head: the record's `ci.requiredContext` (a commit status or check run, whoever
   posts it) must be success on the exact head before any proof runs.
2. Rename residue: after CI is green and before proofs, harness `guardrails rename-residue
   --repo <worktree> --record <record dir> --base <parent> --head <head>` rejects leftover
   retired names in renamed folders or files. Every finding blocks, even with no frozen proofs.
   Each of these checkers logs its rule results to the decision store (source `check`); a store
   error is listed in `decisionLogErrors` and never changes a verdict.
   The same merge-base/head range runs naming, structure and path-check for added/renamed
   paths and new segments only. Legacy paths and content-only edits are never findings.
   `guardrails responsibility-overlap` compares every folder with an added, modified or renamed
   `.py` file against the rest of the head tree and blocks on repeated code, and
   `guardrails prefix-folder` blocks sibling files that share a name prefix for one concept.
   These PR pre-checks are the principles 5/17 exception in
   [AGENTS.md](../../../AGENTS.md#exceptions).
   A term still naming an owned directory or module stem at head is not retired (a concept split
   can preserve its old owner); this compares whole word sequences, not individual words.
3. Map coverage: every feature-map doc under `verification_surface.feature_map_path` changed in
   `parent..head` (`git diff --name-only`, deletions and the profile's map index files excluded)
   must link at least one proof file in the manifest. Otherwise prove blocks and names the docs.
   A record with no feature map skips this check.
4. The frozen proofs: manifest and file hashes match, the head bytes are unchanged, and every
   proof passes at head (twice; `journey: true` proofs 3 times, all of which must pass).

With an empty manifest and no changed map doc prove still checks CI, rename residue and
new-path naming/structure. It runs no repository suites, whole-repo naming/structure scans
or `checks.profile`: rails ran the other harness checks at commit and CI ran repo gates.
Red at base was shown at freeze; prove does not rerun base. Partial proofs,
tracked mutation and changed frozen head bytes fail. Launch, timeout, IO, setup and cleanup
errors are attributed infrastructure failures (`ok: null`, error/output/location), not failed
assertions. Cleanup failure prevents pass.

Output includes `status`, `deterministic`, `blocking`, `head`, `objective`, `manifestSha256`
and the loaded `manifest`. Prove writes that result to
`<git-common-dir>/omp-prove/<head>.result.json` and posts it as a PR comment on the one open PR
at head (`prComment` holds the comment URL). Long proof outputs are tail-truncated in the
comment only; the file keeps them whole. A pass whose result cannot be written or posted
becomes `fail` with blocking `infrastructure: publishing prove result: ...`. The result file
with `status: pass` for the exact head is the one pass signal, and rails accept it only from a `prove()` call
they watched in the merging omp process.
