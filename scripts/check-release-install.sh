#!/bin/sh
set -eu

target=$1
set -- dist/psydcar-*-"$target".tar.gz
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Expected one $target release archive." >&2
    exit 1
fi

work=$(mktemp -d)
cleanup() {
    if [ -n "${dashboard_pid:-}" ]; then
        kill "$dashboard_pid" 2>/dev/null || true
        wait "$dashboard_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM
tar -xzf "$1" -C "$work"
set -- "$work"/psydcar-*/install.sh
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Release archive is missing one installer." >&2
    exit 1
fi
installer=$1
version=$(cat "$(dirname "$installer")/VERSION")
test "$(cat "$(dirname "$installer")/TARGET")" = "$target"

case "$target" in
    macos-arm64) shell=/bin/zsh ;;
    linux-x86_64) shell=/bin/bash ;;
    *) echo "Unsupported test target: $target" >&2; exit 1 ;;
esac
home="$work/home"
store="$work/store"
mkdir -p "$home" "$work/source" "$work/private"
printf 'Release install keeps this searchable note.\n' > "$work/source/note.md"
printf 'This collection contains privateonly text.\n' > "$work/private/note.md"
HOME="$home" SHELL="$shell" PSYDCAR_HOME="$store" sh "$installer"
launcher="$home/.local/bin/psydcar"
test -x "$launcher"
test "$(HOME="$home" PSYDCAR_HOME="$store" "$launcher" --version)" = "psydcar $version"
test "$(env -i HOME="$home" SHELL="$shell" PSYDCAR_HOME="$store" "$shell" -l -c 'command -v psydcar')" = "$launcher"
test "$(env -i HOME="$home" SHELL="$shell" PSYDCAR_HOME="$store" "$shell" -l -c 'psydcar --version')" = "psydcar $version"

HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar create 'Release test' "$work/source"
HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar index 'Release test'
HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar create 'Private test' "$work/private"
HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar index 'Private test'
sidecar_id=$(HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar show 'Release test' | sed -n 's/^ID: //p')
test -n "$sidecar_id"
HOME="$home" PSYDCAR_HOME="$store" "$launcher" search --sidecar "$sidecar_id" 'searchable note' | grep -q 'note.md'

HOME="$home" PSYDCAR_HOME="$store" "$launcher" dashboard --port 0 --no-open > "$work/dashboard.log" 2>&1 &
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
python3 scripts/check-release-mcp.py save "$dashboard_url" "$home" "$store" "$sidecar_id" "$work/connection.json"
kill "$dashboard_pid"
wait "$dashboard_pid" 2>/dev/null || true
dashboard_pid=
python3 scripts/check-release-mcp.py check "$work/connection.json" "$home" "$store" "$sidecar_id"

previous="$home/.local/opt/psydcar/releases/previous"
mkdir -p "$previous"
printf '#!/bin/sh\nprintf "psydcar previous\\n"\n' > "$previous/psydcar"
chmod 755 "$previous/psydcar"
ln -sfn releases/previous "$home/.local/opt/psydcar/current"
test "$(HOME="$home" "$launcher" --version)" = 'psydcar previous'
HOME="$home" SHELL="$shell" PSYDCAR_HOME="$store" sh "$installer"
test "$(HOME="$home" PSYDCAR_HOME="$store" "$launcher" --version)" = "psydcar $version"
test "$(HOME="$home" PSYDCAR_HOME="$store" "$launcher" sidecar show "$sidecar_id" | sed -n 's/^ID: //p')" = "$sidecar_id"
test "$(readlink "$home/.local/opt/psydcar/current")" = "releases/$version"
test -f "$store/psydcar.sqlite3"
python3 scripts/check-release-mcp.py check "$work/connection.json" "$home" "$store" "$sidecar_id"
