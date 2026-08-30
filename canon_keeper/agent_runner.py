"""Starting the autopilot agent for the DM, so they never open a terminal.

Pressing **Autopilot** should hand the table over. Telling someone to go and run
a command with their campaign's file path in it is not handing anything over, it
is homework.

So the app does the whole thing: makes the agent a login the first time, keeps
the password in the OS credential store, and runs ``canonkeeper-agent`` as a
child process pointed at its own session.

**None of that weakens the arrangement it exists to make convenient.** The agent
is still a separate process holding a socket and a login, connecting over the
wire like any player. It cannot be handed the database because it has no code
that could read one -- see ``tests/test_protocol_package.py``, which fails if
anything outside the app so much as imports it.

If an agent is already connected -- one someone started themselves, or one
running on a spare box -- nothing is spawned. The switch just gets flipped.
"""

from __future__ import annotations

import collections
import importlib.util
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from canon_keeper import campaigns, credentials
from canon_keeper_protocol import auth

log = logging.getLogger("canonkeeper.agent_runner")

#: The login the app makes for itself. A fixed name so a campaign ends up with
#: one agent account rather than a new one per press.
AGENT_USERNAME = "autopilot"
AGENT_DISPLAY_NAME = "Autopilot"

#: Generated, never typed. Nobody has to remember it and nothing is weakened by
#: it being long: the app writes it to the credential store and hands it to the
#: child process, and neither step involves a human.
_PASSWORD_BYTES = 24

def _password_key(repos) -> str:
    """Where this campaign's agent password lives in the credential store.

    Keyed by the campaign's own random id, **not** ``campaign.id``. Every
    campaign is a separate SQLite file, so almost all of them are campaign 1 --
    keying by that made two campaigns share one entry, and opening the second
    quietly overwrote the first's password. The symptom was an agent that could
    no longer log in to a campaign nobody had touched.
    """
    return f"agent://campaign/{campaigns.campaign_key(repos)}"


class AgentUnavailable(RuntimeError):
    """The agent cannot be started, and the message says what to do about it."""


# --------------------------------------------------------------------- the login


def ensure_account(repos, campaign_id: int) -> tuple[str, str]:
    """The agent's username and password, creating the login if needed.

    Returns ``(username, password)``. Raises :class:`AgentUnavailable` when the
    password cannot be kept anywhere -- a machine with no credential store can
    still run an agent, but someone has to supply the password themselves.
    """
    key = _password_key(repos)
    existing = repos.accounts.by_username(campaign_id, AGENT_USERNAME)
    saved = credentials.load(key, AGENT_USERNAME)

    if existing is not None and saved and _password_works(existing, saved):
        return AGENT_USERNAME, saved
    if existing is not None and saved:
        # A saved password the host would refuse. Rather than hand it over and
        # let the agent fail at the door with "that did not match", mint a new
        # one -- the DM owns this campaign, and this login exists to serve them.
        log.warning("the saved agent password no longer works; resetting it")

    if not credentials.is_available():
        raise AgentUnavailable(
            "This machine has no credential store, so the app cannot keep the "
            "agent's password for you.\n\nCreate the login yourself with "
            "`canonkeeper-server --add-agent autopilot`, then run "
            "`canonkeeper-agent` and press Autopilot again."
        )

    password = secrets.token_urlsafe(_PASSWORD_BYTES)
    if existing is None:
        repos.accounts.create(
            campaign_id,
            AGENT_USERNAME,
            password,
            role="agent",
            display_name=AGENT_DISPLAY_NAME,
        )
        log.info("created the agent login for campaign %s", campaign_id)
    else:
        # The account outlived its saved password -- someone cleared the
        # keychain, or made the account by hand. Reset rather than fail: the
        # DM owns this campaign and this login exists to serve them.
        repos.accounts.set_password(existing.id, password)
        log.info("reset the agent password for campaign %s", campaign_id)

    if not credentials.save(key, AGENT_USERNAME, password):
        raise AgentUnavailable(
            "The agent login was created, but its password could not be saved "
            "to your credential store."
        )
    return AGENT_USERNAME, password


def _password_works(account, password: str) -> bool:
    """Whether the host would actually accept this password.

    Checked rather than assumed. Anything that can put the store and the
    campaign file out of step -- a restored backup, a copied campaign, a
    cleared keychain, a bug like the one above -- otherwise surfaces as a login
    failure with no way for the app to fix itself.
    """
    try:
        return auth.derive_verifier(password, account.salt) == account.verifier
    except Exception:  # noqa: BLE001 - a malformed stored value is a mismatch
        log.debug("could not check the saved agent password", exc_info=True)
        return False


# ------------------------------------------------------------------ the process


class RunningAgent:
    """The agent process, and what it has said.

    Its output is read on a thread rather than left in a pipe. A child whose
    stdout nobody reads eventually blocks on a full buffer, and -- the reason
    this exists -- an agent that exits immediately would otherwise do so in
    silence, leaving the app showing "Starting the agent..." forever.
    """

    #: Enough to explain a failure, not enough to hold a session's logging.
    KEEP_LINES = 60

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._lines: collections.deque[str] = collections.deque(maxlen=self.KEEP_LINES)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip()
                if text:
                    self._lines.append(text)
                    log.debug("agent: %s", text)
        except (ValueError, OSError):  # pragma: no cover - the pipe closed
            pass

    # Delegated so this can be passed anywhere a Popen was.
    def poll(self):
        return self._process.poll()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def wait(self, timeout: float | None = None):
        return self._process.wait(timeout=timeout)

    @property
    def returncode(self):
        return self._process.returncode

    def why_it_stopped(self, lines: int = 4) -> str:
        """The last thing it said, for showing to a person.

        The tail rather than the head: an agent that fails does it on the way
        out, and the first lines are usually just it starting up.
        """
        tail = list(self._lines)[-lines:]
        return "\n".join(tail) if tail else "It stopped without saying why."


def missing_requirement() -> str:
    """What is stopping the agent from running, or empty if nothing is.

    Checked before starting rather than after: a process that exits in the
    first millisecond is a worse way to learn this than a sentence.
    """
    if find_executable() is None:
        return explain_missing()
    if importlib.util.find_spec("anthropic") is None:
        return (
            "The agent is installed but cannot reach a model: the `anthropic` "
            "package is missing.\n\n"
            '    pip install "canon-keeper[agent]"\n\n'
            "Install it into the same environment Canon Keeper is running from."
        )
    return ""


def find_executable() -> list[str] | None:
    """How to run the agent, or None if it is not installed.

    Prefers the console script beside the running interpreter, so a virtualenv
    gets its own rather than whatever is first on PATH.
    """
    scripts = Path(sys.executable).parent
    for name in ("canonkeeper-agent", "canonkeeper-agent.exe"):
        candidate = scripts / name
        if candidate.exists():
            return [str(candidate)]

    found = shutil.which("canonkeeper-agent")
    if found:
        return [found]

    # Installed as a package but without its script (a source checkout run in
    # an odd way). Running the module does the same thing.
    try:
        import canon_keeper_dm_agent  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "canon_keeper_dm_agent"]


def explain_missing() -> str:
    return (
        "The autopilot agent is not installed.\n\n"
        "    pip install \"canon-keeper[agent]\"\n\n"
        "It runs as a separate process and talks to your session over the "
        "network, the same way a player's app does."
    )


def api_key() -> str:
    """The key the agent will answer with, from the environment or the store."""
    return os.environ.get("ANTHROPIC_API_KEY", "") or (
        credentials.load("anthropic://api", "key") or ""
    )


def remember_api_key(key: str) -> bool:
    return credentials.save("anthropic://api", "key", key.strip())


def start(
    url: str, username: str, password: str, key: str = "", model: str = ""
) -> RunningAgent:
    """Launch the agent against a running session."""
    command = find_executable()
    if command is None:
        raise AgentUnavailable(explain_missing())

    command = [*command, "--url", url, "--user", username]
    if model:
        command += ["--model", model]

    environment = dict(os.environ)
    # Passed through the environment rather than the command line: arguments are
    # visible to anyone who can list processes, and both of these are secrets.
    environment["CANONKEEPER_AGENT_PASSWORD"] = password
    if key:
        environment["ANTHROPIC_API_KEY"] = key

    log.info("starting the agent: %s", " ".join(command[:-1] + ["***"]))
    creation_flags = 0
    if sys.platform == "win32":
        # Otherwise a console window opens over the app every time.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return RunningAgent(
        subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Line buffered, so a failure is readable before the process dies
            # rather than sitting in an unflushed block buffer.
            bufsize=1,
            creationflags=creation_flags,
        )
    )


def stop(process: "RunningAgent | subprocess.Popen | None", timeout: float = 5.0) -> None:
    """Ask the agent to finish, then insist."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("the agent did not stop; killing it")
        process.kill()
