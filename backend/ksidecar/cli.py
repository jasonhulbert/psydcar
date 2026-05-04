"""Command-line entrypoint for Knowledge Sidecar."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ksidecar import __version__
from ksidecar.config import AppConfig
from ksidecar.paths import ensure_app_storage_root, sidecars_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ksidecar",
        description="Manage local Knowledge Sidecar indexes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    paths_parser = subparsers.add_parser(
        "paths",
        help="Show app-managed storage paths.",
    )
    paths_parser.add_argument(
        "--init",
        action="store_true",
        help="Create the app storage directories before printing paths.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "paths":
        config = AppConfig.load()
        storage_root = (
            ensure_app_storage_root(config.storage_root) if args.init else config.storage_root
        )
        print(f"storage_root={storage_root}")
        print(f"sidecars_root={sidecars_root(storage_root)}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
