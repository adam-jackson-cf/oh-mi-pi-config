# OMP extensions

## No npm package imports in extensions

Extensions in this folder, and the `lib/` modules they import, MUST NOT import packages from
`node_modules` (for example `zod`). Use what OMP itself provides:

- Validation: `import { z } from "@oh-my-pi/omptype/zod"` (zod-compatible) or
  `@oh-my-pi/omptype`.
- Host APIs: `@oh-my-pi/*` packages (type imports or runtime), and `node:` built-ins.

Why: OMP 18.4.x's extension loader pays roughly 0.1 s per module it loads from `node_modules`,
synchronously, before the TUI accepts input. `zod` alone is about 80 modules, so each extension
importing it added about 10 s to startup; three of them added 30–110 s. The same zod code
pre-bundled into one file loads in 0.1 s, and `@oh-my-pi/omptype` loads in about 1 s.

`@oh-my-pi/omptype/zod` gaps to work around:

- No `.loose()`: use `.passthrough()`, or let unknown keys strip if they are not read.
- No `discriminatedUnion`: use `z.union` with `z.literal` tags.
- `z.union` rejects object branches that overlap (for example two objects that both strip
  unknown keys): `safeParse` each schema in order instead.

Standalone scripts run directly with `bun` (`jev-lab/`, `fastino-bench/`, skill scripts) are not
loaded by OMP and may keep using `zod`.

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
