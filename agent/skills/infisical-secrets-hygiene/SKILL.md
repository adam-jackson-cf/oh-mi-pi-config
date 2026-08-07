---
name: "infisical-secrets-hygiene"
description: "Infisical-first secure variable workflows for local development. USE WHEN a project needs to setup sensitve variables or the user wants to migrate an existing secrets file like .env etc or cleaning up local plaintext sensitive data in files."
---

# Infisical Secrets Hygiene Skill

- Canonical source for secrets is Infisical.
- Never read or print secret values in terminal output, logs, or agent responses.
- Use key names, file paths, presence checks, and counts only.
- Prefer runtime injection with `infisical run -- <command>`.
- For full workflows and decision points, read:
  - `references/when-to-use.md`
  - `references/workflow-from-scratch.md`
  - `references/workflow-migrate-env.md`
  - `references/llm-safety-guardrails.md`

## Primary Commands

- Bootstrap or migrate a repo:
  - `scripts/infisical-bootstrap-or-migrate.sh --repo <repo-path> [--env dev] [--source-env .env] [--delete-env]`
- Cleanup/delete local env file:
  - `scripts/delete-local-env.sh --file <path-to-.env>`

## Safety Defaults

- `--delete-env` is opt-in; destructive action is never default.
- `delete-local-env.sh` does best-effort secure overwrite, then removes file.
- After migration, keep `.env.example`/`.env.sample` placeholders only.
