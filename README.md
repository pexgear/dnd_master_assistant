# Canon Keeper

A dockable desktop assistant for running D&D 5e, built around one rule:
**what the DM actually says is the only source of truth.**

Every panel docks, undocks into a real window you can throw onto a second
monitor, and the whole arrangement can be saved as a named layout — one for prep,
one for the table, one for combat.

Runs on Windows, macOS and Linux. Your data is a single SQLite file you own.

## Status

Early. Phase 1 of the build plan: the plugin shell, the Characters panel and the
Cities panel. No AI and no microphone yet — those arrive in phases 2 and 3, and
nothing in this phase needs an API key or an internet connection.

## Install

Requires Python 3.11 or newer.

```bash
pip install canon-keeper
canonkeeper
```

### From a source checkout

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/canonkeeper
```

On macOS and Linux the venv paths are `.venv/bin/` instead of `.venv/Scripts/`.

## Where your data lives

One SQLite file per install, in the standard per-OS location — `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS, `~/.local/share` on Linux.
Find it from the app with **File ▸ Open Data Folder**. Back it up by copying it.

Set `CANONKEEPER_DATA_DIR` to put it somewhere else, for example on a synced
drive.

## Panels

**Characters** — NPCs and PCs, narrative-first: who they are, what they want,
what they are hiding, and what the party has actually worked out. Stat blocks
stay in your books.

**Cities & Places** — region, city, district, building, room. Characters are
parented to a place, so every location knows who is standing in it.

## Writing a plugin

Every panel in the app, first-party ones included, loads through the same entry
point group. A plugin is an ordinary Python package that declares:

```toml
[project.entry-points."canonkeeper.panels"]
weather = "my_pkg.panel:WeatherPanel"
```

and provides a class satisfying `canon_keeper.plugin.PanelPlugin`:

```python
from PySide6.QtCore import Qt
from canon_keeper.plugin import API_VERSION, AppContext

class WeatherPanel:
    id = "weather"          # permanent: it is the dock's objectName in saved layouts
    title = "Weather"
    api_version = API_VERSION

    def create_widget(self, ctx: AppContext):
        ...                  # ctx.repos, ctx.bus, ctx.campaign_id, ctx.log

    def default_area(self):
        return Qt.DockWidgetArea.BottomDockWidgetArea
```

Install it into the same environment and it appears on next launch. Panels never
import each other — they coordinate through `ctx.bus` signals, so a panel that
is not installed simply does not exist rather than breaking its neighbours.

A panel that fails to import, declares the wrong API version, or raises while
building its widget is disabled and reported under **Help ▸ Installed Panels**;
it never stops the app from opening. Set `CANONKEEPER_DISABLE_PLUGINS=id1,id2`
to turn one off from outside the app.

## Licence

MIT — see [LICENSE](LICENSE).

Canon Keeper uses PySide6, which is licensed under the LGPLv3. Installing it
from PyPI links it dynamically, which is what the LGPL asks for. If you
redistribute a bundled build, read the Qt licensing terms first.

No adventure text ships with this repository. Loading your own copy of a
published module into a tool you run privately is ordinary personal use; putting
that text in a public repository is not.
