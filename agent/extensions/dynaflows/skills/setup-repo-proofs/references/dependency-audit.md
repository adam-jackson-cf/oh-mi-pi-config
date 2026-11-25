# Dependency audit

`SECURITY.dependency-audit` in [the catalogue](../assets/guardrail-rules.json) is the contract. The repo always owns the audit
(`keep` or `grow`); a duplicate audit is forbidden. A repo with no audit gets one.

## Scope and triggers

- Scope: every lock file and dependency manifest from `git ls-files --cached --others --exclude-standard`, in every ecosystem.
- An unknown lock format is a failure; a manifest with dependencies and no lock is a failure.
- Exclusions name fixture, vendored or frozen digest-pinned record paths (design principle 10) one by one with origin
  evidence; exclusion by glob, ecosystem or package is forbidden.
- It runs over the whole repo at every commit and every CI run. A staged-file trigger is forbidden; a scheduled-only run is a gap.
- Every advisory blocks. The checker never scores severity, filters by it, or waives advisories outside the exception file.

## Output grammar

One finding per line, rule ID first, fields separated by one space in this order, values nonempty and without whitespace
(except `reason`), list values comma-separated with no spaces, an empty list as an empty value.
An advisory line is one physical line; it is wrapped here only for width:

```text
SECURITY.dependency-audit lock=<lock-path> ecosystem=<osv-ecosystem> package=<name>
  version=<exact-locked-version> advisory=<advisory-id> aliases=<id>,<id> fix=<version>,<version>
SECURITY.dependency-audit failure=<kind> path=<repo-path> reason=<text-to-end-of-line>
```

Failure kinds: `unknown-lock-format`, `manifest-without-lock`, `exception`, `auditor`. Untrusted or unparsable auditor output is a failure.
A malicious package is an advisory whose ID or an alias starts with `MAL-` (an OSV malicious-package record); an advisory the auditor
marks malicious without one lists the alias `MAL-<advisory-id>`.

## Exceptions

The exception file is a repo JSON file protected by owner review (for example CODEOWNERS).
Agents never add or edit entries.

- Allowed only for an advisory with no fix version, or a fixed release blocked by a pinned dependent.
- No-fix entries hold exactly `advisory`, `package`, `version`, `lock`, `reason`, `exposure`, `expires` (`YYYY-MM-DD`; expired when
  before today's UTC date). Blocked-fix entries add `blocked_by` (`package@exact-version`, split at the last `@`), `constraint`
  (the exact upstream declaration, for example `semgrep==1.185.0 requires opentelemetry-api~=1.37.0`) and `constraint_source`
  (an absolute HTTPS URL).
- The checker verifies the blocker is present at that exact version in the same lock and that the constraint excludes every reported
  fixed version; it never fetches the URL. The owner attests the upstream evidence when writing the entry.
- An entry suppresses only its exact advisory, package, version and lock. Expired, malformed, stale, unmatched and unblocked-fix
  entries fail, and an expired entry reports the advisory again.

## Responding to findings

1. Parse each line strictly by the grammar; a line that starts with the rule ID but does not match is a failure, never skipped.
   Key each advisory `<lock>:<package>@<version>:<advisory>`.
2. Gather evidence per advisory: the published advisory text and URL (OSV or GHSA), `runtime` or `dev` scope with the manifest line,
   code-search hits for the vulnerable function or option in owned code, fix options (a replacement package or mitigation, required
   when no fix version is listed), and any pinned dependent that blocks the fix (exact version, declared constraint, upstream HTTPS
   source). A fix that merely needs an upgrade is not blocked. Evidence routes a finding; it never suppresses it.
3. A malicious package is critical and goes to the owner regardless of scope, reachability or fix: remove or replace it and rotate
   every credential the environments that installed it exposed.
4. Rate every other advisory with this rubric:
   - impact class sets the base: remote code execution, authentication bypass and unclear impact are critical; data exposure and
     integrity loss are high; denial of service and information leak are medium;
   - one level lower when a precondition (local only, privileges, user interaction) clearly applies;
   - one level lower for a dev-only dependency;
   - one level lower when owned code clearly does not reach the vulnerable feature;
   - never below low; unclear evidence rounds up.
5. Stop and report to the owner every malicious package, every high or critical advisory, every advisory with no fixed release or with a
   fix blocked by a pinned dependent, and every exception-file failure, naming the advisory, severity, evidence and suggested fix.
   Do not upgrade, replace or mitigate those in the flow; a blocked fix waits for an owner-approved dated exception.
   Otherwise upgrade each package to a listed fixed version, fix failures (unknown lock format, missing lock, auditor error), and rerun.
