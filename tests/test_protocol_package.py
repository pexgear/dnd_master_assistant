"""The protocol package must stay installable without the app.

Its whole reason to exist separately is that an agent, a bot or an MCP server
can speak to a session without installing Qt. That is a property of the import
graph, and import graphs drift silently -- one convenient ``from canon_keeper
import ...`` and a headless client suddenly needs 660 MB of PySide6.

So it is checked here rather than remembered.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import canon_keeper_protocol

PACKAGE = Path(canon_keeper_protocol.__file__).parent

#: Everything the package is allowed to import. Standard library only, plus
#: itself. Adding to this list means the headless install stops being free --
#: do it deliberately, not to make a test pass.
ALLOWED_ROOTS = frozenset(sys.stdlib_module_names) | {"canon_keeper_protocol"}


def _imported_roots(source: Path) -> set[str]:
    """Top-level package names imported by one module."""
    roots: set[str] = set()
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import (level > 0) cannot leave the package.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_the_protocol_imports_nothing_but_the_standard_library():
    offenders: dict[str, set[str]] = {}
    for source in sorted(PACKAGE.rglob("*.py")):
        outside = _imported_roots(source) - ALLOWED_ROOTS
        if outside:
            offenders[source.name] = outside

    assert not offenders, (
        f"{offenders} -- the protocol package must stay installable without the "
        "app. If this dependency is genuinely needed, add it to ALLOWED_ROOTS "
        "and accept that headless clients now carry it."
    )


def test_it_never_imports_the_app():
    """The arrow points one way. Canon Keeper knows the protocol; not the reverse."""
    for source in sorted(PACKAGE.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        assert "canon_keeper." not in text.replace("canon_keeper_protocol.", ""), (
            f"{source.name} reaches back into the app, which would make the "
            "dependency circular and the headless install impossible."
        )


def test_it_imports_in_a_bare_interpreter_with_no_qt():
    """The real proof: import it in a subprocess where PySide6 is unimportable."""
    program = (
        "import sys;"
        "sys.modules['PySide6'] = None;"  # any attempt to use it raises
        "import canon_keeper_protocol as p;"
        "print(p.PROTOCOL_VERSION)"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=PACKAGE.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(canon_keeper_protocol.PROTOCOL_VERSION)


def test_the_public_names_are_all_reachable():
    """__all__ that lies is worse than no __all__."""
    for name in canon_keeper_protocol.__all__:
        assert hasattr(canon_keeper_protocol, name), f"__all__ names a missing {name}"


# ----------------------------------------------------------------- the layering
#
# Four packages now, and the whole argument for splitting them is the direction
# of the arrows:
#
#     canon_keeper_protocol        (stdlib only)
#         ^                ^
#     canon_keeper_client   canon_keeper (the app)
#         ^          ^
#   dm_agent      mcp
#
# Nothing outside the app may import the app. That is what keeps a headless
# install free of Qt, and what keeps "the agent cannot reach the database" a
# fact about the import graph rather than a habit.

import canon_keeper_client
import canon_keeper_dm_agent
import canon_keeper_mcp

OUTSIDE_THE_APP = (
    canon_keeper_protocol,
    canon_keeper_client,
    canon_keeper_dm_agent,
    canon_keeper_mcp,
)


def test_nothing_outside_the_app_imports_the_app():
    offenders: dict[str, set[str]] = {}
    for package in OUTSIDE_THE_APP:
        root = Path(package.__file__).parent
        for source in sorted(root.rglob("*.py")):
            reaching = {
                name
                for name in _imported_roots(source)
                if name == "canon_keeper"
            }
            if reaching:
                offenders[f"{package.__name__}/{source.name}"] = reaching

    assert not offenders, (
        f"{offenders} -- these reach into the app. A client that can import "
        "canon_keeper can open a campaign database directly, which is exactly "
        "the authority these packages are built not to have."
    )


def test_the_client_needs_no_qt():
    """The agent and the MCP server both sit on it, and neither has a window."""
    root = Path(canon_keeper_client.__file__).parent
    for source in sorted(root.rglob("*.py")):
        assert "PySide6" not in _imported_roots(source), (
            f"{source.name} imports Qt; the headless client must not."
        )
