"""Temporary workspace for the app's successful browser journeys."""
from pathlib import Path
import socket
from tempfile import TemporaryDirectory

import uvicorn

from psydcar.api import create_app
from psydcar.core.proposals import Proposals
from psydcar.store import SidecarStore


def main() -> None:
    with TemporaryDirectory(prefix="psydcar-ui-") as scratch:
        root = Path(scratch)
        source = root / "notes"
        source.mkdir()
        content = "---\ntags: [product]\n---\n\n# Notebook\n\nFirst paragraph.\nSecond paragraph.\n\n- First item\n- Second item\n\n| Name | Value |\n| --- | --- |\n| Alpha | Beta |\n"
        (source / "notes.md").write_text(content, encoding="utf-8")
        (source / "proposal.md").write_text("# Decision\n\nOriginal decision.\n", encoding="utf-8")
        (source / "large.md").write_text("# Research\n\n" + ("A paragraph of research and useful context. " * 8 + "\n\n") * 3000, encoding="utf-8")
        store = SidecarStore(root / "state" / "psydcar.sqlite3")
        sidecar = store.create("Product knowledge", source)
        proposals = Proposals(store)
        opened = proposals.workspace.files(sidecar.id).read("proposal.md")
        proposals.submit(sidecar.id, "proposal.md", opened["version"], "# Decision\n\nUpdated decision.\n", "Clarify the decision", "Test agent")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 8789))
            app = create_app(store, 8789, watch=False)
            uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    main()
