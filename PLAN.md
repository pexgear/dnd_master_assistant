# Canon Keeper — build plan & cost model

A push-to-talk AI assistant for running D&D 5e, built around one rule: **what the DM
actually says is the only source of truth.**

- Input: push-to-talk voice (DM only)
- Cadence: weekly, 3–4 hour sessions
- Runs: locally, on your machine
- API cost: ~$3–8 per session, $15–35 per month

The storytelling is the easy part — any current model improvises a decent tavern brawl.
The hard part is bookkeeping: keeping straight what the machine *suggested* versus what
you actually *said*, session after session, and never letting the first quietly become
the second.

---

## 1. The one decision everything hangs on

Write this into the data model on day one. Retrofitting it later means throwing the
database away.

| | Canon — what is true | Proposal — what was offered |
|---|---|---|
| Example | "Sildar doesn't make it. He bleeds out while Elara is still tying the tourniquet." | "Sildar survives but loses the use of his sword arm…" |
| Written from | Your transcribed voice, only | The model, only |
| Table | `fact` | `proposal` |
| States | confirmed; superseded (never deleted) | `open` / `taken` / `discarded` — never `true` |

**The invariant:** a proposal row can never be the source of a fact row. Every fact
carries a `source_utterance` pointing at something you said. If a fact has no utterance
behind it, it came from the prepared module — and even that yields the moment your voice
contradicts it.

---

## 2. The loop, five stages

Each stage has a different cost and latency profile, so keep them as separate calls
rather than one giant prompt.

1. **Capture** — global hotkey held down, mic recorded to a WAV. 10–30 seconds of you
   narrating the beat.
2. **Transcribe** — local Whisper, primed with your campaign's proper nouns. Sub-second
   on a GPU.
3. **Extract** — cheap model turns the transcript into structured facts. Lands in a
   confirm tray, *not* the database.
4. **Reconcile** — new facts commit; contradicted ones get superseded. Open proposals
   get closed as taken or discarded.
5. **Propose** — stronger model reads canon + the module and offers three branches.
   Fires speculatively so it's on screen before you need it.

Stages 1–4 run on every push-to-talk. Stage 5 is the expensive one — throttle it to
roughly every other beat, or bind it to a second hotkey.

---

## 3. What to use

Everything runs locally except the two API calls. One language, no hosting bill, no
account to keep alive.

| Layer | Pick | Why this one |
|---|---|---|
| Shell | Python + local web UI | FastAPI serving one HTML page on localhost, WebSocket for live updates. Package as Tauri later if you want a real app icon. |
| Hotkey | `pynput` | Works when the window isn't focused — essential, since you'll be looking at your players, not the screen. Browsers can't do global hotkeys. |
| Capture | `sounddevice` | Push-to-talk means you only ever record your own voice. No diarization problem, no consent problem with your players. |
| Transcription | `faster-whisper` | **Free.** `distil-large-v3` on your GPU handles 20-second clips in well under a second. Deepgram Nova-3 at $0.0048/min is the fallback if your machine struggles. |
| Extraction | Claude Haiku 4.5 | $1/$5 per M tokens. Runs 80+ times a session; this is where a cheap model earns its keep. It's only doing transcript → JSON. |
| Branching | Claude Sonnet 5 | $2/$10 per M. Needs to hold the module, your canon, and the last few minutes at once. Opus 5 if you want richer prose. |
| Storage | SQLite | One file you can back up, diff, and open in a GUI when extraction gets something wrong at 11pm. |
| Retrieval | Structured, **not** vector | An adventure is a graph of scenes, NPCs and locations — not a blob of prose. Index by entity ID and pull by current location. Add embeddings only if that stops working. |

The retrieval row is the one most people get wrong. Chunking a module into a vector store
loses exactly the structure — "this room connects to that one", "this NPC knows that
secret" — that makes suggestions coherent.

---

## 4. The canon store

Four tables. The supersession column is what lets you change your mind mid-campaign
without corrupting the record.

```sql
-- everything you actually said, verbatim
CREATE TABLE utterance (
  id INTEGER PRIMARY KEY, session_id INTEGER,
  t REAL, text TEXT, audio_path TEXT
);

-- what the model offered. never a source of truth.
CREATE TABLE proposal (
  id INTEGER PRIMARY KEY, created_at REAL,
  label TEXT, body TEXT,
  status TEXT CHECK(status IN ('open','taken','discarded'))
);

-- the truth. one row per assertion.
CREATE TABLE fact (
  id INTEGER PRIMARY KEY,
  subject TEXT,            -- npc:sildar, loc:cragmaw_hideout
  predicate TEXT,          -- status, location, owes_favour_to
  object TEXT,
  source_utterance INTEGER REFERENCES utterance(id),
  confirmed INTEGER DEFAULT 0,   -- you pressed accept
  asserted_at REAL,
  superseded_by INTEGER REFERENCES fact(id)
);

-- prepared module content, same shape, source_utterance NULL
CREATE TABLE scene (
  id TEXT PRIMARY KEY, title TEXT, body TEXT,
  connects_to TEXT, entities TEXT   -- JSON arrays of ids
);
```

Current state is `SELECT * FROM fact WHERE superseded_by IS NULL`. That view — scoped to
the entities in play right now — is what you send to the model, never the whole log.

---

## 5. What it costs

Anthropic and Deepgram list prices as of August 2026, with 1-hour prompt caching applied
— that caching is roughly a third of the bill on its own.

### Assumptions (your cadence)

| Parameter | Value |
|---|---|
| Spoken beats per session | 80 |
| Session length | 3.5 h |
| Sessions per month | 4.3 |
| Beats that trigger a suggestion | 50% (40 calls) |
| Avg push-to-talk clip | 20 s |
| Extraction call | 800 fresh + 2,400 cached in, 300 out |
| Suggestion call | 8,000 fresh + 10,000 cached in, 700 out |

### Per session

| Component | Model | Cost |
|---|---|---|
| Extraction (80 calls) | Haiku 4.5 | $0.22 |
| Suggestions (40 calls) | Sonnet 5 | $1.16 |
| Transcription | local Whisper | free |
| **Total** | | **$1.51** |

At ~4.3 sessions/month that is **$6.50/month, $78/year**.

### Swapping the suggestion model

| Suggestion model | Per session | Per month |
|---|---|---|
| Haiku 4.5 | $0.80 | $3.40 |
| Sonnet 5 | $1.51 | $6.50 |
| Opus 5 | $3.25 | $14.00 |

### The honest number

Those are the mechanical figures. Budget **2× the headline** for real play — retries when
extraction mangles a name, rules lookups, NPC dialogue on demand, the end-of-session
recap. That lands you at roughly **$3–8 a session, $15–35 a month**.

### One-time and zero costs

| Item | Cost | Note |
|---|---|---|
| Ingesting a 250-page module | < $2 | One time per adventure. Use the Batch API for 50% off. |
| Hosting | $0 | It runs on your machine. There's no reason for this to be a web service. |
| Embeddings | $0 | Structured retrieval needs none. If you add them later, `sentence-transformers` runs locally. |
| Whisper model | $0 | ~1.5 GB download, one time. |
| Your time | 3–4 weekends | The real cost. |

### Reference prices used

| Item | Input | Output | Cache read | 1h cache write |
|---|---|---|---|---|
| Claude Haiku 4.5 | $1 /M | $5 /M | $0.10 /M | $2 /M |
| Claude Sonnet 5 | $2 /M | $10 /M | $0.20 /M | $4 /M |
| Claude Opus 5 | $5 /M | $25 /M | $0.50 /M | $10 /M |
| Deepgram Nova-3 streaming | $0.0048 / min | | | |

---

## 6. What will bite you

Five failure modes, in the order you'll hit them.

**1. Fantasy names get mangled.** Whisper renders "Cragmaw" as "crag more", and now your
canon has a new location in it.
→ *Fix:* maintain a glossary of proper nouns and pass it to Whisper as `initial_prompt`.
It's one string and it transforms accuracy on invented words. Regenerate it from the
entity table after every session.

**2. Silent bad writes.** Extraction misreads intent, commits a fact, and three sessions
later the model insists a dead NPC is alive.
→ *Fix:* nothing writes to `fact` unconfirmed. Extracted facts land in a tray; one
keypress accepts all, one rejects. Low-confidence ones stay greyed until you look.

**3. Latency at the table.** Eight seconds of dead air while four people watch you stare
at a laptop kills the scene.
→ *Fix:* generate speculatively — fire stage 5 the instant canon commits, before you ask.
Stream the tokens, and keep the previous suggestions on screen while new ones build.

**4. Canon outgrows the context.** By session twenty the fact log is enormous and you're
paying to resend your whole campaign every call.
→ *Fix:* never send the log. Send a rolling per-session summary plus facts scoped to the
entities currently in play. Cache the stable half for an hour at a time.

**5. It out-talks you.** Ten lush paragraphs of options every beat, and you stop
improvising because reading is easier.
→ *Fix:* hard-cap it. Three branches, one sentence each, each citing the fact IDs it drew
on so ungrounded invention is visible. A hotkey that hides the panel entirely.

---

## 7. Build order

The sequencing matters more than it looks. Prove the physical loop is comfortable at a
real table before any model is involved — if holding a key and narrating a beat feels
awkward, no amount of AI quality saves it.

**Weekend 1 — capture only, no AI.**
Hotkey, recording, local Whisper, transcript scrolling on screen, rows in SQLite. Run one
real session with it. You'll learn more here than from any of the later steps.

**Weekend 2 — extraction and the confirm tray.**
Haiku turns transcripts into facts; you accept or reject them. Add the canon panel,
editable by hand. Still no suggestions.

**Weekend 3 — the module and the branches.**
Ingest the adventure into the scene table, wire entity-scoped retrieval, and turn on the
three-branch panel with speculative generation.

**Weekend 4 — the table comforts.**
Session recap generation, NPC voice on demand, initiative and HP tracking, and a "what did
the party learn about X" query box.

---

## Notes

**On the adventure text:** the 5e SRD is released under CC BY 4.0, so rules content is
fair game. A published module is copyrighted — loading your own copy into a tool you run
privately is ordinary personal use, but the ingested scene table isn't something to ship
with the app if you ever share it.

**On pricing:** checked August 2026 against Anthropic and Deepgram list rates. Token
estimates are per-call averages; your real numbers will shift once you see how verbose
your own prompts get.

Sources: <https://platform.claude.com/docs/en/about-claude/pricing> ·
<https://deepgram.com/pricing>
