#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./install-ssh-publisher.sh --public-key-file PATH [OPTIONS]

Create a dedicated OpenSSH publisher account.
Install the Tailplan forced-command guard.
Copy the server-local client configuration into the publisher account.

Options:
  --public-key-file PATH  Read one OpenSSH public key from PATH.
  --publisher-user NAME   Set the account name. The default is tailplan-publisher.
  --publisher-home PATH   Set the account home. The default is /var/lib/tailplan-publisher.
  --token-file PATH       Set the server token source. The default is /var/lib/tailplan/token.
  --env-file PATH         Set the server environment source. The default is /etc/tailplan.env.
  --share-command PATH    Set the local publisher source.
                          The default is /usr/local/bin/tailplan-share.
EOF
}

PUBLIC_KEY_FILE=""
PUBLISHER_USER="tailplan-publisher"
PUBLISHER_HOME="/var/lib/tailplan-publisher"
TOKEN_FILE="/var/lib/tailplan/token"
ENV_FILE="/etc/tailplan.env"
SHARE_COMMAND="/usr/local/bin/tailplan-share"
INSTALLED_SHARE_COMMAND="/usr/local/libexec/tailplan-share"
while (($#)); do
  case "$1" in
    --public-key-file|--publisher-user|--publisher-home|--token-file|--env-file|--share-command)
      (($# >= 2)) || { printf 'Missing value for %s.\n' "$1" >&2; exit 2; }
      option="$1"
      value="$2"
      shift 2
      case "$option" in
        --public-key-file) PUBLIC_KEY_FILE="$value" ;;
        --publisher-user) PUBLISHER_USER="$value" ;;
        --publisher-home) PUBLISHER_HOME="$value" ;;
        --token-file) TOKEN_FILE="$value" ;;
        --env-file) ENV_FILE="$value" ;;
        --share-command) SHARE_COMMAND="$value" ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID:-$(id -u)}" == 0 ]] || {
  printf 'Run this installer as root.\n' >&2
  exit 1
}
[[ -n "$PUBLIC_KEY_FILE" ]] || {
  printf 'Use --public-key-file with one OpenSSH public key.\n' >&2
  exit 2
}
[[ "$PUBLISHER_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
  printf 'Publisher account name is invalid.\n' >&2
  exit 2
}

for path in "$PUBLISHER_HOME" "$TOKEN_FILE" "$ENV_FILE" "$SHARE_COMMAND"; do
  [[ "$path" == /* && "$path" != *$'\n'* ]] || {
    printf 'Each configured path must be absolute and must not contain a newline.\n' >&2
    exit 2
  }
done
for source in "$PUBLIC_KEY_FILE" "$TOKEN_FILE" "$ENV_FILE" "$SHARE_COMMAND"; do
  [[ -f "$source" && ! -L "$source" ]] || {
    printf 'Required source is not a regular file: %s\n' "$source" >&2
    exit 1
  }
done
[[ -s "$TOKEN_FILE" ]] || {
  printf 'Token source is empty: %s\n' "$TOKEN_FILE" >&2
  exit 1
}
if ((8#$(stat -c '%a' "$TOKEN_FILE") & 8#077)); then
  printf 'Token source must not grant access to group or other users.\n' >&2
  exit 1
fi
[[ -x "$SHARE_COMMAND" ]] || {
  printf 'Publisher command is not executable: %s\n' "$SHARE_COMMAND" >&2
  exit 1
}
if ((8#$(stat -c '%a' "$SHARE_COMMAND") & 8#022)); then
  printf 'Publisher command must not be writable by group or other users.\n' >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD_SOURCE="$SCRIPT_DIR/bin/tailplan-publish-guard"
GUARD_PATH="/usr/local/libexec/tailplan-publish-guard"
CONFIG_PATH="/etc/tailplan-publisher.json"
[[ -f "$GUARD_SOURCE" && ! -L "$GUARD_SOURCE" ]] || {
  printf 'Guard source is missing: %s\n' "$GUARD_SOURCE" >&2
  exit 1
}
python3 -m py_compile "$GUARD_SOURCE"

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tailplan-publisher.XXXXXXXX")"
trap 'rm -rf -- "$STAGE_DIR"' EXIT
NORMALIZED_KEY="$STAGE_DIR/public-key"
python3 - "$PUBLIC_KEY_FILE" "$NORMALIZED_KEY" <<'PY'
import base64
import binascii
import sys
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(lines) != 1:
    raise SystemExit("Public key file must contain exactly one non-empty line.")
fields = lines[0].split()
if len(fields) < 2 or fields[0] not in {
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-ed25519",
    "ssh-rsa",
}:
    raise SystemExit("Public key type is not supported.")
try:
    decoded = base64.b64decode(fields[1], validate=True)
except (binascii.Error, ValueError) as error:
    raise SystemExit("Public key data is not valid base64.") from error
if not decoded:
    raise SystemExit("Public key data is empty.")
destination.write_text(f"{fields[0]} {fields[1]}\n", encoding="utf-8")
PY

if account="$(getent passwd "$PUBLISHER_USER")"; then
  IFS=: read -r _ _ _ _ _ existing_home existing_shell <<<"$account"
  [[ "$existing_home" == "$PUBLISHER_HOME" && "$existing_shell" == /bin/bash ]] || {
    printf 'Existing publisher account has an incompatible home or shell.\n' >&2
    exit 1
  }
else
  useradd --system --user-group --create-home --home-dir "$PUBLISHER_HOME" \
    --shell /bin/bash "$PUBLISHER_USER"
fi
usermod --password '*' "$PUBLISHER_USER"
PUBLISHER_GROUP="$(id -gn "$PUBLISHER_USER")"

install -d -o root -g root -m 755 /usr/local/libexec
install -o root -g root -m 755 "$GUARD_SOURCE" "$GUARD_PATH"
install -o root -g root -m 755 "$SHARE_COMMAND" "$INSTALLED_SHARE_COMMAND"
python3 - "$STAGE_DIR/publisher.json" "$INSTALLED_SHARE_COMMAND" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps({"share_command": sys.argv[2]}, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
install -o root -g root -m 644 "$STAGE_DIR/publisher.json" "$CONFIG_PATH"

install -d -o "$PUBLISHER_USER" -g "$PUBLISHER_GROUP" -m 700 "$PUBLISHER_HOME"
install -d -o "$PUBLISHER_USER" -g "$PUBLISHER_GROUP" -m 700 \
  "$PUBLISHER_HOME/.ssh" "$PUBLISHER_HOME/.tailplan"
printf 'restrict,command="%s" ' "$GUARD_PATH" > "$STAGE_DIR/authorized_keys"
cat "$NORMALIZED_KEY" >> "$STAGE_DIR/authorized_keys"
install -o "$PUBLISHER_USER" -g "$PUBLISHER_GROUP" -m 600 \
  "$STAGE_DIR/authorized_keys" "$PUBLISHER_HOME/.ssh/authorized_keys"
install -o "$PUBLISHER_USER" -g "$PUBLISHER_GROUP" -m 600 \
  "$TOKEN_FILE" "$PUBLISHER_HOME/.tailplan/token"
install -o "$PUBLISHER_USER" -g "$PUBLISHER_GROUP" -m 600 \
  "$ENV_FILE" "$PUBLISHER_HOME/.tailplan/env"

printf 'Installed Tailplan SSH publisher account: %s\n' "$PUBLISHER_USER"
printf 'Installed forced-command guard: %s\n' "$GUARD_PATH"
printf 'Installed local publisher: %s\n' "$INSTALLED_SHARE_COMMAND"
printf 'Copied the token only within this server.\n'
printf 'Run this installer again after each Tailplan upgrade, token rotation, or public-key rotation.\n'
