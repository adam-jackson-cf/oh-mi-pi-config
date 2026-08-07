#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  infisical-bootstrap-or-migrate.sh --repo <path> [--env dev] [--path /] [--source-env .env] [--delete-env]

Examples:
  infisical-bootstrap-or-migrate.sh --repo ~/Projects/my-app
  infisical-bootstrap-or-migrate.sh --repo ~/Projects/my-app --source-env .env --env dev --delete-env

Notes:
  - Initializes Infisical in the repo if not already initialized.
  - Imports an existing .env into Infisical when source file exists.
  - Never prints secret values.
EOF
}

REPO=""
ENV_NAME="dev"
SECRET_PATH="/"
SOURCE_ENV=".env"
DELETE_ENV="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --env)
      ENV_NAME="${2:-}"
      shift 2
      ;;
    --path)
      SECRET_PATH="${2:-}"
      shift 2
      ;;
    --source-env)
      SOURCE_ENV="${2:-}"
      shift 2
      ;;
    --delete-env)
      DELETE_ENV="true"
      shift 1
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

if [ -z "$REPO" ]; then
  echo "ERROR: --repo is required" >&2
  usage
  exit 1
fi

if ! command -v infisical >/dev/null 2>&1; then
  echo "ERROR: infisical CLI is not installed" >&2
  exit 1
fi

if [ ! -d "$REPO" ]; then
  echo "ERROR: repo does not exist: $REPO" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DELETE_SCRIPT="${SCRIPT_DIR}/delete-local-env.sh"

cd "$REPO"

if [ ! -f ".infisical.json" ]; then
  echo "Infisical project not initialized in repo. Starting init..."
  infisical init
else
  echo "Infisical project already initialized."
fi

if [ -f "$SOURCE_ENV" ]; then
  echo "Importing env file into Infisical (values hidden): $SOURCE_ENV"
  infisical secrets set --env "$ENV_NAME" --path "$SECRET_PATH" --file "$SOURCE_ENV" --silent >/dev/null
  echo "Imported keys from $SOURCE_ENV to env=$ENV_NAME path=$SECRET_PATH"
else
  echo "No source env file found at: $SOURCE_ENV (bootstrap-only mode)"
fi

echo "Generating .env.example from Infisical key names"
infisical secrets generate-example-env --env "$ENV_NAME" --path "$SECRET_PATH" --silent > .env.example
echo "Wrote: $REPO/.env.example"

echo "Validation command (run manually with your app command):"
echo "  infisical run --env=$ENV_NAME --path=$SECRET_PATH -- <your command>"

if [ "$DELETE_ENV" = "true" ] && [ -f "$SOURCE_ENV" ]; then
  echo "Delete requested for source env file."
  "$DELETE_SCRIPT" --file "$REPO/$SOURCE_ENV"
fi
