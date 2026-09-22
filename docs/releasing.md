# Releasing plexgraph

plexgraph is published as **one** package, `plexgraph`. It contains three importable packages, `plexgraph` (the front door),
`plexgraph_core` (graph model and loaders) and `plexgraph_bridge` (viewer server and styling), plus the built viewer inside
`plexgraph_bridge/static`. In the repository they live in `packages/`, and a release is assembled from them.

## Build and check locally

```sh
pnpm install
python -m pip install build twine
python scripts/build_release.py          # builds the viewer if needed; writes dist/plexgraph-X.Y.Z.tar.gz and .whl
python scripts/check_release.py          # installs the wheel and the sdist into fresh environments and uses them
python scripts/check_release.py --browser   # also renders the installed viewer in Chrome
```

`build_release.py` copies the packages and the built viewer into a temporary staging tree, fills in
`packaging/pyproject.release.toml`, and builds from that tree, so the sdist contains everything and installs on its own
(`python -m build` also builds the wheel *from* the sdist, which proves it). Use `--build-frontend` to force a viewer rebuild
and `--keep-stage` to inspect the staging tree.

`check_release.py` runs `twine check --strict`, inspects both archives (viewer, `py.typed`, licence; no tests or caches),
and, for each artifact, installs it into an empty virtual environment and runs a smoke test from a directory that cannot see
this checkout: the version, the implementation packages, `python -m plexgraph info`, the `plexgraph` command, the bundled viewer
over HTTP, a WebSocket client receiving the graph and its style, a live style change, and clear errors.

## Branches

Day-to-day work happens on `develop` (the GitHub default branch: new PRs target it unless you say otherwise). `master`
only ever moves by a `develop` → `master` pull request, right before a release, and only tags on `master` reach real PyPI
(`release.yml`'s tag trigger fires wherever the tag lands, so a tag must be made on `master`, not `develop`). This is
deliberate: TestPyPI is where you find out a release is broken, and it costs nothing to be wrong there; PyPI does not let
you reuse a version number, so `master` should only ever get code that has already been tried on TestPyPI.

## Making a release

1. **Pick the version** (`0.2.0`, `0.2.0rc1`, ...) and set it everywhere, on `develop`: `python scripts/bump_version.py
   0.2.0`. It updates `VERSION`, `packages/plexgraph/plexgraph/_version.py` and the `version` of the three source
   packages; a test fails if any copy disagrees.
2. **Write the changelog**: move the `[Unreleased]` entries in `CHANGELOG.md` under a new `## [0.2.0]` heading and add the
   comparison link at the bottom. The release notes on GitHub are taken from this section, and a test fails if the current
   version has no entry.
3. **Refresh the lock** if dependencies changed (`uv lock`) and commit to `develop`. CI must pass there: the frontend,
   Python 3.11 to 3.14 on Linux, macOS and Windows, mypy, the browser end-to-end tests, and the release build and install
   checks.
4. **Try TestPyPI, from `develop`**: Actions, Release, Run workflow, pick the `develop` branch as the ref, target
   `testpypi`. Then in a clean environment:
   `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ plexgraph==0.2.0`.
   If something's wrong, fix it on `develop` and try again — nothing has touched `master` or real PyPI yet.
5. **Open the `develop` → `master` pull request** once TestPyPI looks right, and merge it. This is the one PR that goes
   to `master`; everything else targets `develop`.
6. **Tag `master`**: `git checkout master && git pull && git tag v0.2.0 && git push origin v0.2.0`. The Release workflow
   checks that the tag matches `VERSION`, builds, runs the tests and `check_release.py`, checks the wheel on Linux, macOS
   and Windows, publishes to PyPI, and creates a GitHub release with the artifacts and the changelog section.

## One-time setup

The workflows use PyPI **trusted publishing**, so no API token is stored anywhere.

1. Create the project on [PyPI](https://pypi.org/manage/account/publishing/) and on
   [TestPyPI](https://test.pypi.org/manage/account/publishing/) as a *pending publisher*: project `plexgraph`, owner
   `instabaines`, repository `plexgraph`, workflow `release.yml`, environment `pypi` (`testpypi` on TestPyPI).
2. In the GitHub repository settings, create two environments named `pypi` and `testpypi`. Adding required reviewers to `pypi`
   makes every real release wait for approval.

## Notes

- The wheel is pure Python (`py3-none-any`): nothing to compile, one file for every platform.
- Do not install `plexgraph` in the same environment as the development packages `plexgraph-core` or `plexgraph-bridge`
  (used only inside this repository): both provide `plexgraph_core` and `plexgraph_bridge`.
- Before 1.0 the API may change in minor versions; say so in the changelog when it does.
