#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/opt/voice-shopping-agents}"
SOURCE_CONFIG="${DEPLOY_PATH}/deploy/nginx/ivaris.top.conf"
DEFAULT_TARGET_CONFIG="/etc/nginx/sites-available/ivaris.top.conf"

if [[ ! -r "$SOURCE_CONFIG" ]]; then
    echo "Nginx configuration is missing or unreadable: ${SOURCE_CONFIG}" >&2
    exit 1
fi

enabled_config="$(
    grep -RIl --include='*.conf' 'server_name[[:space:]].*voice\.ivaris\.top' \
        /etc/nginx/sites-enabled 2>/dev/null | head -n 1 || true
)"
if [[ -n "$enabled_config" ]]; then
    TARGET_CONFIG="$(readlink -f "$enabled_config")"
else
    TARGET_CONFIG="$DEFAULT_TARGET_CONFIG"
    if [[ ! -e /etc/nginx/sites-enabled/ivaris.top.conf ]]; then
        echo "No enabled Nginx site for voice.ivaris.top was found." >&2
        echo "Enable ${DEFAULT_TARGET_CONFIG} before running automated deployments." >&2
        exit 1
    fi
fi

backup_config="$(mktemp)"
had_previous_config=false

cleanup() {
    rm -f "$backup_config"
}
trap cleanup EXIT

if [[ -f "$TARGET_CONFIG" ]]; then
    cp -- "$TARGET_CONFIG" "$backup_config"
    had_previous_config=true
fi

restore_previous_config() {
    if [[ "$had_previous_config" == true ]]; then
        sudo -n install -m 0644 "$backup_config" "$TARGET_CONFIG"
    else
        sudo -n rm -f "$TARGET_CONFIG"
    fi
}

echo "Installing Nginx configuration to ${TARGET_CONFIG}..."
sudo -n install -m 0644 "$SOURCE_CONFIG" "$TARGET_CONFIG"

if ! sudo -n nginx -t; then
    echo "Nginx configuration test failed; restoring the previous configuration." >&2
    restore_previous_config
    sudo -n nginx -t || true
    exit 1
fi

if ! sudo -n systemctl reload nginx; then
    echo "Nginx reload failed; restoring and reloading the previous configuration." >&2
    restore_previous_config
    sudo -n nginx -t || true
    sudo -n systemctl reload nginx || true
    exit 1
fi

echo "Nginx configuration installed and reloaded."
