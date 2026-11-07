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

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests for behavior you change.
- Update `README.md` when user-facing behavior changes, and add a line under **Unreleased** in `CHANGELOG.md`.

## Releasing

Releases are published to PyPI by `.github/workflows/release.yml` through PyPI trusted publishing.

1. Bump `version` in `pyproject.toml`. Move the **Unreleased** changelog entries under a new version heading. Merge both changes to `main`.
2. Tag the merge commit and push the tag:

   ```console
   git tag -a v0.1.4 -m "psydcar 0.1.4"
   git push origin v0.1.4
   ```

3. The workflow builds and checks the distributions, then publishes them to PyPI. Next it installs the release from public PyPI on macOS and Linux and runs a smoke test. Finally it creates the GitHub Release with generated notes.

PyPI never allows a version's files to be replaced. If a published version is broken, release a fix under a new version. Consider [yanking](https://pypi.org/help/#yanked) the broken version if new installs could be harmed. Yanking doesn't repair existing installations.
