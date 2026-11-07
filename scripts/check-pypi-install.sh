#!/bin/sh
set -eu

tag=$1
version=${tag#v}
test "$tag" = "v$version"

# PyPI's index can lag a few minutes behind an upload, so retry the pinned install.
attempt=1
until python -m pipx install "psydcar==$version"; do
    if [ "$attempt" -ge 30 ]; then
        echo "psydcar $version could not be installed from PyPI" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 10
done
PATH="$HOME/.local/bin:$PATH"
export PATH

test "$(command -v psydcar)" = "$HOME/.local/bin/psydcar"
test "$(psydcar --version)" = "psydcar $version"
psydcar --help >/dev/null

work=$(mktemp -d)
dashboard_pid=
cleanup() {
    if [ -n "$dashboard_pid" ]; then
        kill "$dashboard_pid" 2>/dev/null || true
        wait "$dashboard_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM

store="$work/store"
mkdir -p "$work/source" "$work/private"
printf 'Release install keeps this searchable note.\n' > "$work/source/note.md"
printf 'This collection contains privateonly text.\n' > "$work/private/note.md"
export PSYDCAR_HOME="$store"
psydcar sidecar create 'Release test' "$work/source"
psydcar sidecar index 'Release test'
psydcar sidecar create 'Private test' "$work/private"
psydcar sidecar index 'Private test'
sidecar_id=$(psydcar sidecar show 'Release test' | sed -n 's/^ID: //p')
test -n "$sidecar_id"
psydcar search --sidecar "$sidecar_id" 'searchable note' | grep -q 'note.md'

psydcar dashboard --port 0 --no-open > "$work/dashboard.log" 2>&1 &
dashboard_pid=$!
dashboard_url=
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    dashboard_url=$(sed -n 's/^Dashboard: //p' "$work/dashboard.log" | head -1)
    [ -n "$dashboard_url" ] && break
    sleep 1
done
if [ -z "$dashboard_url" ]; then
    cat "$work/dashboard.log" >&2
    exit 1
fi
curl -fsS "$dashboard_url" > "$work/dashboard.html"
curl -fsS "$dashboard_url/assets/dashboard.js" > "$work/dashboard.js"
curl -fsS "$dashboard_url/assets/dashboard.css" > "$work/dashboard.css"
grep -q '<title>Sidecars' "$work/dashboard.html"
grep -q 'sidecar' "$work/dashboard.js"
grep -q 'sidebar' "$work/dashboard.css"

python scripts/check-release-mcp.py save \
    "$dashboard_url" "$HOME" "$store" "$sidecar_id" "$work/connection.json"
kill "$dashboard_pid"
wait "$dashboard_pid" 2>/dev/null || true
dashboard_pid=
python scripts/check-release-mcp.py check \
    "$work/connection.json" "$HOME" "$store" "$sidecar_id"
