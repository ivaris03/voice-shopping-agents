#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:-https://voice.ivaris.top}"

health_body="$(curl --fail --silent --show-error "${BASE_URL}/health")"
if [[ "$health_body" != *'"status":"ok"'* ]]; then
    echo "Health endpoint did not return the API health payload: ${health_body}" >&2
    exit 1
fi

login_status="$(
    curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        -H 'Content-Type: application/json' \
        --data-binary '{}' \
        "${BASE_URL}/api/v1/auth/login"
)"
if [[ "$login_status" != "422" ]]; then
    echo "Login route did not reach FastAPI; expected 422 for an empty payload, got ${login_status}." >&2
    exit 1
fi

echo "Production routing smoke test passed."
