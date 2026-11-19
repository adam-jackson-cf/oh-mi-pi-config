# Dependency severity label guide

Labels for the five questions in [questions.json](questions.json). A labeler sees one case's
state: the advisory (`advisory.text` and its fields) and the code search (`code_search.queries`
and `code_search.hits`). A labeler never sees Jev's answers or the computed severity. Cite the
rule below that decided each label, and give a one-sentence reason per question.

The guide is versioned with the questions' version key in [thresholds.json](thresholds.json).
Relabel the affected cases when a question's wording or a rule changes.

## impact

The definitions are the question's own criteria, in `questions.json`. Pick the worst outcome
the advisory text states, not the worst outcome imaginable.

- I1. Command injection, unsafe deserialization, template or expression evaluation and a
  malicious published version that runs code are `rce`.
- I2. Forged or unverified tokens or signatures that are accepted, missing or skipped authorization
  checks (acting on other users' records, a spoofed identity header) and privilege escalation,
  local or remote, are `auth_bypass`.
- I3. SQL injection is `data_exposure`, whether or not the text says it reads data; leaked
  credentials, cookies or proxy credentials are `data_exposure`; directory traversal that reads
  files is `data_exposure`; reading source code, decrypting traffic and reading another tenant's
  data are `data_exposure`.
- I4. Cross-site scripting and open redirects are `integrity_loss`, even when the text adds what
  injected script could steal; so are overwriting files or configuration, injecting cookies or
  headers, and breaking a lock or other exclusivity guarantee. A file overwrite that the text says
  leads to code execution is `rce` (I1).
- I5. ReDoS, hangs, recursion errors and resource exhaustion are `dos`.
- I6. Learning only whether an account, e-mail address or file exists is `info_leak`.
- I7. Text that names no attacker outcome ("improper input validation, upgrade") is `unclear`.
- I8. A malicious package (ID or alias `MAL-`) never reaches these questions: code routes it as
  critical to the owner. Its labels still follow I1.

Examples: "execute arbitrary code by abusing the python/object/new constructor" is `rce` (I1).
"Allows remote attackers to enumerate user e-mail addresses" is `info_leak` (I6). "Subject to
SQL injection in column aliases" is `data_exposure` (I3).

## local_only

Yes: the advisory says exploitation needs local access, such as running the package's
command-line tool with crafted arguments or formatting a local file. No otherwise.

- L0. The text does not say exploitation needs local access: no.
- L1. "Only when running the tool on the command line with untrusted arguments" is yes.
- L2. "Running the formatter on untrusted input" with no network path is yes.
- L3. Network sniffing, HTTP requests, query strings and a malicious web site are no.

Examples: "the tqdm Python API used inside programs is not affected" with CLI-only `eval` is yes
(L1). "A remote attacker can read files the dev server should deny" is no (L3).

## needs_privileges

Yes: the attacker must already be authenticated or hold an account, role or configuration
access. No otherwise.

- P0. The text does not say the attacker needs an account, role or access: no.
- P1. "An unauthenticated remote attacker" is no.
- P2. A precondition on the application's own configuration (for example, it must set an
  option) is not an attacker privilege: no.
- P3. "An authenticated user", "a user with edit permission", "an administrator" or an attacker
  who must be able to change the project's configuration is yes.

Examples: "Any authenticated user can read other tenants' records" is yes (P3). "The
application must enable `follow_symlinks`" is no (P2).

## needs_interaction

Yes: a victim person must act, such as visiting a malicious or attacker-crafted web page,
following a link, viewing injected content, or pointing their tool at attacker-hosted content.
No: the attacker's own requests suffice.

- U0. The text names no action by a victim: no.
- U1. Cross-site scripting and open redirects need a victim to view or follow: yes.
- U2. "When you access a malicious web site while the dev server is running" is yes.
- U3. Sniffing traffic, sending requests or uploading files is no.
- U4. A victim running the tool against an attacker-hosted package index, repository or page is
  yes. Installing an entry from the repository's own lock file is not victim interaction.

Examples: "A victim who follows a crafted link is redirected" is yes (U1). "A crafted package or
custom PackageIndex page" is yes (U4).

## reached

Yes: a hit shows owned code using the exact function, option, command or feature the advisory
names as vulnerable, in the exploitable way. No otherwise.

- R0. No hits: no.
- R1. `jwt.verify(token, secret)` with no `algorithms` argument reaches an advisory about
  missing algorithms: yes.
- R2. `add_static('/assets', DIR)` without `follow_symlinks=True` does not reach an advisory
  about `follow_symlinks=True`: no.
- R3. Importing the package or calling an unrelated function is no.
- R4. A build, install or server command in a tracked script that runs the vulnerable command
  is yes.
- R5. A hit counts even when the search cannot show the attack's other runtime conditions, such
  as attacker-controlled input or a cross-origin redirect.
- R6. When the advisory names another package's feature as the path to the vulnerable code, a
  hit using that feature is yes: "qs, as used in Express … in the query string" is reached by
  `req.query` in an Express app.
