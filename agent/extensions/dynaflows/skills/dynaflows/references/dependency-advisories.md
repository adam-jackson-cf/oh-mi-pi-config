# Dependency advisories

## Helpers

```python
found = dependency_audit_findings(repo)  # {ok, advisories, failures}
result = await triage_advisories(
    found, evidence
)  # {halt, owner, fix, failures, report}
```

Use this when a commit is blocked and the gate output holds `SECURITY.dependency-audit` lines.
Every advisory blocks. The repo's checker gives no severity; the harness assesses it.

1. `dependency_audit_findings` runs the record's `checks.commit` in the worktree and parses each
   line strictly by the catalogue row's `advisory_line` and `failure_line`. A line that starts
   with the rule ID but does not match, or output the rails truncated, raises: a finding is never
   skipped. Each advisory is keyed `<lock>:<package>@<version>:<advisory>`.
2. Main delegates evidence gathering, one bounded read-only task per advisory. Each advisory's
   evidence holds exactly these fields:
   - `advisory_text`, `advisory_source`: the published advisory text and its URL (OSV or GHSA).
   - `scope` and `scope_source`: `runtime` or `dev` (dev or test only), with the manifest line.
   - `code_search`: `queries` for the vulnerable function, option or command, and every hit in
     owned code as `{location, line}`.
   - `fix_options`: a replacement package or mitigation. It is required when the finding lists
     no fix version.
   - `blocked_fix`: `null` when no pinned dependent blocks the fix, otherwise exactly
     `{blocked_by, constraint, constraint_source}` with the catalogue exception-field meanings.
     Gather the exact blocker version from the same lock, its declared constraint and the upstream
     HTTPS evidence URL; verify that the constraint excludes every reported fixed version.
     A fix that merely needs an upgrade is not blocked. This evidence routes the finding; it
     neither suppresses it nor authorizes an agent to write an exception.
3. A malicious package is decided in code first: an advisory whose ID or an alias starts with
   `MAL-` (an OSV malicious-package record, or the auditor's own malicious mark as the catalogue
   contract states) is critical and goes to the owner, whatever its scope, reachability or fix.
   No judgment is asked. Its suggested fix is to remove or replace the package and rotate every
   credential the environments that installed it exposed.
4. For every other advisory, `triage_advisories` asks the frozen questions in
   [dependency-severity](../../../judgments/dependency-severity/questions.json) through `judge()`. Code
   applies the fixed rubric:
   - impact class sets the base level: `rce`, `auth_bypass` and `unclear` are critical;
     `data_exposure` and `integrity_loss` are high; `dos` and `info_leak` are medium;
   - one level lower when a precondition (local only, privileges, user interaction) clears its
     threshold;
   - one lower for a dev-only dependency;
   - one lower when owned code clearly does not reach the vulnerable feature;
   - never below low.
   The thresholds live in that directory's `thresholds.json`. Unclear evidence rounds up: the
   impact is the most severe class above the round-up floor, and a mitigation counts only past
   its threshold. A failed judgment, invalid answers, stale thresholds or missing evidence raise;
   they never pass.
5. Act on the result:
   - `halt` true: stop the workflow and give the owner `report`. It names the advisory, severity,
     per-dimension evidence and suggested fix of every malicious package, every high or critical
     advisory, every advisory with no fixed release or with fixed releases blocked by a pinned
     dependent (blocked until the owner approves a dated exception), and every exception-file
     failure. The report includes the blocker, exact constraint and source URL. Do not upgrade,
     replace or mitigate these in the flow.
   - Otherwise upgrade each `fix` entry to a listed fixed version, and fix `failures` (an
     unknown lock format, a manifest without a lock, an auditor error) as any failing check.
     Then commit again.

Agents never add or edit exceptions. The exception file is in the record's `ownerOnlyPaths`, and
the rails refuse any agent commit that changes it.

The catalogue's `SECURITY.dependency-audit.exceptions` is the exception-file contract.
No-fix entries retain `advisory`, `package`, `version`, `lock`, `reason`, `exposure`, `expires`.
Fixed-but-blocked entries additionally require `blocked_by` (`package@exact-version`),
`constraint` (the exact upstream declaration, including blocker and requirement), and
`constraint_source` (an absolute HTTPS URL). Repo checkers deterministically verify the blocker
in the same lock and parse the constraint to exclude every reported fixed release; they never
fetch the URL. The owner attests the upstream evidence when writing the entry. Expired,
malformed, stale, unmatched and unblocked-fix entries still fail. Nothing changes the owner-only
path or the exact advisory/package/version/lock match.
