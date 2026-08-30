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

import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from canon_keeper import credentials

log = logging.getLogger("canonkeeper.agent_runner")

#: The login the app makes for itself. A fixed name so a campaign ends up with
#: one agent account rather than a new one per press.
AGENT_USERNAME = "autopilot"
AGENT_DISPLAY_NAME = "Autopilot"

#: Generated, never typed. Nobody has to remember it and nothing is weakened by
#: it being long: the app writes it to the credential store and hands it to the
#: child process, and neither step involves a human.
_PASSWORD_BYTES = 24

#: Where the agent's password lives in the credential store. Not a session URL
#: like a player's saved login, because it belongs to the campaign rather than
#: to any one address it is reachable at.
def _password_key(campaign_id: int) -> str:
    return f"agent://campaign/{campaign_id}"


class AgentUnavailable(RuntimeError):
    """The agent cannot be started, and the message says what to do about it."""


# --------------------------------------------------------------------- the login


def ensure_account(repos, campaign_id: int) -> tuple[str, str]:
    """The agent's username and password, creating the login if needed.

    Returns ``(username, password)``. Raises :class:`AgentUnavailable` when the
    password cannot be kept anywhere -- a machine with no credential store can
    still run an agent, but someone has to supply the password themselves.
    """
    existing = repos.accounts.by_username(campaign_id, AGENT_USERNAME)
    saved = credentials.load(_password_key(campaign_id), AGENT_USERNAME)

    if existing is not None and saved:
        return AGENT_USERNAME, saved

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

    if not credentials.save(_password_key(campaign_id), AGENT_USERNAME, password):
        raise AgentUnavailable(
            "The agent login was created, but its password could not be saved "
            "to your credential store."
        )
    return AGENT_USERNAME, password


# ------------------------------------------------------------------ the process


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
) -> subprocess.Popen:
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

    return subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creation_flags,
    )


def stop(process: subprocess.Popen | None, timeout: float = 5.0) -> None:
    """Ask the agent to finish, then insist."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("the agent did not stop; killing it")
        process.kill()
