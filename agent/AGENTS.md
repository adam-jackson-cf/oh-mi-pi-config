<!-- OMP_GLOBAL_RULES_START -->

# AI-Assisted Workflows (OMP Global Rules) v2.0.1 - Auto-generated, do not edit

## Security Requirements

- **NEVER** commit, echo, print, or log API keys, tokens, passwords, or other
secrets in command output or transcripts
- **ALWAYS** use presence checks instead of value printing when verifying
environment variables
- **ALWAYS** mask sensitive values if display is required; show only the first
and last 4 characters

## Execution Permission

- When a user asks how to execute a task or make a change, explain the
recommended approach only. NEVER execute it or make changes until the user
explicitly asks or gives permission.

## Research Coverage

- When performing research or analysis, enumerate every candidate source to
review, then read each one in full. NEVER sample, skim, or keyword-scan a
subset. Name any source that cannot be read and why. Require the same of
delegated agents, with a read / not-read coverage list.

        <!-- OMP_GLOBAL_RULES_END -->

Never run Git in or copy into the NUC's `~/.omp`; `omp-config-pull.service` deploys it from `nuc-config`.
Restart OMP sessions on each device after rails or extension changes; old sessions keep the old rails.
