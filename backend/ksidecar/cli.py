"""Command-line entrypoint for Knowledge Sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ksidecar import __version__
from ksidecar.config import AppConfig
from ksidecar.index import (
    IndexingErrorRecord,
    RebuildResult,
    RefreshResult,
    SearchResult,
    list_sidecar_indexing_errors,
    rebuild_sidecar_index,
    refresh_sidecar_index,
)
from ksidecar.mcp import McpError, parse_sidecar_ids, run_mcp_server
from ksidecar.paths import ensure_app_storage_root, sidecars_root
from ksidecar.search import DEFAULT_SEARCH_LIMIT, SearchError, search_sidecar
from ksidecar.sidecars import Sidecar, SidecarError, SidecarRegistry
from ksidecar.watcher import (
    DEFAULT_WATCH_DEBOUNCE_SECONDS,
    SidecarWatchService,
    WatcherError,
    WatchStatus,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ksidecar",
        description="Manage local Knowledge Sidecar indexes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    sidecar_parser = subparsers.add_parser(
        "sidecar",
        help="Create, list, and delete sidecars.",
    )
    sidecar_subparsers = sidecar_parser.add_subparsers(dest="sidecar_command")

    sidecar_create_parser = sidecar_subparsers.add_parser(
        "create",
        help="Register a local directory as a sidecar.",
    )
    sidecar_create_parser.add_argument("source_root", help="Local directory to index.")
    sidecar_create_parser.add_argument("--name", help="Display name for the sidecar.")
    sidecar_create_parser.add_argument("--id", dest="sidecar_id", help="Stable sidecar id.")
    add_json_option(sidecar_create_parser)

    sidecar_list_parser = sidecar_subparsers.add_parser(
        "list",
        help="List registered sidecars.",
    )
    add_json_option(sidecar_list_parser)

    sidecar_delete_parser = sidecar_subparsers.add_parser(
        "delete",
        help="Delete a sidecar's app-managed metadata and indexes.",
    )
    sidecar_delete_parser.add_argument("sidecar_id", help="Sidecar id to delete.")
    add_json_option(sidecar_delete_parser)

    rebuild_parser = subparsers.add_parser(
        "rebuild",
        help="Fully rebuild a sidecar index.",
    )
    rebuild_parser.add_argument("sidecar_id", help="Sidecar id to rebuild.")
    add_json_option(rebuild_parser)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Incrementally refresh a sidecar index.",
    )
    refresh_parser.add_argument("sidecar_id", help="Sidecar id to refresh.")
    add_json_option(refresh_parser)

    search_parser = subparsers.add_parser(
        "search",
        help="Search a sidecar index.",
    )
    search_parser.add_argument("sidecar_id", help="Sidecar id to search.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--mode",
        choices=("keyword", "semantic", "hybrid"),
        default="hybrid",
        help="Search mode to use.",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        help="Maximum result count.",
    )
    add_json_option(search_parser)

    status_parser = subparsers.add_parser(
        "status",
        help="Show sidecar indexing status.",
    )
    status_parser.add_argument("sidecar_id", help="Sidecar id to inspect.")
    add_json_option(status_parser)

    watch_parser = subparsers.add_parser(
        "watch",
        help="Watch sidecar source roots and refresh indexes after changes.",
        description="Watch sidecar source roots and refresh indexes after changes.",
    )
    watch_parser.add_argument(
        "sidecar_id",
        nargs="?",
        help="Sidecar id to watch. Omit to watch every registered sidecar.",
    )
    watch_parser.add_argument(
        "--debounce",
        type=float,
        default=DEFAULT_WATCH_DEBOUNCE_SECONDS,
        help="Debounce window in seconds.",
    )
    add_json_option(watch_parser)

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Start a read-only MCP server over stdio.",
        description="Start a read-only MCP server over stdio.",
    )
    mcp_parser.add_argument(
        "--sidecars",
        required=True,
        help="Comma-separated sidecar ids to expose, for example frontend,backend.",
    )

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

    try:
        return run_command(args, parser)
    except (OSError, SidecarError, SearchError, McpError, WatcherError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def run_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = AppConfig.load()
    registry = SidecarRegistry(config.storage_root)

    if args.command == "sidecar":
        if args.sidecar_command == "create":
            sidecar = registry.create(
                Path(args.source_root),
                name=args.name,
                sidecar_id=args.sidecar_id,
            )
            if args.json:
                print_json(sidecar)
            else:
                print(format_sidecar_created(sidecar))
            return 0

        if args.sidecar_command == "list":
            sidecars = registry.list()
            if args.json:
                print_json(sidecars)
            else:
                print(format_sidecar_list(sidecars))
            return 0

        if args.sidecar_command == "delete":
            registry.delete(args.sidecar_id)
            payload = {"deleted": args.sidecar_id}
            if args.json:
                print_json(payload)
            else:
                print(f"Deleted sidecar {args.sidecar_id}")
            return 0

    if args.command == "rebuild":
        result = rebuild_sidecar_index(registry, args.sidecar_id)
        if args.json:
            print_json(result)
        else:
            print(format_rebuild_result(args.sidecar_id, result))
        return 0

    if args.command == "refresh":
        result = refresh_sidecar_index(registry, args.sidecar_id)
        if args.json:
            print_json(result)
        else:
            print(format_refresh_result(args.sidecar_id, result))
        return 0

    if args.command == "search":
        results = search_sidecar(
            registry,
            args.sidecar_id,
            args.query,
            mode=args.mode,
            limit=args.limit,
        )
        if args.json:
            print_json(results)
        else:
            print(format_search_results(results))
        return 0

    if args.command == "status":
        sidecar = registry.get(args.sidecar_id)
        errors = list_sidecar_indexing_errors(registry, args.sidecar_id)
        payload = {"sidecar": sidecar, "errors": errors}
        if args.json:
            print_json(payload)
        else:
            print(format_status(sidecar, error_count=len(errors)))
        return 0

    if args.command == "watch":
        service = SidecarWatchService(registry, debounce_seconds=args.debounce)
        sidecar_ids = (
            [args.sidecar_id] if args.sidecar_id else [sidecar.id for sidecar in registry.list()]
        )
        if not sidecar_ids:
            print("No sidecars registered.")
            return 0
        if args.json:
            statuses = [service.start(sidecar_id) for sidecar_id in sidecar_ids]
            print_json(statuses)
            service.stop_all()
            return 0
        print(f"Watching {', '.join(sidecar_ids)}. Press Ctrl+C to stop.")
        service.run_until_interrupted(sidecar_ids)
        return 0

    if args.command == "mcp":
        run_mcp_server(registry, parse_sidecar_ids(args.sidecars))
        return 0

    if args.command == "paths":
        storage_root = (
            ensure_app_storage_root(config.storage_root) if args.init else config.storage_root
        )
        print(f"storage_root={storage_root}")
        print(f"sidecars_root={sidecars_root(storage_root)}")
        return 0

    parser.print_help()
    return 0


def add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )


def print_json(value: Any) -> None:
    print(json.dumps(to_jsonable(value), indent=2, sort_keys=True))


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Sidecar):
        return value.to_json_dict()
    if isinstance(
        value,
        RebuildResult | RefreshResult | SearchResult | IndexingErrorRecord | WatchStatus,
    ):
        return {key: to_jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def format_sidecar_created(sidecar: Sidecar) -> str:
    return "\n".join(
        [
            f"Created sidecar {sidecar.id}",
            f"name={sidecar.name}",
            f"root_path={sidecar.root_path}",
            f"status={sidecar.indexing_status}",
        ]
    )


def format_sidecar_list(sidecars: list[Sidecar]) -> str:
    if not sidecars:
        return "No sidecars registered."
    rows = ["ID\tName\tStatus\tRoot"]
    rows.extend(
        f"{sidecar.id}\t{sidecar.name}\t{sidecar.indexing_status}\t{sidecar.root_path}"
        for sidecar in sidecars
    )
    return "\n".join(rows)


def format_rebuild_result(sidecar_id: str, result: RebuildResult) -> str:
    return "\n".join(
        [
            f"Rebuilt sidecar {sidecar_id}",
            f"documents={result.document_count}",
            f"chunks={result.chunk_count}",
            f"errors={result.error_count}",
        ]
    )


def format_refresh_result(sidecar_id: str, result: RefreshResult) -> str:
    return "\n".join(
        [
            f"Refreshed sidecar {sidecar_id}",
            f"documents={result.document_count}",
            f"chunks={result.chunk_count}",
            f"errors={result.error_count}",
            f"new={result.new_count}",
            f"modified={result.modified_count}",
            f"unchanged={result.unchanged_count}",
            f"deleted={result.deleted_count}",
        ]
    )


def format_search_results(results: list[Any]) -> str:
    if not results:
        return "No results."
    blocks: list[str] = []
    for result in results:
        blocks.append(
            "\n".join(
                [
                    f"{result.relative_path}:{result.start_line}-{result.end_line}",
                    f"score={result.score:.4f} chunk_id={result.chunk_id}",
                    result.preview,
                ]
            )
        )
    return "\n\n".join(blocks)


def format_status(sidecar: Sidecar, *, error_count: int) -> str:
    last_refresh_at = sidecar.last_refresh_at.isoformat() if sidecar.last_refresh_at else "never"
    return "\n".join(
        [
            f"id={sidecar.id}",
            f"name={sidecar.name}",
            f"root_path={sidecar.root_path}",
            f"status={sidecar.indexing_status}",
            f"last_refresh_at={last_refresh_at}",
            f"indexed_files={sidecar.indexed_file_count}",
            f"chunks={sidecar.chunk_count}",
            f"errors={sidecar.error_count}",
            f"stored_errors={error_count}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
