"""The app has a face.

Small, and the sort of thing that breaks quietly: an icon that is not packaged
into the wheel loads fine from a checkout and is missing for everybody who
installed it, with nothing on screen to say so except a Python logo in the
taskbar.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon

from canon_keeper import app as app_module
from canon_keeper import assets


def test_the_icon_ships_with_the_package(qtbot):
    assert assets.icon_path().exists()


def test_it_is_inside_the_package(qtbot):
    """Not next to it: only what is under ``canon_keeper/`` goes in the wheel."""
    from pathlib import Path

    import canon_keeper

    package = Path(canon_keeper.__file__).resolve().parent
    assert package in assets.icon_path().parents


def test_qt_can_read_it(qtbot):
    assert QIcon(str(assets.icon_path())).isNull() is False


def test_it_is_square_and_big_enough_to_scale_down(qtbot):
    """Windows asks for 256, macOS for 512. One file, scaled by Qt."""
    sizes = QIcon(str(assets.icon_path())).availableSizes()
    assert sizes
    biggest = max(sizes, key=lambda s: s.width())
    assert biggest.width() == biggest.height()
    assert biggest.width() >= 256


def test_claiming_the_taskbar_never_raises(qtbot, caplog):
    """Cosmetic, and on three platforms. It must not be able to end the app."""
    import logging

    app_module._own_the_taskbar_entry(logging.getLogger("test"))
