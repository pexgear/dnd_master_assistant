"""ARCHITECTURE.md, checked against the code it describes.

An architecture document rots. It rots quietly, and the first person to notice
is someone who trusted it and lost an afternoon.

So the claims in it that *can* be checked are checked here: which packages
exist, which way they depend, what the version constants are, what migrations
there are, which panels ship. Change the shape without changing the file and
this fails.

What is deliberately not checked is the prose about *why* -- the reasoning,
the costs, the known gaps. No test can tell whether that is still true, which
is exactly why it is the part worth reading and the part worth maintaining by
hand.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

import canon_keeper
import canon_keeper_protocol
from canon_keeper.plugin import API_VERSION

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "ARCHITECTURE.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), "ARCHITECTURE.md has been deleted, not merely gone stale"
    return DOC.read_text(encoding="utf-8")


def _packages() -> set[str]:
    return {
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and path.name.startswith("canon_keeper")
    }


# ----------------------------------------------------------------- the packages


def test_every_package_is_described(text):
    missing = {name for name in _packages() if name not in text}
    assert not missing, (
        f"{missing} exist but ARCHITECTURE.md does not mention them. A new "
        "package is a change of shape, which is what this file is for."
    )


def test_it_describes_no_package_that_does_not_exist(text):
    named = set(re.findall(r"`(canon_keeper\w*)`", text))
    # Module paths like `canon_keeper/net` are not package claims.
    named = {n for n in named if "/" not in n}
    ghosts = named - _packages()
    assert not ghosts, f"{ghosts} are described but do not exist"


def test_the_dependency_arrows_still_point_one_way():
    """The claim the whole split rests on."""
    for package in _packages() - {"canon_keeper"}:
        for source in (ROOT / package).rglob("*.py"):
            body = source.read_text(encoding="utf-8")
            reaching = re.findall(r"^\s*(?:from|import)\s+(canon_keeper)\b", body, re.M)
            assert not reaching, (
                f"{package}/{source.name} imports the app. ARCHITECTURE.md says "
                "nothing outside the app does, and the safety argument rests on it."
            )


def test_the_protocol_still_needs_nothing(text):
    """It is described as stdlib-only, which is why a headless install is cheap."""
    assert "stdlib only" in text or "standard library" in text
    root = Path(canon_keeper_protocol.__file__).parent
    allowed = set(sys.stdlib_module_names) | {"canon_keeper_protocol"}
    for source in root.rglob("*.py"):
        body = source.read_text(encoding="utf-8")
        for name in re.findall(r"^\s*(?:from|import)\s+(\w+)", body, re.M):
            assert name in allowed, f"{source.name} imports {name}"


# ------------------------------------------------------------------ the numbers


def test_the_protocol_version_is_right(text):
    stated = re.search(r"`PROTOCOL_VERSION = (\d+)`", text)
    assert stated, "ARCHITECTURE.md no longer states the protocol version"
    assert int(stated.group(1)) == canon_keeper_protocol.PROTOCOL_VERSION


def test_the_panel_api_version_is_right(text):
    stated = re.search(r"`API_VERSION = (\d+)`", text)
    assert stated, "ARCHITECTURE.md no longer states the panel API version"
    assert int(stated.group(1)) == API_VERSION


def test_the_frame_caps_are_right(text):
    """Both numbers, because the whole point is that they differ."""
    from canon_keeper_protocol import MAX_FRAME_BYTES, MAX_HOST_FRAME_BYTES

    assert f"({MAX_FRAME_BYTES // 1024} KB)" in text
    assert f"({MAX_HOST_FRAME_BYTES // 1024 // 1024} MB)" in text


# --------------------------------------------------------------- the migrations


def test_every_migration_is_listed(text):
    on_disk = sorted(p.name for p in (ROOT / "canon_keeper/db/migrations").glob("*.sql"))
    missing = [name for name in on_disk if name not in text]
    assert not missing, (
        f"{missing} are not in ARCHITECTURE.md. A migration is a change to the "
        "shape of the data, which is the thing this file describes."
    )


def test_it_lists_no_migration_that_does_not_exist(text):
    on_disk = {p.name for p in (ROOT / "canon_keeper/db/migrations").glob("*.sql")}
    listed = set(re.findall(r"`(\d{3}_\w+\.sql)`", text))
    assert not listed - on_disk, f"{listed - on_disk} are listed but missing"


# ------------------------------------------------------------------- the panels


def test_every_shipped_panel_is_mentioned(text):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    panels = config["project"]["entry-points"]["canonkeeper.panels"]
    missing = [name for name in panels if name not in text]
    assert not missing, f"{missing} ship as panels but are not described"


def test_the_console_scripts_still_exist(text):
    """The doc names what each package is for; a removed entry point is a lie."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]
    for module in ("canon_keeper_dm_agent", "canon_keeper_mcp"):
        assert any(module in target for target in scripts.values()), (
            f"{module} is described as having a command; it no longer does"
        )


# --------------------------------------------------------------- the file map


def test_the_file_map_is_not_fiction(text):
    """Every path in the layout block exists."""
    block = text.split("## Where to look", 1)[1].split("```", 2)[1]
    paths = re.findall(r"^\s{2}(\S+)", block, re.M)
    missing = [p for p in paths if not (ROOT / "canon_keeper" / p.rstrip("/")).exists()]
    assert not missing, f"{missing} are in the file map but not on disk"


def test_the_version_is_not_hard_coded_anywhere_in_it(text):
    """A doc that names the current version is out of date one release later."""
    assert canon_keeper.__version__ not in text, (
        "ARCHITECTURE.md names a version number. It describes the shape, which "
        "outlives any release; CHANGELOG.md is where versions belong."
    )
