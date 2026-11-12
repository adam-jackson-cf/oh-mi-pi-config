"""``omp-guardrails init``: draft a repository's personal per-repo record for owner confirmation.

Init writes only into the record directory (``--record``): ``gate.json``, ``profile.md`` and,
under ``proposed/``, native-tool configs the owner may apply to the repository as a fix. It
writes nothing into the repository. The profile template is the test-guidance skill's
``assets/templates/test-profile.md``, read from the harness (``omp_guardrails.harness``).
"""
