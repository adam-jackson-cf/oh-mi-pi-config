# When To Use

Use this skill for optional workflows:

1. New repo or machine setup where secrets are not yet managed.
2. Existing repo currently using `.env` that needs Infisical migration.
3. Post-migration cleanup where local plaintext `.env` should be removed.
4. LLM-assisted operations where secret-safe deterministic behavior is required.

Do not use this skill when:

1. The repo intentionally ships only non-sensitive `.env.example` templates.
2. Secrets are managed by another mandated platform/workflow for that repo.
