# Workflow: From Scratch

Goal: initialize Infisical in a repo without importing local plaintext secrets.

1. Confirm prerequisites:
   - `infisical --version`
   - `infisical login`
2. Initialize project binding in repo root:
   - `infisical init`
3. Add secret keys/values directly to Infisical (UI or CLI):
   - `infisical secrets set <KEY=value> --env=dev`
4. Run app with injected runtime secrets:
   - `infisical run --env=dev -- <your command>`
5. Generate/update non-sensitive example template:
   - `infisical secrets generate-example-env --env=dev > .env.example`
6. Ensure plaintext local `.env` does not exist unless explicitly needed for temporary migration.
