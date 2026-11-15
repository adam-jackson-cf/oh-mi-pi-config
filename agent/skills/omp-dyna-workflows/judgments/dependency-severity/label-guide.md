# Dependency severity label guide

Labels for the five questions in [questions.json](questions.json). A labeler sees one case's
state: the advisory (`advisory.text` and its fields) and the code search (`code_search.queries`
and `code_search.hits`). A labeler never sees Jev's answers or the computed severity. Cite the
rule below that decided each label.

The guide is versioned with the questions' version key in [thresholds.json](thresholds.json).
Relabel the affected cases when a question's wording changes.

## impact

Yes/no definitions are the question's own criteria, in `questions.json`. Pick the worst outcome
the advisory text states, not the worst outcome imaginable.

- I1. Command injection, unsafe deserialization, template or expression evaluation and a
  malicious published version that runs code are `rce`.
- I2. Forged or unverified tokens or signatures that are accepted are `auth_bypass`.
- I3. SQL injection that reads data is `data_exposure`; leaked credentials, cookies or proxy
  credentials are `data_exposure`; directory traversal that reads files is `data_exposure`.
- I4. Cross-site scripting and open redirects are `integrity_loss`.
- I5. ReDoS, hangs, recursion errors and resource exhaustion are `dos`.
- I6. Learning only whether an account, e-mail address or file exists is `info_leak`.
- I7. Text that names no attacker outcome ("improper input validation, upgrade") is `unclear`.

Examples: "execute arbitrary code by abusing the python/object/new constructor" is `rce` (I1).
"Allows remote attackers to enumerate user e-mail addresses" is `info_leak` (I6).

## local_only

Yes: the advisory says exploitation needs local access, such as running the package's
command-line tool with crafted arguments or formatting a local file. No: a remote attacker can
send the input over a network, or the text does not say.

- L1. "Only when running the tool on the command line with untrusted arguments" is yes.
- L2. "Running the formatter on untrusted input" with no network path is yes.
- L3. Network sniffing, HTTP requests, query strings and a malicious web site are no.

## needs_privileges

Yes: the attacker must already be authenticated or hold an account, role or configuration
access. No otherwise, including when the text does not say.

- P1. "An unauthenticated remote attacker" is no.
- P2. A precondition on the application's own configuration (for example, it must set an
  option) is not an attacker privilege: no.

## needs_interaction

Yes: a victim person must act, such as visiting a malicious or attacker-crafted web page,
following a link, or viewing injected content. No: the attacker's own requests suffice.

- U1. Cross-site scripting and open redirects need a victim to view or follow: yes.
- U2. "When you access a malicious web site while the dev server is running" is yes.
- U3. Sniffing traffic, sending requests or uploading files is no.

## reached

Yes: a hit shows owned code using the exact function, option, command or feature the advisory
names as vulnerable, in the exploitable way. No: no hits, or hits that use other parts of the
package, or the vulnerable option is not set.

- R1. `jwt.verify(token, secret)` with no `algorithms` argument reaches an advisory about
  missing algorithms: yes.
- R2. `add_static('/assets', DIR)` without `follow_symlinks=True` does not reach an advisory
  about `follow_symlinks=True`: no.
- R3. Importing the package or calling an unrelated function is no.
- R4. A build, install or server command in a tracked script that runs the vulnerable command
  is yes.
- R5. A hit counts even when the search cannot show the attack's other runtime conditions, such
  as attacker-controlled input or a cross-origin redirect.
