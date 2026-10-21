#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

case "$(uname -s):$(uname -m)" in
    Darwin:arm64) target=macos-arm64 ;;
    Linux:x86_64) target=linux-x86_64 ;;
    *) echo "This release builder supports Apple silicon macOS and Linux x86-64 only." >&2; exit 1 ;;
esac

version=$(python3 -c 'from importlib.metadata import version; print(version("psydcar"))')
if [ "${RELEASE_TAG:-v$version}" != "v$version" ]; then
    echo "Release tag must match package version v$version." >&2
    exit 1
fi

export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-${TMPDIR:-/tmp}/psydcar-pyinstaller-config}"
python3 -m PyInstaller --clean --noconfirm --onedir --name psydcar --paths src \
    --copy-metadata psydcar --add-data "$(pwd)/src/psydcar/dashboard_assets:psydcar/dashboard_assets" \
    --distpath build/release-dist --workpath build/pyinstaller --specpath build \
    scripts/frozen_entry.py

asset="psydcar-v$version-$target"
stage="build/release-stage/$asset"
mkdir -p build/release-stage dist
rm -rf "$stage"
mkdir -p "$stage"
cp -R build/release-dist/psydcar "$stage/psydcar"
if [ "$target" = macos-arm64 ]; then
    python_library=
    for candidate in "$stage/psydcar/_internal/Python.framework/Versions"/*/Python \
        "$stage/psydcar/_internal"/libpython*.dylib; do
        [ -f "$candidate" ] || continue
        if [ -n "$python_library" ]; then
            echo "Release bundle contains more than one Python library." >&2
            exit 1
        fi
        python_library=$candidate
    done
    if [ -z "$python_library" ]; then
        echo "Release bundle is missing its Python library." >&2
        exit 1
    fi
    # A collected Python framework binary retains a signature for omitted resources.
    # Sign the staged binary as a standalone library.
    codesign --force --sign - --timestamp=none "$python_library"
    codesign --verify --strict "$python_library"
    codesign --verify --strict "$stage/psydcar/psydcar"
fi
cp scripts/install-release.sh "$stage/install.sh"
printf '%s\n' "$version" > "$stage/VERSION"
printf '%s\n' "$target" > "$stage/TARGET"
chmod 755 "$stage/install.sh"
tar -czf "dist/$asset.tar.gz" -C build/release-stage "$asset"
printf '%s\n' "dist/$asset.tar.gz"
