#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./install-client.sh [--uninstall]

Install the tokenless OpenSSH client, Tailplan UI package, and agent skill.
Use --uninstall to remove only the installed client files.
EOF
}

ACTION=install
while (($#)); do
  case "$1" in
    --uninstall) ACTION=uninstall ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${TAILPLAN_BIN_DIR:-$HOME/.local/bin}"
SKILLS_ROOT="${TAILPLAN_SKILLS_ROOT:-$HOME/.agents/skills}"
SHARE_DIR="${TAILPLAN_SHARE_DIR:-$HOME/.local/share/tailplan}"
CLIENT_PATH="$BIN_DIR/tailplan-share"
SKILL_PATH="$SKILLS_ROOT/tailplan/SKILL.md"
UI_DIR="$SHARE_DIR/tailplan_ui"

if [[ "$ACTION" == uninstall ]]; then
  rm -f -- "$CLIENT_PATH" "$BIN_DIR/tailplan" "$BIN_DIR/tailplan-publisher" "$SKILL_PATH"
  rm -rf -- "$UI_DIR"
  rmdir -- "$SKILLS_ROOT/tailplan" 2>/dev/null || true
  rmdir -- "$SHARE_DIR" 2>/dev/null || true
  printf 'Removed Tailplan client: %s\n' "$CLIENT_PATH"
  printf 'Removed Tailplan UI package: %s\n' "$UI_DIR"
  printf 'Removed Tailplan agent skill: %s\n' "$SKILL_PATH"
  exit 0
fi

for command in install python3 scp ssh; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command is not available: %s\n' "$command" >&2
    exit 1
  }
done

[[ -f "$SCRIPT_DIR/bin/tailplan-share-remote" ]] || {
  printf 'Client source is missing: %s\n' "$SCRIPT_DIR/bin/tailplan-share-remote" >&2
  exit 1
}
[[ -f "$SCRIPT_DIR/skills/tailplan/SKILL.md" ]] || {
  printf 'Skill source is missing: %s\n' "$SCRIPT_DIR/skills/tailplan/SKILL.md" >&2
  exit 1
}
for source in bin/tailplan bin/tailplan-share tailplan_ui/__init__.py tailplan_ui/bundler.py tailplan_ui/registry.json; do
  [[ -f "$SCRIPT_DIR/$source" ]] || { printf 'Client source is missing: %s\n' "$source" >&2; exit 1; }
done

install -d "$BIN_DIR" "$SKILLS_ROOT/tailplan" "$SHARE_DIR"
install -m 755 "$SCRIPT_DIR/bin/tailplan-share-remote" "$CLIENT_PATH"
install -m 755 "$SCRIPT_DIR/bin/tailplan" "$BIN_DIR/tailplan"
install -m 755 "$SCRIPT_DIR/bin/tailplan-share" "$BIN_DIR/tailplan-publisher"
install -m 644 "$SCRIPT_DIR/skills/tailplan/SKILL.md" "$SKILL_PATH"

rm -rf -- "$UI_DIR"
cp -a "$SCRIPT_DIR/tailplan_ui" "$UI_DIR"
find "$UI_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$UI_DIR" -type f -name '*.pyc' -delete
find "$UI_DIR" -type d -exec chmod 755 {} +
find "$UI_DIR" -type f -exec chmod 644 {} +
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SHARE_DIR" python3 -c 'import tailplan_ui; tailplan_ui.catalog(); tailplan_ui.bundler.chunks()'

printf 'Installed Tailplan client: %s\n' "$CLIENT_PATH"
printf 'Installed Tailplan UI package: %s\n' "$UI_DIR"
printf 'Installed Tailplan agent skill: %s\n' "$SKILL_PATH"
printf 'Configure the tailplan-server SSH alias before you publish.\n'
