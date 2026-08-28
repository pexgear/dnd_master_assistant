"""Finding known entity names inside free text.

Used to light up the transcript, and general enough for anything later that
needs to know which of your NPCs and places a passage mentions.

Two rules worth knowing:

* **Longest match wins.** With both "Cragmaw" and "Cragmaw Castle" on file, the
  sentence "they reach Cragmaw Castle" reports the castle, not the goblin tribe.
* **Aliases resolve to their entity.** Highlighting "the Castle" and clicking it
  should take you to Cragmaw Castle, not to a second thing of the same name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

#: Below this length a "name" produces more noise than signal -- an NPC called
#: "Al" would light up every "al" in the transcript.
MIN_NAME_LENGTH = 3


@dataclass(slots=True, frozen=True)
class Match:
    start: int
    end: int
    text: str
    entity_id: int
    kind: str
    name: str  # the entity's canonical name, which may differ from `text`


@dataclass(slots=True, frozen=True)
class _Term:
    term: str
    entity_id: int
    kind: str
    name: str


class EntityMatcher:
    """Case-insensitive, whole-word matching of entity names and aliases."""

    def __init__(self, terms: list[_Term] | None = None) -> None:
        self._by_term: dict[str, _Term] = {}
        self._pattern: re.Pattern[str] | None = None
        if terms:
            self._build(terms)

    # ------------------------------------------------------------- construction

    @classmethod
    def from_repos(cls, repos, campaign_id: int) -> "EntityMatcher":
        terms: list[_Term] = []
        for entity in repos.entities.list(campaign_id):
            for raw in [entity.name, *entity.aliases]:
                cleaned = (raw or "").strip()
                if len(cleaned) < MIN_NAME_LENGTH:
                    continue
                # Placeholder names would highlight the words "new character"
                # wherever they appeared.
                if cleaned.lower().startswith("new "):
                    continue
                terms.append(
                    _Term(
                        term=cleaned,
                        entity_id=entity.id,
                        kind=entity.kind,
                        name=entity.name,
                    )
                )
        return cls(terms)

    def _build(self, terms: list[_Term]) -> None:
        # Longest first so the alternation prefers "Cragmaw Castle" over
        # "Cragmaw"; Python's `|` is first-match, not longest-match.
        ordered = sorted(terms, key=lambda t: len(t.term), reverse=True)
        seen: set[str] = set()
        patterns: list[str] = []
        for term in ordered:
            key = term.term.lower()
            if key in seen:
                continue
            seen.add(key)
            self._by_term[key] = term
            patterns.append(re.escape(term.term))

        if not patterns:
            return

        # Lookarounds rather than \b: a name may begin or end with a character
        # that is not a word character, and \b would then never fire.
        self._pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(patterns) + r")(?!\w)", re.IGNORECASE
        )

    # ------------------------------------------------------------------ queries

    def __bool__(self) -> bool:
        return self._pattern is not None

    def finditer(self, text: str) -> Iterator[Match]:
        if self._pattern is None or not text:
            return
        for found in self._pattern.finditer(text):
            term = self._by_term.get(found.group(0).lower())
            if term is None:  # pragma: no cover - defensive
                continue
            yield Match(
                start=found.start(),
                end=found.end(),
                text=found.group(0),
                entity_id=term.entity_id,
                kind=term.kind,
                name=term.name,
            )

    def lookup(self, text: str) -> Match | None:
        """Resolve an exact name or alias, for 'do we already know this?'."""
        term = self._by_term.get(text.strip().lower())
        if term is None:
            return None
        return Match(
            start=0,
            end=len(text),
            text=text,
            entity_id=term.entity_id,
            kind=term.kind,
            name=term.name,
        )
