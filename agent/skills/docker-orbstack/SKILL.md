---
name: "docker-orbstack"
description: "Use the local OrbStack storage and status helpers consistently. USE WHEN Docker work needs OrbStack storage-selection helpers, status checks, or a requested switch to local OrbStack storage."
---

# Docker OrbStack Skill

## Commands

- Use `orbstack-data-status [external-data-dir]` before storage changes to inspect the current OrbStack `data_dir`, external-drive availability, and current OrbStack status.
- Use `orbstack-use-auto [external-data-dir]` to prefer `/Volumes/UGreen-External/Docker` and fall back to `~/.orbstack-local-data` when the external drive is unavailable.
- Use `orbstack-use-local [local-data-dir]` to force OrbStack onto local macOS storage and start it there.
- Use `orbstack-use-external [external-data-dir]` to force OrbStack onto the external drive and start it there. Fail fast if the drive is not mounted.
- Use `orbstack-eject-external-use-local [--dry-run] [external-data-dir] [local-data-dir]` when asked to switch to local storage and eject the UGreen external drive.

## Scenarios

### Switch to Local and Eject UGreen

- Run `orbstack-data-status` first and report the configured `data_dir`, whether `/Volumes/UGreen-External/Docker` is available, and the OrbStack status.
- Run `orbstack-eject-external-use-local --dry-run` before changing state. Report the mounted volume, target local path, OrbStack status, and any process blockers it prints.
- Run `orbstack-eject-external-use-local` for a scripted switch/eject flow. The script stops OrbStack, checks that OrbStack/Docker-related processes are no longer running, ejects `/Volumes/UGreen-External`, verifies it is no longer mounted, then starts OrbStack on `~/.orbstack-local-data`.
- If the script refuses to eject because OrbStack/Docker processes are still present, terminate those OrbStack/Docker blocker processes, rerun `orbstack-eject-external-use-local`, and proceed with ejection. Only stop and report back if blockers remain after the scripted retry.
- Run `orbstack-data-status` after the script succeeds and confirm the configured `data_dir` is local storage.
