# Changelog

All notable changes to Psydcar are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-11-13

### Added

- Grant individual MCP connections permission to propose edits per sidecar. A proposed edit waits in a local queue and doesn't reach disk unless you approve it. Review proposals in the dashboard, which shows each agent's name, reason, and diff and keeps a list of recent decisions, or from the CLI with `psydcar proposal list`, `show`, `approve`, and `reject`. An approved edit is written in one step with its line endings and byte order mark preserved, and it's searchable right away. A proposal for a file that changed after the agent read it is marked out of date and can't be approved.
- Evidence from a folder in a git repository reports where it came from. Search and retrieve results gain a `git` field with the current commit and whether the file has uncommitted changes. Psydcar only reads git state and never changes the repository. Results from other folders are unchanged.
- Record approvals as git commits. For a sidecar whose folder is in a git repository, turn on the git record with `psydcar sidecar git-record NAME on` or the **Git record** switch in the dashboard. Each approval then becomes a commit on the checked-out branch whose subject is the agent's reason and whose message names the agent. A commit holds only the approved change to that one file, and your staged and uncommitted work stays out of it. When that can't be guaranteed, or git fails, the approval still stands, no commit is made, and Psydcar says why. The record is off by default. Psydcar never pulls or pushes, so you move commits between devices with your own git commands.

### Changed

- Local state and search now live in separate SQLite databases. The first run upgrades an existing database automatically. Sidecar IDs, evidence IDs, provider settings, and MCP connections carry over, and the original database stays in place as a backup.

### Removed

- The dashboard no longer edits files. The file list, the editor, and creating, renaming, moving, and deleting files and folders from the dashboard are gone. Your files belong to you, and the editor you already use writes to them. While `psydcar watch` or the dashboard is running, Psydcar picks up those changes and re-indexes them. Removing the editor keeps Psydcar focused on giving agents evidence from your folders and on controlling what each agent can see and change. It also removes the dashboard's JavaScript build, so building and releasing Psydcar needs only Python.

## [0.3.0] - 2026-11-07

### Added

- The dashboard can edit a sidecar's files. **Browse and edit files** opens a file list and an editor. Markdown opens in live preview: formatting renders as you type, and the raw syntax appears on the line you're editing. Code files open with syntax highlighting. Saving writes the file in place and re-indexes it right away. Line endings are preserved, and you're warned when a file changes on disk while you're editing it. The file list can be hidden. On narrow screens, it opens as a drawer over the editor.
- The dashboard can create, rename, move, and delete files and folders inside a sidecar's folder. It only deletes empty folders.

## [0.2.0] - 2026-11-07

### Changed

- The dashboard has a new design. Sidecars appear in a table you can filter by status, and the selected sidecar's details stay beside the list. It has a violet and pink color palette that meets WCAG AA text contrast. It uses the Geist and Geist Mono fonts and Lucide icons, both bundled locally, and follows the system light or dark theme.
- The dashboard footer shows the installed version and the data folder.

## [0.1.4] - 2026-11-07

### Added

- MIT license, PyPI project metadata, and a CI workflow that tests macOS and Linux on Python 3.10–3.14.
- Merging a version bump into `main` now publishes the release automatically. It publishes to PyPI, tags the commit, and creates a GitHub Release.

### Changed

- Psydcar can be installed with pipx, uv, or pip on any supported Python version. The install docs no longer require pyenv or Python 3.11.
- Dashboard MCP connections work however Psydcar was installed. Previously they required a pipx install at `~/.local/bin/psydcar`.
- The README was rewritten around a quick start and agent setup.

## [0.1.3] - 2026-11-07

### Fixed

- Embedding requests now send a `psydcar/<version>` User-Agent. Gateways behind Cloudflare no longer reject them with HTTP 403 (error 1010). ([#1](https://github.com/jasonhulbert/psydcar/issues/1))
- Provider HTTP errors include a short excerpt of the response body, with the credential redacted.
- The dashboard hint for 401 and 403 errors mentions gateways, proxies, and firewalls.

## [0.1.2] - 2026-10-23

### Changed

- Psydcar is now published to PyPI and installed with pipx. It replaces the self-contained release archives.

## [0.1.1] - 2026-10-21

### Fixed

- The code signature of the bundled Python in the macOS release archive.

## [0.1.0] - 2026-10-21

### Added

- First release: local sidecars for text, code, and PDFs with lexical and optional embedding search, a file watcher, a browser dashboard, and an MCP server limited to selected sidecars.

[Unreleased]: https://github.com/jasonhulbert/psydcar/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/jasonhulbert/psydcar/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jasonhulbert/psydcar/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jasonhulbert/psydcar/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/jasonhulbert/psydcar/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/jasonhulbert/psydcar/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jasonhulbert/psydcar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonhulbert/psydcar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonhulbert/psydcar/releases/tag/v0.1.0
