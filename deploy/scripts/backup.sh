#!/usr/bin/env bash
# Restic-based backup of SQLite DB + raw artefacts.
set -euo pipefail
: "${RESTIC_REPOSITORY:?need RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?need RESTIC_PASSWORD}"

TARGET=/opt/vnukovo-bot/data
TAG="$(date -u +%Y%m%d-%H%M%S)"

restic snapshots >/dev/null 2>&1 || restic init
restic backup "$TARGET" --tag "$TAG"
restic forget --keep-daily 30 --keep-weekly 12 --prune
