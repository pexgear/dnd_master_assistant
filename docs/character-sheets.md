# Character sheets — architecture

Full 5e sheets on SRD 5.1, created by players and by the DM. This records the
decisions and why, because most of them are cheap now and expensive later.

## Layering

```
  content/      SRD JSON + campaign homebrew, merged      no Qt, no database
  rules/        derivations and validation over a sheet   no Qt, no database
  ─────────────────────────────────────────────────────────────────────────
  repo/         sheets stored with the entity
  net/          the host validates player edits with rules/
  ─────────────────────────────────────────────────────────────────────────
  panels/       creation wizard, and a Sheet tab in Characters
```

The middle line is the one that matters. `rules/` imports neither Qt nor the
database **because the host runs it too**: a player's edit has to be checked
somewhere the player does not control.

## Decisions

### Store the inputs, derive the rest

Stored: ability scores, species, class, level, chosen proficiencies, equipment,
rolled hit points, current state.

Derived: modifiers, proficiency bonus, maximum hit points, AC, saving throws,
skill bonuses, spell slots, spell save DC.

Storing derived values means sheets that claim AC 15 while wearing nothing, the
moment anything changes underneath. Deriving also makes levelling up nearly
free: raise `level`, record the choices, and every number recomputes.

The exception that keeps it usable: an explicit `overrides` map. DMs hand out
"+1 AC from the amulet" constantly, and a model with nowhere to put that gets
fought instead of used.

### The sheet lives in `entity.data["sheet"]`

Not a table of its own. One entity stays one row, so sharing, syncing and the
existing panels need no restructuring.

The cost, stated plainly: you cannot ask SQL for "every level-five wizard". If
that is ever wanted, a table can be added underneath without disturbing anything
above.

Sheets carry a `schema` number so they can be migrated later.

### Content is a merge, not a file

The SRD has twelve classes, nine species, 319 spells -- and exactly **one**
background. Every table will add their own, so nothing reads the SRD directly:
lookups go through `content/`, which returns SRD plus whatever the campaign
defines, campaign content winning on a clash.

Writing `srd.backgrounds()` across the wizard would make homebrew a rewrite;
`content.backgrounds()` makes it a row in a table.

### Ownership is one-to-many

`entity.owner_account_id` says who owns a character. A player may own several.
`account.character_entity_id` narrows in meaning to *which of mine I am playing
right now*, which is what chat labels them as.

This gives the projection rule its shape:

| The entity | What a player receives |
|---|---|
| One they own | The whole sheet, every field |
| Shared with them | Name, one-liner, class, level, HP, AC, conditions |
| Not shared | Nothing at all; it does not exist for them |

### The version is the host's, never the client's

An edit carries no version. The host records what it last sent each connection
and checks against that, because a client able to name its own version could
choose a convenient one -- or omit it entirely and be written unconditionally,
which is worse.

A change made against a copy the DM has since altered is refused outright rather
than merged, and the current copy is sent back so the player's screen shows what
is true rather than what they attempted.

The same rule reaches the queue: when the DM changes a sheet, any proposal made
against the older version is refused automatically. Approving it would apply a
decision made about a character that no longer exists, and asking the DM to work
out whether it still makes sense is worse than asking the player to ask again.

### The player proposes, the DM confirms -- but only for the build

Two kinds of change, and conflating them would make the app miserable to use:

- **State** -- hit points, conditions, prepared spells, inventory, notes. Applied
  immediately. Pausing these for approval mid-combat would be unusable.
- **Build** -- level, class, subclass, species, ability scores. Queued as a
  pending change for the DM to approve, and announced in chat.

Validation is separate from approval and runs first: a level of 25 or a strength
of 30 is refused outright, not queued, because approving nonsense should not be
possible.

## What crosses the wire

The SRD does not. Both sides have the same files on disk, so a sheet is only the
choices made -- `"class": "wizard"`, `"level": 5`, a list of spell ids.

Measured: one full level-five wizard is **1,047 bytes**; a party of five is
5 KB. This is why the frame cap needed splitting by direction -- a campaign
snapshot is legitimately large, while a client sending one is not legitimate.
