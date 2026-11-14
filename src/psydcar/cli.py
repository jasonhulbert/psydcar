"""Human-facing commands for local sidecar management."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from .access import connect_agent
from .embeddings import ProviderStore
from .dashboard import run_dashboard
from .evidence import EvidenceStore, IndexedSidecar
from .freshness import SidecarWatcher, WatchEvent
from .mcp_server import run_mcp
from .proposals import Proposals
from .registry import SidecarStore, StoreError


def _display_status(status: str) -> str:
    return status.replace("_", " ").capitalize()


def _details(sidecar: IndexedSidecar) -> str:
    return "\n".join(
        (
            f"Name: {sidecar.name}",
            f"ID: {sidecar.id}",
            f"Source: {sidecar.source_path}",
            f"Status: {_display_status(sidecar.status)}",
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psydcar", description="Manage local knowledge sidecars.")
    try:
        installed_version = version("psydcar")
    except PackageNotFoundError:
        installed_version = "development"
    parser.add_argument("--version", action="version", version=f"psydcar {installed_version}")
    commands = parser.add_subparsers(dest="command", required=True)
    sidecar = commands.add_parser("sidecar", help="Create and manage sidecars")
    actions = sidecar.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create", help="Create a sidecar from one local directory tree")
    create.add_argument("name", help="Unique sidecar name")
    create.add_argument("source", help="Existing source directory")

    actions.add_parser("list", help="List managed sidecars")

    show = actions.add_parser("show", help="Inspect a sidecar")
    show.add_argument("reference", help="Sidecar name or ID")

    rename = actions.add_parser("rename", help="Change a sidecar name without changing its ID")
    rename.add_argument("reference", help="Sidecar name or ID")
    rename.add_argument("new_name", help="New unique name")

    remove = actions.add_parser("remove", help="Remove a sidecar record; source files remain in place")
    remove.add_argument("reference", help="Sidecar name or ID")

    index = actions.add_parser("index", help="Build or refresh a sidecar index")
    index.add_argument("reference", help="Sidecar name or ID")

    update = actions.add_parser("update", help="Refresh added, changed, and deleted source files")
    update.add_argument("reference", help="Sidecar name or ID")

    proposal = commands.add_parser("proposal", help="Review edits that agents proposed")
    proposal_actions = proposal.add_subparsers(dest="proposal_action", required=True)
    proposal_list = proposal_actions.add_parser("list", help="List proposals waiting for review")
    proposal_list.add_argument("sidecar", nargs="?", metavar="SIDECAR", help="Sidecar name or ID (default: every sidecar)")
    proposal_list.add_argument("--reviewed", action="store_true", help="List recent decisions instead")
    for action, description in (
        ("show", "Show a proposal with its diff"),
        ("approve", "Write a proposal to its file and update the search index"),
        ("reject", "Reject a proposal; the file is not changed"),
    ):
        proposal_actions.add_parser(action, help=description).add_argument(
            "proposal", metavar="ID", help="Proposal ID, or its first characters as shown by 'proposal list'",
        )

    provider = commands.add_parser("provider", help="Configure and inspect embedding access")
    provider_actions = provider.add_subparsers(dest="provider_action", required=True)
    configure = provider_actions.add_parser("set", help="Configure an OpenAI-compatible embeddings route")
    configure.add_argument("--base-url", required=True, help="Base URL ending before /embeddings")
    configure.add_argument("--model", required=True, help="Embedding model name")
    configure.add_argument("--api-key-env", help="Environment variable containing the API key")
    configure.add_argument("--allow-insecure-http", action="store_true", help="Allow remote HTTP without TLS")
    provider_actions.add_parser("show", help="Show provider settings without credential values")
    provider_actions.add_parser("clear", help="Remove provider settings and stored embeddings")

    search = commands.add_parser("search", help="Search explicitly selected sidecars; output JSON evidence")
    search.add_argument("--sidecar", action="append", required=True, metavar="NAME_OR_ID", help="Select a sidecar; repeat to search several")
    search.add_argument("--limit", type=int, default=10, metavar="N", help="Maximum results (1–100; default 10)")
    search.add_argument("query", help="Words to find across indexed text, code, and PDFs")

    retrieve = commands.add_parser("retrieve", help="Read current source for evidence in selected sidecars")
    retrieve.add_argument("--sidecar", action="append", required=True, metavar="NAME_OR_ID", help="Select a sidecar; repeat to allow several")
    retrieve.add_argument("evidence_id", help="Evidence ID returned by search")

    watch = commands.add_parser("watch", help="Follow sidecar sources and reconcile changes after restart")
    watch.add_argument("--interval", type=float, default=1.0, metavar="SECONDS", help="Source check interval (default 1 second)")
    watch.add_argument("--reconcile-interval", type=float, default=60.0, metavar="SECONDS", help="Full reconciliation interval (default 60 seconds)")
    watch.add_argument("--once", action="store_true", help="Reconcile all sidecars once and exit")

    dashboard = commands.add_parser("dashboard", help="Open the local sidecar management dashboard")
    dashboard.add_argument("--port", type=int, default=8765, metavar="PORT", help="Local port (default 8765; 0 chooses a free port)")
    dashboard.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")

    mcp = commands.add_parser("mcp", help="Serve selected sidecars to one MCP client over stdio")
    mcp.add_argument("--sidecar-id", action="append", default=[], metavar="ID", help="Stable sidecar ID to expose; repeat to select several")
    mcp.add_argument("--propose-sidecar-id", action="append", default=[], metavar="ID",
                     help="Allow proposals for this selected sidecar; repeat for each grant")
    mcp.add_argument("--agent-name", metavar="NAME", help="Name shown with this connection's proposals")
    return parser


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(row[index]) for row in [headers, *rows]) for index in range(len(headers))]
    for row in [headers, *rows]:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())


def _short_time(value: str | None) -> str:
    """Show a stored UTC timestamp in local time, to the minute."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _proposal_command(store: SidecarStore, args: argparse.Namespace) -> int:
    proposals = Proposals(store)
    if args.proposal_action == "list":
        sidecars = [store.get(args.sidecar)] if args.sidecar else store.list()
        rows = []
        for sidecar in sidecars:
            for item in proposals.list(sidecar.id, reviewed=args.reviewed)["items"]:
                rows.append((item, sidecar.name))
        if not rows:
            scope = f' in "{sidecars[0].name}"' if args.sidecar else ""
            print(f"No reviewed proposals{scope} yet." if args.reviewed else f"No proposals{scope} are waiting for review.")
            return 0
        if args.reviewed:
            rows.sort(key=lambda pair: pair[0]["decided_at"] or "", reverse=True)
            _table(("ID", "SIDECAR", "FILE", "AGENT", "DECISION", "DECIDED", "REASON"), [
                (item["id"][:8], name, item["path"], item["agent_name"], item["status"].capitalize(),
                 _short_time(item["decided_at"]), item["summary"]) for item, name in rows
            ])
        else:
            rows.sort(key=lambda pair: pair[0]["created_at"])
            _table(("ID", "SIDECAR", "FILE", "AGENT", "PROPOSED", "STATUS", "REASON"), [
                (item["id"][:8], name, item["path"], item["agent_name"], _short_time(item["created_at"]),
                 "Out of date" if item["stale"] else "Pending", item["summary"])
                for item, name in rows
            ])
            print("\nShow one with 'psydcar proposal show ID', then approve or reject it.")
        return 0
    found = proposals.find(args.proposal)
    sidecar = store.get(found["sidecar_id"], exact_id=True)
    if args.proposal_action == "show":
        detail = proposals.detail(sidecar.id, found["id"])
        status = "Out of date" if detail["stale"] else detail["status"].capitalize()
        print("\n".join((
            f"Proposal: {detail['id']}",
            f"Sidecar: {sidecar.name}",
            f"File: {detail['path']}",
            f"Agent: {detail['agent_name']}",
            f"Reason: {detail['summary']}",
            f"Proposed: {_short_time(detail['created_at'])}",
            f"Status: {status}",
        )))
        if detail["decided_at"]:
            print(f"Decided: {_short_time(detail['decided_at'])}")
        if detail["stale"]:
            print(f"Out of date: {detail['stale_message']}")
        if detail["diff"] is not None:
            print(f"\n{detail['diff']}", end="")
        return 0
    decision = "approved" if args.proposal_action == "approve" else "rejected"
    result = proposals.review(sidecar.id, found["id"], decision)
    if decision == "rejected":
        print(f'Rejected proposal {result["id"][:8]} for {result["path"]} in "{sidecar.name}". The file was not changed.')
        return 0
    if "index_notice" not in result:
        print(f'Proposal {result["id"][:8]} was already approved.')
        return 0
    notice = result["index_notice"]
    print(f'Approved proposal {result["id"][:8]}. Wrote {result["path"]} in "{sidecar.name}"'
          + ("." if notice else " and updated the search index."), flush=True)
    if notice:
        print(notice, file=sys.stderr)
    return 0


def _watch_event(event: WatchEvent) -> None:
    if event.error:
        print(f'Watch {event.reason} for "{event.sidecar_name or "store"}": {event.error}', file=sys.stderr, flush=True)
        return
    assert event.report is not None
    report = event.report
    print(
        f'Watch {event.reason}: "{report.sidecar.name}". {report.added} added, '
        f'{report.changed} changed, {report.deleted} deleted; '
        f'status {_display_status(report.sidecar.status)}.',
        flush=True,
    )
    for issue in (
        *report.skipped,
        *([report.provider_error] if report.provider_error else []),
        *(f"Removed source: {path}" for path in report.deleted_paths),
    ):
        print(f'Needs attention: {issue}', file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        store = SidecarStore()
        if args.command == "sidecar":
            if args.action == "create":
                sidecar = EvidenceStore(store).sidecar(store.create(args.name, args.source).id, exact_id=True)
                print(f'Created sidecar "{sidecar.name}".\n{_details(sidecar)}')
            elif args.action == "list":
                sidecars = EvidenceStore(store).list()
                if not sidecars:
                    print("No sidecars yet. Use 'psydcar sidecar create NAME DIRECTORY' to add one.")
                else:
                    headers = ("NAME", "ID", "SOURCE", "STATUS")
                    rows = [(item.name, item.id, str(item.source_path), _display_status(item.status)) for item in sidecars]
                    widths = [max(len(str(row[index])) for row in [headers, *rows]) for index in range(4)]
                    for row in [headers, *rows]:
                        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)).rstrip())
            elif args.action == "show":
                evidence_store = EvidenceStore(store)
                sidecar = evidence_store.sidecar(args.reference)
                print(_details(sidecar))
                update_state = evidence_store.update_state(sidecar.id)
                state_label = {
                    "current": "Current", "pending": "Pending", "attention": "Needs attention",
                    "not_indexed": "Not indexed",
                }[update_state["state"]]
                print(f"Update state: {state_label}")
                if update_state["started_at"]:
                    print(f'Last update started: {update_state["started_at"]}')
                    print(f'Last update completed: {update_state["completed_at"] or "Pending"}')
                    print(f'Last successful update: {update_state["last_success_at"] or "None"}')
                    print(
                        f'Changes: {update_state["added"]} added, {update_state["changed"]} changed, '
                        f'{update_state["deleted"]} deleted, {update_state["unchanged"]} unchanged'
                    )
                    for issue in update_state["issues"]:
                        print(f"Needs attention: {issue}")
                provider = ProviderStore(store)
                state = provider.sidecar_state(sidecar.id)
                if provider.get() is None:
                    print("Embeddings: Not configured")
                elif state is None:
                    print("Embeddings: Pending (reindex this sidecar)")
                else:
                    print(f'Embeddings: {state["status"].capitalize()} ({state["embedded_count"]}/{state["expected_count"]})')
                    if state["message"]:
                        print(f'Embedding note: {state["message"]}')
                for notice in evidence_store.pdf_page_notices(sidecar.id):
                    print(f'PDF page: {notice["source"]}, page {notice["page"]}: {notice["message"]}')
            elif args.action == "rename":
                sidecar = EvidenceStore(store).sidecar(store.rename(args.reference, args.new_name).id, exact_id=True)
                print(f'Renamed sidecar to "{sidecar.name}".\n{_details(sidecar)}')
            elif args.action == "remove":
                sidecar = EvidenceStore(store).remove(args.reference)
                print(f'Removed sidecar "{sidecar.name}" (ID: {sidecar.id}). Source files were not changed.')
            elif args.action in {"index", "update"}:
                report = EvidenceStore(store).update(args.reference)
                if args.action == "index":
                    print(f'Indexed {report.files} files and {report.passages} passages in "{report.sidecar.name}".')
                else:
                    print(
                        f'Updated "{report.sidecar.name}": scanned {report.files} readable files; '
                        f'the index contains {report.passages} passages.'
                    )
                print(
                    f'Changes: {report.added} added, {report.changed} changed, '
                    f'{report.deleted} deleted, {report.unchanged} unchanged.'
                )
                if report.embedding_status == "not_configured":
                    print("Embeddings: Not configured; lexical index is available.")
                else:
                    print(f"Embeddings: {report.embedding_status.capitalize()} ({report.embeddings}/{report.passages}).")
                if report.provider_error:
                    print(f"Embedding error: {report.provider_error}", file=sys.stderr)
                for notice in report.pdf_page_notices:
                    print(f"PDF page: {notice}")
                for reason in report.skipped:
                    print(f"Skipped: {reason}", file=sys.stderr)
                for path in report.deleted_paths:
                    print(f"Removed source: {path}")
                if report.skipped:
                    print("Some files could not be refreshed. Sidecar status: Index error; update needs attention.", file=sys.stderr)
                elif report.deleted_paths:
                    print("Sidecar status: Source attention. Review removed sources in 'sidecar show'.")
                if report.provider_error:
                    print("Embedding processing is incomplete; lexical evidence remains available.", file=sys.stderr)
                if report.skipped or report.provider_error:
                    return 1
        elif args.command == "proposal":
            return _proposal_command(store, args)
        elif args.command == "provider":
            provider = ProviderStore(store)
            if args.provider_action == "set":
                config = provider.set(args.base_url, args.model, args.api_key_env, args.allow_insecure_http)
                print(f"Embedding provider configured.\nBase URL: {config.base_url}\nModel: {config.model}")
                print(f"Credential: {config.api_key_env or 'None (gateway without authentication)'}")
                print("Reindex sidecars to build embeddings.")
            elif args.provider_action == "show":
                config = provider.get()
                if config is None:
                    print("Embedding provider: Not configured")
                else:
                    print(f"Embedding provider: Configured\nBase URL: {config.base_url}\nModel: {config.model}")
                    if config.api_key_env:
                        print(f"Credential environment variable: {config.api_key_env}")
                        print(f"Credential available: {'Yes' if config.credential_available else 'No'}")
                    else:
                        print("Credential: None (gateway without authentication)")
            elif args.provider_action == "clear":
                provider.clear()
                print("Embedding provider cleared. Lexical evidence remains available.")
        elif args.command == "search":
            evidence = EvidenceStore(store)
            results = evidence.search(args.sidecar, args.query, args.limit)
            print(json.dumps(results, ensure_ascii=False, indent=2))
            if evidence.last_search_warning:
                print(f"Search notice: {evidence.last_search_warning}", file=sys.stderr)
        elif args.command == "retrieve":
            result = EvidenceStore(store).retrieve(args.sidecar, args.evidence_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "watch":
            watcher = SidecarWatcher(store, args.interval, args.reconcile_interval)
            if args.once:
                events = watcher.step()
                for event in events:
                    _watch_event(event)
                return int(any(event.error or (event.report and (event.report.skipped or event.report.provider_error)) for event in events))
            stop = threading.Event()
            prior_handler = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
            print("Watching sidecar sources. Press Ctrl-C to stop.", flush=True)
            try:
                watcher.run(stop, _watch_event)
            except KeyboardInterrupt:
                stop.set()
            finally:
                signal.signal(signal.SIGTERM, prior_handler)
        elif args.command == "dashboard":
            run_dashboard(store, args.port, not args.no_open)
        elif args.command == "mcp":
            access = connect_agent(store, args.sidecar_id, proposal_ids=args.propose_sidecar_id, agent_name=args.agent_name)
            run_mcp(store, access)
    except StoreError as exc:
        print(f"psydcar: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
