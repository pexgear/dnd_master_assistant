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
