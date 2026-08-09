#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./install.sh [--system] [--operator USER] [--rotate-token] [--no-serve] [--defer-https-verify]

By default, install Tailplan as a user service and add only /tailplan to Tailscale Serve.
Use --system to install a root-managed system service.
Use --operator USER (or TAILPLAN_OPERATOR) to provision one system operator.
Use --rotate-token with --system and an operator to replace both token copies.
Use --no-serve (or TAILPLAN_CONFIGURE_SERVE=0) for a portable install;
TAILPLAN_BASE_URL and, unless Tailscale is available, TAILPLAN_HOST are then required.
Use --defer-https-verify only with Serve and a loopback TAILPLAN_HOST when HTTPS
must be verified from a second tailnet node after the local deployment succeeds.
Set TAILPLAN_SKILLS_ROOT to change the agent skill directory.
EOF
}

CONFIGURE_SERVE="${TAILPLAN_CONFIGURE_SERVE:-1}"
DEFER_HTTPS_VERIFY=0
OPERATOR="${TAILPLAN_OPERATOR:-}"
ROTATE_TOKEN=0
INSTALL_SCOPE=user
while (($#)); do
  case "$1" in
    --system) INSTALL_SCOPE=system ;;
    --no-serve) CONFIGURE_SERVE=0 ;;
    --operator)
      shift
      (($#)) || { echo "--operator requires a user name." >&2; exit 2; }
      OPERATOR="$1"
      ;;
    --rotate-token) ROTATE_TOKEN=1 ;;
    --defer-https-verify) DEFER_HTTPS_VERIFY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
if [[ "$CONFIGURE_SERVE" != 0 && "$CONFIGURE_SERVE" != 1 ]]; then
  echo "TAILPLAN_CONFIGURE_SERVE must be 0 or 1." >&2
  exit 2
fi
if [[ "$DEFER_HTTPS_VERIFY" == 1 && "$CONFIGURE_SERVE" != 1 ]]; then
  echo "--defer-https-verify requires Tailscale Serve." >&2
  exit 2
fi

if [[ "$INSTALL_SCOPE" == system && "$EUID" != 0 ]]; then
  echo "System installation requires root (effective UID 0)." >&2
  exit 1
fi
if [[ "$INSTALL_SCOPE" != system && -n "$OPERATOR" ]]; then
  echo "--operator and TAILPLAN_OPERATOR require --system." >&2
  exit 2
fi
if [[ "$ROTATE_TOKEN" == 1 && "$INSTALL_SCOPE" != system ]]; then
  echo "--rotate-token requires --system." >&2
  exit 2
fi

if [[ "$INSTALL_SCOPE" == system ]]; then
  APP_DIR="${TAILPLAN_APP_DIR:-/opt/tailplan}"
  BIN_DIR="${TAILPLAN_BIN_DIR:-/usr/local/bin}"
  DATA_DIR="${TAILPLAN_DATA_DIR:-/var/lib/tailplan}"
  ENV_FILE="${TAILPLAN_ENV_FILE:-/etc/tailplan.env}"
  UNIT_FILE="${TAILPLAN_UNIT_FILE:-/etc/systemd/system/tailplan.service}"
  BACKUP_ROOT="${TAILPLAN_BACKUP_DIR:-/var/backups/tailplan}"
  SERVICE_USER="${TAILPLAN_USER:-tailplan}"
  SERVICE_GROUP="${TAILPLAN_GROUP:-tailplan}"
  SYSTEMCTL=(systemctl)
else
  APP_DIR="${TAILPLAN_APP_DIR:-$HOME/apps/tailplan}"
  BIN_DIR="${TAILPLAN_BIN_DIR:-$HOME/.local/bin}"
  DATA_DIR="${TAILPLAN_DATA_DIR:-$HOME/.tailplan}"
  ENV_FILE="$DATA_DIR/env"
  UNIT_FILE="$HOME/.config/systemd/user/tailplan.service"
  BACKUP_ROOT="${TAILPLAN_BACKUP_DIR:-$HOME/.tailplan-backups}"
  SERVICE_USER=""
  SERVICE_GROUP=""
  SYSTEMCTL=(systemctl --user)
fi
UNIT_DIR="$(dirname "$UNIT_FILE")"
SKILLS_ROOT=""
SKILL_DIR=""
SKILL_FILE=""
PORT="${TAILPLAN_PORT:-9127}"
PROXY_HOST="${TAILPLAN_PROXY_HOST:-127.0.0.1}"
PROXY_PORT="${TAILPLAN_PROXY_PORT:-9128}"
VERIFY_ATTEMPTS="${TAILPLAN_VERIFY_ATTEMPTS:-30}"
VERIFY_INTERVAL="${TAILPLAN_VERIFY_INTERVAL:-0.5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_DIR=""
BACKUP_DIR=""
MUTATED=0
SERVE_MUTATED=0
TOKEN_EXISTED=0
TOKEN_MODE=600
DATA_DIR_EXISTED=0
DATA_DIR_MODE=700
DRAFTS_DIR_EXISTED=0
DRAFTS_DIR_MODE=700
GENERATED_DIR_EXISTED=0
GENERATED_DIR_MODE=700
DATA_OWNER_MANIFEST=""
APP_DIR_EXISTED=0
APP_DIR_MODE=755
APP_DIR_OWNER=""
APP_BIN_DIR_EXISTED=0
APP_BIN_DIR_MODE=755
APP_BIN_DIR_OWNER=""
PRIOR_UNIT_EXISTED=0
PRIOR_ENABLED=0
PRIOR_ACTIVE=0
PRIOR_SERVE_PRESENT=0
PRIOR_PROXY=""
GROUP_CREATED=0
USER_CREATED=0
GROUP_ENTRY=""
USER_ENTRY=""
SERVICE_UID=""
SERVICE_GID=""
CREATED_DIRS=()
TEMP_FILES=()
OPERATOR_FROM_SUDO=0
OPERATOR_ENTRY=""
OPERATOR_UID=""
OPERATOR_GID=""
OPERATOR_HOME=""
OPERATOR_CONFIG_DIR=""
OPERATOR_TOKEN_FILE=""
OPERATOR_ENV_FILE=""
OPERATOR_CONFIG_EXISTED=0
OPERATOR_CONFIG_MODE=700
OPERATOR_CONFIG_OWNER=""
OPERATOR_TOKEN_EXISTED=0
OPERATOR_ENV_EXISTED=0
SKILLS_FOR_OPERATOR=0
PENDING_TEMP=""
token_tmp=""
env_tmp=""
unit_tmp=""
operator_token_tmp=""
operator_env_tmp=""
umask 077

cleanup_temp_files() {
  local temporary failed=0
  if [[ -n "$PENDING_TEMP" ]]; then
    rm -f -- "$PENDING_TEMP" || failed=1
  fi
  for temporary in "${TEMP_FILES[@]}"; do
    rm -f -- "$temporary" || failed=1
  done
  return "$failed"
}

allocate_temp() {
  local variable="$1" template="$2"
  PENDING_TEMP="$(mktemp "$template")"
  TEMP_FILES+=("$PENDING_TEMP")
  printf -v "$variable" '%s' "$PENDING_TEMP"
  PENDING_TEMP=""
}

cleanup() {
  cleanup_temp_files || true
  if [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]]; then
    rm -rf -- "$STAGE_DIR"
  fi
}
trap cleanup EXIT

die() {
  echo "$*" >&2
  return 1
}

reject_unsafe_value() {
  local name="$1" value="$2"
  python3 - "$name" "$value" <<'PY'
import sys

name, value = sys.argv[1:]
if any(ord(character) < 32 or ord(character) == 127 for character in value):
    raise SystemExit(f"{name} must not contain control characters")
PY
}

if [[ "$INSTALL_SCOPE" == system && -z "$OPERATOR" && -n "${SUDO_USER:-}" ]]; then
  OPERATOR="$SUDO_USER"
  OPERATOR_FROM_SUDO=1
fi
reject_unsafe_value TAILPLAN_OPERATOR "$OPERATOR"

validate_operator_identity() {
  local entry="$1" expected_sudo_uid="" home_owner home_mode
  if [[ "$OPERATOR_FROM_SUDO" == 1 ]]; then
    expected_sudo_uid="${SUDO_UID:-}"
  fi
  reject_unsafe_value "operator account entry" "$entry"
  IFS=: read -r _ _ OPERATOR_UID OPERATOR_GID _ OPERATOR_HOME _ <<< "$entry"
  reject_unsafe_value "operator home" "$OPERATOR_HOME"
  [[ -d "$OPERATOR_HOME" && ! -L "$OPERATOR_HOME" ]] ||
    die "The operator home must be an existing non-symlink directory."
  home_owner="$(stat -c '%u:%g' "$OPERATOR_HOME")"
  home_mode="$(stat -c '%a' "$OPERATOR_HOME")"
  python3 - \
    "$OPERATOR" \
    "$SERVICE_USER" \
    "$entry" \
    "$expected_sudo_uid" \
    "$home_owner" \
    "$home_mode" <<'PY'
import os
import re
import stat
import sys

operator, service_user, entry, sudo_uid, home_owner, home_mode = sys.argv[1:]
fields = entry.split(":")
if len(fields) < 7 or fields[0] != operator:
    raise SystemExit("The operator account entry is invalid")
if not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", operator):
    raise SystemExit("TAILPLAN_OPERATOR is not a valid account name")
if not fields[2].isdigit() or not fields[3].isdigit():
    raise SystemExit("The operator account identifiers are invalid")
uid = int(fields[2])
gid = int(fields[3])
if uid == 0 or operator == service_user:
    raise SystemExit("The operator must be a non-root, non-service account")
if sudo_uid and (not sudo_uid.isdigit() or int(sudo_uid) != uid):
    raise SystemExit("SUDO_UID does not match SUDO_USER")
home = fields[5]
if not os.path.isabs(home) or os.path.normpath(home) != home or home == os.sep:
    raise SystemExit("The operator home path is invalid")
if os.path.realpath(home) != home:
    raise SystemExit("The operator home path must not contain symlinks")
if home_owner != f"{uid}:{gid}":
    raise SystemExit("The operator must own the operator home directory")
mode = int(home_mode, 8)
if mode & (stat.S_IWGRP | stat.S_IWOTH):
    raise SystemExit("The operator home directory must not be group-writable or world-writable")
if fields[6] in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"):
    raise SystemExit("The operator must use a login shell")
PY
}

if [[ -n "$OPERATOR" ]]; then
  if ! OPERATOR_ENTRY="$(getent passwd "$OPERATOR")"; then
    die "The operator account does not exist."
  fi
  validate_operator_identity "$OPERATOR_ENTRY"
  OPERATOR_CONFIG_DIR="$OPERATOR_HOME/.tailplan"
  OPERATOR_TOKEN_FILE="$OPERATOR_CONFIG_DIR/token"
  OPERATOR_ENV_FILE="$OPERATOR_CONFIG_DIR/env"
fi

if [[ "$ROTATE_TOKEN" == 1 && -z "$OPERATOR" ]]; then
  die "--rotate-token requires an operator."
fi
if [[ -n "${TAILPLAN_SKILLS_ROOT:-}" ]]; then
  SKILLS_ROOT="$TAILPLAN_SKILLS_ROOT"
elif [[ "$INSTALL_SCOPE" == system && -n "$OPERATOR" ]]; then
  SKILLS_ROOT="$OPERATOR_HOME/.agents/skills"
  SKILLS_FOR_OPERATOR=1
else
  SKILLS_ROOT="$HOME/.agents/skills"
fi
SKILL_DIR="$SKILLS_ROOT/tailplan"
SKILL_FILE="$SKILL_DIR/SKILL.md"

python3 - \
  "$OPERATOR_CONFIG_DIR" \
  "$OPERATOR_TOKEN_FILE" \
  "$OPERATOR_ENV_FILE" \
  "$APP_DIR" \
  "$BIN_DIR" \
  "$DATA_DIR" \
  "$BACKUP_ROOT" \
  "$SKILLS_ROOT" <<'PY'
import os
import stat
import sys

config_dir, token_file, env_file, *protected_roots = sys.argv[1:]
if not config_dir:
    raise SystemExit(0)
if os.path.realpath(config_dir) != config_dir:
    raise SystemExit("The operator configuration path must not contain symlinks")
try:
    mode = os.lstat(config_dir).st_mode
except FileNotFoundError:
    pass
else:
    if not stat.S_ISDIR(mode):
        raise SystemExit("The operator configuration path must be a directory")
for label, path in (("token", token_file), ("environment file", env_file)):
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        continue
    if not stat.S_ISREG(mode):
        raise SystemExit(f"The existing operator {label} must be a regular file")
for root in protected_roots:
    common = os.path.commonpath((config_dir, root))
    if common == config_dir or common == root:
        raise SystemExit("The operator configuration path must not overlap an install path")
PY

if [[ -n "$OPERATOR" && -d "$OPERATOR_CONFIG_DIR" ]]; then
  [[ "$(stat -c '%u:%g' "$OPERATOR_CONFIG_DIR")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
    die "The operator must own the operator configuration directory."
  [[ "$(stat -c '%a' "$OPERATOR_CONFIG_DIR")" == 700 ]] ||
    die "The operator configuration directory mode must be 700."
  for operator_file in "$OPERATOR_TOKEN_FILE" "$OPERATOR_ENV_FILE"; do
    if [[ -e "$operator_file" ]]; then
      [[ "$(stat -c '%u:%g' "$operator_file")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
        die "The operator must own each existing operator configuration file."
      [[ "$(stat -c '%a' "$operator_file")" == 600 ]] ||
        die "Each existing operator configuration file mode must be 600."
    fi
  done
fi
if [[ "$SKILLS_FOR_OPERATOR" == 1 ]]; then
  for skill_directory in "$(dirname "$SKILLS_ROOT")" "$SKILLS_ROOT" "$SKILL_DIR"; do
    if [[ -d "$skill_directory" ]]; then
      [[ "$(stat -c '%u:%g' "$skill_directory")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
        die "The operator must own each existing operator skill directory."
    fi
  done
fi

for pair in \
  "APP_DIR:$APP_DIR" \
  "BIN_DIR:$BIN_DIR" \
  "DATA_DIR:$DATA_DIR" \
  "ENV_FILE:$ENV_FILE" \
  "UNIT_FILE:$UNIT_FILE" \
  "BACKUP_ROOT:$BACKUP_ROOT" \
  "SKILLS_ROOT:$SKILLS_ROOT" \
  "SERVICE_USER:$SERVICE_USER" \
  "SERVICE_GROUP:$SERVICE_GROUP" \
  "OPERATOR_CONFIG_DIR:$OPERATOR_CONFIG_DIR" \
  "TAILPLAN_HOST:${TAILPLAN_HOST:-}" \
  "TAILPLAN_PROXY_HOST:$PROXY_HOST" \
  "TAILPLAN_PORT:$PORT" \
  "TAILPLAN_PROXY_PORT:$PROXY_PORT" \
  "TAILPLAN_BASE_URL:${TAILPLAN_BASE_URL:-}" \
  "TAILPLAN_REDIRECT_VIEW_BASE_URL:${TAILPLAN_REDIRECT_VIEW_BASE_URL:-}" \
  "HOME:$HOME"; do
  reject_unsafe_value "${pair%%:*}" "${pair#*:}"
done

python3 - \
  "$INSTALL_SCOPE" \
  "$HOME" \
  "$APP_DIR" \
  "$BIN_DIR" \
  "$DATA_DIR" \
  "$ENV_FILE" \
  "$UNIT_FILE" \
  "$BACKUP_ROOT" \
  "$SKILLS_ROOT" \
  "$SKILL_FILE" <<'PY'
import os
import stat
import sys

(
    scope,
    home,
    app_dir,
    bin_dir,
    data_dir,
    env_file,
    unit_file,
    backup_root,
    skills_root,
    skill_file,
) = sys.argv[1:]
unit_dir = os.path.dirname(unit_file)

if scope == "system" and os.path.basename(unit_file) != "tailplan.service":
    raise SystemExit("TAILPLAN_UNIT_FILE must be named tailplan.service")


def validate_directory(name: str, path: str) -> None:
    if not os.path.isabs(path) or os.path.normpath(path) != path or path == os.sep:
        raise SystemExit(f"{name} must be an absolute, normalized, non-root path")
    if os.path.realpath(path) != path:
        raise SystemExit(f"{name} must not contain symlinks")

    current = os.sep
    for component in path.split(os.sep)[1:]:
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise SystemExit(f"{name} must not contain symlinks")
        if current != path and not stat.S_ISDIR(mode):
            raise SystemExit(f"{name} has a non-directory ancestor")
        if current == path and not stat.S_ISDIR(mode):
            raise SystemExit(f"{name} must be a directory or absent")


def validate_file_target(name: str, path: str) -> None:
    if not os.path.isabs(path) or os.path.normpath(path) != path or path == os.sep:
        raise SystemExit(f"{name} must be an absolute, normalized, non-root path")
    current = os.sep
    components = path.split(os.sep)[1:]
    for index, component in enumerate(components):
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise SystemExit(f"{name} must not contain symlinks")
        if index < len(components) - 1 and not stat.S_ISDIR(mode):
            raise SystemExit(f"{name} has a non-directory ancestor")
        if index == len(components) - 1 and not stat.S_ISREG(mode):
            raise SystemExit(f"Existing {name} must be a regular, non-symlink file")


for name, path in (
    ("HOME", home),
    ("TAILPLAN_APP_DIR", app_dir),
    ("TAILPLAN_BIN_DIR", bin_dir),
    ("TAILPLAN_DATA_DIR", data_dir),
    ("systemd unit directory", unit_dir),
    ("TAILPLAN_BACKUP_DIR", backup_root),
    ("TAILPLAN_SKILLS_ROOT", skills_root),
    ("Tailplan skill directory", os.path.dirname(skill_file)),
    ("Tailplan app bin directory", os.path.join(app_dir, "bin")),
    ("Tailplan drafts directory", os.path.join(data_dir, "drafts")),
    ("Tailplan generated directory", os.path.join(data_dir, "generated")),
):
    validate_directory(name, path)

validate_file_target("environment file", env_file)
validate_file_target("systemd unit", unit_file)
validate_file_target("Tailplan skill", skill_file)

deployment_roots = (
    ("TAILPLAN_APP_DIR", app_dir),
    ("TAILPLAN_BIN_DIR", bin_dir),
    ("TAILPLAN_DATA_DIR", data_dir),
    ("TAILPLAN_SKILLS_ROOT", skills_root),
)
protected_roots = deployment_roots + (
    ("systemd unit directory", unit_dir),
    ("TAILPLAN_BACKUP_DIR", backup_root),
)
for index, (left_name, left) in enumerate(protected_roots):
    for right_name, right in protected_roots[index + 1 :]:
        common = os.path.commonpath((left, right))
        if common == left or common == right:
            raise SystemExit(f"{left_name} and {right_name} must not overlap")

if scope == "system":
    for root_name, root in protected_roots:
        common = os.path.commonpath((env_file, root))
        if common == env_file or common == root:
            raise SystemExit(f"TAILPLAN_ENV_FILE and {root_name} must not overlap")

for name, path in (
    ("installed server", os.path.join(app_dir, "tailplan_server.py")),
    ("installed runner", os.path.join(app_dir, "bin", "run-tailplan")),
    ("obsolete runner", os.path.join(app_dir, "run-tailplan.sh")),
    ("tailplan-share", os.path.join(bin_dir, "tailplan-share")),
    ("tailplan-share-public", os.path.join(bin_dir, "tailplan-share-public")),
    ("environment file", env_file),
    ("token file", os.path.join(data_dir, "token")),
    ("systemd unit", unit_file),
    ("Tailplan skill", skill_file),
):
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        continue
    if not stat.S_ISREG(mode):
        raise SystemExit(f"Existing {name} must be a regular, non-symlink file")

if scope == "system" and os.path.isdir(data_dir):
    for current, directories, files in os.walk(data_dir, followlinks=False):
        for entry in directories + files:
            path = os.path.join(current, entry)
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise SystemExit("TAILPLAN_DATA_DIR migrated data tree must not contain symlinks")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SystemExit("TAILPLAN_DATA_DIR migrated data tree must contain only regular files and directories")

token_path = os.path.join(data_dir, "token")
if os.path.exists(token_path) and os.path.getsize(token_path) == 0:
    raise SystemExit("Existing token file must be nonempty")
PY

validate_service_identity() {
  local group_entry="$1" user_entry="$2"
  python3 - "$SERVICE_USER" "$SERVICE_GROUP" "$DATA_DIR" "$group_entry" "$user_entry" <<'PY'
import re
import sys

user, group, data_dir, group_entry, user_entry = sys.argv[1:]
name_pattern = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
for label, value in (("TAILPLAN_USER", user), ("TAILPLAN_GROUP", group)):
    if not name_pattern.fullmatch(value):
        raise SystemExit(f"{label} is not a valid system account name")
if not group_entry or not user_entry:
    raise SystemExit("Tailplan service identity is incomplete")
group_fields = group_entry.split(":")
user_fields = user_entry.split(":")
if len(group_fields) < 4 or group_fields[0] != group or not group_fields[2].isdigit():
    raise SystemExit("Existing Tailplan group is invalid")
if len(user_fields) < 7 or user_fields[0] != user or not user_fields[2].isdigit() or not user_fields[3].isdigit():
    raise SystemExit("Existing Tailplan user is invalid")
if int(user_fields[2]) == 0:
    raise SystemExit("Tailplan service user must not be root")
if user_fields[3] != group_fields[2]:
    raise SystemExit("Tailplan service user primary group does not match TAILPLAN_GROUP")
if user_fields[5] != data_dir:
    raise SystemExit("Tailplan service user home must match TAILPLAN_DATA_DIR")
if user_fields[6] not in ("/usr/sbin/nologin", "/sbin/nologin"):
    raise SystemExit("Tailplan service user must use nologin")
PY
}

if [[ "$INSTALL_SCOPE" == system ]]; then
  [[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || die "TAILPLAN_USER is not a valid system account name."
  [[ "$SERVICE_GROUP" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || die "TAILPLAN_GROUP is not a valid system account name."
  if GROUP_ENTRY="$(getent group "$SERVICE_GROUP")"; then
    :
  else
    GROUP_ENTRY=""
  fi
  if USER_ENTRY="$(getent passwd "$SERVICE_USER")"; then
    :
  else
    USER_ENTRY=""
  fi
  if [[ -n "$USER_ENTRY" && -z "$GROUP_ENTRY" ]]; then
    die "Tailplan service user exists but its group is absent."
  fi
  if [[ -n "$USER_ENTRY" ]]; then
    validate_service_identity "$GROUP_ENTRY" "$USER_ENTRY"
  fi
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
  die "TAILPLAN_PORT must be a valid TCP port."
fi
if ! [[ "$PROXY_PORT" =~ ^[0-9]+$ ]] || ((PROXY_PORT < 1 || PROXY_PORT > 65535)); then
  die "TAILPLAN_PROXY_PORT must be a valid TCP port."
fi
if ! [[ "$VERIFY_ATTEMPTS" =~ ^[0-9]+$ ]] || ((VERIFY_ATTEMPTS < 1 || VERIFY_ATTEMPTS > 300)); then
  die "TAILPLAN_VERIFY_ATTEMPTS must be between 1 and 300."
fi
if ! [[ "$VERIFY_INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  die "TAILPLAN_VERIFY_INTERVAL must be a non-negative number of seconds."
fi
[[ -n "$PROXY_HOST" ]] || die "TAILPLAN_PROXY_HOST must not be empty."

# The installer validates each required source file before it changes installed files.
for source in \
  tailplan_server.py \
  bin/run-tailplan \
  bin/tailplan-share \
  bin/tailplan-share-public \
  skills/tailplan/SKILL.md \
  systemd/tailplan.service; do
  [[ -f "$SCRIPT_DIR/$source" && ! -L "$SCRIPT_DIR/$source" ]] || die "Missing canonical source: $source"
done
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tailplan-install.XXXXXX")"
mkdir -p "$STAGE_DIR/bin" "$STAGE_DIR/skills/tailplan" "$STAGE_DIR/systemd"
install -m 755 "$SCRIPT_DIR/tailplan_server.py" "$STAGE_DIR/tailplan_server.py"
install -m 755 "$SCRIPT_DIR/bin/run-tailplan" "$STAGE_DIR/bin/run-tailplan"
install -m 755 "$SCRIPT_DIR/bin/tailplan-share" "$STAGE_DIR/bin/tailplan-share"
install -m 755 "$SCRIPT_DIR/bin/tailplan-share-public" "$STAGE_DIR/bin/tailplan-share-public"
install -m 644 "$SCRIPT_DIR/skills/tailplan/SKILL.md" "$STAGE_DIR/skills/tailplan/SKILL.md"
install -m 644 "$SCRIPT_DIR/systemd/tailplan.service" "$STAGE_DIR/systemd/tailplan.service"
bash -n "$STAGE_DIR/bin/run-tailplan"
python3 - \
  "$STAGE_DIR/tailplan_server.py" \
  "$STAGE_DIR/bin/tailplan-share" \
  "$STAGE_DIR/bin/tailplan-share-public" <<'PY'
import ast
import sys
from pathlib import Path

for source in sys.argv[1:]:
    ast.parse(Path(source).read_text(encoding="utf-8"), filename=source)
PY
EXPECTED_BUILD="$(sha256sum "$STAGE_DIR/tailplan_server.py" | cut -c1-12)"

TS_IP=""
TS_DNS=""
if [[ "$CONFIGURE_SERVE" == 1 || -z "${TAILPLAN_HOST:-}" ]]; then
  command -v tailscale >/dev/null 2>&1 || die "Tailscale is required; connect it or use --no-serve with explicit settings."
  TS_IP="$(tailscale ip -4 2>/dev/null | { IFS= read -r line || true; printf '%s' "$line"; } || true)"
  [[ -n "$TS_IP" ]] || die "Could not determine Tailscale IPv4 state. Connect Tailscale or set TAILPLAN_HOST."
fi
if [[ "$CONFIGURE_SERVE" == 1 ]]; then
  TS_DNS="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName", "").rstrip("."))' 2>/dev/null || true)"
  [[ -n "$TS_DNS" ]] || die "Could not determine Tailscale Self.DNSName; Tailscale state is incomplete."
fi

HOST="${TAILPLAN_HOST:-$TS_IP}"
[[ -n "$HOST" ]] || die "Could not determine a listener address. Set TAILPLAN_HOST explicitly."
python3 - "$HOST" "$PROXY_HOST" "$DEFER_HTTPS_VERIFY" <<'PY'
import ipaddress
import sys

host, proxy_host, defer_https_verify = sys.argv[1:]
for name, value in (("TAILPLAN_HOST", host), ("TAILPLAN_PROXY_HOST", proxy_host)):
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise SystemExit(f"{name} must not contain control characters")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an IP address") from exc
    if name == "TAILPLAN_PROXY_HOST" and not address.is_loopback:
        raise SystemExit("TAILPLAN_PROXY_HOST must be a loopback IP address")
    if name == "TAILPLAN_HOST" and defer_https_verify == "1" and not address.is_loopback:
        raise SystemExit("--defer-https-verify requires a loopback TAILPLAN_HOST")
PY
HOST_URL="$HOST"
PROXY_URL_HOST="$PROXY_HOST"
if [[ "$HOST" == *:* ]]; then
  HOST_URL="[$HOST]"
fi
if [[ "$PROXY_HOST" == *:* ]]; then
  PROXY_URL_HOST="[$PROXY_HOST]"
fi
if [[ "$CONFIGURE_SERVE" == 0 ]]; then
  [[ -n "${TAILPLAN_BASE_URL:-}" ]] || die "TAILPLAN_BASE_URL is required when Tailscale Serve is disabled."
  BASE_URL="$TAILPLAN_BASE_URL"
else
  BASE_URL="${TAILPLAN_BASE_URL:-https://${TS_DNS}/tailplan}"
fi
REDIRECT_VIEW_BASE_URL="${TAILPLAN_REDIRECT_VIEW_BASE_URL:-$BASE_URL}"
reject_unsafe_value "TAILPLAN_BASE_URL" "$BASE_URL"
reject_unsafe_value "TAILPLAN_REDIRECT_VIEW_BASE_URL" "$REDIRECT_VIEW_BASE_URL"
python3 - "$BASE_URL" "$REDIRECT_VIEW_BASE_URL" <<'PY'
import sys
from urllib.parse import urlparse

for name, value in zip(("TAILPLAN_BASE_URL", "TAILPLAN_REDIRECT_VIEW_BASE_URL"), sys.argv[1:]):
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"{name} has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.path and not parsed.path.startswith("/"))
    ):
        raise SystemExit(f"{name} must be a clean absolute HTTPS URL")
PY
BASE_URL="${BASE_URL%/}"
REDIRECT_VIEW_BASE_URL="${REDIRECT_VIEW_BASE_URL%/}"

if [[ "$CONFIGURE_SERVE" == 1 ]]; then
  tailscale serve status --json > "$STAGE_DIR/serve-before.json"
  python3 - "$STAGE_DIR/serve-before.json" "$TS_DNS" "$STAGE_DIR/serve-prior.json" <<'PY'
import json
import sys
from pathlib import Path

status_path, dns_name, metadata_path = sys.argv[1:]
with open(status_path, encoding="utf-8") as stream:
    config = json.load(stream)

web = config.get("Web", {})
if not isinstance(web, dict):
    raise SystemExit("Tailscale Serve Web status is not an object")
host = web.get(f"{dns_name}:443", {})
if not isinstance(host, dict):
    raise SystemExit("Tailscale Serve host status is not an object")
handlers = host.get("Handlers", {})
if not isinstance(handlers, dict):
    raise SystemExit("Tailscale Serve Handlers status is not an object")

handler = handlers.get("/tailplan")
metadata: dict[str, object]
if handler is None:
    metadata = {"present": False}
elif (
    isinstance(handler, dict)
    and set(handler) == {"Proxy"}
    and isinstance(handler["Proxy"], str)
    and handler["Proxy"]
    and not any(ord(character) < 32 or ord(character) == 127 for character in handler["Proxy"])
):
    metadata = {"present": True, "proxy": handler["Proxy"]}
else:
    raise SystemExit("Existing Tailscale Serve /tailplan handler is not a supported Proxy")

Path(metadata_path).write_text(json.dumps(metadata), encoding="utf-8")
PY
  chmod 600 "$STAGE_DIR/serve-prior.json"
  PRIOR_SERVE_PRESENT="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1], encoding="utf-8"))["present"]))' "$STAGE_DIR/serve-prior.json")"
  if [[ "$PRIOR_SERVE_PRESENT" == 1 ]]; then
    PRIOR_PROXY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["proxy"], end="")' "$STAGE_DIR/serve-prior.json")"
  fi
fi
if [[ -e "$UNIT_FILE" || -L "$UNIT_FILE" ]]; then
  PRIOR_UNIT_EXISTED=1
fi
if "${SYSTEMCTL[@]}" is-enabled tailplan.service >/dev/null 2>&1; then
  PRIOR_ENABLED=1
fi
if "${SYSTEMCTL[@]}" is-active tailplan.service >/dev/null 2>&1; then
  PRIOR_ACTIVE=1
fi
if [[ -e "$DATA_DIR/token" || -L "$DATA_DIR/token" ]]; then
  TOKEN_EXISTED=1
  TOKEN_MODE="$(stat -c '%a' "$DATA_DIR/token")"
  if [[ "$ROTATE_TOKEN" == 1 ]]; then
    cp -a -- "$DATA_DIR/token" "$STAGE_DIR/server-token.before"
  fi
fi
if [[ -n "$OPERATOR" && -d "$OPERATOR_CONFIG_DIR" ]]; then
  OPERATOR_CONFIG_EXISTED=1
  OPERATOR_CONFIG_MODE="$(stat -c '%a' "$OPERATOR_CONFIG_DIR")"
  OPERATOR_CONFIG_OWNER="$(stat -c '%u:%g' "$OPERATOR_CONFIG_DIR")"
fi
if [[ -n "$OPERATOR" && -f "$OPERATOR_TOKEN_FILE" ]]; then
  OPERATOR_TOKEN_EXISTED=1
  cp -a -- "$OPERATOR_TOKEN_FILE" "$STAGE_DIR/operator-token.before"
fi
if [[ -n "$OPERATOR" && -f "$OPERATOR_ENV_FILE" ]]; then
  OPERATOR_ENV_EXISTED=1
  cp -a -- "$OPERATOR_ENV_FILE" "$STAGE_DIR/operator-env.before"
fi
if [[ -d "$DATA_DIR" ]]; then
  DATA_DIR_EXISTED=1
  DATA_DIR_MODE="$(stat -c '%a' "$DATA_DIR")"
fi
if [[ -d "$DATA_DIR/drafts" ]]; then
  DRAFTS_DIR_EXISTED=1
  DRAFTS_DIR_MODE="$(stat -c '%a' "$DATA_DIR/drafts")"
fi
if [[ -d "$DATA_DIR/generated" ]]; then
  GENERATED_DIR_EXISTED=1
  GENERATED_DIR_MODE="$(stat -c '%a' "$DATA_DIR/generated")"
fi
if [[ "$INSTALL_SCOPE" == system && -d "$APP_DIR" ]]; then
  APP_DIR_EXISTED=1
  APP_DIR_MODE="$(stat -c '%a' "$APP_DIR")"
  APP_DIR_OWNER="$(stat -c '%u:%g' "$APP_DIR")"
fi
if [[ "$INSTALL_SCOPE" == system && -d "$APP_DIR/bin" ]]; then
  APP_BIN_DIR_EXISTED=1
  APP_BIN_DIR_MODE="$(stat -c '%a' "$APP_DIR/bin")"
  APP_BIN_DIR_OWNER="$(stat -c '%u:%g' "$APP_DIR/bin")"
fi

snapshot_data_ownership() {
  local paths_file="$STAGE_DIR/data-ownership-paths" path owner
  DATA_OWNER_MANIFEST="$STAGE_DIR/data-ownership.manifest"
  : > "$DATA_OWNER_MANIFEST"
  find -P "$DATA_DIR" -print0 > "$paths_file"
  while IFS= read -r -d '' path; do
    if [[ -L "$path" || (! -f "$path" && ! -d "$path") ]]; then
      die "TAILPLAN_DATA_DIR ownership snapshot found an unsafe entry."
    fi
    owner="$(stat -c '%u:%g' "$path")"
    [[ "$owner" =~ ^[0-9]+:[0-9]+$ ]] || die "TAILPLAN_DATA_DIR ownership snapshot failed."
    printf '%s\0%s\0' "$owner" "$path" >> "$DATA_OWNER_MANIFEST"
  done < "$paths_file"
}

if [[ "$INSTALL_SCOPE" == system && "$DATA_DIR_EXISTED" == 1 ]]; then
  snapshot_data_ownership
fi

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
BACKUP_DIR="$(mktemp -d "$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
chmod 700 "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR/files" "$BACKUP_DIR/state"
chmod 700 "$BACKUP_DIR/files" "$BACKUP_DIR/state"

backup_item() {
  local name="$1" path="$2"
  if [[ -e "$path" || -L "$path" ]]; then
    : > "$BACKUP_DIR/state/$name.existed"
    cp -a -- "$path" "$BACKUP_DIR/files/$name"
  fi
}
backup_item app-server "$APP_DIR/tailplan_server.py"
backup_item app-runner "$APP_DIR/bin/run-tailplan"
backup_item cli-share "$BIN_DIR/tailplan-share"
backup_item cli-public "$BIN_DIR/tailplan-share-public"
backup_item env "$ENV_FILE"
backup_item unit "$UNIT_FILE"
backup_item legacy-runner "$APP_DIR/run-tailplan.sh"
backup_item agent-skill "$SKILL_FILE"

if [[ "$CONFIGURE_SERVE" == 1 ]]; then
  install -m 600 "$STAGE_DIR/serve-prior.json" "$BACKUP_DIR/state/serve-prior.json"
fi

restore_item() {
  local name="$1" path="$2"
  rm -f -- "$path"
  if [[ -f "$BACKUP_DIR/state/$name.existed" ]]; then
    mkdir -p "$(dirname "$path")"
    cp -a -- "$BACKUP_DIR/files/$name" "$path"
  fi
}

restore_operator_file() {
  local source="$1" destination="$2" existed="$3"
  rm -f -- "$destination"
  if [[ "$existed" == 1 ]]; then
    cp -a -- "$STAGE_DIR/$source.before" "$destination"
  fi
}

restore_data_ownership() {
  local owner path restored=0
  [[ -n "$DATA_OWNER_MANIFEST" && -f "$DATA_OWNER_MANIFEST" ]] || return 1
  while IFS= read -r -d '' owner; do
    IFS= read -r -d '' path || return 1
    [[ "$owner" =~ ^[0-9]+:[0-9]+$ ]] || return 1
    [[ "$path" == "$DATA_DIR" || "$path" == "$DATA_DIR/"* ]] || return 1
    if [[ -L "$path" || (! -f "$path" && ! -d "$path") ]]; then
      return 1
    fi
    chown --no-dereference "$owner" "$path" || return 1
    restored=1
  done < "$DATA_OWNER_MANIFEST"
  [[ "$restored" == 1 ]]
}

rollback() {
  local original_status="$1"
  trap - ERR INT TERM HUP
  set +e
  local rollback_failed=0
  echo "Install failed; rolling back." >&2
  restore_item app-server "$APP_DIR/tailplan_server.py" || rollback_failed=1
  restore_item app-runner "$APP_DIR/bin/run-tailplan" || rollback_failed=1
  restore_item cli-share "$BIN_DIR/tailplan-share" || rollback_failed=1
  restore_item cli-public "$BIN_DIR/tailplan-share-public" || rollback_failed=1
  restore_item env "$ENV_FILE" || rollback_failed=1
  restore_item unit "$UNIT_FILE" || rollback_failed=1
  restore_item legacy-runner "$APP_DIR/run-tailplan.sh" || rollback_failed=1
  restore_item agent-skill "$SKILL_FILE" || rollback_failed=1
  if [[ -n "$OPERATOR" ]]; then
    restore_operator_file operator-token "$OPERATOR_TOKEN_FILE" "$OPERATOR_TOKEN_EXISTED" ||
      rollback_failed=1
    restore_operator_file operator-env "$OPERATOR_ENV_FILE" "$OPERATOR_ENV_EXISTED" ||
      rollback_failed=1
    if [[ "$OPERATOR_CONFIG_EXISTED" == 1 ]]; then
      chmod "$OPERATOR_CONFIG_MODE" "$OPERATOR_CONFIG_DIR" || rollback_failed=1
      chown "$OPERATOR_CONFIG_OWNER" "$OPERATOR_CONFIG_DIR" || rollback_failed=1
    fi
  fi
  if [[ "$TOKEN_EXISTED" == 1 ]]; then
    if [[ "$ROTATE_TOKEN" == 1 ]]; then
      cp -a -- "$STAGE_DIR/server-token.before" "$DATA_DIR/token" || rollback_failed=1
    else
      chmod "$TOKEN_MODE" "$DATA_DIR/token" || rollback_failed=1
    fi
  else
    rm -f -- "$DATA_DIR/token" || rollback_failed=1
  fi
  if [[ "$SERVE_MUTATED" == 1 ]]; then
    if [[ "$PRIOR_SERVE_PRESENT" == 1 ]]; then
      tailscale serve --bg --https=443 --set-path=/tailplan --yes "$PRIOR_PROXY" >/dev/null 2>&1 || rollback_failed=1
    else
      tailscale serve --https=443 --set-path=/tailplan off >/dev/null 2>&1 || rollback_failed=1
    fi
  fi
  "${SYSTEMCTL[@]}" daemon-reload >/dev/null 2>&1 || rollback_failed=1
  if [[ "$PRIOR_UNIT_EXISTED" == 1 ]]; then
    if [[ "$PRIOR_ENABLED" == 1 ]]; then
      "${SYSTEMCTL[@]}" enable tailplan.service >/dev/null 2>&1 || rollback_failed=1
    else
      "${SYSTEMCTL[@]}" disable tailplan.service >/dev/null 2>&1 || rollback_failed=1
    fi
    if [[ "$PRIOR_ACTIVE" == 1 ]]; then
      "${SYSTEMCTL[@]}" restart tailplan.service >/dev/null 2>&1 || rollback_failed=1
    else
      "${SYSTEMCTL[@]}" stop tailplan.service >/dev/null 2>&1 || rollback_failed=1
    fi
  else
    "${SYSTEMCTL[@]}" disable --now tailplan.service >/dev/null 2>&1 || true
  fi
  if [[ "$DATA_DIR_EXISTED" == 1 ]]; then
    chmod "$DATA_DIR_MODE" "$DATA_DIR" || rollback_failed=1
  fi
  if [[ "$DRAFTS_DIR_EXISTED" == 1 ]]; then
    chmod "$DRAFTS_DIR_MODE" "$DATA_DIR/drafts" || rollback_failed=1
  fi
  if [[ "$GENERATED_DIR_EXISTED" == 1 ]]; then
    chmod "$GENERATED_DIR_MODE" "$DATA_DIR/generated" || rollback_failed=1
  fi
  if [[ "$INSTALL_SCOPE" == system ]]; then
    if [[ "$APP_DIR_EXISTED" == 1 && -n "$APP_DIR_OWNER" ]]; then
      chmod "$APP_DIR_MODE" "$APP_DIR" || rollback_failed=1
      chown "$APP_DIR_OWNER" "$APP_DIR" || rollback_failed=1
    fi
    if [[ "$APP_BIN_DIR_EXISTED" == 1 && -n "$APP_BIN_DIR_OWNER" ]]; then
      chmod "$APP_BIN_DIR_MODE" "$APP_DIR/bin" || rollback_failed=1
      chown "$APP_BIN_DIR_OWNER" "$APP_DIR/bin" || rollback_failed=1
    fi
    if [[ "$DATA_DIR_EXISTED" == 1 ]]; then
      restore_data_ownership || rollback_failed=1
    fi
  fi
  cleanup_temp_files || rollback_failed=1
  local directory index
  for ((index = ${#CREATED_DIRS[@]} - 1; index >= 0; index--)); do
    directory="${CREATED_DIRS[index]}"
    rmdir -- "$directory" >/dev/null 2>&1 || rollback_failed=1
  done
  if [[ "$USER_CREATED" == 1 ]]; then
    userdel "$SERVICE_USER" >/dev/null 2>&1 || rollback_failed=1
  fi
  if [[ "$GROUP_CREATED" == 1 ]]; then
    groupdel "$SERVICE_GROUP" >/dev/null 2>&1 || rollback_failed=1
  fi
  if [[ "$rollback_failed" == 0 ]]; then
    echo "Rollback complete. Backup retained: $BACKUP_DIR" >&2
  else
    echo "Rollback incomplete; inspect backup: $BACKUP_DIR" >&2
  fi
  exit "$original_status"
}

on_error() {
  local status=$?
  if [[ "$MUTATED" == 1 ]]; then
    rollback "$status"
  fi
  exit "$status"
}
on_signal() {
  local status="$1"
  if [[ "$MUTATED" == 1 ]]; then
    rollback "$status"
  fi
  exit "$status"
}
trap on_error ERR
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

ensure_directory() {
  local target="$1" current parent index
  local -a missing=()
  current="$target"
  while [[ ! -e "$current" && ! -L "$current" ]]; do
    missing+=("$current")
    parent="$(dirname "$current")"
    [[ "$parent" != "$current" ]] || break
    current="$parent"
  done
  [[ -d "$current" && ! -L "$current" ]] || die "Unsafe directory path: $target"
  for ((index = ${#missing[@]} - 1; index >= 0; index--)); do
    mkdir -- "${missing[index]}"
    CREATED_DIRS+=("${missing[index]}")
  done
}

MUTATED=1
if [[ "$INSTALL_SCOPE" == system ]]; then
  if [[ -z "$GROUP_ENTRY" ]]; then
    GROUP_CREATED=1
    groupadd --system "$SERVICE_GROUP"
  fi
  if [[ -z "$USER_ENTRY" ]]; then
    USER_CREATED=1
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$DATA_DIR" \
      --shell /usr/sbin/nologin --no-create-home "$SERVICE_USER"
  fi
  GROUP_ENTRY="$(getent group "$SERVICE_GROUP")"
  USER_ENTRY="$(getent passwd "$SERVICE_USER")"
  validate_service_identity "$GROUP_ENTRY" "$USER_ENTRY"
  SERVICE_GID="${GROUP_ENTRY#*:*:}"
  SERVICE_GID="${SERVICE_GID%%:*}"
  SERVICE_UID="${USER_ENTRY#*:*:}"
  SERVICE_UID="${SERVICE_UID%%:*}"
fi
ensure_directory "$APP_DIR/bin"
ensure_directory "$BIN_DIR"
ensure_directory "$DATA_DIR/drafts"
ensure_directory "$DATA_DIR/generated"
ensure_directory "$(dirname "$ENV_FILE")"
ensure_directory "$UNIT_DIR"
ensure_directory "$SKILL_DIR"
if [[ -n "$OPERATOR" ]]; then
  ensure_directory "$OPERATOR_CONFIG_DIR"
  chmod 700 "$OPERATOR_CONFIG_DIR"
  chown "$OPERATOR_UID:$OPERATOR_GID" "$OPERATOR_CONFIG_DIR"
fi
chmod 700 "$DATA_DIR" "$DATA_DIR/drafts" "$DATA_DIR/generated"
if [[ "$INSTALL_SCOPE" == system ]]; then
  chmod 755 "$APP_DIR" "$APP_DIR/bin"
fi
install -m 755 "$STAGE_DIR/tailplan_server.py" "$APP_DIR/tailplan_server.py"
install -m 755 "$STAGE_DIR/bin/run-tailplan" "$APP_DIR/bin/run-tailplan"
install -m 755 "$STAGE_DIR/bin/tailplan-share" "$BIN_DIR/tailplan-share"
install -m 755 "$STAGE_DIR/bin/tailplan-share-public" "$BIN_DIR/tailplan-share-public"
install -m 644 "$STAGE_DIR/skills/tailplan/SKILL.md" "$SKILL_FILE"
if [[ "$SKILLS_FOR_OPERATOR" == 1 ]]; then
  chown "$OPERATOR_UID:$OPERATOR_GID" \
    "$(dirname "$SKILLS_ROOT")" \
    "$SKILLS_ROOT" \
    "$SKILL_DIR" \
    "$SKILL_FILE"
fi
rm -f -- "$APP_DIR/run-tailplan.sh"

if [[ "$ROTATE_TOKEN" == 1 || ! -s "$DATA_DIR/token" ]]; then
  allocate_temp token_tmp "$DATA_DIR/.token.XXXXXX.tmp"
  python3 - <<'PY' > "$token_tmp"
import secrets
print(secrets.token_urlsafe(32))
PY
  chmod 600 "$token_tmp"
  mv -f -- "$token_tmp" "$DATA_DIR/token"
fi
chmod 600 "$DATA_DIR/token"

allocate_temp env_tmp "$(dirname "$ENV_FILE")/.tailplan.env.XXXXXX.tmp"
python3 - \
  "$env_tmp" \
  "$APP_DIR" \
  "$DATA_DIR" \
  "$HOST" \
  "$PORT" \
  "$PROXY_HOST" \
  "$PROXY_PORT" \
  "$BASE_URL" \
  "$REDIRECT_VIEW_BASE_URL" <<'PY'
import sys
from pathlib import Path

(
    destination,
    app_dir,
    data_dir,
    host,
    port,
    proxy_host,
    proxy_port,
    base_url,
    redirect_view_base_url,
) = sys.argv[1:]


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


values = (
    ("TAILPLAN_APP_DIR", app_dir),
    ("TAILPLAN_SERVER", f"{app_dir}/tailplan_server.py"),
    ("TAILPLAN_DATA_DIR", data_dir),
    ("TAILPLAN_TOKEN_FILE", f"{data_dir}/token"),
    ("TAILPLAN_HOST", host),
    ("TAILPLAN_PORT", port),
    ("TAILPLAN_PROXY_HOST", proxy_host),
    ("TAILPLAN_PROXY_PORT", proxy_port),
    ("TAILPLAN_BASE_URL", base_url),
    ("TAILPLAN_REDIRECT_VIEW_BASE_URL", redirect_view_base_url),
)
content = "".join(f"{name}={quote(value)}\n" for name, value in values)
Path(destination).write_text(content, encoding="utf-8")
PY
chmod 600 "$env_tmp"
mv -f -- "$env_tmp" "$ENV_FILE"

if [[ -n "$OPERATOR" ]]; then
  allocate_temp operator_token_tmp "$OPERATOR_CONFIG_DIR/.token.XXXXXX.tmp"
  install -m 600 "$DATA_DIR/token" "$operator_token_tmp"
  mv -f -- "$operator_token_tmp" "$OPERATOR_TOKEN_FILE"
  allocate_temp operator_env_tmp "$OPERATOR_CONFIG_DIR/.env.XXXXXX.tmp"
  printf 'TAILPLAN_BASE_URL="%s"\n' "$BASE_URL" > "$operator_env_tmp"
  chmod 600 "$operator_env_tmp"
  mv -f -- "$operator_env_tmp" "$OPERATOR_ENV_FILE"
  chown "$OPERATOR_UID:$OPERATOR_GID" "$OPERATOR_TOKEN_FILE" "$OPERATOR_ENV_FILE"
fi

allocate_temp unit_tmp "$UNIT_DIR/.tailplan.service.XXXXXX.tmp"
python3 - \
  "$STAGE_DIR/systemd/tailplan.service" \
  "$unit_tmp" \
  "$INSTALL_SCOPE" \
  "$ENV_FILE" \
  "$DATA_DIR" \
  "$APP_DIR" \
  "$SERVICE_USER" \
  "$SERVICE_GROUP" <<'PY'
import sys
from pathlib import Path

source, destination, scope, env_file, data_dir, app_dir, service_user, service_group = sys.argv[1:]


def unit_escape(value: str) -> str:
    """Escape a path as systemd unit syntax without losing whitespace."""
    safe = b"/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-:"
    return "".join(
        chr(byte) if byte in safe else f"\\x{byte:02x}" for byte in value.encode()
    )


template = Path(source).read_text(encoding="utf-8")
if scope == "system":
    replacements = {
        "__TAILPLAN_AFTER__": "network-online.target tailscaled.service",
        "__TAILPLAN_WANTS__": "network-online.target tailscaled.service",
        "__TAILPLAN_SYSTEM_DIRECTIVES__\n": (
            f"User={service_user}\n"
            f"Group={service_group}\n"
            f"WorkingDirectory={unit_escape(app_dir)}\n"
        ),
        "__TAILPLAN_ENV_FILE__": unit_escape(env_file),
        "__TAILPLAN_DATA_DIR__": unit_escape(data_dir),
        "__TAILPLAN_WANTED_BY__": "multi-user.target",
    }
else:
    replacements = {
        "__TAILPLAN_AFTER__": "default.target tailscaled.service",
        "__TAILPLAN_WANTS__": "network-online.target",
        "__TAILPLAN_SYSTEM_DIRECTIVES__\n": "",
        "__TAILPLAN_ENV_FILE__": unit_escape(env_file),
        "__TAILPLAN_DATA_DIR__": unit_escape(data_dir),
        "__TAILPLAN_WANTED_BY__": "default.target",
    }
for placeholder, value in replacements.items():
    if template.count(placeholder) != 1:
        raise SystemExit("invalid Tailplan systemd unit template")
    template = template.replace(placeholder, value)
if "__TAILPLAN_" in template:
    raise SystemExit("unrendered Tailplan systemd unit placeholder")
Path(destination).write_text(template, encoding="utf-8")
PY
chmod 644 "$unit_tmp"
mv -f -- "$unit_tmp" "$UNIT_FILE"

if [[ "$INSTALL_SCOPE" == system ]]; then
  chown root:root \
    "$APP_DIR" \
    "$APP_DIR/bin" \
    "$APP_DIR/tailplan_server.py" \
    "$APP_DIR/bin/run-tailplan" \
    "$BIN_DIR/tailplan-share" \
    "$BIN_DIR/tailplan-share-public" \
    "$ENV_FILE" \
    "$UNIT_FILE"
  if [[ "$SKILLS_FOR_OPERATOR" == 0 ]]; then
    chown root:root "$SKILL_FILE"
  fi
  chown -R -P --no-dereference "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
fi

verify_installed_file() {
  local source="$1" destination="$2" expected_mode="$3"
  cmp -s -- "$source" "$destination" || die "Installed file checksum mismatch: $destination"
  [[ "$(stat -c '%a' "$destination")" == "$expected_mode" ]] || die "Installed file mode mismatch: $destination"
}
verify_installed_file "$STAGE_DIR/tailplan_server.py" "$APP_DIR/tailplan_server.py" 755
verify_installed_file "$STAGE_DIR/bin/run-tailplan" "$APP_DIR/bin/run-tailplan" 755
verify_installed_file "$STAGE_DIR/bin/tailplan-share" "$BIN_DIR/tailplan-share" 755
verify_installed_file "$STAGE_DIR/bin/tailplan-share-public" "$BIN_DIR/tailplan-share-public" 755
verify_installed_file "$STAGE_DIR/skills/tailplan/SKILL.md" "$SKILL_FILE" 644
[[ "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || die "Installed environment file mode mismatch."
[[ "$(stat -c '%a' "$DATA_DIR/token")" == 600 ]] || die "Credential file mode mismatch."
[[ "$(stat -c '%a' "$UNIT_FILE")" == 644 ]] || die "Installed systemd unit mode mismatch."
[[ ! -L "$UNIT_FILE" && -f "$UNIT_FILE" ]] || die "Installed systemd unit path is unsafe."
if grep -q '__TAILPLAN_' "$UNIT_FILE"; then
  die "Installed systemd unit contains an unrendered placeholder."
fi
if [[ -n "$OPERATOR" ]]; then
  cmp -s -- "$DATA_DIR/token" "$OPERATOR_TOKEN_FILE" ||
    die "The operator token does not match the server token."
  [[ "$(stat -c '%a' "$OPERATOR_CONFIG_DIR")" == 700 ]] ||
    die "Installed operator configuration directory mode mismatch."
  for operator_file in "$OPERATOR_TOKEN_FILE" "$OPERATOR_ENV_FILE"; do
    [[ "$(stat -c '%a' "$operator_file")" == 600 ]] ||
      die "Installed operator configuration file mode mismatch."
    [[ "$(stat -c '%u:%g' "$operator_file")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
      die "Installed operator configuration ownership mismatch."
  done
  [[ "$(stat -c '%u:%g' "$OPERATOR_CONFIG_DIR")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
    die "Installed operator configuration directory ownership mismatch."
  grep -Fx "TAILPLAN_BASE_URL=\"$BASE_URL\"" "$OPERATOR_ENV_FILE" >/dev/null ||
    die "Installed operator environment mismatch."
fi
if [[ "$INSTALL_SCOPE" == system ]]; then
  grep -Fx "User=$SERVICE_USER" "$UNIT_FILE" >/dev/null || die "Installed systemd unit user mismatch."
  grep -Fx "Group=$SERVICE_GROUP" "$UNIT_FILE" >/dev/null || die "Installed systemd unit group mismatch."
  grep -Fx "WantedBy=multi-user.target" "$UNIT_FILE" >/dev/null || die "Installed systemd target mismatch."
  validate_service_identity "$(getent group "$SERVICE_GROUP")" "$(getent passwd "$SERVICE_USER")"
  [[ "$(stat -c '%a' "$APP_DIR")" == 755 ]] || die "Installed app directory mode mismatch."
  [[ "$(stat -c '%a' "$APP_DIR/bin")" == 755 ]] || die "Installed app bin directory mode mismatch."
  [[ "$(stat -c '%u:%g' "$APP_DIR/tailplan_server.py")" == "0:0" ]] || die "Installed app ownership mismatch."
  [[ "$(stat -c '%u:%g' "$APP_DIR")" == "0:0" ]] || die "Installed app directory ownership mismatch."
  [[ "$(stat -c '%u:%g' "$APP_DIR/bin")" == "0:0" ]] || die "Installed app bin directory ownership mismatch."
  [[ "$(stat -c '%u:%g' "$BIN_DIR/tailplan-share")" == "0:0" ]] || die "Installed command ownership mismatch."
  [[ "$(stat -c '%u:%g' "$ENV_FILE")" == "0:0" ]] || die "Installed environment ownership mismatch."
  [[ "$(stat -c '%u:%g' "$UNIT_FILE")" == "0:0" ]] || die "Installed unit ownership mismatch."
  if [[ "$SKILLS_FOR_OPERATOR" == 1 ]]; then
    [[ "$(stat -c '%u:%g' "$SKILL_FILE")" == "$OPERATOR_UID:$OPERATOR_GID" ]] ||
      die "Installed operator skill ownership mismatch."
  else
    [[ "$(stat -c '%u:%g' "$SKILL_FILE")" == "0:0" ]] ||
      die "Installed skill ownership mismatch."
  fi
  [[ "$(stat -c '%u:%g' "$DATA_DIR")" == "$SERVICE_UID:$SERVICE_GID" ]] || die "Data ownership mismatch."
  [[ "$(stat -c '%u:%g' "$DATA_DIR/token")" == "$SERVICE_UID:$SERVICE_GID" ]] || die "Token ownership mismatch."
else
  grep -Fx "WantedBy=default.target" "$UNIT_FILE" >/dev/null || die "Installed user target mismatch."
  if grep -Eq '^(User|Group)=' "$UNIT_FILE"; then
    die "User service unit must not set a static identity."
  fi
fi

"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" enable tailplan.service >/dev/null
"${SYSTEMCTL[@]}" restart tailplan.service

verify_endpoint() {
  local url="$1" response="$STAGE_DIR/response.json" attempt
  for ((attempt = 1; attempt <= VERIFY_ATTEMPTS; attempt++)); do
    if curl -fsS --max-time 2 "$url" > "$response" 2>/dev/null &&
      python3 - "$response" "$EXPECTED_BUILD" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if payload.get("ok") is not True or payload.get("service") != "tailplan" or payload.get("build") != sys.argv[2]:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep "$VERIFY_INTERVAL"
  done
  die "Endpoint verification failed: $url"
}

verify_endpoint "http://${HOST_URL}:${PORT}/healthz"
verify_endpoint "http://${PROXY_URL_HOST}:${PROXY_PORT}/readyz"
"${SYSTEMCTL[@]}" is-enabled tailplan.service >/dev/null
"${SYSTEMCTL[@]}" is-active tailplan.service >/dev/null
verify_installed_file "$STAGE_DIR/bin/tailplan-share" "$BIN_DIR/tailplan-share" 755

if [[ "$CONFIGURE_SERVE" == 1 ]]; then
  # Set the mutation state before the command runs.
  # A failed command can change host state and require snapshot restoration.
  SERVE_MUTATED=1
  tailscale serve --bg --https=443 --set-path=/tailplan --yes "http://${PROXY_URL_HOST}:${PROXY_PORT}" >/dev/null
  tailscale serve status --json > "$STAGE_DIR/serve-status.json"
  python3 - "$STAGE_DIR/serve-status.json" "$TS_DNS" "http://${PROXY_URL_HOST}:${PROXY_PORT}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
dns_name, expected = sys.argv[2:]
handler = (
    config.get("Web", {})
    .get(f"{dns_name}:443", {})
    .get("Handlers", {})
    .get("/tailplan")
)
if handler != {"Proxy": expected}:
    raise SystemExit("Tailscale Serve /tailplan handler does not exactly match")
PY
  if [[ "$DEFER_HTTPS_VERIFY" == 0 ]]; then
    verify_endpoint "$BASE_URL/healthz"
    verify_endpoint "$BASE_URL/readyz"
  fi
fi

trap - ERR INT TERM HUP
if [[ "$DEFER_HTTPS_VERIFY" == 1 ]]; then
  printf 'Tailplan local deployment ready; external HTTPS verification required: %s\n' "$BASE_URL"
else
  printf 'Tailplan ready: %s\n' "$BASE_URL"
fi
printf 'Backup retained: %s\n' "$BACKUP_DIR"
printf 'Agent skill: %s\n' "$SKILL_FILE"
if [[ -n "$OPERATOR" ]]; then
  printf 'Operator configuration: %s\n' "$OPERATOR_CONFIG_DIR"
fi
