#!/usr/bin/env bash
# First-time install. Run as botuser. Expects /etc/vnukovo-bot.env to exist (chmod 600).
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/yourorg/vnukovo-bot.git}"
TAG="${TAG:-latest}"
TARGET=/opt/vnukovo-bot

if [ ! -d "$TARGET/.git" ]; then
    git clone "$REPO_URL" "$TARGET"
fi
cd "$TARGET"
git fetch --all --tags

ln -sf /etc/vnukovo-bot.env "$TARGET/.env"
TAG="$TAG" docker compose -f deploy/docker-compose.yml pull
TAG="$TAG" docker compose -f deploy/docker-compose.yml up -d --remove-orphans

echo "Install OK. Tail with: make logs"
