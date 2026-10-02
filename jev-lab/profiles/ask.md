# Profile: `jev_ask` tool (`ask`)

- **Process:** Agent-authored choice, noul or score questions answered per file or over captured
  output without reading content into context; `agent/extensions/jev-ask.ts`. The audit mode is
  fixed to `enforce` and the tool has no entry in `agent/jev-policies.json`, so it cannot be
  shadowed or turned off like the guards; `JEV_AUDIT=0` disables the audit only
- **Cases:** `bun jev-lab/scripts/export-cases.ts --source policy:ask --out /tmp/jev-cases.jsonl`
  (labels applied; each line carries `sent`, the exact state Jev saw, read from the snapshot).
  The source is listed only after the first record
- **Version:** `policyVersion` (`ASK_POLICY_VERSION`)
- **Judgment:** Whatever the calling agent asked: each record stores its `questions` and `answers`
- **Stages:** Paths are confined to the real workspace root (symlinks resolved), at most 64 files
  and 5,000 scanned entries. A deterministic pre-filter drops VCS, dependency, build, lock,
  binary, empty and oversized files, and sensitive paths (dotenv, key and credential files,
  `.ssh`/`.aws`/`.gnupg`, plus every path the integrity rules' read-context matcher selects, such
  as `integrity-maintainer/`; skip reason `sensitive path`). Every remaining unit asks Jev
  (`jev`, or `jev_error`). A call refused before any request (invalid questions, no input, no
  credential, unavailable rules, combined content too large, no judgeable input) writes a record
  with stage `skipped`, rule `precheck:<reason>` and verdict `error`; audit write failures are
  counted in the result (`details.auditFailures`)
- **Verdict mapping:** `answered` or `error`; the agent decides what to do with the answers
- **Label kind:** `verdict-grade`
- **Labels:** `correct`, `incorrect`, `uncertain`
- **R1 criteria:** The judged state is recoverable: `state.blob` names a snapshot
  (`agent/jev-audit/ask/blobs/<sha256>`, redacted, the exact JSON state sent) that exists. Combined
  and inline rows use the same snapshot; the inline excerpt in the record is clipped to 2,000
  characters, the snapshot is not. A missing snapshot makes the case R1 = 0. `state.sha256` (raw
  file hash) only shows whether the file has changed since; it does not decide R1
- **R2 procedure:** Answer the stored question yourself from the snapshot; grade Jev's answer
  `correct` or `incorrect`. A combined row holds one answer for the whole set: grade that answer
  against the set. An inline row is graded against the inline snapshot. Locate the transcript call
  with `state.toolCallId` and `sessionId`
- **Error costs:** Set by the calling task; report errors in agent decisions that relied on the
  answer first
- **Confirm set:** Every noul between 0.4 and 0.6 and every choice whose winning probability is
  below 0.6
- **Wider evidence:** The calling session transcript (`sessionId`, `agentName`, `state.toolCallId`
  in the raw record under `agent/jev-audit/ask/`), to see how the answer was used. Adoption:
  `bun jev-lab/scripts/ask-adoption.ts [--since YYYY-MM-DD]` counts `jev_ask` calls, writes to
  `xd://jev_ask` and reads of its docs per day from `agent/sessions`; zero records plus zero calls
  means unused, precheck records mean used but refused
- **Baseline and value:** The alternative is `read`ing the files. Per call, compare the estimated
  read cost (sum of `state.chars` / 4 tokens for the judged files) with the returned table
  (about the length of the table plus the question block), and the latency of one `decide` with
  reading. The tool is shipped as valuable only when the replay set and the adoption check show
  both a saving and correct answers; until then it is unproven. A `fallbackRead` signal (a `read`
  of the same path in the next calls) is derived from the transcript at evaluation time, not
  recorded by the tool
- **Result 2026-10-02 (`ask-2026-10-02.1`):** `ask-adoption.ts --since 2026-09-01` found 0
  `jev_ask` calls and 0 audit records, so there is no history to replay or label; value is
  unproven. Next measurement: re-run the adoption check after a week on `.1`, and build the
  replay set only once there are calls whose questions show what agents actually ask
- **Change bar:** None for thresholds (there are none); changes are to the tool's guidance on
  writing questions, which lives in the tool description

## Question defects

Because agents write these questions, many errors are question defects rather than Jev errors.
Grade each stored question against `skill://evaluate-jev/references/jev-design.md` ("Design each
question"): one narrow judgment per question, complete meaning in the instructions (question IDs
are not sent to the model), criteria that define every answer, the right primitive for the
meaning, and a no-match outcome when nothing may fit. The tool prints non-blocking `Question
notes` for a choice without a no-match option and a noul without criteria. Report recurring
defects as guidance changes for `jev_ask` callers.
