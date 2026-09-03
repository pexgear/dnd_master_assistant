"""Starting and stopping one stand-in per handed-over character.

A character handed to autopilot needs something sitting in its seat, and that
something is a process: one per character, so that two handed-over characters
have two views and neither knows what the other was told. This keeps the set of
running processes matching the set of handed-over characters, and does nothing
else.

**The token goes through the environment.** A command line is readable by
anyone who can list processes, and a seat token buys somebody's character for as
long as the handover lasts. The DM agent's password is passed the same way and
for the same reason.

**Only the machine hosting the session runs these.** They connect over loopback
to the server in this very app, so there is no address to get wrong and nothing
leaves the machine.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("canonkeeper.stand_ins")


def find_executable() -> list[str] | None:
    """How to run a stand-in, or None if it is not installed.

    Prefers the console script beside the running interpreter, so a virtualenv
    gets its own rather than whatever is first on PATH.
    """
    scripts = Path(sys.executable).parent
    for name in ("canonkeeper-player", "canonkeeper-player.exe"):
        candidate = scripts / name
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("canonkeeper-player")
    return [found] if found else None


def start(url: str, seat: str, pause: float | None = None) -> subprocess.Popen:
    """Launch one stand-in against a running session."""
    command = find_executable()
    if command is None:
        raise RuntimeError("canonkeeper-player is not installed")

    command = [*command, "--url", url]
    if pause is not None:
        command += ["--pause", str(pause)]

    environment = dict(os.environ)
    environment["CANONKEEPER_SEAT"] = seat

    creation_flags = 0
    if sys.platform == "win32":
        # Otherwise a console window opens over the app for every character.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    log.info("starting a stand-in against %s", url)
    return subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creation_flags,
    )


def stop(process: subprocess.Popen | None, timeout: float = 5.0) -> None:
    """Ask a stand-in to finish, then insist."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("a stand-in did not stop; killing it")
        process.kill()


class StandIns:
    """The running stand-ins, kept matching the handed-over characters.

    Deliberately dumb: it is asked to look, it compares two sets, and it starts
    or stops the difference. Nothing here decides *whether* a character should
    be played -- that is the DM pressing a button, recorded on the combatant.
    """

    def __init__(self) -> None:
        self._running: dict[int, subprocess.Popen] = {}

    @property
    def playing(self) -> set[int]:
        """Entity ids with a stand-in running right now."""
        return set(self._running)

    def look(self, server, pause: float | None = None) -> None:
        """Start what is missing, stop what is no longer wanted.

        Safe to call as often as the fight changes, which is what it is wired
        to: the work is a set comparison, and the expensive half only happens
        when the answer actually differs.
        """
        if server is None or not server.is_running:
            self.stop_all()
            return

        wanted = self._wanted(server)
        self._reap()

        for entity_id in wanted - self.playing:
            self._begin(server, entity_id, pause)
        for entity_id in self.playing - wanted:
            self._end(server, entity_id)

    def _wanted(self, server) -> set[int]:
        """Characters handed over in the running fight, that somebody plays.

        A character nobody plays has no seat to mint, so there is nothing for a
        stand-in to sit in -- those are the DM's own monsters and autopilot
        runs them directly.
        """
        encounter = server.repos.encounters.running(server.campaign_id)
        if encounter is None:
            return set()

        wanted = set()
        for combatant in server.repos.encounters.combatants(encounter.id):
            if not combatant.simulated or combatant.entity_id is None:
                continue
            entity = server.repos.entities.get(combatant.entity_id)
            if entity is not None and server._who_plays(entity) is not None:
                wanted.add(combatant.entity_id)
        return wanted

    def _begin(self, server, entity_id: int, pause: float | None) -> None:
        seat = server.mint_seat(entity_id)
        if not seat:
            log.info("no seat for entity %s; not starting a stand-in", entity_id)
            return
        try:
            self._running[entity_id] = start(
                f"ws://127.0.0.1:{server.port}", seat, pause
            )
        except (RuntimeError, OSError) as exc:
            # Not fatal, and not silent. Without a stand-in the character is
            # simply played by autopilot, which is where it was before.
            log.warning("could not start a stand-in: %s", exc)
            server.revoke_seat(entity_id)

    def _end(self, server, entity_id: int) -> None:
        stop(self._running.pop(entity_id, None))
        server.revoke_seat(entity_id)

    def _reap(self) -> None:
        """Forget stand-ins that have already exited.

        One that died -- the package missing, a crash -- must not be counted as
        running, or the character sits there with an empty seat and nothing
        starts another.
        """
        for entity_id, process in list(self._running.items()):
            if process.poll() is not None:
                log.info("a stand-in exited on its own; forgetting it")
                self._running.pop(entity_id, None)

    def stop_all(self) -> None:
        """Every stand-in, gone. Called when hosting stops or the app closes."""
        for entity_id in list(self._running):
            stop(self._running.pop(entity_id, None))
