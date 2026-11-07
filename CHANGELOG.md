# Changelog

All notable changes to Psydcar are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/jasonhulbert/psydcar/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/jasonhulbert/psydcar/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/jasonhulbert/psydcar/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/jasonhulbert/psydcar/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonhulbert/psydcar/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonhulbert/psydcar/releases/tag/v0.1.0
