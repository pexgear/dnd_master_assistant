# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

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
