# Structure and grouping

A file's path shows its owner and dependency direction.

## Rules

- Group by owner (capability or domain), never by technical kind. One folder = one ownership unit whose contents change together.
- Tiers are folders. Lower tier: what others read (contracts, shared types, parsing). Higher tier: what drives them (entry points, commands).
  Import only from lower tiers, never from a sibling on the same tier.
  - Add a tier only once a part is read by two or more others; until then keep modules flat in one package.
  - Existing project: read the current order from its import direction.
  - Add an automated import check where the toolchain allows one.
- Code two siblings both need moves down to the lowest tier both may read; never import one sibling from the other to reach it.
- A part one owner uses lives under that owner; once a second owner uses it, move it to the lowest tier both may read.
- Never create grab-bag folders (`common`, `utils`, `misc`, `helpers`); shared code goes in the lowest tier, in a folder named for what it holds.
  Sibling files sharing a name prefix (`audit_*`) that change together are a candidate folder named for that prefix.
- Merge units with overlapping responsibilities into one owner; no near-duplicates.
- Data loaded at run time (definitions, configuration, templates): outside the code package, in a root folder named for what it holds.
- Tests: one root test folder, each file named for the behaviour it checks, unless the ecosystem places tests beside code.
  Fixtures live in the root test folder beside the tests that load them.
  A code move updates test imports in the same step; test files stay where they are.
- A CLI or API entry point is the product's interface, in the package's top tier.
  A consumer of the finished product (dashboard, report generator, operator tool) gets its own root folder, reads the package, and is never imported by it.
- Generated output and run artifacts: outside source folders (outside the checkout, or behind a link the VCS ignore file excludes).
- Version only what must be reproduced or compared, as `<name>/v<N>` folders or files; name the current version in one place (e.g. a pointer file).
  Never edit a measured version; a change is a new version. Never version code modules.
- Reference another part by explicit path; never via search order, shadowing or a fallback location.
- Every instance of one kind shares one layout: a new instance matches its siblings; older instances change only in a brownfield restructure.
- Never create an empty folder for a planned layout; create each folder with its first file.

## By project state

- Greenfield: apply every rule from the first file.
- Brownfield addition: place new code where the rules put it. Creating the folder or file it needs is in scope; moving or renaming existing code is not.
  - Kind-grouped project: kind folders are not owners. New code with behaviour of its own (retry, caching) gets a capability folder
    at the source root beside the kind folders, even when one file uses it.
  - Rule-correct home needs existing code moved: put the new code in the existing folder whose role matches (never a grab-bag) and state the mismatch in the result.
- Brownfield restructure: move one concept (domain folder or module) per step, with no behaviour change in the move.
  - Each step updates every import, configuration, doc, test and tool exclude, and leaves build and tests passing; no re-export shim or alias.
  - Domain folders share one tier and never import each other; first move a part two domains share into the shared tier, in its own step.
  - Dissolve grab-bag folders: a file one owner uses moves under that owner; a file several use moves to the shared tier.
  - Apply [Clean cutovers](clean-cutovers.md) to each move; delete migration scaffolding once the moves land.
