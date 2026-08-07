#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  delete-local-env.sh --file <path-to-env-file>

Behavior:
  - Best-effort secure overwrite, then delete file.
  - Prints only status/path information, never file contents.
EOF
}

ENV_FILE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$ENV_FILE" ]; then
  echo "ERROR: --file is required" >&2
  usage
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: file not found: $ENV_FILE" >&2
  exit 1
fi

if command -v srm >/dev/null 2>&1; then
  srm -fz "$ENV_FILE"
  echo "Deleted with srm: $ENV_FILE"
  exit 0
fi

if command -v gshred >/dev/null 2>&1; then
  gshred -u "$ENV_FILE"
  echo "Deleted with gshred: $ENV_FILE"
  exit 0
fi

if command -v shred >/dev/null 2>&1; then
  shred -u "$ENV_FILE"
  echo "Deleted with shred: $ENV_FILE"
  exit 0
fi

# Fallback: overwrite once, then remove.
size="$(wc -c < "$ENV_FILE" | tr -d ' ')"
if [ "$size" -gt 0 ]; then
  dd if=/dev/zero of="$ENV_FILE" bs=4096 count=$(( (size + 4095) / 4096 )) conv=notrunc >/dev/null 2>&1 || true
fi
rm -f "$ENV_FILE"
echo "Deleted with fallback overwrite+rm: $ENV_FILE"
