# Canon Keeper

A dockable desktop assistant for running D&D 5e, built around one rule:
**what the DM actually says is the only source of truth.**

Every panel docks, undocks into a real window you can throw onto a second
monitor, and the whole arrangement can be saved as a named layout — one for prep,
one for the table, one for combat.

Runs on Windows, macOS and Linux. Your data is a single SQLite file you own.

## Status

Early, but usable at a table. The plugin shell with Characters, Cities and
Transcript panels; local speech-to-text; and LAN sessions with logins, shared
chat and dice, and role-filtered sharing.

No AI yet — nothing here needs an API key or an internet connection. The NPC
conversation holder is the next phase.

## Install

Requires Python 3.11 or newer.

### From a source checkout

Run the installer for your system. It finds a suitable Python, builds a
virtualenv, installs everything, and writes a launcher:

```bash
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
./install.sh
```

Then start it from that folder:

```bash
.\canonkeeper
```

```bash
./canonkeeper
```

Add `-Shortcut` (Windows) to also put Canon Keeper on your desktop.

> **`canonkeeper` on its own will not be found**, and that is expected: the
> command lives inside the project's virtualenv, which is only on your `PATH`
> while the virtualenv is activated. The `.\canonkeeper` launcher exists so you
> do not have to think about that. If you would rather activate it,
> `.\.venv\Scripts\Activate.ps1` (or `source .venv/bin/activate`) makes the bare
> name work for the rest of the session.

### With pip

```bash
pip install canon-keeper
canonkeeper
```

Installing into your own environment does put `canonkeeper` on `PATH` — provided
that environment's scripts directory is on it, which is not always true of the
Microsoft Store build of Python.

## Starting a campaign

The campaign comes first: the app opens a chooser before anything else, because
a Characters panel with no campaign behind it is a list of nobody.

- **On this computer** lists the campaigns you run. Each is a single `.sqlite3`
  file you own, so there is no login -- holding the file makes you its DM. Copy
  one to another machine and it appears there.
- **Join a session** lists sessions running on your network, plus ones you have
  joined before. That needs the username and password the DM gave you.

### Skipping the chooser

Tick **Remember my password for this session** when you join, and next time
picking that session fills the password in for you. Tick **Open this
automatically next time** and the app goes straight in without asking at all --
on either tab, so it works for the campaign you run as well as one you join.

Passwords go to the operating system's credential store: Windows Credential
Manager, the macOS Keychain, or the Secret Service on Linux. Never to a file of
ours. On a machine with no credential store the box is simply disabled and you
type your password each time.

To get the chooser back: **File > Open a Different Campaign...**, or **File >
Stop Opening This Automatically**, or start with `canonkeeper --choose`. If the
campaign file has been deleted or the saved password no longer works, the
chooser comes back on its own rather than failing a login you never saw.

## Playing together (LAN)

Open your campaign and press **Go online** in the Table panel. Players open
Canon Keeper, pick your session from the list, and log in.

Before anyone can join you need to give them a login -- **Table ▸ Players...**:
a username, a password, and the character they play. Chat then shows the
character's name, because at the table people are their characters.

**Speak** beside the chat box records you and puts the words in the box — it does
not send them. Correct whatever came out wrong, then press Enter. It runs
locally like the Transcript panel, and is primed with the names in your campaign
(or, for a player, the names shared with them), which is what makes it get
*Cragmaw Castle* right. The button is disabled with an explanation if
`faster-whisper` is not installed.

Dice are rolled **on the host**, never on the client that asked. `/roll 2d6+3`,
`/roll 4d6kh3` (keep highest three), `/roll 2d20kl1` (disadvantage), or the quick
buttons. `/r` works too.

### What players see

Every panel works for both roles, but a player's copy is built from what the
host sent, and the host sends only what you shared.

- **Characters** and **Cities** have a **Players see** button. Share an entity
  with the whole party, or with particular people -- so only the rogue knows
  about the contact in the Dock Ward.
- Players get the name, the one-liner, and the text you wrote for them: *what
  the party knows* on a character, *players read* on a place. Your motives,
  secrets, notes and rumours are never sent. Not hidden on their screen -- never
  sent.
- An entity you have not shared does not appear at all. Its existence is itself
  a secret.
- Players own their character sheet: hit points, conditions, inventory, their
  own notes. They cannot touch anyone else's, and the host enforces that rather
  than trusting the client.
- The Transcript stays yours alone.

Take a share back and it disappears from their screen, rather than going stale.

### Passwords

Your password is never sent over the network. The host sends a challenge, your
app proves it knows the password without transmitting it, and a recorded login
cannot be replayed. That matters because a LAN has no TLS, and people reuse
passwords.

Stored passwords are scrypt verifiers, never the password itself. Anyone holding
the campaign file could still log in as a player -- but they would already have
your secrets, which is the thing actually worth protecting.

### Hosting somewhere else

The host can be a separate machine -- a spare box on the LAN, or a server anyone
can reach. It hosts one campaign, and creating logins is part of the same
command because a dedicated server has no other way to be told who may join:

```bash
canonkeeper-server --db our-campaign.sqlite3 --add-player marco
canonkeeper-server --db our-campaign.sqlite3
```

The DM then joins it like everyone else, with a `--add-dm` login.

**Windows will ask** to allow the app on private networks the first time you
host. Say yes, or nobody can connect.

### Playing over the internet

Press **Share on the internet** while hosting. That publishes the session
through [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) and gives you an
address like `wss://your-machine.tailXXXX.ts.net` to send your players.

It solves three things at once:

- **NAT** — the tunnel dials outward, so no port forwarding, and it works behind
  carrier-grade NAT where forwarding is not even possible.
- **Encryption** — Tailscale provisions a real certificate to your machine, so
  the connection is `wss://` rather than plain text. Nothing to configure or
  renew.
- **The address** — a stable hostname, instead of a home IP that changes.

**Only you install Tailscale.** Your players need nothing: they paste the
address into the chooser and log in as usual. Sign in once at
[tailscale.com/download](https://tailscale.com/download).

Funnel has to be switched on for your tailnet the first time, and it is off by
default. The app checks before doing anything and, if it is off, offers an
**Open Tailscale settings** button that takes you straight to the right page —
because the `tailscale` command itself does something unhelpful here: it prints
the instructions and then sits there waiting for you to act, looking like a
hang.

Sharing stops when you press the button again, when you leave the session, or
when you close the app, so the tunnel never outlives the game.

**Copy invite** gives you the right address to send either way: the public one
when you are sharing, your LAN address otherwise.

If you would rather not use a tunnel, the alternatives are unchanged: forward a
port on your router (but then the traffic is unencrypted), or run
`canonkeeper-server` on a machine that already has a public address.

## Naming the panels

**Panels ▸ Rename Panels…** lets you call things whatever you call them at your
table. There are three layers, and the more specific one wins:

| | Who sets it | Who sees it |
|---|---|---|
| **Your name** | you | you |
| **The party calls it** | whoever runs the campaign | everyone in the session |
| **Default** | the panel | the fallback |

So the DM can rename *Cities & Places* to **The Sword Coast** for the whole
table, and a player who prefers *Places* can still call it that on their own
machine. Clearing your own name falls back to the party's; clearing that falls
back to the default. Hover a panel's title bar to see which is which.

Party names travel with the session and update live, so renaming something
mid-game reaches everyone immediately. What you call a panel yourself is never
sent anywhere.

## Light and dark

**View ▸ Theme** offers *Follow System*, *Light* and *Dark*. The default follows
your desktop and switches live when your OS does — handy if your machine already
goes dark in the evening, which is when you are usually running a game.

The choice is remembered. Transcript highlight colours are picked separately for
each appearance, so names stay legible either way.

## Where your data lives

One SQLite file per campaign, in `campaigns/` under the standard per-OS location
— `%APPDATA%` on Windows, `~/Library/Application Support` on macOS,
`~/.local/share` on Linux. Find it from the app with **File ▸ Open Data Folder**.
Back a campaign up by copying its file; move it to another machine and it shows
up in the chooser there.

Your own settings, theme and dock layout live separately in `profile.sqlite3`,
so they follow you rather than the campaign — and so a player, whose campaign
lives on someone else's machine, still has somewhere to keep them.

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

## Contributing and releases

Tests run on Windows, macOS and Linux on every push. To run them yourself:

```bash
pytest
```

Releases are cut by pushing a `v*` tag; see [RELEASING.md](RELEASING.md).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT — see [LICENSE](LICENSE).

Canon Keeper uses PySide6, which is licensed under the LGPLv3. Installing it
from PyPI links it dynamically, which is what the LGPL asks for. If you
redistribute a bundled build, read the Qt licensing terms first.

This app includes material from the **System Reference Document 5.1**
("SRD 5.1") by Wizards of the Coast LLC, available at
<https://dnd.wizards.com/resources/systems-reference-document>, licensed under
the [Creative Commons Attribution 4.0 International
License](https://creativecommons.org/licenses/by/4.0/legalcode). The JSON
transcription comes from [5e-bits/5e-database](https://github.com/5e-bits/5e-database).

No adventure text ships with this repository. Loading your own copy of a
published module into a tool you run privately is ordinary personal use; putting
that text in a public repository is not.
