-- The dynaflows decision store: every Jev judgment answer, deterministic check finding and rail
-- decision, for analysis queries and shadow review. One file, <agent dir>/dynaflows/dynaflows.db.
-- This file is the only schema source: engine/guardrails/foundation/decision_store.py (sqlite3) and
-- lib/decision-store.ts (bun:sqlite) both apply it. A database whose user_version is neither 0 (new)
-- nor the version set at the end is refused. Every statement is idempotent, so concurrent first
-- opens are safe under the immediate transaction.
BEGIN IMMEDIATE;

-- One row per question version a judgment registry load saw: the taxonomy and decision in force.
CREATE TABLE IF NOT EXISTS questions (
  judgment TEXT NOT NULL,
  question TEXT NOT NULL,
  question_version TEXT NOT NULL,
  approach TEXT NOT NULL CHECK (approach IN ('Gate', 'Route', 'Rerank', 'Select', 'Grade', 'Verify', 'Eval judge')),
  overlays TEXT NOT NULL CHECK (json_valid(overlays) AND json_type(overlays) = 'array'),
  primitive TEXT NOT NULL CHECK (primitive IN ('noul', 'choice', 'score')),
  evidence_unit TEXT NOT NULL CHECK (evidence_unit IN ('item', 'pair', 'placement')),
  combination TEXT NOT NULL CHECK (combination IN ('jev_only', 'proposer_product', 'chain_all', 'any', 'max_per_unit')),
  action TEXT NOT NULL CHECK (action IN ('block', 'confirm', 'review_queue', 'log', 'route')),
  policy_area TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('enforced', 'shadow')),
  decision TEXT NOT NULL CHECK (json_valid(decision)),
  precision_target REAL,
  override TEXT CHECK (override IS NULL OR json_valid(override)),
  shadow_exit TEXT CHECK (shadow_exit IS NULL OR json_valid(shadow_exit)),
  model TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  PRIMARY KEY (judgment, question, question_version)
);

-- One row per question answered (source jev) or per rule result (source check). A check row's
-- judgment is its rule ID and its question the checker that ran it.
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  call_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('jev', 'check')),
  judgment TEXT NOT NULL,
  question TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('enforced', 'shadow')),
  status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'incomplete')),
  error TEXT,
  outcome TEXT CHECK (outcome IN ('flag', 'pass', 'escalate')),
  probability REAL,
  choice_probabilities TEXT CHECK (choice_probabilities IS NULL OR json_valid(choice_probabilities)),
  threshold REAL,
  proposer_score REAL,
  combined_score REAL,
  model TEXT,
  question_version TEXT,
  judgment_version TEXT,
  repo TEXT,
  commit_sha TEXT,
  pr INTEGER,
  trigger TEXT NOT NULL CHECK (trigger IN ('commit', 'prove', 'pr', 'manual')),
  item_kind TEXT,
  item TEXT,
  machine TEXT NOT NULL,
  state_sha256 TEXT,
  state_kept INTEGER NOT NULL DEFAULT 0 CHECK (state_kept IN (0, 1)),
  review_label TEXT CHECK (review_label IN ('0', '1', 'abstain')),
  review_rule TEXT,
  reviewed_at TEXT,
  review_batch TEXT,
  verdict TEXT CHECK (verdict IN ('true_flag', 'false_flag', 'missed', 'true_pass')),
  -- ok: the answer decided. incomplete: this answer was valid but another in its call was not, so
  -- the call decided nothing. error: no valid answer (the judge failed or returned an invalid one).
  CHECK (status = 'ok' OR outcome IS NULL),
  CHECK (status != 'ok' OR outcome IS NOT NULL OR choice_probabilities IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS decisions_question ON decisions (judgment, question, outcome, ts);
CREATE INDEX IF NOT EXISTS decisions_review ON decisions (mode, review_label);
CREATE INDEX IF NOT EXISTS decisions_commit ON decisions (repo, commit_sha);
CREATE INDEX IF NOT EXISTS decisions_status ON decisions (status);
CREATE INDEX IF NOT EXISTS decisions_item ON decisions (item);
CREATE INDEX IF NOT EXISTS decisions_source ON decisions (source);
CREATE INDEX IF NOT EXISTS decisions_call ON decisions (call_id);

-- The exact state, the questions sent and Jev's raw answers for one call: zlib-compressed JSON
-- {state, questions, response}. Kept while a flag, an error or a sampled pass of the call is
-- unreviewed; the reviewed label goes to the judgment's eval data, the permanent record.
CREATE TABLE IF NOT EXISTS payloads (
  call_id TEXT PRIMARY KEY,
  state_sha256 TEXT NOT NULL,
  body BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payloads_state ON payloads (state_sha256);

-- Every imported review label; decisions.review_* holds the counting one (an arbiter's over the council's).
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES decisions (id),
  label TEXT NOT NULL CHECK (label IN ('0', '1', 'abstain')),
  rule TEXT,
  reason TEXT,
  labeller TEXT NOT NULL CHECK (labeller IN ('council', 'arbiter')),
  batch TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_decision ON reviews (decision_id);

-- The rails ledger: one row per blocking rail decision, post-edit path-check failure and later
-- resolution. reason is a first line or summary of at most 200 characters, never command output.
CREATE TABLE IF NOT EXISTS rail_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  session TEXT NOT NULL,
  repo TEXT,
  mode TEXT CHECK (mode IN ('minimal', 'structured')),
  tool TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('block', 'context', 'resolved')),
  code TEXT NOT NULL,
  reason TEXT CHECK (reason IS NULL OR length(reason) <= 200),
  calls INTEGER CHECK (calls IS NULL OR calls >= 0),
  refusals INTEGER CHECK (refusals IS NULL OR refusals > 0),
  machine TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rail_events_code ON rail_events (code, ts);
CREATE INDEX IF NOT EXISTS rail_events_repo ON rail_events (repo, mode);

-- Shadow questions' review progress: labelled flags, precision and whether shadow_exit is met.
CREATE VIEW IF NOT EXISTS shadow_progress AS
SELECT
  q.judgment,
  q.question,
  q.question_version,
  q.shadow_exit,
  COUNT(d.id) AS decisions,
  COALESCE(SUM(d.outcome = 'flag'), 0) AS flags,
  COALESCE(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0) AS labelled_flags,
  COALESCE(SUM(d.verdict = 'true_flag'), 0) AS true_flags,
  CAST(SUM(d.verdict = 'true_flag') AS REAL)
    / NULLIF(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0) AS precision,
  COALESCE(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0)
    >= json_extract(q.shadow_exit, '$.labelled_flags') AS exit_met
FROM questions AS q
LEFT JOIN decisions AS d
  ON d.source = 'jev'
  AND d.mode = 'shadow'
  AND d.judgment = q.judgment
  AND d.question = q.question
  AND d.question_version = q.question_version
WHERE q.mode = 'shadow'
GROUP BY q.judgment, q.question, q.question_version;

PRAGMA user_version = 1;
COMMIT;
