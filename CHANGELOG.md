# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

## 0.5.1

0.5.0's combat, finished: the rules it was missing, turns that end by
themselves, and enough of it happening on screen to follow without reading the
chat.

**Everyone at a table needs this version.** The wire moved to 4 — a turn ending,
a rule bending, and everything you now watch happen are all new things said over
it — and a mixed table is refused at the door with a readable reason rather than
half-working. Campaign files are upgraded when you open them, and nothing needs
converting by hand.

### Combat, continued

- **A speed limit.** A turn's movement is what the character's speed allows --
  six squares for most people, read off the sheet, with `overrides.speed` for a
  monster that differs. Checked when a turn is proposed *and* again when it is
  accepted, since the map moves while somebody is deciding.
- **What you have left.** The Combat panel shows the turn in progress: *"Your
  turn: 15 feet of 30 left, 15 used · attack still to come."*
- **Anything else, or done?** After you act, the turn is still yours -- say what
  else you do, press **Done**, or the turn passes on its own after fifteen
  seconds. The clock runs on the host, so it is a promise to the whole table
  rather than to whoever's laptop is awake. Anything you type stops it.
- **Taking a turn without autopilot.** **Attack...** in the Combat panel: who is
  up, a target, a weapon off their sheet. The host still rolls it.
- **Autopilot takes the monsters' turns.** It waits a few seconds in case
  somebody objects, then acts. Player characters are never taken this way --
  theirs are proposed and confirmed, as before.
- **The DM can overrule the rules.** Ask autopilot for something the rules
  refuse -- a creature moving out of turn, or further than its speed -- and it
  comes back to you naming the rule it breaks: *allow it, or not*. Squares off
  the map, or ones somebody is standing in, are not rules and are still refused
  outright.

- **Autopilot rolls its attacks.** It could move a goblin and talk about it
  hitting somebody, and had no way to actually swing -- so it described
  outcomes instead of asking for them. Now it swings, the host rolls, and it
  narrates what came back.
- **Simulate turn.** Hand a character to autopilot for this fight -- an empty
  chair, or a player who has stepped out. Either of you can do it, from the
  Combat panel, and either can take them back. Everyone is told, and the
  initiative order says which of them is being played by a machine.
- **A machine-played turn ends by itself.** Monsters and handed-over characters
  acted and then held the table: the only thing that ever ended a turn was a
  person pressing **Done**, and there is no person. The turn now passes a few
  seconds after autopilot stops acting -- each thing it does restarts the wait,
  and it does not run while autopilot is still thinking.
- **Things you pick up go on your sheet.** Autopilot records loot as it hands
  it over, instead of describing a sword that then exists nowhere. With
  autopilot off the DM still types it into the Inventory field.

### You can see it happen

- Tokens **walk square by square** instead of jumping.
- An attacker **leans in**, and the damage floats off whoever took it -- or the
  word *miss*, which is half of what happened.
- A creature at zero **fades off the map**, and stays in the initiative order so
  you can bring them round.
- Everyone sees the same thing at the same moment: the host says what happened
  and every screen draws that, rather than each inventing its own version. A
  creature you have not been shown does not animate on your map.

## 0.5.0

Combat: a map, an initiative order, and a way for a player to take their turn
in plain words.

**Everyone at a table needs this version.** The wire moved to 3, and a mixed
table is refused at the door with a readable reason rather than half-working.
Campaign files are upgraded when you open them, and nothing needs converting by
hand.

### Combat

- **A new Combat panel**: an initiative order and a grid, side by side. **New
  fight** makes one immediately -- no questions -- and **Add...** puts
  characters and NPCs into it. Name it or resize it later, under **Fight...**,
  if you ever want to.
- **Drag someone out of the order and onto the map** to place them, or drag a
  token from square to square to move it. **Roll initiative** rolls a d20 for
  everyone and adds their Dexterity where there is a sheet to read it from;
  double-click a row to set one by hand.
- **Start**, **Next turn** and **End fight** keep the round. Taking whoever is
  up out of the fight passes the turn on rather than dropping it, so nobody
  gets a second go.
- **Ctrl-click a square** to put something in the way -- a rock, a pillar, an
  overturned cart. Nobody can stand there and anybody can hide behind it. The
  **+ / −** buttons on the edges of the map push the walls in and out a row at
  a time.
- **Off the map is not out of the fight.** Someone who has fled, or has not
  come through the door yet, keeps their place in the order. Right-click for
  either.
- **Squares are numbered from the middle.** 0,0 is the centre, x to the right
  and y downwards, with rulers along two edges. "The one at minus three, two"
  is a square everyone can find, and it is still that square after the map
  grows.
- **Players see the fight as it happens**, read-only, and see exactly the
  creatures you have shared with them. Putting a monster on the map does *not*
  reveal it, so you can lay an ambush out in front of them. Tokens the party
  cannot see are drawn dotted on your map, and right-clicking one offers to
  share it.
- *Test Combat* opens on round one with everyone already placed, the terrain
  laid out and the order already rolled -- the same fight every time.

### Taking a turn

- **The chat says when it is your turn.**
- Say what you want in plain words -- *"I get behind the orc and hit it with my
  axe"* -- and autopilot works out what that is in rules and hands it back:
  *"Move to 0,-1 and attack Yeemik with a battleaxe."*
- The map shows it while you decide: a dotted line to where you would end up, a
  ghost of your token there, and a sword over whoever you would hit.
- The chat box waits for **Do it**, **Say more...** or **Refuse**. **Say
  more...** unlocks it for one message and what comes back is the same turn,
  changed. Nothing touches your character until you accept -- then the host
  moves you and rolls the attack, with your bonus off your sheet against the
  target's armour class.
- Weapon attacks only, and melee reaches one square. Spells, advantage and the
  rest are still the DM's to rule on.

### Autopilot

- **It can run a fight.** Start one, place everyone where the scene it just
  described put them, add the cover it mentioned, move monsters, pass the turn,
  and put a player's turn to them. All of it goes over the wire through the
  same checks your own buttons use, so it cannot build a fight the app could
  not have built itself -- and none of it works with the switch off. Start the
  agent with `--talk-only` to keep its hands off the map entirely.
- **While autopilot is on, what you type no longer reaches the party.** There
  is one voice at the table and it is the agent's. Your line goes to it instead
  -- marked *(to autopilot)* on your own screen -- and it works your direction
  into the scene in its own words, without repeating it back or letting on that
  anybody said anything. Press the switch to speak to the party yourself.
- **It answers you.** It used to drop whatever was queued the moment the DM
  spoke, which is how autopilot came to look broken: you switched it on, said
  something, and nothing ever happened.
- **It reads more of the conversation**, with the pauses marked, so an exchange
  that began several messages up is answered as a whole rather than from
  whichever line happened to arrive last.

### Rolls you can click

- When your DM writes "make a DC 14 Perception check", the words become a link.
  Clicking it opens a die that already knows what your character adds, and the
  answer comes from the table's dice, not from your own machine. Skill checks,
  saving throws, ability checks, initiative and plain dice notation all count.
- Only your DM's lines -- and autopilot's, when it is standing in for them --
  and only when you have a character to roll with.

### Sheets

- **The bundled one-shots have real characters**: species, class, level,
  abilities, skills and equipment. Every sheet now passes the same validation
  the host runs on a player's edit, which the old ones did not -- so a player's
  first change to one came back refused as an illegal sheet.
- **And real monsters.** Every NPC has a statblock: ability scores, armour
  class, hit points and what it is carrying. Which is also what makes them
  something a character can attack.

### Fixed

- **Private lines are no longer read out to the next player who logs in.** The
  chat log is handed over on every login, and everything the host had ever told
  the DM privately was in it: refusals, requests waiting for approval, and the
  text of an expired API key. Lines now record who they were for, and old ones
  stay public, which is what they were.

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
