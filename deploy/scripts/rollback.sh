#!/usr/bin/env bash
set -euo pipefail
TARGET=/opt/vnukovo-bot
PREV_TAG_FILE="$TARGET/.deploy_prev_tag"

if [ ! -s "$PREV_TAG_FILE" ]; then
    echo "No previous tag recorded — cannot rollback" >&2
    exit 1
fi
PREV=$(cat "$PREV_TAG_FILE")
echo "Rolling back to $PREV"
TAG="$PREV" docker compose -f "$TARGET/deploy/docker-compose.yml" up -d --remove-orphans
