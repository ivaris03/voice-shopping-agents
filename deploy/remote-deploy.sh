#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_TAG="${1:?usage: remote-deploy.sh <image-tag>}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/voice-shopping-agents}"
IMAGE_PREFIX="${IMAGE_PREFIX:?IMAGE_PREFIX is required}"
ENV_FILE="${DEPLOY_PATH}/.env"

cd "$DEPLOY_PATH"
export IMAGE_TAG IMAGE_PREFIX

if [[ ! -r "$ENV_FILE" ]]; then
    echo "Deployment env file is missing or unreadable: ${ENV_FILE}" >&2
    exit 1
fi

compose=(docker compose --env-file "$ENV_FILE" -f deploy/docker-compose.prod.yml)

echo "Pulling images for ${IMAGE_TAG}..."
"${compose[@]}" pull api user-web merchant-web platform-web

echo "Starting PostgreSQL and Redis..."
"${compose[@]}" up -d postgres redis

echo "Stopping the existing API before migrations..."
# A live WebSocket turn can keep an open database transaction while the
# migration container waits for DDL locks. Stop the old API first so a stalled
# realtime request cannot block deployment indefinitely.
"${compose[@]}" stop api

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
"${compose[@]}" run --rm --no-deps api python apps/api/scripts/migrate.py --seed-demo

echo "Starting application services..."
"${compose[@]}" up -d --wait --wait-timeout 120 --remove-orphans api user-web merchant-web platform-web
"${compose[@]}" ps

echo "Removing dangling images..."
docker image prune -f
