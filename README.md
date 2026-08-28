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

## Playing together (LAN)

**Table** panel ▸ *Host session*. You get a six-character join code; read it out.
Players open Canon Keeper, hit *Join session*, and your game is already listed —
the host broadcasts a beacon on the local network, so nobody types an IP address.
Then chat and shared dice.

Players can start in a reduced mode with just the table and none of your prep:

```bash
canonkeeper --player
```

Dice are rolled **on the host**, never on the client that asked. `/roll 2d6+3`,
`/roll 4d6kh3` (keep highest three), `/roll 2d20kl1` (disadvantage), or the quick
buttons. `/r` works too.

### Hosting somewhere else

The DM's app hosting the session is the simple case, but the host can be a
separate machine — a spare box on the LAN, or a server anyone can reach:

```bash
canonkeeper-server --name "Our campaign" --port 8765
```

It prints a join code and everyone connects to it, the DM included. Same server
code as the in-app host; where it runs is a deployment choice.

**Windows will ask** to allow the app on private networks the first time you
host. Say yes, or nobody can connect.

**Over the internet**, someone has to be reachable: port-forward the host, or put
everyone on a tunnel like Tailscale. Nothing in a chat protocol can avoid that —
it is why the dedicated-server option exists.

### What is and is not shared

Only chat and dice cross the wire today. Your characters, places and transcript
stay on your machine — including the `secrets` field, which is DM-only by design.

## Light and dark

**View ▸ Theme** offers *Follow System*, *Light* and *Dark*. The default follows
your desktop and switches live when your OS does — handy if your machine already
goes dark in the evening, which is when you are usually running a game.

The choice is remembered. Transcript highlight colours are picked separately for
each appearance, so names stay legible either way.

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

**Transcript** — press **F9** (or the Record button), narrate a beat, press it
again. The clip is transcribed locally and appears on screen. You can also just
type a line instead.

Names you already know are **highlighted as they appear** — places in green,
characters in blue, factions and items in their own colours. Double-click one to
jump to it in the panel that owns it.

Names you *don't* know yet are one gesture away from existing: select the words
(or just right-click a single word) and choose **Add "…" as ▸ Character /
Place / Faction / Item**. The name lights up immediately, the owning panel
selects it so you can fill in details, and — the part that compounds — it joins
the Whisper glossary, so the next time you say it out loud it is transcribed
correctly.

Right-click a line for **Edit this line… / Transcribe again / Delete**. Editing
matters: an utterance is the only thing allowed to be the source of a fact, so
the text here is what everything downstream will treat as what you actually
said.

### Transcription

Transcription runs entirely on your machine — no audio leaves it, and there is
nothing to pay for. It needs one extra package:

```bash
pip install faster-whisper
```

Without it the panel still opens and tells you the command; recording is
disabled until it is installed.

The first recording downloads the chosen model (a few hundred MB for `small`,
the default). Larger models in the dropdown are more accurate and slower. If you
have an NVIDIA GPU *and* the CUDA runtime installed it will use it, and quietly
falls back to the CPU if the CUDA libraries are missing.

**The glossary is the reason this works on fantasy names.** Before every
transcription the app builds a prompt from the names in your Characters and
Cities panels and primes Whisper with it. Same audio, same `tiny` model:

| | Result |
|---|---|
| Without glossary | "Crag**more** Castle … **Silder Hall Winter**" |
| With glossary | "**Cragmaw** Castle … **Sildar Hallwinter**" |

So the more you write down, the better the transcription gets. If a name still
comes out wrong, fix the row — and add the name to an entity so it is right next
time.

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
