# Task

Prepare the small `1.4.1` release with this precise list of mechanical edits; do not make any other
changes.

1. In `package.json`, change only the package `version` from `1.4.0` to `1.4.1`.
2. In `src/status.ts`, add `Cancelled = "cancelled"` immediately after `Failed` in `JobStatus`.
3. In the existing `describeStatus` switch, add `case JobStatus.Cancelled:` immediately after the
   `Failed` arm and return exactly `"cancelled by user"`.
4. In `src/constants.ts`, change only `DEFAULT_TIMEOUT_MS` from `30_000` to `45_000`; leave
   `MAX_JOB_NAME_LENGTH` unchanged.
5. In `CHANGELOG.md`, append this entry after the existing release, preserving blank lines: `##
   1.4.1`, a blank line, then `- Added cancelled job status and increased the default timeout.`

Acceptance criteria: the version, enum value, switch description, timeout value, unchanged
name-length constant, and exact changelog placement/text all match this plan.
