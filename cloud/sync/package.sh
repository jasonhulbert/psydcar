#!/bin/sh
set -eu

repo=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
output=${1:-"$repo/build/psydcar-sync.zip"}
case "$output" in
  /*) ;;
  *) output="$PWD/$output" ;;
esac

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT HUP INT TERM
python3 -m pip install --disable-pip-version-check --no-compile --only-binary=:all: \
  --platform manylinux2014_aarch64 --python-version 3.13 --target "$staging" -r "$repo/cloud/sync/requirements.txt"
mkdir -p "$staging/cloud/sync" "$staging/psydcar" "$(dirname -- "$output")"
touch "$staging/cloud/__init__.py" "$staging/cloud/sync/__init__.py" "$staging/psydcar/__init__.py"
cp "$repo/cloud/sync/handler.py" "$repo/cloud/sync/service.py" "$staging/cloud/sync/"
cp "$repo/src/psydcar/sync_protocol.py" "$staging/psydcar/"
python3 - "$staging" "$output" <<'PY'
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

root, output = map(Path, sys.argv[1:])
with ZipFile(output, "w") as archive:
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            info = ZipInfo(path.relative_to(root).as_posix())
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED)
PY
printf 'Packaged %s\n' "$output"
