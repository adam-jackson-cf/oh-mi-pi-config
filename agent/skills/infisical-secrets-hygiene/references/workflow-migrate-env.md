# Workflow: Migrate Existing `.env`

Goal: absorb an existing local `.env` into Infisical, validate runtime injection, then remove plaintext `.env`.

1. Run migration helper:
   - `scripts/infisical-bootstrap-or-migrate.sh --repo <repo-path> --source-env .env --env dev`
2. Validate app startup with injected variables:
   - `cd <repo-path>`
   - `infisical run --env=dev -- <your command>`
3. Confirm no regressions in app behavior.
4. Delete plaintext `.env` only after successful validation:
   - `scripts/delete-local-env.sh --file <repo-path>/.env`
   - or re-run migration helper with `--delete-env`
5. Keep `.env.example` for key-name template only.

Notes:

- This workflow does not print or expose secret values.
- If rollback is needed, restore from your secure backup process only.
