#!/usr/bin/env bash
set -euo pipefail
TAG="${1:-latest}"
TARGET=/opt/vnukovo-bot
PREV_TAG_FILE="$TARGET/.deploy_prev_tag"

cd "$TARGET"

# Save current tag for potential rollback
docker compose -f deploy/docker-compose.yml ps --format json bot 2>/dev/null \
    | grep -oE '"Image":\s*"[^"]+"' | head -1 | awk -F: '{print $NF}' | tr -d '"' \
    > "$PREV_TAG_FILE" || true

TAG="$TAG" docker compose -f deploy/docker-compose.yml pull
TAG="$TAG" docker compose -f deploy/docker-compose.yml up -d --remove-orphans

# Health gate
echo "Waiting for /healthz=200 ..."
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
        echo "Healthy. Deploy $TAG OK."
        exit 0
    fi
    sleep 2
done

echo "Health check failed; rolling back" >&2
exec bash deploy/scripts/rollback.sh
