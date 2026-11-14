# Contributing to Psydcar

Thanks for your interest in improving Psydcar. Bug reports, ideas, and pull requests are all welcome. For anything larger than a small fix, please [open an issue](https://github.com/jasonhulbert/psydcar/issues) first so we can agree on the approach.

## Development setup

You need Python 3.10 or newer. Clone the repository and install it in editable mode in a virtual environment:

```console
git clone https://github.com/jasonhulbert/psydcar.git
cd psydcar
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

With uv, `uv venv && uv pip install -e .` does the same.

To keep experiments away from your real sidecars, point `PSYDCAR_HOME` at a scratch folder:

```console
export PSYDCAR_HOME="$(mktemp -d)"
```

## Running tests

The tests use the standard library's `unittest` and need no extra packages:

```console
python -m unittest discover -s tests
```

CI runs the suite on macOS and Linux for every supported Python version. Some tests start local HTTP servers on `127.0.0.1`, so a sandbox that blocks loopback sockets will skip them.

## Dashboard assets

The dashboard in `src/psydcar/dashboard_assets` is plain HTML, CSS, and JavaScript with no build step. Its content security policy allows only same-origin assets, so fonts and icons are bundled:

- **Fonts:** Geist and Geist Mono variable fonts from the [`geist`](https://www.npmjs.com/package/geist) npm package, under the SIL Open Font License in `Geist-OFL.txt`.
- **Icons:** `icons.svg` is a sprite with only the [Lucide](https://lucide.dev) icons the dashboard uses. The server inlines it into the page, and markup references an icon as `<use href="#NAME">`. To add one, put its name in `ICONS` in `scripts/build-dashboard-icons.py` and regenerate the sprite:

```console
npm pack lucide-static && mkdir lucide && tar -xzf lucide-static-*.tgz -C lucide
python scripts/build-dashboard-icons.py lucide/package
```

- **Editor:** `editor.js` is a bundle of [CodeMirror 6](https://codemirror.net) plus Psydcar's formatted editing and rendered reading view, built from the `editor/` folder. The dashboard loads it only when a file is opened. Its third-party licenses are in `editor-licenses.txt`. Both files are committed, so installing Psydcar never needs Node. After changing `editor/src` or its dependencies, rebuild both files with Node 18 or newer:

```console
cd editor
npm ci
npm run build
```

CodeMirror adds its base styles in a `<style>` element. The dashboard's content security policy admits that element through a per-page nonce, which the server puts in a `<meta>` tag for the editor.

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests for behavior you change.
- Update `README.md` when user-facing behavior changes, and add a line under **Unreleased** in `CHANGELOG.md`.

## Releasing

Releases are automatic. `.github/workflows/release.yml` runs on every push to `main`. If the `version` in `pyproject.toml` has no matching `vX.Y.Z` tag yet, the workflow runs these steps:

1. Builds and checks the distributions.
2. Publishes them to PyPI through trusted publishing.
3. Tags the commit and creates a GitHub Release with generated notes.
4. Installs the release from public PyPI on macOS and Linux and runs a smoke test.

To ship a release, bump `version` in `pyproject.toml` and move the **Unreleased** changelog entries under a new version heading, all in the pull request. Merging it publishes the release. Merges that don't change the version publish nothing.

PyPI never allows a version's files to be replaced. If a published version is broken, release a fix under a new version. Consider [yanking](https://pypi.org/help/#yanked) the broken version if new installs could be harmed. Yanking doesn't repair existing installations.
