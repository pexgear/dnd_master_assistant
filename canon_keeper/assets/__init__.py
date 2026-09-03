"""Files that ship with the app and are read at runtime.

Only the icon so far. Reached through :func:`path` rather than by building one
from ``__file__`` at each call site, because an app installed as a wheel, run
from a checkout, and frozen into a bundle put their files in three different
places, and exactly one piece of code should have an opinion about that.
"""

from __future__ import annotations

from pathlib import Path

#: The application icon. One file at a size every platform can scale down from:
#: Windows wants 16 through 256 for the taskbar, macOS wants 512, and Qt does
#: the scaling well enough that carrying a dozen files would be work for
#: nothing.
ICON = "icon.png"


def path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def icon_path() -> Path:
    return path(ICON)
