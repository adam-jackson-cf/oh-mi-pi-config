# Profile: `jev_ask` tool (`ask`)

- **Process:** Agent-authored choice, noul or score questions answered per file or over captured
  output without reading content into context; `agent/extensions/jev-ask.ts`; always on
- **Cases:** `bun jev-lab/scripts/export-cases.ts --source policy:ask --out /tmp/jev-cases.jsonl`
  (labels applied)
- **Version:** `policyVersion` (`ASK_POLICY_VERSION`)
- **Judgment:** Whatever the calling agent asked: each record stores its `questions` and `answers`
- **Stages:** A deterministic pre-filter drops VCS, dependency, build, lock, binary, empty and
  oversized files; every remaining unit asks Jev (`jev`, or `jev_error`)
- **Verdict mapping:** `answered` or `error`; the agent decides what to do with the answers
- **Label kind:** `verdict-grade`
- **Labels:** `correct`, `incorrect`, `uncertain`
- **R1 criteria:** The file's current content still matches the recorded `sha256` (per-file units
  store path, length and hash, not content), or the clipped inline excerpt holds what the question
  needs. A hash mismatch makes the case R1 = 0
- **R2 procedure:** Answer the stored question yourself from the reconstructed content; grade Jev's
  answer `correct` or `incorrect`
- **Error costs:** Set by the calling task; report errors in agent decisions that relied on the
  answer first
- **Confirm set:** Every noul between 0.4 and 0.6 and every choice whose winning probability is
  below 0.6
- **Wider evidence:** The calling session transcript (`sessionId`, `agentName` in the raw record
  under `agent/jev-audit/ask/`), to see how the answer was used
- **Change bar:** None for thresholds (there are none); changes are to the tool's guidance on
  writing questions

## Question defects

Because agents write these questions, many errors are question defects rather than Jev errors.
Grade each stored question against `skill://evaluate-jev/references/jev-design.md` ("Design each
question"): one narrow judgment per question, complete meaning in the instructions (question IDs
are not sent to the model), criteria that define every answer, the right primitive for the
meaning, and a no-match outcome when nothing may fit. Report recurring defects as guidance changes
for `jev_ask` callers.
