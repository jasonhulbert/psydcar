# Changelog

All notable changes to Psydcar are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Grant individual MCP connections permission to propose edits per sidecar. A proposed edit waits in a local queue and doesn't reach disk unless it's approved.
- Local state and search now live in separate SQLite databases. The upgrade preserves sidecar and evidence IDs and keeps the original database as a backup.
- Markdown links open other files in the sidecar. ⌘-click (Ctrl-click on Linux) a relative link such as `../ideas.md` or `notes/plan#next-steps` to open that file. A link without an extension matches a Markdown file of that name, and a `#heading` part scrolls to that heading. ⌘Enter opens the link under the cursor. Hovering a link shows where it points.
- Markdown front matter shows as a properties list in Reading mode. Lists render as chips and tags as `#tag` links. Switch to Edit or Source to edit the YAML. Psydcar never rewrites front matter.
- Tags from front matter filter the file list. Click a tag, or type `#tag` in the filter, to list the files with that tag. Nested tags such as `#project/q4` also match `#project`.
- Markdown tables render as tables in Reading mode. Wide tables can use the space beside the text column on large screens and scroll sideways on small ones. Switch to Edit or Source to edit the table's source.
- Images in Markdown render in Reading mode. Local images, such as `![Chart](images/chart.png)`, load from the sidecar's folder. Images on the web aren't loaded and show their alt text. ⌘-click an image to open its file.
- The file list shows image files. Open one to view it with its size and dimensions, and rename, move, or delete it like any other file. Images aren't indexed.
- The Markdown view switcher offers Edit, Reading, and Source. Reading renders the whole file, keeps it read-only, and follows links on a plain click.
- An outline lists the open Markdown file's headings. Click a heading to scroll to it. The section you're reading is highlighted. Open it with the outline button in the editor toolbar.
- Sections under top-level headings fold in Reading mode. Click the arrow beside a heading to fold or unfold it.
- Callouts such as `> [!note]` and `> [!warning] Title` render as colored boxes with an icon. GitHub's five alert types and Obsidian's callout types are recognized.
- Footnotes render in Reading mode. A reference such as `[^1]` shows as a number. Following it scrolls to the footnote, and following the footnote's number scrolls back.
- Wiki links such as `[[Project plan]]`, `[[Project plan|the plan]]`, and `[[Project plan#Goals]]` open the file of that name anywhere in the sidecar. `![[photo.png]]` embeds an image, and `![[photo.png|300]]` sets its width.
- HTML blocks render in Reading mode through an allowlist of safe elements. Scripts, styles, forms, and embedded content are removed.

### Fixed

- Markdown editing keeps its layout stable across caret movement, selection, and focus changes. Rendered block spacing follows CodeMirror’s layout requirements.

### Changed

- Edit mode keeps Markdown text in place while navigating, selecting, and typing. Source has line numbers. Tab moves focus in prose and indents code. Prose no longer inserts automatic closing brackets.
- Lists in Reading mode are easier to read. Nested items are indented with guide lines, and wrapped text lines up under the item's first line. Text no longer shifts when the cursor reaches a list marker, and a task's checkbox stands in for its bullet.
- Markdown opens in stable formatted editing, with visible syntax. Reading mode provides the fully rendered document.
- Reading mode hides the underline of `===` and `---` headings, hides the backslash in escapes such as `\*`, and shows entities such as `&copy;` as the characters they stand for.

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

[Unreleased]: https://github.com/jasonhulbert/psydcar/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/jasonhulbert/psydcar/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jasonhulbert/psydcar/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/jasonhulbert/psydcar/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/jasonhulbert/psydcar/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jasonhulbert/psydcar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonhulbert/psydcar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonhulbert/psydcar/releases/tag/v0.1.0
