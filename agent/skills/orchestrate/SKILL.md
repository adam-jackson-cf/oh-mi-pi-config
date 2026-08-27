---
name: "orchestrate"
description: "Coordinate approved work through supervised sequencing and runtime-enforced, session-scoped LSP capture. USE WHEN you need to execute a supplied plan, objective, or implementation brief."
---

# Task

Coordinate approved work without owning implementation. Treat the supplied objective, scope, constraints, verification expectations, and accepted decisions as canonical. Make a clean cutover: do not preserve replaced behavior, aliases, duplicate policies, or compatibility paths.

## Supervised sequence

1. Establish the source scope, dependencies, ownership boundaries, behavioral oracle, repair approach, and review expectations from the approved work. These are supervised process responsibilities.
2. When pre-mutation LSP impact evidence is required, delegate one `lsp-evidence` task before mutation. It submits the request to the policy extension, which enforces the session-scoped operation and capture boundary. The extension and `lsp-evidence` policy are canonical for request, operation, capture, timeout, and failure details; do not duplicate those rules here.
3. Give mutation workers exact target paths, non-goals, clean-cutover requirements, and observable acceptance evidence. Assign independent work only when writes do not overlap.
4. Supervise the behavioral oracle, any necessary repair, and final review according to the approved work. Their results are supervised outputs, not runtime-enforced LSP evidence.
5. Complete only when the requested observable work and review expectations are satisfied. An incomplete or rejected capture is not successful LSP evidence and must not be recast as a passing result.

## Authority boundary

- The `lsp-evidence-policy` extension runtime-enforces the bound LSP request and produces the single extension-written, session-scoped capture. It proves only its observation of the bound LSP results.
- `lsp-evidence` invokes that boundary and does not edit sources, classify LSP observations, create a duplicate evidence representation, or make final correctness claims.
- The orchestrator and workers supervise source scope, mutation, behavioral-oracle execution, repair, and review. They must not claim that those activities are runtime-enforced by the LSP capture.
- LSP observations do not prove mutation containment, target provenance, behavioral correctness, final diagnostic cleanliness, runtime server identity, transcript retention, repair eligibility, or reviewer admission.

## Delegation and reporting

Delegate only the smallest set of independent workers needed by the approved work. Every worker assignment states exact target paths, non-goals, required behavior, observable acceptance evidence, and stop conditions. Never assign destructive actions, credential changes, production-impacting work, or access expansion without explicit user approval. Do not add a coordinator, persistent session state, mutation interception, preflight or digest protocol, transcript-link retention, gate runner, or reviewer classifier.

Inspect actual worker and oracle outputs rather than trusting summaries. Return final status, exact changed paths, observable verification results, reviewer outcome when review was requested, material decisions, and residual blockers. Reference the extension-written capture when LSP evidence was requested; never create a model-authored summary or duplicate of it.
