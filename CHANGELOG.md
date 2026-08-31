# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

## 0.4.1

One-shots, and a table that says what is happening.

### One-shots

- **Start a one-shot** in the chooser builds a campaign from a template, with
  the characters, places, facts, shares and logins already in it -- the same
  every time. Three ship: *The Last Coach*, an evening for three; *Test
  Combat*, which opens on initiative and ends when the fight does; and *Test
  Table*, for exercising the app without typing a world in first.
- What you get is an ordinary campaign. It only remembers which template it
  came from, which is what lets **File > Start Again from the Beginning** put
  it back. **File > Storyline...** holds the beats and where it ends, and
  **File > Keep This One** makes it a campaign of your own.

### At the table

- **A refused change is put back.** Saying no used to leave the rejected value
  on the player's screen, which reads exactly like it was accepted. The host
  now sends the character as it actually stands and the panel reloads --
  including over a form still being edited, because the host's copy is the true
  one.
- **The log is out of the way.** The chat hid the game under the app talking
  about itself. Arrivals, departures, autopilot switching and the rest sit
  behind **Show log**. Anything addressed to you -- a refusal, a roll, an agent
  that could not answer -- is never filtered.
- **Errors are in the log, and say so.** **Show log** turns red when something
  goes wrong, and the message appears in the status bar at once. Hidden *and*
  unannounced would be the worst of both.
- **A panel with something new is highlighted** until you look at it, then the
  colour fades. An update in a panel behind another tab may as well not have
  arrived.

### Fewer buttons

- **Going online publishes it.** Hosting for the people in the room and for the
  one who could not make it was two buttons and one wish. **Share on the
  internet** is gone. A session whose publish fails stays up on your network --
  that is half a thing succeeding, not an error.
- **Join session** is gone from the DM's view, where it never meant anything.
- **Fixed: the agent was listed as a player.** It answers for the DM, and now
  says so.
- Double-clicking any list in the chooser opens what it points at.

### Underneath

- **Fixed: the canon log could reorder itself between reads.** Facts were
  ordered by timestamp alone, so several asserted in the same millisecond came
  back in whatever order SQLite chose. Found by the test that asserts two runs
  of a template are identical.

## 0.4.0

Autopilot: hand the table to an agent, and take it back.

### Autopilot

- **Press it and an agent answers for you** -- for a break, a second voice, or a
  shopkeeper haggled with while you read ahead. Press it again and the table is
  yours, mid-sentence if that is when you pressed it.
- **It cannot speak while the switch is off.** Not by good behaviour: the host
  refuses its messages. It stays connected and listening, so switching back on
  is instant, but nothing it says reaches the table.
- **It is named on the roster as an agent**, and switching autopilot on or off
  is announced in the chat and kept in the log. A table deserves to know when it
  is being answered by a machine.
- **It has no path to your campaign.** It holds a socket and a login, exactly
  like a player's app, even when the app started it for you.
- **It never rolls.** Dice are the host's; it asks for a roll like anyone else.
- **A turn is a lull, not a message.** Three players talking to each other gets
  one answer when they stop, not three interruptions. Answer them yourself and
  its queued reply is dropped. `--pause` tunes the wait.
- **Autopilot is never remembered between sessions.** Opening a campaign to find
  a machine already running your table is not a state to arrive in by accident.

### Setting it up

- **The button does the work.** It creates the agent's login, keeps its password
  in your credential store, and starts the agent against your own session. An
  agent you started yourself, on this machine or a spare box, is left alone.
- **Agent...** holds the key, the model and -- for keys that need one -- a
  workspace id. It stays reachable, so a mistyped key can be corrected.

### Knowing what it is doing

- ***Autopilot is writing...*** appears under the chat while it composes, since
  several seconds of silence looks exactly like a broken agent.
- **What it has cost** -- answers, tokens, dollars -- on the DM's screen only,
  updated after every turn rather than at the end.
- **When it cannot answer, you are told why**, privately. An expired key, a
  refused parameter, a stopped process: all of it used to be silence.

### Saying what you mean

- **`canonkeeper-mcp`** exposes one seat at the table to an MCP client, so a
  player can talk instead of typing. It holds one login and has exactly that
  login's authority: dice are rolled on the host, and a change to a character is
  a request the DM answers.

### Underneath

- **The wire contract is its own package.** `canon_keeper_protocol` depends on
  nothing but the standard library, so anything headless can speak to a session
  without installing Qt. Nothing outside the app imports the app, and that is
  checked by tests rather than remembered.
- **Fixed: two campaigns could share one saved password.** Credentials were
  keyed by `campaign.id`, which is 1 for almost every campaign there has ever
  been. Keyed by the campaign's own id now, and a saved password the host would
  refuse is replaced rather than handed over to fail at the door.

## 0.3.1

- **Speak into the chat.** A **Speak** button beside the chat box transcribes
  what you said into the box for you to correct before sending — useful for
  anyone who would rather talk than type mid-scene. Local, and primed with your
  campaign's names.

## 0.3.0

Character sheets, and a table where the DM decides.

- **The chat is kept.** Rejoin a campaign and the last hundred messages are
  already there, so a session picks up where the last one stopped. Everything is
  kept and filed by evening; only the recent tail is loaded.
- **You are the authority on your campaign.** A player's change is checked
  against the copy they were actually sent, not against anything their app
  claims. If you changed the character in the meantime their change is refused
  and their screen corrects itself, and anything they had proposed against the
  old sheet is refused automatically.
- **Players ask, you decide — for everything.** Nothing a player changes is
  written on their say-so, hit points included. Their sheet's button reads *Ask
  my DM*, a **Waiting for you** button appears with the queue, and refusing
  prompts you for a reason which is sent privately to them. Requests go to you
  alone, not the whole table.
- **Hand a character to a player** with *Played by* in the Characters panel, or
  by assigning it in **Table ▸ Players…**. They then see its whole sheet.
- **Reconnecting is cheap.** Your copy is cached, so the app shows your
  character before it connects and a reconnect fetches only what changed.
- **Equipment and spells on the sheet**: add gear (worn armour changes the
  armour class), learn and forget spells, tick which are prepared.
- **Guided character creation.** **Build...** in the Characters panel walks
  through species, class, abilities, skills and spells, with the standard array
  and point buy, and adds the class's starting equipment at the end.
- **Players get their sheet too.** Their own characters show the whole sheet and
  they keep their own hit points and conditions; level, class and ability scores
  stay the DM's to set. Other people's sheets are read-only.
- **Character sheets.** A **Sheet** tab beside Story in the Characters panel:
  species, class, level, ability scores and skills, with hit points, armour
  class, saving throws, skill bonuses and spell slots worked out as you type.
  Built on the SRD 5.1, bundled, so nothing needs the internet.
- Players can own **more than one character**, and see the whole sheet of each.
  Anyone else's shows only what a party would know: class, level, hit points,
  conditions. An NPC's statblock is never sent.
- Every character now carries a **version**, so two people editing at once no
  longer means one edit silently disappearing.

- **Panels can be renamed.** Three layers: what you call it, what the party
  calls it, and the default — the more specific one wins. The DM's names travel
  with the session and update live; yours stay on your machine.

## 0.2.0

Everything below the shell: campaigns, players, and playing together.

### Campaigns

- The app now starts from a **campaign chooser**. Campaigns you run are files on
  your machine; joining someone else's needs the login they gave you.
- **Remember my password** saves it to your operating system's credential store,
  and **Open this automatically next time** skips the chooser entirely.
- **File ▸ Open a Different Campaign…** returns to the chooser without
  restarting.

### Playing together

- **Go online** hosts your campaign so players can join over the network. They
  are listed automatically on a LAN — nobody reads out an IP address.
- **Share on the internet** publishes the session through Tailscale Funnel, with
  a real certificate. Only the host installs Tailscale.
- Shared chat and **dice rolled on the host**, so nobody can nudge them.
  `/roll 2d6+3`, `4d6kh3`, `2d20kl1`, or the quick buttons.
- Per-campaign logins with the character each player plays. Chat shows the
  character's name.
- **Players see** on any character or place: share it with the whole party or
  with particular people. Your motives, secrets and notes are never sent — an
  unshared entity does not reach a player's machine at all.
- Players own their character sheet; the host enforces that they cannot touch
  anyone else's.
- Passwords are never sent over the network, and a recorded login cannot be
  replayed.

### Transcript

- Press **F9**, narrate a beat, press it again: the clip is transcribed locally
  by Whisper. No audio leaves your machine and there is nothing to pay for.
- Known names are **highlighted** as they appear, and selecting unknown ones
  turns them into characters or places — which feeds them back into the
  transcriber, so the next time you say the name it comes out right.

### Everywhere

- **Light and dark themes**, following your desktop by default.
- Panels dock, undock into real windows, and the arrangement can be saved as
  named layouts.

## 0.1.0

The shell: dockable plugin workspace, Characters and Cities panels, saved
layouts, and a SQLite campaign file you own.
