# ADR 0008: Generic setup workflow and owned mechanism documentation

- Status: Superseded in part by [ADR 0010](0010-dynaflows-package-layout.md) (engine and skill
  paths), [ADR 0013](0013-dynaflows-bundles-its-skills.md) (skill locations) and
  [ADR 0015](0015-dynaflows-minimal-and-structured-modes.md) (record docs in
  `agent/extensions/dynaflows/record/`)
- Date: 2026-11-18

## Context

Repository setup mixed reusable control-adoption practice with personal harness commands, record
schema, adapters and delivery mechanics. The generic workflow could not be read independently.
The engine also lived under skill assets even though runtime callers depend on it as an extension.

## Decision

Keep `agent/skills/setup-repo-proofs/` and its hidden, non-model-invocable skill entry intact.
Its markdown outside assets describes only generic repository setup: select a rule contract,
inventory source evidence, extend existing controls, dry-run whole-repository impact, obtain
explicit
approval, apply approved paths and demonstrate effective checks. It does not choose a catalogue or
introduce a second control authority.

DynaFlows-specific setup belongs in `agent/skills/omp-dyna-workflows/references/harness-setup.md`;
adapter authoring moves into the same references directory. Record format/lifecycle and profile
contracts belong to `agent/lib/record/README.md` and `agent/lib/record/profile.md`, linked from the
harness reference. The project setup playbook composes the generic workflow with those deltas and
retains the six-stage surface/proof/bootstrap integration. The final engine home is
`agent/extensions/dynaflows/guardrails/`; commands never install it in target repositories.

Preserve capability paragraphs rather than treating relocation as permission to drop obligations.
Generic controls stay repo-owned; source-line evidence, no duplicate controls, whole-repository
scope, approval STOP, strict record loading, machine budgets, adapter conformance and exact-head
delivery requirements remain. Event additions are enabled-only under ADR 0004. Historical ADRs and
digest-pinned studies keep their wording. No skill folder is deleted and no compatibility shim is
introduced. Caller/engine relocation and record documentation are coordinated sibling changes.

## Consequences

The generic skill can serve any selected language/runtime and rule contract. Harness and record
owners can evolve mechanisms without contaminating target projects or duplicating the schema in
setup instructions. The extraction retains stage-oriented mechanism detail for traceable coverage;
record field mentions there are procedural consumers, not another format definition.

No checks, tests, builds or formatters were run for this documentation slice; verification belongs
to the integrating agent.

## Paragraph traceability

Source coordinates below refer to the pre-split documents. Destination coordinates refer to this
change. `S/` = `agent/skills/setup-repo-proofs/`; `D/` =
`agent/skills/omp-dyna-workflows/references/`; `R/` = `agent/lib/record/`.
Blank separators and headings carry no independent capability. Where a paragraph spans generic and
mechanism content, the precise transferred subrange is listed separately. Field-level record
contracts are linked rather than duplicated. Record coordinates were supplied by their owning slice.

### Transferred mechanism paragraphs

| Source paragraph | Destination | Preservation |
| --- | --- | --- |
| `S/SKILL.md:10-13` | `D/harness-setup.md:31-34` | [^n1] |
| `S/SKILL.md:18-30` | `D/harness-setup.md:38-50` | [^n2] |
| `S/README.md:48-56` | `D/harness-setup.md:54-62` | [^n3] |
| `S/README.md:58-65` | `D/harness-setup.md:64-71` | [^n4] |
| `S/README.md:73-75` | `D/harness-setup.md:75-77` | [^n5] |
| `S/README.md:77-94` | `D/harness-setup.md:79-96` | [^n6] |
| `S0:20-38` | `D/harness-setup.md:100-118` | [^n7] |
| `S1:16-51` | `D/harness-setup.md:122-157` | [^n8] |
| `S2:9-72` | `D/harness-setup.md:161-224` | [^n9] |
| `S2:74-82` | `D/harness-setup.md:226-234` | [^n10] |
| `S2:84-111` | `D/harness-setup.md:236-263` | [^n11] |
| `S3:9-39` | `D/harness-setup.md:267-297` | [^n12] |
| `S3:41-113` | `D/harness-setup.md:299-371` | [^n13] |
| `S4:12-26` | `D/harness-setup.md:375-389` | [^n14] |
| `S5:9-47` | `D/harness-setup.md:393-431` | [^n15] |
| `S6:13-39` | `D/harness-setup.md:435-461` | [^n16] |
| `S6:71-82` | `D/harness-setup.md:465-476` | [^n17] |
| `S6:84-94` | `D/harness-setup.md:478-488` | [^n18] |
| `S6:134-145` | `D/harness-setup.md:492-503` | [^n19] |
| `S7:10-49` | `D/harness-setup.md:507-546` | [^n20] |

### Record-owned paragraphs

| Source | Destination | Preservation |
| --- | --- | --- |
| `S/README.md:28-36` | `R/README.md:9-20` | [^n21] |
| `S/README.md:38-46` | `R/README.md:24-45,58-67,82-85` | [^n22] |
| `S/README.md:67-69` | `R/README.md:172-177` | [^n23] |
| `S/README.md:77-85` | `R/README.md:148-159` | [^n24] |
| `S/README.md:86-87` | `R/README.md:100-109,132-133` | [^n25] |
| `S/SKILL.md:14-16` | `R/README.md:9-20` | [^n26] |
| `S0:11-16` | `R/README.md:9-14` | [^n27] |
| `S3:17-29` | `R/README.md:148-159` | [^n28] |
| `S3:44-51` | `R/README.md:105-109,120-133,158-159` | [^n29] |
| `S5:15-20` | `R/README.md:161-163` | [^n30] |
| `S5:36-39` | `R/README.md:164-166` | [^n31] |
| `S5:40-45` | `R/README.md:167-170` | [^n32] |
| `S6:28-39` | `R/README.md:16-20,108-109,142-144,169-170` | [^n33] |
| `S6:40-69` | `R/README.md:24-45,87-90; gate.json:1-11` | [^n34] |
| `S6:95-101` | `R/README.md:34-37,58-60` | [^n35] |
| `S6:103-105` | `R/README.md:82-85` | [^n36] |
| `S6:107-127` | `R/README.md:62-78` | [^n37] |
| `S6:129-133` | `R/README.md:172-177` | [^n38] |

### Generic workflow and terminal contract

| Source | Destination | Preservation |
| --- | --- | --- |
| `S/SKILL.md:1-6` | `S/SKILL.md:1-6` | [^n39] |
| `S/SKILL.md:32-37` | `S/SKILL.md:15-19` | [^n40] |
| `S/SKILL.md:39-46` | `S/SKILL.md:21-25` | [^n41] |
| `S/SKILL.md:48-53` | `S/SKILL.md:27-31` | [^n42] |
| `S/SKILL.md:55-62` | `S/SKILL.md:33-37` | [^n43] |
| `S/SKILL.md:64-68` | `S/SKILL.md:39-42` | [^n44] |
| `S/SKILL.md:70-74` | `S/SKILL.md:44-48` | [^n45] |
| `S/SKILL.md:76-80` | `S/SKILL.md:50-54` | [^n46] |
| `S/SKILL.md:82-87` | `S/SKILL.md:56-59` | [^n47] |
| `S/SKILL.md:89-131` | `S/SKILL.md:61-78` | [^n48] |
| `S/README.md:1-9` | `S/README.md:1-6` | [^n49] |
| `S/README.md:11-17` | `S/README.md:8-12` | [^n50] |
| `S/README.md:19-26` | `S/README.md:19-24` | [^n51] |
| `S/README.md:96-101` | `S/README.md:14-17` | [^n52] |
| `S0:1-10` | `S0:9-24` | [^n53] |
| `S0:17-19` | `S0:11-24` | [^n54] |
| `S0:20-23` | `S0:13-24` | [^n55] |
| `S0:24-26` | `S0:16-24` | [^n56] |
| `S0:27-29` | `S0:18-24` | [^n57] |
| `S0:30-32` | `S0:20-24` | [^n58] |
| `S0:33-38` | `S0:22-24` | [^n59] |
| `S0:40-44` | `S0:26-29` | [^n60] |
| `S1:1-8` | `S1:1-7` | [^n61] |
| `S1:9-11` | `S1:9-35` | [^n62] |
| `S1:12-13` | `S1:11-35` | [^n63] |
| `S1:14-20` | `S1:13-35` | [^n64] |
| `S1:21-24` | `S1:16-35` | [^n65] |
| `S1:25-34` | `S1:19-35` | [^n66] |
| `S1:35-41` | `S1:23-35` | [^n67] |
| `S1:42-51` | `S1:26-35` | [^n68] |
| `S1:52-56` | `S1:31-35` | [^n69] |
| `S1:57-58` | `S1:34-35` | [^n70] |
| `S1:60-66` | `S1:37-40` | [^n71] |
| `S2:1-8` | `S2:1-7` | [^n72] |
| `S2:113-118` | `S2:42-45` | [^n73] |
| `S3:1-8` | `S3:1-7` | [^n74] |
| `S3:115-119` | `S3:30-33` | [^n75] |
| `S4:1-8` | `S4:1-7` | [^n76] |
| `S4:9-11` | `S4:9-22` | [^n77] |
| `S4:27-28` | `S4:17-22` | [^n78] |
| `S4:29-30` | `S4:19-22` | [^n79] |
| `S4:31-33` | `S4:21-22` | [^n80] |
| `S4:35-38` | `S4:24-27` | [^n81] |
| `S5:1-8` | `S5:1-7` | [^n82] |
| `S5:49-53` | `S5:29-32` | [^n83] |
| `S6:1-9` | `S6:1-7` | [^n84] |
| `S6:10-12` | `S6:9-28` | [^n85] |
| `S6:146-150` | `S6:26-28` | [^n86] |
| `S6:152-157` | `S6:30-33` | [^n87] |
| `S7:1-9` | `S7:1-7` | [^n88] |
| `S7:51-56` | `S7:24-27` | [^n89] |

### Adapter and project delivery paragraphs

| Source | Destination | Preservation |
| --- | --- | --- |
| `SA:1-99` | `DA:1-99` | [^n90] |
| `DP:1-9` | `DP:1-14` | [^n91] |
| `DP:10-10` | `DP:15-15` | [^n92] |
| `DP:12-16` | `DP:17-21` | [^n93] |
| `DP:18-23` | `DP:23-28` | [^n94] |
| `DP:25-28` | `DP:30-33` | [^n95] |
| `DP:30-30` | `DP:35-35` | [^n96] |
| `DP:32-35` | `DP:37-40` | [^n97] |
| `DP:37-37` | `DP:42-42` | [^n98] |
| `DP:39-39` | `DP:44-44` | [^n99] |
| `DP:41-43` | `DP:46-48` | [^n100] |
| `DP:45-59` | `DP:50-64` | [^n101] |
| `DP:61-61` | `DP:66-66` | [^n102] |
| `DP:63-67` | `DP:68-72` | [^n103] |
| `DP:69-86` | `DP:74-91` | [^n104] |
| `DP:88-88` | `DP:93-93` | [^n105] |
| `DP:90-94` | `DP:95-99` | [^n106] |
| `DP:96-96` | `DP:101-101` | [^n107] |
| `DP:98-101` | `DP:103-106` | [^n108] |

### Adapter paragraph detail

| Source | Destination | Capability |
| --- | --- | --- |
| `SA:3-13` | `DA:3-13` | [^n109] |
| `SA:19-19` | `DA:19-19` | [^n110] |
| `SA:20-27` | `DA:20-27` | [^n111] |
| `SA:28-31` | `DA:28-31` | [^n112] |
| `SA:32-36` | `DA:32-36` | [^n113] |
| `SA:37-40` | `DA:37-40` | [^n114] |
| `SA:41-44` | `DA:41-44` | [^n115] |
| `SA:46-47` | `DA:46-47` | [^n116] |
| `SA:51-62` | `DA:51-62` | [^n117] |
| `SA:64-72` | `DA:64-72` | [^n118] |
| `SA:74-75` | `DA:74-75` | [^n119] |
| `SA:79-84` | `DA:79-84` | [^n120] |
| `SA:85-88` | `DA:85-88` | [^n121] |
| `SA:92-99` | `DA:92-99` | [^n122] |

## Transfer notes

Within the tables, `S0`–`S7` name the corresponding numbered workflow files under
`S/references/`; `SA` and `DA` name adapter-authoring.md under the source and destination
`references/` roots, and `DP` names `D/playbooks/project-setup.md`.

[^n1]: Capability text retained; engine paths/links relocated.
[^n2]: Capability text retained; engine paths/links relocated.
[^n3]: Capability text retained; engine paths/links relocated.
[^n4]: Capability text retained; engine paths/links relocated.
[^n5]: Capability text retained; engine paths/links relocated.
[^n6]: Capability text retained; engine paths/links relocated.
[^n7]: Capability text retained; engine paths/links relocated.
[^n8]: Capability text retained; engine paths/links relocated.
[^n9]: Capability text retained; engine paths/links relocated.
[^n10]: Capability text retained; engine paths/links relocated.
[^n11]: Capability text retained; engine paths/links relocated.
[^n12]: Capability text retained; engine paths/links relocated.
[^n13]: Capability text retained; engine paths/links relocated.
[^n14]: Capability text retained; engine paths/links relocated.
[^n15]: Capability text retained; engine paths/links relocated.
[^n16]: Capability text retained; engine paths/links relocated.
[^n17]: Capability text retained; engine paths/links relocated.
[^n18]: Capability text retained; engine paths/links relocated.
[^n19]: Capability text retained; engine paths/links relocated.
[^n20]: Capability text retained; engine paths/links relocated.
[^n21]: Record owner retains authoritative format/lifecycle details.
[^n22]: Record owner retains authoritative format/lifecycle details.
[^n23]: Record owner retains authoritative format/lifecycle details.
[^n24]: Record owner retains authoritative format/lifecycle details.
[^n25]: Record owner retains authoritative format/lifecycle details.
[^n26]: Record owner retains authoritative format/lifecycle details.
[^n27]: Record owner retains authoritative format/lifecycle details.
[^n28]: Record owner retains authoritative format/lifecycle details.
[^n29]: Record owner retains authoritative format/lifecycle details.
[^n30]: Record owner retains authoritative format/lifecycle details.
[^n31]: Record owner retains authoritative format/lifecycle details.
[^n32]: Record owner retains authoritative format/lifecycle details.
[^n33]: Record owner retains authoritative format/lifecycle details.
[^n34]: Record owner retains authoritative format/lifecycle details.
[^n35]: Record owner retains authoritative format/lifecycle details.
[^n36]: Record owner retains authoritative format/lifecycle details.
[^n37]: Record owner retains authoritative format/lifecycle details.
[^n38]: Record owner retains authoritative format/lifecycle details.
[^n39]: Generic obligation retained; mechanism clauses move as mapped above.
[^n40]: Generic obligation retained; mechanism clauses move as mapped above.
[^n41]: Generic obligation retained; mechanism clauses move as mapped above.
[^n42]: Generic obligation retained; mechanism clauses move as mapped above.
[^n43]: Generic obligation retained; mechanism clauses move as mapped above.
[^n44]: Generic obligation retained; mechanism clauses move as mapped above.
[^n45]: Generic obligation retained; mechanism clauses move as mapped above.
[^n46]: Generic obligation retained; mechanism clauses move as mapped above.
[^n47]: Generic obligation retained; mechanism clauses move as mapped above.
[^n48]: Generic obligation retained; mechanism clauses move as mapped above.
[^n49]: Generic obligation retained; mechanism clauses move as mapped above.
[^n50]: Generic obligation retained; mechanism clauses move as mapped above.
[^n51]: Generic obligation retained; mechanism clauses move as mapped above.
[^n52]: Generic obligation retained; mechanism clauses move as mapped above.
[^n53]: Generic obligation retained; mechanism clauses move as mapped above.
[^n54]: Generic obligation retained; mechanism clauses move as mapped above.
[^n55]: Generic obligation retained; mechanism clauses move as mapped above.
[^n56]: Generic obligation retained; mechanism clauses move as mapped above.
[^n57]: Generic obligation retained; mechanism clauses move as mapped above.
[^n58]: Generic obligation retained; mechanism clauses move as mapped above.
[^n59]: Generic obligation retained; mechanism clauses move as mapped above.
[^n60]: Generic obligation retained; mechanism clauses move as mapped above.
[^n61]: Generic obligation retained; mechanism clauses move as mapped above.
[^n62]: Generic obligation retained; mechanism clauses move as mapped above.
[^n63]: Generic obligation retained; mechanism clauses move as mapped above.
[^n64]: Generic obligation retained; mechanism clauses move as mapped above.
[^n65]: Generic obligation retained; mechanism clauses move as mapped above.
[^n66]: Generic obligation retained; mechanism clauses move as mapped above.
[^n67]: Generic obligation retained; mechanism clauses move as mapped above.
[^n68]: Generic obligation retained; mechanism clauses move as mapped above.
[^n69]: Generic obligation retained; mechanism clauses move as mapped above.
[^n70]: Generic obligation retained; mechanism clauses move as mapped above.
[^n71]: Generic obligation retained; mechanism clauses move as mapped above.
[^n72]: Generic obligation retained; mechanism clauses move as mapped above.
[^n73]: Generic obligation retained; mechanism clauses move as mapped above.
[^n74]: Generic obligation retained; mechanism clauses move as mapped above.
[^n75]: Generic obligation retained; mechanism clauses move as mapped above.
[^n76]: Generic obligation retained; mechanism clauses move as mapped above.
[^n77]: Generic obligation retained; mechanism clauses move as mapped above.
[^n78]: Generic obligation retained; mechanism clauses move as mapped above.
[^n79]: Generic obligation retained; mechanism clauses move as mapped above.
[^n80]: Generic obligation retained; mechanism clauses move as mapped above.
[^n81]: Generic obligation retained; mechanism clauses move as mapped above.
[^n82]: Generic obligation retained; mechanism clauses move as mapped above.
[^n83]: Generic obligation retained; mechanism clauses move as mapped above.
[^n84]: Generic obligation retained; mechanism clauses move as mapped above.
[^n85]: Generic obligation retained; mechanism clauses move as mapped above.
[^n86]: Generic obligation retained; mechanism clauses move as mapped above.
[^n87]: Generic obligation retained; mechanism clauses move as mapped above.
[^n88]: Generic obligation retained; mechanism clauses move as mapped above.
[^n89]: Generic obligation retained; mechanism clauses move as mapped above.
[^n90]: Moved intact except context, engine location and conformance link. All six interfaces, six
  example verdicts, frozen fixtures, acceptance blockers, rule of two and evidence isolation
  remain.
[^n91]: Entry now explicitly composes generic workflow, harness deltas and record docs; independent
  target and prerequisite PR preserved.
[^n92]: Delivery paragraph retained unchanged.
[^n93]: Delivery paragraph retained unchanged.
[^n94]: Delivery paragraph retained unchanged.
[^n95]: Delivery paragraph retained unchanged.
[^n96]: Delivery paragraph retained unchanged.
[^n97]: Delivery paragraph retained unchanged.
[^n98]: Delivery paragraph retained unchanged.
[^n99]: Delivery paragraph retained unchanged.
[^n100]: Delivery paragraph retained unchanged.
[^n101]: Delivery paragraph retained unchanged.
[^n102]: Delivery paragraph retained unchanged.
[^n103]: Delivery paragraph retained unchanged.
[^n104]: Delivery paragraph retained unchanged.
[^n105]: Delivery paragraph retained unchanged.
[^n106]: Delivery paragraph retained unchanged.
[^n107]: Delivery paragraph retained unchanged.
[^n108]: Delivery paragraph retained unchanged.
[^n109]: Missing-framework trigger and normalized-evidence boundary.
[^n110]: Per-tier JUnit report.
[^n111]: Collect-only IDs, static case collection, behavior names and fingerprints.
[^n112]: Local fast-tier process/network guard and CI container network denial.
[^n113]: Framework AST patterns, owned-code boundary and test-only production symbols.
[^n114]: Pinned mutation tooling and shared T3 report.
[^n115]: Framework-specific report normalization.
[^n116]: Per-repository adapter and tier selectors remain configuration, not code.
[^n117]: Canonical conformance specification and six example failure verdicts.
[^n118]: External fixture location, frozen cases, deterministic acceptance and unsupported-case
  blocker.
[^n119]: Framework-independent synthetic shared-checker fixtures.
[^n120]: Engine ownership from first use and evidence outside the target worktree.
[^n121]: Rule-of-two generalization with conformance preservation.
[^n122]: Prose guides authors; all six interface items and conformance determine completion.
