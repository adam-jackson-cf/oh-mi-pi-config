"""``omp-guardrails init``: bootstrap a repository's profile, gate, hook, pin and LSP notes.

``test-profile.md`` in this package is a byte-for-byte copy of the test-guidance skill's
template (``agent/skills/test-guidance/assets/templates/test-profile.md``). The package is
installed from a git subdirectory, so it cannot read the skill folder at run time; the repo's
gate test fails when the two copies differ.
"""
