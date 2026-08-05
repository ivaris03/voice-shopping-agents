#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_TAG="${1:?usage: remote-deploy.sh <image-tag>}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/voice-shopping-agents}"
IMAGE_PREFIX="${IMAGE_PREFIX:?IMAGE_PREFIX is required}"

cd "$DEPLOY_PATH"
export IMAGE_TAG IMAGE_PREFIX

compose=(docker compose -f deploy/docker-compose.prod.yml)

echo "Pulling images for ${IMAGE_TAG}..."
"${compose[@]}" pull api user-web merchant-web platform-web

echo "Starting PostgreSQL and Redis..."
"${compose[@]}" up -d postgres redis

for attempt in $(seq 1 30); do
    if "${compose[@]}" exec -T postgres pg_isready -q; then
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "PostgreSQL did not become ready" >&2
        exit 1
    fi
    sleep 2
done

echo "Applying database migrations..."
"${compose[@]}" run --rm --no-deps api python apps/api/scripts/migrate.py

echo "Starting application services..."
"${compose[@]}" up -d --remove-orphans api user-web merchant-web platform-web
"${compose[@]}" ps

echo "Removing dangling images..."
docker image prune -f

