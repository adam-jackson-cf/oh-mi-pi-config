# ADR 0005: Comment ban and implementation-integrity blockers

- Status: accepted by owner, 2026-11-18. Superseded in part by
  [ADR 0021](0021-judgments-enforced-from-the-start.md): the structure judge blocks from the
  start; it has no shadow mode
- Scope: dynaflows commit checks and explicit diff checks

## Decision

`CODE.no-comments` blocks comments on added diff lines, including an edited line that still carries
a comment. Python module, class and function first-statement string literals are docstrings and count
as comments. Unchanged pre-existing comments and docstrings remain on disk. This is an explicit
exception to the whole-repository/no-baseline principle in the dynaflows design directive: the owner
chose added-line scope, not debt removal.

Tool directives are exempt: shebangs, noqa, type: ignore, pyright, fmt off/on, ruff, isort, pylint,
eslint-*, @ts-expect-error/@ts-ignore/@ts-nocheck, prettier-ignore, biome-ignore, //go:build, //nolint,
SPDX-License-Identifier, markdownlint-*, Vim/Emacs modelines and encoding cookies. Rust `#[allow]` is
syntax, not a comment. Exemption does not turn ordinary prose elsewhere into a directive.

Python uses standard-library tokenize and AST, not substring matching. Other common extensions use
a small quote-aware lexer for hash, slash, block, double-dash, HTML/XML/Markdown block,
TeX/Erlang percent, Lisp semicolon and INI/properties comments. The catalogue lists syntax and limits.
Quoted strings and escapes are respected; language-specific heredocs, raw-string delimiters, regex
literals and embedded-language parsing are not interpreted. This is a lexical check, not a claim to
parse every language grammar. Unsupported syntax requires extending the lexer and outcome cases,
not a silent exception to the policy.

`CODE.no-placeholder-bodies` uses Python AST and blocks functions whose complete implementation is
`pass`, `...` or `raise NotImplementedError` (with or without constructor arguments). Protocol methods,
abstract methods, overload declarations and `.pyi` files are exempt. Existing docstrings do not make
an otherwise empty implementation real. Non-Python placeholder-body analysis is outside this
deterministic check and is explicitly stated in its contract.

`CODE.no-product-mocks` blocks added mock-library imports and jest/vi mock calls outside test paths.
Test paths use the same profile tier locations, support/fixture locations and path-matching semantics
as `TEST.placement`. Python imports are AST based; JS/TS imports/requires and named jest/vi calls are
lexical. Computed names and aliases are not resolved. Tests may use mock libraries; existing
test-integrity rules still restrict self-mocking.

The three CODE checks run unconditionally inside `omp-guardrails check` against both HEAD-to-index
and HEAD-to-working-tree added lines, including new non-ignored files. A staged violation cannot hide
behind a clean unstaged file. Before a first commit the base is Git's empty tree. Rails already invokes
that command after the repository commit gate for dynaflows-enabled repositories, so no second
TypeScript invocation or new user-configurable step is needed. Whole-repository exclusions cannot
hide CODE findings. They also run through `diff-check --base ... --head ...` for an explicit committed
range. No `prove()` quality-check phase is introduced: quality checks remain commit checks; this
repository contains no prove diff-check dispatcher to extend.

Ruff S110, BLE001 and SIM105 become native-tool catalogue selectors, covering swallowed exceptions,
broad exception catches and silent context suppression. They are not duplicated in the harness
Python checker.

The catalogue and proposed native settings remove Ruff D selection, pydocstyle convention and
docstring-code-format. D-related ignores are obsolete and removed too. Repository-owned linter
configurations and personal records are only inspected and reported, never changed by this PR.
Existing docstrings remain structure-judge evidence. Banning additions does not erase evidence.
Superseded by [ADR 0021](0021-judgments-enforced-from-the-start.md): the structure judge blocks
from the start; it has no shadow mode and no `ready` decision.

`TEST.growth.justified` previously required inline behaviour comments and therefore contradicted the
ban. Its input now comes from descriptive Python function names or JS/TS test titles. The deterministic
presence check requires at least two alphabetic words excluding generic
test/case/todo/tbd/placeholder/example tokens; semantic truthfulness remains judged outside the checker.
One name covers all parameter rows. Duplicate-call-path detection remains unchanged. This migration
removes the comment requirement rather than inventing a comment exemption.

Deprecated coding-guidance and test-guidance source files are not deleted or modified here. They
remain until the final owner-signed-off cleanup PR.

## Consequences

Added comments and the two integrity failures block dynaflows commits even when the repository's own
gate passes. Existing debt does not prevent an unrelated edit. The outcome tests exercise actual
temporary repositories and the public CLI, including directives, string literals, docstrings,
unchanged lines, declarations and mocks in test paths. Language grammar limits are visible in the
catalogue instead of hidden behind regex claims.
