#!/bin/sh
# Run the browser lane in the official Playwright image (Ubuntu 24.04, as in CI).
# Use it on hosts where `playwright install --with-deps` cannot work, such as Arch.
# Usage: scripts/browser-container.sh [chromium|webkit] [extra pytest args]
set -eu

browser=${1:-webkit}
[ "$#" -gt 0 ] && shift
root=$(cd "$(dirname "$0")/.." && pwd)
engine=${CONTAINER_ENGINE:-docker}
version=$(awk '/^name = "playwright"$/ { getline; gsub(/"/, "", $3); print $3; exit }' "$root/uv.lock")
[ -n "$version" ] || { echo "playwright version not found in uv.lock" >&2; exit 1; }

# Rootless Podman maps --user to a subordinate id; keep-id maps it to the host user.
userns=
case $(basename "$engine") in podman*) userns=--userns=keep-id ;; esac

out="$root/test-results/browser-$browser"
rm -rf "$out"
mkdir -p "$out"

# The source mount is read-only, so the container builds from a private copy.
# shellcheck disable=SC2016,SC2086 # The inner script expands its own variables; $userns may be empty.
exec $engine run --rm --ipc=host $userns --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv -e TAILPLAN_BROWSER_TESTS=1 \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -v "$root:/src:ro" -v "$out:/out" \
  "mcr.microsoft.com/playwright/python:v$version-noble" \
  sh -euc '
    browser=$1; shift
    mkdir /tmp/w
    tar -C /src --exclude=./.venv --exclude=./.git --exclude=./test-results \
      --exclude="*.egg-info" -cf - . | tar -C /tmp/w -xf -
    cd /tmp/w
    pip install -q --disable-pip-version-check --user uv
    export PATH=/tmp/.local/bin:$PATH
    uv sync -q --locked --extra oidc
    uv run --no-sync python -m pytest -q -p no:cacheprovider tests/test_theme_browser.py tests/test_duo_themes.py tests/test_oidc_browser.py \
      --browser "$browser" --tracing retain-on-failure --screenshot only-on-failure \
      --output /out --junitxml=/out/browser.xml "$@"
    uv run --no-sync python tests/check_junit.py /out/browser.xml --minimum 45
  ' sh "$browser" "$@"
