# Repo test profile

Fill every section with values. `none: <owner-approved reason>` is allowed only in Verification
surface, Nightly scheduler (a Run events field), and Identity-bearing artefacts; elsewhere a
missing value is a reported gap.

## Tiers

- T1: selector or marker `<selector>`, location `<location>`, command `<command>`.
- T2: selector or marker `<selector>`, location `<location>`, command `<command>`.
- T3: selector or marker `<selector>`, location `<location>`, command `<command>`.
- Public entry point per package: `<package>`: `<public entry point>`.

## Run events

- Commit command: `<command>`.
- CI command: `<command>`.
- Nightly scheduler: `<scheduler or none: owner-approved reason>`.
- Nightly branches: `<branches or branch selection rule>`.

## Fast-tier budget

- Per machine: `<machine id>`, platform `<platform>`, runtime `<runtime>`.
- Per machine limits: wall `<wall limit>`, per-test `<per-test limit>`.
- Per machine owner approval reference: `<approval reference>`.
- Duration-report command: `<command>`.

## Coverage

- Scope: `<owned files and tiers>`.
- Line floor: `<line floor>`; branch floor: `<branch floor>`.
- Where defined: `<configuration or policy location>`.
- Floors are met only by outcome or property cases or deleting unreachable code.

## Guards

- Fast-tier process guard location: `<location>`.
- Fast-tier network guard location: `<location>`.
- User-data guard: `<guard and location>`.

## Environments

- Required environments: `<environments>`.
- Require flag: `<flag>`.
- Readiness probe: `<command or probe>`.

## Verification surface

- Lever command: `<command>`.
- Feature map path: `<path>`.
- Journey file pattern: `<pattern>`.
- Data directory argument: `<argument>`.
- Evidence directory argument: `<argument>`.

## Ownership areas

- Map of T2 files to areas: `<file patterns and areas>`.
- Affected T2 test selection: `<selection command or rule>`.

## Identity-bearing artefacts

- Artefacts and preservation rules: `<paths and rules>`.

## Layout and naming

- Support directory: `<directory>`.
- Fixtures directory: `<directory>`.
- Test file patterns: `<patterns>`.

## Command notes

- Working directory and prerequisites: `<directory and prerequisites>`.
- Flags, exit codes, and interpretation: `<command notes>`.
