# Psydcar

Psydcar makes a local directory of notes, code, and searchable PDFs available to your coding agents. Each **sidecar** has one source directory, a name, and a stable ID. Psydcar stores an index for one OS user; it does not move or edit the source files. The same sidecar can serve several projects and tools.

## Install on an Apple silicon Mac

The interim release supports Apple silicon Macs running macOS 15 or newer. Install [Homebrew](https://brew.sh/) if it is not already available, then install pyenv and pipx. Pyenv provides Python 3.11.14 for Psydcar without changing your selected Python version:

```console
brew install pyenv pipx
pyenv install -s 3.11.14
pipx ensurepath
```

Open a new terminal so pipx's command directory is on `PATH`. Install the package from PyPI using the pyenv Python:

```console
pipx install --python "$(pyenv prefix 3.11.14)/bin/python3.11" psydcar
command -v psydcar
psydcar --version
psydcar --help
```

`command -v psydcar` should print `/Users/YOUR_NAME/.local/bin/psydcar`. If it reports another path, inspect that command before continuing. The pipx command stays at `~/.local/bin/psydcar`; Psydcar keeps sidecar data outside its installed package. `PSYDCAR_HOME` can select another absolute store directory.

To try the installed CLI, follow the temporary sidecar example below. Then run `psydcar dashboard` from the same terminal to open the local dashboard. Press Ctrl-C to stop it.

### Move from an earlier installation

Check the command your current shell runs before installing with pipx. If it runs, save the output of `psydcar sidecar list` so you can compare IDs later. Also check whether the pipx command location is already occupied:

```console
type -a psydcar
ls -ld "$HOME/.local/bin/psydcar"
```

The `ls` command may report that the file does not exist; that is safe for a new pipx install. An earlier Psydcar release installer created a regular file at this path with exactly these three lines:

```sh
#!/bin/sh
# Installed Psydcar launcher. The current link changes on reinstall.
exec "$HOME/.local/opt/psydcar/current/psydcar" "$@"
```

If the path is a regular file, inspect it with `cat "$HOME/.local/bin/psydcar"`. The command below also checks its exact contents before moving it. Change the backup name if you already have a file with that name:

```console
old="$HOME/.local/bin/psydcar"
backup="$HOME/.local/bin/psydcar.old-release"
if [ ! -f "$old" ] || [ -L "$old" ] || ! cmp -s "$old" <(printf '%s\n' '#!/bin/sh' '# Installed Psydcar launcher. The current link changes on reinstall.' 'exec "$HOME/.local/opt/psydcar/current/psydcar" "$@"'); then
    printf 'Stop: this is not the earlier Psydcar release launcher.\n' >&2
    false
elif [ -e "$backup" ] || [ -L "$backup" ]; then
    printf 'Stop: backup name is already in use. Choose another.\n' >&2
    false
else
    mv "$old" "$backup"
fi
```

If the launcher check fails, stop and resolve ownership before installing. A symlink or file with different contents is not the recognized release launcher. If only the backup name is occupied, choose an unused backup name and rerun the check. Do not use `pipx install --force` to replace a command. Leave `~/.local/opt/psydcar` and your sidecar store in place while you verify the new install.

Now follow the pipx install commands above. Keep the same absolute `PSYDCAR_HOME` value in the new shell if you used one before. Otherwise, Psydcar continues to use its default macOS store. Compare `psydcar sidecar list` before and after migration to confirm the same stable IDs and source directories are present.

If `type -a psydcar` shows a pyenv shim before the pipx path, check which pyenv installation owns it:

```console
pyenv which psydcar
pyenv exec python -m pip show psydcar
```

Only when `pip show` reports an `Editable project location` that points to your earlier Psydcar checkout, remove that package from the selected pyenv environment with `pyenv exec python -m pip uninstall psydcar`, then run `pyenv rehash`. Do not remove a pyenv command whose owner you cannot identify. If pyenv still puts its shim first, put `export PATH="$HOME/.local/bin:$PATH"` after your pyenv initialization in `~/.zshrc`. Open a new terminal and check `command -v psydcar`; it must report `$HOME/.local/bin/psydcar`. Then run `psydcar --version` and `psydcar sidecar list` in that new shell. A saved MCP connection still uses the same absolute command and store path.

### Upgrade and recover

After a newer version is published to PyPI, upgrade the pipx installation:

```console
pipx upgrade psydcar
command -v psydcar
psydcar --version
psydcar sidecar list
```

The command should still resolve to `$HOME/.local/bin/psydcar`, and `sidecar list` should show the same IDs and sources. Pipx replaces the program environment, not the sidecar store. Keep the same absolute `PSYDCAR_HOME` value if you use a custom store. Restart a running dashboard and MCP client so their processes use the new version. Saved MCP configurations keep working because their absolute command path and selected IDs do not change.

There is no certified rollback version for an upgraded store yet. Do not point an earlier Psydcar version at your live store until its compatibility with a copy of the upgraded store has been checked. If a published version is defective, use a newer fixed version when available. PyPI does not allow replacing files for an existing version. Maintainers should consider yanking a defective version when new installs could be harmed, and publish the fix under a new version; yanking does not repair installations that already have it.

For development from this repository, use Python 3.10 or newer and install Psydcar into the Python environment you intend to use:

```console
python -m pip install -e .
```

These commands create an isolated example. Keep this terminal open so the `PSYDCAR_HOME` setting remains available. For a lasting collection, use an existing source directory from a fresh terminal without `PSYDCAR_HOME`, or set it to a permanent absolute directory.

```console
export PSYDCAR_DEMO_DIR="$(mktemp -d)"
export PSYDCAR_HOME="$PSYDCAR_DEMO_DIR/store"
mkdir "$PSYDCAR_DEMO_DIR/source"
printf 'The willow project stores tokens in a vault.\n' > "$PSYDCAR_DEMO_DIR/source/notes.md"
psydcar sidecar create "Demo notes" "$PSYDCAR_DEMO_DIR/source"
psydcar sidecar index "Demo notes"
psydcar search --sidecar "Demo notes" "willow"
```

Search prints a JSON array. Copy the first result's `evidence_id` and substitute it below. Retrieval reads the current source and reports whether it still matches the indexed version.

```console
psydcar retrieve --sidecar "Demo notes" EVIDENCE_ID
```

Open the browser dashboard from the same terminal with `psydcar dashboard`. It opens at `http://127.0.0.1:8765` and stays in the foreground. Press Ctrl-C to stop it. The dashboard and CLI use the same store and stable sidecar IDs.

The example store is under the temporary `PSYDCAR_DEMO_DIR`. On macOS, Psydcar normally uses `~/Library/Application Support/psydcar`. `PSYDCAR_HOME` selects a different absolute store directory. Start the dashboard, CLI, and MCP client with the same value when you use one.

## Create and manage a real sidecar

Create a sidecar from an existing local directory. Creation records the source tree but does not index it until an update runs.

```console
psydcar sidecar create "Personal notes" "$HOME/Notes"
psydcar sidecar list
psydcar sidecar show "Personal notes"
psydcar sidecar index "Personal notes"
```

Replace `$HOME/Notes` with a directory that exists on your computer. `sidecar list` shows each name, stable ID, source, and status. Most CLI commands accept a name or ID. Rename without changing the ID with `psydcar sidecar rename "Personal notes" "Reference notes"`. Remove the managed record and index with `psydcar sidecar remove "Reference notes"`; the source directory remains untouched.

In the dashboard, choose **New sidecar**, enter a name and source directory, then choose **Create sidecar**. Creating there starts an update. Select a card under **Managed sidecars** to see coverage, source changes, update history, and the stable ID. Use **Update now**, **Rename**, or **Remove** in the detail panel. The dashboard shows **Current**, **Updating**, **Stale**, **Not indexed**, or **Needs attention** for each sidecar. `psydcar dashboard --port 8766 --no-open` uses another local port without opening a browser.

## Keep the index current

Run `psydcar sidecar update "Personal notes"` after adding, changing, or deleting source files. `sidecar index` and `sidecar update` run the same refresh; `update` reports how many readable files were added, changed, deleted, or unchanged. `psydcar sidecar show "Personal notes"` shows the last attempt, last successful update, problems needing attention, embedding progress, and PDF page notices. An update from the dashboard uses the same index and state.

For automatic updates, leave one of these foreground commands running:

```console
psydcar watch
psydcar dashboard
```

Use either command; both include a source watcher. The watcher checks supported source files for changes and periodically reconciles the whole source tree. It checks all sidecars at startup, including changes made while Psydcar was stopped. To catch up once and exit, run `psydcar watch --once`. There is no installed background service; source changes are not followed while neither command is running.

An unreadable or oversized supported file produces an **Index error** or **Needs attention** state. If Psydcar had indexed that file before, it keeps the last indexed passages searchable and marks them stale. Fix the source and update again. A deleted file is removed from the index during the next update and produces a **Source attention** notice. Review the deletion, then run another clean update to clear the notice. Old evidence IDs for changed or deleted files may stop working after an update.

## Choose whether to use embeddings

Without a provider, Psydcar builds a local lexical index and CLI or MCP search remains usable. If you configure an OpenAI-compatible embeddings route, updates send indexed text, code, and extractable PDF passages to that endpoint. A hosted provider or compatible gateway, such as LiteLLM or Bifrost, may forward that content. Search also sends its query to the endpoint when selected sidecars have complete embeddings. If embeddings are pending or the provider fails, search falls back to lexical results and prints a notice.

To configure a provider through the CLI, replace the example URL, model, and key with values for your provider. Set the key in the environment of each Psydcar process that needs it. Psydcar stores only the environment variable's name, not its value.

```console
export PSYDCAR_EMBEDDING_KEY="your-provider-key"
psydcar provider set --base-url https://provider.example/v1 --model your-embedding-model --api-key-env PSYDCAR_EMBEDDING_KEY
psydcar provider show
psydcar sidecar update "Personal notes"
```

The URL ends before `/embeddings`. A local gateway that requires no key can omit `--api-key-env`, for example `psydcar provider set --base-url http://127.0.0.1:4000/v1 --model gateway-model`. Remote HTTP requires `--allow-insecure-http`; use HTTPS when sending source content or credentials over a network. Changing provider settings clears existing vectors and requires another update. `psydcar provider clear` removes the route and vectors while keeping lexical evidence.

In the dashboard, use **Embedding provider** → **Configure provider**. Enter the base URL, model, and optional API key environment variable name. **Test settings** sends only the fixed phrase “Psydcar provider connection test.” It sends no source content and does not save the route. **Save provider** enables it; later manual or automatic sidecar updates can then send source passages. The card shows whether the provider is configured or its credential is missing. If you set a missing variable after launching the dashboard, restart the dashboard before testing again. **Edit provider** and **Remove** change or clear the saved route.

## Search and retrieve selected evidence

Every CLI search and retrieval names at least one sidecar, even if only one exists. Repeat `--sidecar` to combine collections. CLI selection accepts a sidecar name or stable ID.

```console
psydcar search --sidecar "Personal notes" "authentication token"
psydcar search --sidecar "Personal notes" --sidecar "API docs" "authentication token"
psydcar retrieve --sidecar "Personal notes" EVIDENCE_ID
```

The second command requires an existing sidecar named `API docs`. An evidence ID belongs to its sidecar; retrieval rejects it if that sidecar is outside the selected set. Psydcar returns evidence for the agent to interpret, not a synthesized answer.

| Evidence field | How to read it |
| --- | --- |
| `sidecar`, `source.path`, `source.relative_path` | The selected collection and the original local file. |
| `location` | One-based inclusive `start`/`end` lines for text or code, or a one-based `page` for a PDF. |
| `context`, `context_origin` | Search context comes from the index (`index`). A successful retrieval reads the current source (`source`). |
| `freshness` | `current` means the source matches the indexed bytes. `changed`, `missing`, or `unreadable` means it does not provide current context. |
| `index_freshness`, `processing.state` | The index can be `stale` while an update is pending or needs attention, even when a source file still matches its old indexed copy. Processing is `current`, `pending`, or `attention`. |

Search may show the last indexed passage while an update is pending or failed. Check `freshness`, `index_freshness`, and `processing` before relying on it. Retrieval checks the live file or PDF page. If the source changed, disappeared, or cannot be read, retrieval returns `context: null` with the corresponding freshness and does not present old index text as current source evidence. After a successful update, changed passages receive new evidence IDs.

## Give an MCP client explicit access

Install Psydcar with pipx as described above. Then use **MCP connections** → **Prepare connection** in the dashboard. Give the connection a name, check one or more sidecars in **Available sidecars**, then choose **Generate configuration**. The result names and lists the exact selected stable IDs. **Copy JSON** provides an MCP stdio entry with the absolute path to the installed `~/.local/bin/psydcar` command, `--sidecar-id` arguments, and the local `PSYDCAR_HOME` value. Add it to the MCP settings of that project or coding tool, then restart the client. The command path stays the same when you install a newer release, so the saved connection does not need to be regenerated. If your client has a different settings wrapper, use the entry's `command`, `args`, and `env` fields.

For manual setup, copy stable IDs from `psydcar sidecar list` and configure a stdio process with this command. Repeat `--sidecar-id` for more sidecars:

```console
psydcar mcp --sidecar-id SIDECAR_ID --sidecar-id ANOTHER_SIDECAR_ID
```

MCP requires stable IDs, not sidecar names. A process fixes its selection before accepting requests. Its `search` tool takes `query` and an optional `limit`; `retrieve` takes `evidence_id`. The tools cannot expand the selected set. Another client can start a separate process using the same IDs. An empty, unknown, or removed selection fails closed. The copied JSON contains no provider credential. If embeddings use an API key, set its named environment variable in the MCP client's process separately.

## What source content is covered?

Psydcar reads UTF-8 text and code, including UTF-8 with a byte order mark. Supported extensions are `.txt`, `.md`, `.markdown`, `.rst`, `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.json`, `.yaml`, `.yml`, `.toml`, `.go`, `.rs`, `.java`, `.c`, `.h`, `.cpp`, `.hpp`, `.cs`, `.sh`, `.sql`, `.html`, and `.css`. Files over 2 MiB are skipped with an attention notice. Text and code passages span up to 40 lines with five lines of overlap. Locations refer to the original file's lines.

PDFs with a `.pdf` extension are supported up to 50 MiB. Psydcar extracts text page by page; it does not perform OCR. A page without extractable text produces no searchable passage and appears after `PDF page:` in `sidecar show` and under **PDF page notices** in the dashboard. Searchable pages in the same PDF still work. Page numbers start at one. Replace or OCR an image-only PDF outside Psydcar, then update the sidecar. Unreadable, malformed, or encrypted PDFs produce an indexing error. The `pypdf` dependency is included by the install command above.

Psydcar ignores hidden files and directories, unsupported extensions, and directories named `node_modules`, `__pycache__`, `dist`, `build`, `target`, or `vendor`, throughout the source tree. It does not read through symbolic links. Supported symlinked or binary/non-UTF-8 files and files over the size limits are reported as skipped and can require attention. `.gitignore` patterns are not interpreted.

## If a result or connection needs attention

| What you see | What to do |
| --- | --- |
| No results | Confirm the sidecar is selected and indexed. Check the supported extensions and ignore rules, then update it. |
| **Stale** or **Needs attention** | Run `psydcar sidecar show NAME`, fix the reported source or provider problem, and update. |
| A PDF page has no searchable text | Use a text-searchable PDF or OCR the source outside Psydcar, then update. |
| **Credential missing** or a provider test fails | Set the named variable before starting the dashboard or MCP client. Check the URL, model, and gateway, then test again. |
| An MCP connection fails to start | Check that its selected IDs still exist, the pipx command is installed at the saved path, and `PSYDCAR_HOME` points to the same store. Generate the entry again if the selected IDs or store changed. |
