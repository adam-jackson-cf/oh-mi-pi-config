# OMP extensions

## No npm package imports in extensions

Extensions in this folder MUST NOT import packages from `node_modules` (for example `zod` or
`@oh-my-pi/omptype`). Use what the OMP binary itself provides:

- Validation: `import { z } from "@oh-my-pi/pi-coding-agent"` (zod-compatible, omptype) or
  `import { type } from "@oh-my-pi/pi-ai"` (ArkType-compatible, omptype).
- Host APIs: `@oh-my-pi/*` packages the binary provides (type imports or runtime), and `node:`
  built-ins.

Why: the extension loader pays roughly 0.1–0.3 s per module it loads from `node_modules`,
synchronously, before the TUI accepts input. `zod` alone is about 80 modules, so each extension
importing it added about 10 s to startup. `@oh-my-pi/omptype` is not provided by the binary: it
resolves to the dev-dependency copy in `~/.omp/node_modules` and cost about 4 s per extension in
OMP 18.11 (11 s total across four extensions). The same omptype code reached through
`@oh-my-pi/pi-coding-agent` or `@oh-my-pi/pi-ai` is host-provided and loads in ~0 s.

omptype's zod API gaps to work around:

- No `.loose()`: use `.passthrough()`, or let unknown keys strip if they are not read.
- No `discriminatedUnion`: use `z.union` with `z.literal` tags.
- `z.union` rejects object branches that overlap (for example two objects that both strip
  unknown keys): `safeParse` each schema in order instead.

Standalone scripts run directly with `bun` (skill scripts) are not loaded by OMP and may keep
using `zod`.

## Verify startup after changing an extension

Before calling extension work done, start OMP fresh and confirm it is quick:

```sh
PI_DEBUG_STARTUP=1 omp -p "/quota-gate status"
```

It should finish in a few seconds with no `Still starting … phase: loadExtensions` lines. In the
interactive TUI, a slash command sent right after launch should respond within a few seconds, and
the process log (`~/.omp/logs/omp.<date>.<pid>.log`) should have no `ui.loop-blocked` warning.

To find a slow extension, load each one alone:

```sh
omp --no-extensions -e agent/extensions/<name>.ts -p noop
```
