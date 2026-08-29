"""Publishing a session through Tailscale Funnel.

Funnel asks Tailscale to expose one local port to the public internet at
``https://your-machine.tailXXXX.ts.net``, with a real certificate. For us it
solves three problems in one command:

* **NAT** -- the tunnel dials outward, so routers and carrier-grade NAT stop
  mattering and nobody forwards a port.
* **TLS** -- the certificate is provisioned to your machine by Tailscale, so we
  ship no SSL code and renew nothing. Players connect over ``wss://``.
* **The address** -- a stable hostname, rather than a home IP that changes.

And the players install nothing: they need Tailscale no more than they need it
to visit a website.

We drive the ``tailscale`` command rather than reimplementing any of it. That
means the version on the machine decides the exact flags, so everything here is
written defensively: parse what we can, and when we cannot, hand Tailscale's own
message to the user unchanged -- it is usually the actionable one ("Funnel is
not enabled on your tailnet", with a link to enable it).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("canonkeeper.net.funnel")

#: Where the CLI lives when it is not on PATH, which is usual on Windows and
#: macOS because the installer does not always add it.
_KNOWN_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/bin/tailscale",
)

_URL = re.compile(r"https://[A-Za-z0-9._-]+\.ts\.net\b")

COMMAND_TIMEOUT = 25


@dataclass(slots=True)
class Result:
    ok: bool
    url: str = ""
    message: str = ""

    @property
    def websocket_url(self) -> str:
        """The address a player types: Funnel serves HTTPS, we speak WebSocket."""
        return to_websocket_url(self.url)


def to_websocket_url(url: str) -> str:
    if not url:
        return ""
    return url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")


def executable() -> str | None:
    """The tailscale CLI, or None if this machine has not got it."""
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in _KNOWN_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def is_installed() -> bool:
    return executable() is not None


def _run(args: list[str], timeout: int = COMMAND_TIMEOUT) -> tuple[int, str]:
    """Run the CLI and return ``(returncode, combined output)``.

    Everything funnels through here so the tests can stand in for Tailscale.
    """
    binary = executable()
    if binary is None:
        return 127, "The tailscale command was not found."

    try:
        completed = subprocess.run(  # noqa: S603 - a fixed binary and our own args
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # Stops a console window flashing up on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0,
        )
    except subprocess.TimeoutExpired:
        return 124, f"tailscale {' '.join(args)} did not finish in {timeout}s."
    except OSError as exc:
        return 126, f"Could not run tailscale: {exc}"

    return completed.returncode, f"{completed.stdout}\n{completed.stderr}".strip()


def machine_url() -> str:
    """This machine's public Funnel hostname, from ``tailscale status``."""
    code, output = _run(["status", "--json"], timeout=10)
    if code != 0:
        return ""
    try:
        name = json.loads(output).get("Self", {}).get("DNSName", "")
    except (json.JSONDecodeError, AttributeError):
        return ""
    name = name.strip().rstrip(".")
    return f"https://{name}" if name else ""


def start(port: int) -> Result:
    """Publish ``port`` to the internet. Returns the public address."""
    if not is_installed():
        return Result(
            False,
            message=(
                "Tailscale is not installed on this machine.\n\n"
                "Install it from https://tailscale.com/download, sign in, and try "
                "again. Your players do not need it -- only you."
            ),
        )

    code, output = _run(["funnel", "--bg", str(port)])
    if code != 0:
        # Tailscale's own wording is nearly always the useful one here: it names
        # the tailnet setting to change, with a link.
        log.warning("tailscale funnel failed (%s): %s", code, output)
        return Result(False, message=output or "Tailscale refused to start Funnel.")

    match = _URL.search(output)
    url = match.group(0) if match else machine_url()
    if not url:
        return Result(
            False,
            message=(
                "Funnel started, but Tailscale did not report a public address.\n\n"
                + output
            ),
        )

    log.info("funnel serving port %s at %s", port, url)
    return Result(True, url=url, message=output)


def stop(port: int | None = None) -> Result:
    """Take the session off the public internet again."""
    if not is_installed():
        return Result(True)  # nothing of ours is running

    code, output = _run(["funnel", "--https=443", "off"])
    if code != 0:
        # Older releases spell it differently; reset clears whatever is set.
        code, output = _run(["funnel", "reset"])

    if code != 0:
        log.warning("could not stop funnel: %s", output)
        return Result(False, message=output)
    log.info("funnel stopped")
    return Result(True, message=output)


def is_running() -> bool:
    code, output = _run(["funnel", "status"], timeout=10)
    if code != 0:
        return False
    lowered = output.lower()
    return "https://" in lowered and "no serve config" not in lowered
