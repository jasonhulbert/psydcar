# Psydcar

[![PyPI](https://img.shields.io/pypi/v/psydcar)](https://pypi.org/project/psydcar/) [![Python](https://img.shields.io/pypi/pyversions/psydcar)](https://pypi.org/project/psydcar/) [![Tests](https://github.com/jasonhulbert/psydcar/actions/workflows/test.yml/badge.svg)](https://github.com/jasonhulbert/psydcar/actions/workflows/test.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Psydcar lets your coding agents search your local notes, code, and PDFs.

Point Psydcar at a folder and it builds a local search index called a **sidecar**. Coding agents such as Claude Code, Cursor, or any other [MCP](https://modelcontextprotocol.io) client can then search that sidecar and quote the exact file, line, or PDF page that answers their question. Your working files stay in their source folders. Agents get read access by default, and you can let selected connections propose edits for review.

- **Local by default.** Indexing and search run on your machine. Nothing leaves it unless you turn on an embeddings provider.
- **You choose what each agent sees.** Each MCP connection is limited to the sidecars you select.
- **Answers come with sources.** Every result names its file and location, and says whether the file has changed since it was indexed. In a git repository, it also names the commit and says whether the file has uncommitted changes.
- **Stays current.** A built-in watcher re-indexes files as they change.
- **CLI and browser dashboard.** Manage sidecars whichever way you prefer.

## Install

Psydcar needs Python 3.10 or newer and runs on macOS and Linux. Install it as a standalone command with [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/):

```console
pipx install psydcar
```

```console
uv tool install psydcar
```

`pip install psydcar` also works inside a virtual environment. Check the install with `psydcar --version`.

## Quick start

Create a sidecar from any folder, index it, and search it:

```console
psydcar sidecar create "My notes" ~/Notes
psydcar sidecar index "My notes"
psydcar search --sidecar "My notes" "where are API tokens stored"
```

Search prints JSON results. Each result includes the matching text, the file path, its line range or PDF page, and an `evidence_id`. To re-read the current version of a result from disk, pass its ID to `retrieve`:

```console
psydcar retrieve --sidecar "My notes" EVIDENCE_ID
```

Prefer a UI? Run `psydcar dashboard` to open a local dashboard at `http://127.0.0.1:8765`. You can create, update, and inspect sidecars there, set up embeddings, and prepare agent connections. Press Ctrl-C to stop it.

## Connect a coding agent

Agents reach Psydcar through an MCP server that runs on your machine over stdio. Each server serves only the sidecars you pass to it. Find a sidecar's ID with `psydcar sidecar list`.

The easiest way to get a ready-made configuration is the dashboard. Open **MCP connections** → **Prepare connection**, select sidecars, and choose **Copy JSON**.

**Claude Code:**

```console
claude mcp add my-notes -- psydcar mcp --sidecar-id SIDECAR_ID
```

**Cursor, Claude Desktop, and other JSON-configured clients:**

```json
{
  "mcpServers": {
    "my-notes": {
      "command": "/absolute/path/to/psydcar",
      "args": ["mcp", "--sidecar-id", "SIDECAR_ID"]
    }
  }
}
```

Use the output of `command -v psydcar` as the `command` value. Desktop apps often don't inherit your shell's `PATH`, so an absolute path is the reliable choice. Repeat `--sidecar-id` to serve several sidecars, and restart the client after changing its settings.

The server gives the agent two tools by default:

- `search` takes a `query` and an optional `limit`.
- `retrieve` takes an `evidence_id` and returns the current text from disk.

The agent can't widen its selection. Psydcar returns evidence, not answers, so the agent reads the sources and draws its own conclusions.

## Let an agent propose edits

In the dashboard's **Prepare an MCP connection** dialog, select a sidecar and check **Allow proposals** beside it. Generate the configuration, add it to the MCP client, and restart the client. The connection name identifies its proposals. To configure a connection by hand, add `--propose-sidecar-id SIDECAR_ID --agent-name "My agent"` to its arguments. Each proposal grant must also have a corresponding `--sidecar-id` argument. Repeat the grant for each sidecar where the agent can propose edits.

For those sidecars the agent can call `read_editable_file` to get an existing text file and its version, then call `propose_edit` with the complete replacement text and a one-line reason. Proposals wait in the local queue until you decide. Agents can only change existing text files. They can't create, move, or delete files.

## Review proposed edits

Every proposal shows the agent's name, its one-line reason, the file, and a diff. Approving writes the new text to the file in one step and keeps the file's line endings and byte order mark. Psydcar then updates the sidecar's index, so agents can search the new text right away. Rejecting leaves the file unchanged. Either way the decision is final, and Psydcar keeps a list of recent decisions.

If the file changed after the agent read it, the proposal is marked **Out of date** and can't be approved. Psydcar won't overwrite your changes. Reject it and ask the agent for a new proposal.

**In the dashboard,** a banner at the top of the page counts the edits waiting for review, and each sidecar with pending proposals has a marker in the list. Select a sidecar to see its **Proposed edits** and **Recent decisions**. Choose a proposal to open its diff, then select **Approve** or **Reject**.

**From the CLI:**

```console
psydcar proposal list                 # edits waiting for review in every sidecar
psydcar proposal list "My notes"      # only one sidecar
psydcar proposal show 3f2a9c1e        # details and the diff
psydcar proposal approve 3f2a9c1e     # write the file and update the index
psydcar proposal reject 3f2a9c1e      # leave the file unchanged
psydcar proposal list --reviewed      # recent decisions
```

Commands accept a proposal's full ID or the first characters that `proposal list` shows.

## Record approvals as git commits

When a sidecar's folder is in a git repository, including a folder inside a larger repository, you can turn on its **git record**. Each approval in that sidecar then becomes a commit on the checked-out branch. History shows which agent changed what and why, and `git revert` undoes an agent's change in one command. The record is off until you turn it on, and Psydcar offers it only for folders in a repository.

```console
psydcar sidecar git-record "My notes" on    # commit each approval
psydcar sidecar git-record "My notes"       # show the setting and the repository
psydcar sidecar git-record "My notes" off   # approvals write files without commits
```

In the dashboard, select the sidecar and use the **Git record** switch. While the record is on, the review dialog says that approving also records a commit.

The commit's subject is the agent's one-line reason. Its message names the agent in the body and in a `Psydcar-Agent` trailer, and gives the proposal ID in a `Psydcar-Proposal` trailer. Psydcar commits with your git identity and signs the commit if your git configuration signs commits. Git hooks don't run for these commits.

A commit holds only the approved change to that one file. Psydcar builds it from the branch's last commit, so files you staged and other uncommitted work stay out of it and stay as they were. When a commit can't be limited to the approved change, Psydcar still saves the approved edit, makes no commit, and says why. That happens when the file already had uncommitted or staged changes, the file isn't tracked, the repository has no commits, HEAD is detached, a merge, rebase, cherry-pick, revert, or bisect is in progress, or another git command is running. If git fails for any other reason, or isn't installed, the approval still stands and Psydcar reports what went wrong. After each approval, the CLI and the dashboard say whether a commit was recorded, and **Recent decisions** and `psydcar proposal list --reviewed` show its commit ID.

Psydcar never pulls, pushes, or fetches, and it never touches remotes. To move work between devices, use your own git commands, such as `git push` and `git pull`. When pulled changes arrive in a sidecar's folder, the watcher re-indexes them. Rejections never make commits.

## Keep sidecars up to date

Run `psydcar sidecar update NAME` after files change. It reports what was added, changed, deleted, or unchanged.

To update automatically, leave `psydcar watch` or `psydcar dashboard` running. Both watch every sidecar's folder and catch up on anything that changed while Psydcar was stopped. `psydcar watch --once` catches up once and exits. Psydcar doesn't install a background service, so changes are only picked up while one of these commands is running.

`psydcar sidecar show NAME` reports the last update, any files that need attention, and PDF pages without searchable text.

## Better results with embeddings (optional)

Out of the box, Psydcar uses keyword search that runs entirely on your machine. For semantic search, which matches meaning rather than exact words, connect any OpenAI-compatible embeddings endpoint. That includes OpenAI, a hosted gateway such as LiteLLM or Bifrost, or a local model server.

```console
export OPENAI_API_KEY="sk-..."
psydcar provider set --base-url https://api.openai.com/v1 --model text-embedding-3-small --api-key-env OPENAI_API_KEY
psydcar sidecar update "My notes"
```

- **Privacy.** With a provider configured, updates send indexed passages to that endpoint, and searches send the query.
- **Credentials.** Psydcar stores only the name of the environment variable, never the key itself. Set the variable wherever Psydcar runs: your shell, the dashboard, and your MCP client's environment.
- **URLs.** The base URL ends before `/embeddings`. A local server without a key can skip `--api-key-env`, for example `--base-url http://127.0.0.1:4000/v1`. Plain HTTP to a remote host requires `--allow-insecure-http`.
- **Fallback.** If embeddings are pending or the provider fails, search falls back to keyword results and prints a notice.
- **Managing it.** `psydcar provider show` displays the current settings. `psydcar provider clear` removes the provider and its vectors and keeps the keyword index. Changing provider settings clears existing vectors, so run an update afterwards.

In the dashboard, the same settings are under **Embedding provider**. **Test settings** sends only a fixed test phrase, never your files.

## Manage sidecars

| Command | What it does |
| --- | --- |
| `psydcar sidecar create NAME DIR` | Register a folder. It isn't indexed until you run `index` or `update`. |
| `psydcar sidecar list` | Show every sidecar with its ID, folder, and status. |
| `psydcar sidecar show NAME` | Show details, update history, and problems. |
| `psydcar sidecar index NAME` / `update NAME` | Index the folder, or refresh changed files. Both commands run the same refresh. |
| `psydcar sidecar rename OLD NEW` | Rename a sidecar. Its ID stays the same. |
| `psydcar sidecar remove NAME` | Delete the sidecar and its index. The source folder isn't touched. |
| `psydcar sidecar git-record NAME [on\|off]` | Show, or turn on or off, [recording approvals as git commits](#record-approvals-as-git-commits). |

Commands accept a sidecar's name or ID. To search several sidecars, repeat `--sidecar`. Run `psydcar --help` or `psydcar COMMAND --help` for every option.

## What gets indexed

- **Text and code:** UTF-8 files up to 2 MiB with these extensions: `.txt` `.md` `.markdown` `.rst` `.py` `.js` `.jsx` `.ts` `.tsx` `.json` `.yaml` `.yml` `.toml` `.go` `.rs` `.java` `.c` `.h` `.cpp` `.hpp` `.cs` `.sh` `.sql` `.html` `.css`
- **PDFs:** up to 50 MiB, with text extracted page by page. Psydcar doesn't run OCR. Scanned pages without a text layer are listed as PDF page notices, and the rest of the PDF stays searchable.
- **Skipped:** hidden files and folders, symbolic links, unsupported extensions, and any folder named `node_modules`, `__pycache__`, `dist`, `build`, `target`, or `vendor`. `.gitignore` files aren't read.

Text and code are split into passages of up to 40 lines that overlap by 5 lines. Locations always refer to lines in the original file.

## Reading search results

| Field | Meaning |
| --- | --- |
| `source.path`, `source.relative_path` | The original file. |
| `location` | Line range (`start`–`end`, starting at 1) for text and code, or the `page` number for a PDF. |
| `context`, `context_origin` | The matching text. Search returns the indexed copy (`index`). `retrieve` reads the file again (`source`). |
| `freshness` | `current` means the file hasn't changed since indexing. `changed`, `missing`, and `unreadable` mean the indexed text may be out of date. |
| `index_freshness`, `processing.state` | Whether the sidecar has an update pending or a problem that needs attention. |
| `git.commit` | The commit the repository is at when you search or retrieve. It's `null` in a repository with no commits yet. |
| `git.uncommitted` | `true` when the file differs from that commit, staged or not, or isn't tracked by git. |
| `git.tracked` | `false` when git doesn't track the file, for example a new or ignored file. |

The `git` fields appear only when the sidecar's folder is in a git repository, including a folder inside a larger repository. Psydcar reads them with your installed `git`, and reading them never changes the repository. In other folders, or without git installed, results don't include `git`. When `freshness` is `current` and `git.uncommitted` is `false`, the evidence matches the file as committed at `git.commit`.

If a file has changed or disappeared, `retrieve` returns `context: null` instead of outdated text. After an update, changed passages get new evidence IDs.

## Where data is stored

Psydcar keeps its index in one per-user folder:

- **macOS:** `~/Library/Application Support/psydcar`
- **Linux:** `$XDG_DATA_HOME/psydcar`, which defaults to `~/.local/share/psydcar`

To use a different folder, set `PSYDCAR_HOME` to an absolute path. If you do, set the same value for the CLI, the dashboard, and your MCP client.

The data folder contains `state.sqlite3` for sidecar registrations, provider settings, proposed edits, and git record settings and `search.sqlite3` for indexed documents and embeddings. The first run upgrades an existing `psydcar.sqlite3` database automatically and retains it as a pre-upgrade backup. Existing sidecar IDs and indexed evidence IDs carry over. Stop running Psydcar processes before upgrading so all interfaces switch to the new stores together.

## Upgrade and uninstall

Upgrade with `pipx upgrade psydcar` or `uv tool upgrade psydcar`. Your sidecars and their IDs carry over, and existing MCP configurations keep working. Restart a running dashboard or MCP client to pick up the new version.

To uninstall, run `pipx uninstall psydcar` or `uv tool uninstall psydcar`. To delete your sidecars as well, remove the data folder listed above.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| A search returns no results | Is the sidecar indexed? Run `psydcar sidecar show NAME`, check the file types listed under [What gets indexed](#what-gets-indexed), then run an update. |
| A sidecar shows **Stale** or **Needs attention** | `psydcar sidecar show NAME` lists the files or provider problems. Fix them, then run an update. |
| A PDF page has no searchable text | It's probably a scanned image. Run OCR on it with another tool, then run an update. |
| An embeddings test fails or the credential shows as missing | Set the API key variable before starting Psydcar or your MCP client. Then check the URL and model. A 401 or 403 error can also come from a gateway, proxy, or firewall in front of the provider. |
| The agent can't start the MCP server | Use an absolute `command` path, check that the sidecar IDs still exist with `psydcar sidecar list`, and use the same `PSYDCAR_HOME` if you set one. |

Found a bug or have an idea? [Open an issue](https://github.com/jasonhulbert/psydcar/issues).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, and the release process.

## License

[MIT](LICENSE)
