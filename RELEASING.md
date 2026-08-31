# Releasing

Cutting a release is one command. Everything else is automated, and the
automation refuses to publish anything that does not pass its tests.

## How it fits together

| File | When it runs | What it does |
|---|---|---|
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | every push to `main`, every PR | Tests on Windows, macOS and Linux × Python 3.11 and 3.12, then builds the package to prove it still builds |
| [`.github/workflows/release.yml`](.github/workflows/release.yml) | a `v*` tag is pushed | Checks the tag against the code, re-runs the tests on all three platforms, builds, and creates the GitHub Release |

Nothing publishes on an ordinary push. Pushing a tag is the deliberate act.

## Cutting a release

**1. Bump the version.** It lives in exactly one place:

```python
# canon_keeper/__init__.py
__version__ = "0.3.0"
```

`pyproject.toml` reads it from there, so the two can never drift.

**2. Write the changelog entry.** Add a section to
[`CHANGELOG.md`](CHANGELOG.md) describing what changed for someone using the
app, not what changed in the code.

**3. Check the map still fits.** If the release changed the shape of things --
a new package, a migration, a version constant -- update
[`ARCHITECTURE.md`](ARCHITECTURE.md). The suite catches the structural claims,
but not whether the reasoning is still true.

**4. Commit, tag, push:**

```bash
git commit -am "Release 0.3.0"
git tag v0.3.0
git push && git push --tags
```

That last push is what starts the release. Watch it under the repository's
**Actions** tab; it takes a few minutes, most of it the test matrix.

## What the automation will refuse to do

- **Publish a release whose tag disagrees with the code.** Tagging `v0.3.0`
  while `__version__` still says `0.2.0` fails immediately, with a message
  telling you to bump and retag. Otherwise you get a release whose artifacts are
  named after a different version than the tag on the box.
- **Publish anything that fails its tests**, on any of the three platforms.

## Version numbers

Ordinary [semantic versioning](https://semver.org), read from the point of view
of someone using the app:

- **Patch** (0.2.**1**) — fixes, nothing new to learn.
- **Minor** (0.**3**.0) — new panels or features; existing campaigns keep working.
- **Major** (**1**.0.0) — something you have to act on: a campaign file that
  needs converting, a plugin API change, a setting that moved.

While the leading number is 0, minor releases are allowed to break things — but
say so plainly in the changelog when they do.

**Pre-releases** are automatic: any tag that is not three plain numbers
(`v0.3.0-rc1`, `v0.3.0-beta`) is published as a pre-release and does not become
the "latest" download.

## Fixing a bad release

Tags are cheap; deleting a published one is not. If a release is broken, cut the
next patch version rather than re-pointing the tag — someone may already have
installed it, and a tag that changes meaning is worse than a version with a
short life.

```bash
git tag -d v0.3.0 && git push --delete origin v0.3.0   # only if nobody has it
```

## Publishing to PyPI

Off by default, because it needs an account only you can create.

1. Create the `canon-keeper` project on PyPI.
2. Add a **trusted publisher** for it: this repository, workflow
   `release.yml`, environment `pypi`. Trusted publishing uses a short-lived
   OIDC token, so there is no API key to store or leak.
3. Create a `pypi` environment in the repository settings.
4. Set the repository **variable** `PUBLISH_TO_PYPI` to `true`.

Until step 4, the PyPI job is skipped and releases stay on GitHub only.

## What a release contains today

A wheel and an sdist — so `pip install canon-keeper` works, and the GitHub
Release page has both files attached.

It does **not** yet contain a double-clickable application. That needs
PyInstaller bundles built on each operating system, and on macOS it also needs
signing and notarisation or the download will be refused. Worth doing when
players outnumber developers; not before.
