# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

- **Autopilot.** Hand the table to an agent when you want a break, and take it
  back by pressing the button again -- mid-sentence if that is when you pressed
  it. The agent is on the roster as an agent, the switch is announced in the
  chat, and it is never remembered between sessions. While it is off the host
  refuses the agent's messages outright, so "off" is enforced at the table
  rather than trusted to the agent. Give a campaign an agent login with
  Pressing it while hosting does everything: it makes the agent's login, keeps
  the password in your credential store, and starts the agent against your own
  session. An agent you started yourself, on this machine or a spare box, is
  left alone. **Agent...** in the Table panel is where the key and model live,
  and it stays reachable so a mistyped key can be corrected.
- **It waits for a pause before answering.** A turn is a lull, not a message:
  three players talking to each other is answered once, when they stop, rather
  than interrupted three times. If the DM answers first, the queued reply is
  dropped. `--pause` tunes how long it waits.
- **You can see it thinking.** *Autopilot is writing...* appears under the chat
  while it composes, because several seconds of silence looks exactly like a
  broken agent. Anyone at the table can report it, not just the agent.
- **What it has cost, on the DM's screen.** Answers, tokens and dollars for the
  session, updated after each turn. Only the DM is shown it -- it is their bill
  -- and only the agent may report it.
- **When the agent stops, you are told.** It used to fail in silence: the
  process exited, the button stayed on, and the table waited for a machine that
  was not there. Its output is now kept and shown, autopilot switches itself
  off, and a missing `anthropic` package is caught before anything is started.
- **Fixed: two campaigns shared one agent password.** The credential store was
  keyed by `campaign.id`, which is 1 for almost every campaign there has ever
  been -- so opening a second campaign quietly overwrote the first's password,
  and its agent could no longer log in. Keyed by the campaign's own id now, and
  a saved password that the host would refuse is replaced rather than handed
  over to fail at the door.
- **A lone DM gets answered.** The agent answered players only, so switching
  autopilot on with nobody else at the table did nothing at all -- which is
  exactly what a broken agent looks like. With players present the DM speaking
  still means "I have answered"; with nobody else there, they are the table.
- **When it cannot answer, you are told why.** A failed model call used to be
  silent: the indicator flashed and nothing followed. The reason -- usually an
  expired or mistyped key -- now reaches the DM privately, and only the DM.
- **Workspace id, for keys that need one.** An identity-linked key is refused
  outright until it names its workspace. **Agent...** has a field for it, and
  it is sent as `anthropic-workspace-id`. Nothing can detect this in advance;
  the API says so, and now you see what it said.
- **Fixed: Haiku refused every request.** The agent asked for a fast answer
  using a parameter Haiku 4.5 does not accept, and the model rejected the whole
  turn over it. It is now sent only to models known to take it -- and if that
  list is ever wrong, the turn is retried without it rather than failed.
- **Say what you mean.** `canonkeeper-mcp` exposes one seat at the table to an
  MCP client, so a player can talk instead of typing. It holds one login and has
  exactly that login's authority: dice are rolled on the host, and a change to a
  character is a request the DM answers.
- **The wire contract is its own package.** `canon_keeper_protocol` depends on
  nothing but the standard library, so anything headless can speak to a session
  without installing Qt. Nothing outside the app imports the app, which is
  checked by tests rather than remembered.

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
