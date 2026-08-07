# LLM Safety Guardrails

1. Never output secret values.
2. Never `cat` credential files for content display.
3. Use only:
   - path inventory,
   - key-name inventory,
   - presence/permission checks,
   - count-based reporting.
4. If a command could reveal values, redirect output safely or avoid the command.
5. For deletion workflows, require explicit operator intent (`--delete-env`).
6. Favor deterministic CLI steps:
   - `infisical init`
   - `infisical secrets set --file`
   - `infisical run -- ...`
   - `infisical scan`
