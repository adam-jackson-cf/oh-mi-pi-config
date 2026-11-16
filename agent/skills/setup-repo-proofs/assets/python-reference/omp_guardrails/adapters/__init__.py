"""Test-framework adapters (plan §1.12), one subpackage per framework.

The profile's ``adapter`` value names the subpackage; ``omp-guardrails adapter`` runs its
``run(repo, profile, argv)``. An adapter only produces evidence for the shared checkers and
supplies its framework's source patterns: it emits JUnit XML, lists test IDs per tier, guards
the fast tier, ships ``ast-grep`` rules, names its mutation tool and normalises its JUnit dialect.
"""
