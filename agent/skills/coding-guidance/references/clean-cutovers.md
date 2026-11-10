# Clean cutovers

## Objective

Make the target design canonical without legacy compatibility.

## Guidance

- Never implement backward compatibility or refactor code to support both new and legacy objectives.
- Remove references to legacy functionality, version suffixes in file or module names such as `parser-v2.py`, and items that exist only for legacy behavior.
  A versioned folder or file such as `config/v2.toml` is not a suffix; [Structure and grouping](structure-and-grouping.md) says when to use one.
- Preserve exact wording and styling when reusing concepts, constraints, labels, or user-specified phrases across related edits.
- Never introduce semantically equivalent rewrites of canonical wording, labels, limits, or instructions within the same project.
