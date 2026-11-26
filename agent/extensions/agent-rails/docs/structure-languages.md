# Structure rules by language

The structure rules hold for every language, but each language defines a package, an import, an
entry point and a definition its own way.
[structure-languages.json](../engine/data/structure-languages.json) holds each definition with its
published source. The engine reads Python, TypeScript and Go through one parsing layer, `ast-grep`
(tree-sitter), so a rule sees the same definitions, imports and tokens in each language.

The import rules are `dependency-direction`, `tier-readership`, `sibling-isolation` and
`single-owner-placement`.

| Rule | Python | TypeScript | Go |
| --- | --- | --- | --- |
| Import rules | Dotted modules | Files, `tsconfig` paths | Module packages |
| Runtime data | Code folders | Exempts imported JSON | Exempts embeds, `testdata/` |
| Entry points | `__main__.py`, `cli.py` | `bin`, `main` | `package main` in root or `cmd/` |
| Duplicate code | Cutoff 0.336, measured | Runs once measured | Runs once measured |
| Prefix folder | Cutoff 0.15, measured | Runs once measured | Does not apply |
| Rename residue | Defined names | Identifiers | Identifiers |

Duplicate code is `STRUCT.overlapping-responsibilities`; prefix folder is `STRUCT.prefix-folder`,
which does not apply to Go because a Go folder is a package. A scored rule runs for a language only
with a measured cutoff. Shell has no packages, imports or definitions, so no structure rule reads
it. The candidate proposers behind the structure-naming judgments still propose Python only: those
judgments were measured on Python cases.

Python module names and imports have one owner, `guardrails/foundation/source/modules.py`:

1. A file's module is its path under an import root: `a.py` and its `a.pyi` stub are `a`, and
   `pkg/__init__.py` is the package `pkg`.
2. A relative import resolves against the importer's package, as Python's import system does. One
   that climbs above the top-level package is reported where a check names a source root
   (import rules, `FANOUT`).
3. Without a configured source root (`prefix-folder` and the candidate proposers), any folder may
   be an import root. A dotted name matches every file whose path ends with it, and the nearest file
   wins. A single name matches only a file in the importer's folder or outside every regular
   package, so a module inside a package never stands in for a standard-library name.
4. The candidate proposers skip a Python file that does not parse: it is no candidate, imports
   nothing and names no path.
