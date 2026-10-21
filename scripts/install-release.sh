#!/bin/sh
set -eu

src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=$(cat "$src/VERSION")
target=$(cat "$src/TARGET")
case "$version" in
    *[!A-Za-z0-9._-]*|''|.|..) echo "Invalid release version." >&2; exit 1 ;;
esac
case "$target" in
    macos-arm64)
        if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
            echo "This asset requires Apple silicon macOS." >&2; exit 1
        fi
        major=$(sw_vers -productVersion | cut -d. -f1)
        if [ "$major" -lt 15 ]; then
            echo "This asset requires macOS 15 or newer." >&2; exit 1
        fi
        ;;
    linux-x86_64)
        if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
            echo "This asset requires Linux x86-64." >&2; exit 1
        fi
        libc=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
        case "$libc" in
            glibc\ *) libc_version=${libc#glibc } ;;
            *) echo "This asset requires glibc 2.35 or newer." >&2; exit 1 ;;
        esac
        if ! printf '%s\n' "$libc_version" | awk -F. 'NF >= 2 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && ($1 > 2 || ($1 == 2 && $2 >= 35)) { ok = 1 } END { exit !ok }'; then
            echo "This asset requires glibc 2.35 or newer." >&2; exit 1
        fi
        ;;
    *) echo "Unsupported release target: $target" >&2; exit 1 ;;
esac

if [ ! -x "$src/psydcar/psydcar" ]; then
    echo "Release archive is missing its Psydcar executable." >&2; exit 1
fi

app_root="$HOME/.local/opt/psydcar"
bin_dir="$HOME/.local/bin"
launcher="$bin_dir/psydcar"
release_dir="$app_root/releases/$version"
mkdir -p "$app_root/releases" "$bin_dir"
if [ -L "$launcher" ] || { [ -e "$launcher" ] && ! grep -q 'Installed Psydcar launcher' "$launcher" 2>/dev/null; }; then
    echo "Another file already exists at $launcher; move it before installing." >&2
    exit 1
fi
if [ -e "$app_root/current" ] && [ ! -L "$app_root/current" ]; then
    echo "Another file already exists at $app_root/current; move it before installing." >&2
    exit 1
fi

stage="$app_root/releases/.install-$version-$$"
trap 'rm -rf "$stage"' EXIT HUP INT TERM
cp -R "$src/psydcar" "$stage"
if [ -e "$release_dir" ]; then
    rm -rf "$release_dir"
fi
mv "$stage" "$release_dir"
trap - EXIT HUP INT TERM

ln -sfn "releases/$version" "$app_root/current"
cat > "$launcher" <<'LAUNCHER'
#!/bin/sh
# Installed Psydcar launcher. The current link changes on reinstall.
exec "$HOME/.local/opt/psydcar/current/psydcar" "$@"
LAUNCHER
chmod 755 "$launcher"

path_line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac # Psydcar release installer'
add_path() {
    profile=$1
    if [ ! -e "$profile" ]; then
        : > "$profile"
    fi
    if ! grep -Fq '# Psydcar release installer' "$profile"; then
        printf '\n%s\n' "$path_line" >> "$profile"
    fi
}
case "${SHELL:-/bin/zsh}" in
    */zsh) add_path "$HOME/.zprofile"; add_path "$HOME/.zshrc" ;;
    */bash) add_path "$HOME/.bash_profile"; add_path "$HOME/.bashrc" ;;
    *) add_path "$HOME/.profile" ;;
esac

echo "Installed Psydcar $version at $launcher. Open a new terminal and run: psydcar --version"
