"""Names for the things that sit in an empty chair.

A character handed over is played by its own stand-in, one per character, and
calling all of them "autopilot" was wrong twice over: it is the name of the
thing running the *table*, and there may be three of these at once. A table
cannot say "autopilot moved" and be understood.

So each gets a name. They are deliberately not people's names -- nobody should
have to work out whether SPINDLE is an NPC -- and deliberately not sci-fi
either. They read as what they are: something borrowed, doing a job, until the
person whose chair it is comes back.

**The same character always gets the same one** inside a campaign, because the
name is chosen from the character rather than from when the process started.
Marla's stand-in is BRASS on Tuesday and BRASS again next week, which is what
makes it a name rather than a label.
"""

from __future__ import annotations

import hashlib

#: Short, sayable across a table, and unmistakably not a person. Twenty-four is
#: enough that a party of five will almost never see a repeat, and few enough
#: that they stay recognisable rather than becoming noise.
NAMES = (
    "BRASS",
    "COG",
    "TALLY",
    "SPINDLE",
    "LATCH",
    "TICKER",
    "BELLOWS",
    "PLUMB",
    "GIMBAL",
    "RATCHET",
    "SEXTANT",
    "TREADLE",
    "FLYWHEEL",
    "CANTILEVER",
    "ESCAPEMENT",
    "MAINSPRING",
    "PENDULUM",
    "QUADRANT",
    "TRUNNION",
    "VERGE",
    "WINCH",
    "AWL",
    "CALIPER",
    "DIAL",
)


def name_for(seed: str) -> str:
    """A stand-in's name, chosen from something stable about the character.

    Hashed rather than counted, so it does not depend on the order characters
    were handed over in -- two DMs handing the same two characters over in
    different orders should still see the same names.
    """
    if not seed:
        return NAMES[0]
    digest = hashlib.blake2b(str(seed).encode("utf-8"), digest_size=8).digest()
    return NAMES[int.from_bytes(digest, "big") % len(NAMES)]


def name_for_character(campaign_key: str, entity_id: int) -> str:
    """The stand-in name for one character in one campaign.

    The campaign is in the seed as well as the character, so two campaigns that
    both have a character with id 3 -- which is most pairs of campaigns, since
    every campaign is its own file and starts counting at one -- do not end up
    with the same stand-in name for unrelated people.
    """
    return name_for(f"{campaign_key}:{entity_id}")
