# Contributing

Thanks for helping. Bug reports, fixes and ideas are all welcome.

## Set up

```sh
git clone https://github.com/ptmorris05/ecfr-md && cd ecfr-md
uv sync
```

## Before you open a pull request

```sh
uv run ruff format && uv run ruff check
uv run pyright
uv run pytest --cov=ecfr_md --cov-fail-under=100
```

The tests run offline against a fake eCFR (`tests/conftest.py`); coverage must stay at 100%. Tests that call the real eCFR are marked `network` and run nightly; run them with `uv run pytest -m network`, and keep them polite: the eCFR rejects more than two requests in flight from one client.

Add a line to the Unreleased section of `CHANGELOG.md` for anything a user would notice, and mark breaking changes.

## Style

Match the code around you: small functions, plain-English docstrings that say what something is for, and error messages that tell the reader what to do next.

## Releases

Releases go to PyPI and GitHub. Set the version in `pyproject.toml`, move the Unreleased notes under it in `CHANGELOG.md` (`## [X.Y.Z] - YYYY-MM-DD`), and merge to `main`. The release workflow sees the new version, runs the full test matrix, builds the wheel and source distribution with provenance attestations, tags the commit `vX.Y.Z`, publishes the GitHub Release with the changelog section as its notes, and uploads to PyPI (the `pypi` environment's `PYPI_API_TOKEN`). A pre-release version such as `0.3.0rc1` publishes a pre-release with the Unreleased notes. It can also be started by hand from the Actions tab; a version already released is skipped.
