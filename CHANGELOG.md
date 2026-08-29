# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

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
