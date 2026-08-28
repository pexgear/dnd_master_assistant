"""Saved logins for sessions you have joined.

Passwords go to the operating system's credential store -- Windows Credential
Manager, the macOS Keychain, or the Secret Service on Linux -- and never to a
file of ours. A campaign file you can copy about is one thing; a plain-text
password sitting beside it is another.

Everything here degrades rather than fails. On a machine with no usable keyring
(a headless Linux box, most often) saving is simply unavailable, the caller is
told so, and the user types their password each time.
"""

from __future__ import annotations

import logging

log = logging.getLogger("canonkeeper.credentials")

SERVICE = "CanonKeeper"

_backend_checked = False
_backend_usable = False


def _keyring():
    try:
        import keyring
    except ImportError:  # pragma: no cover - depends on the install
        return None
    return keyring


def is_available() -> bool:
    """Whether this machine can actually store a password for us."""
    global _backend_checked, _backend_usable
    if _backend_checked:
        return _backend_usable

    _backend_checked = True
    keyring = _keyring()
    if keyring is None:
        log.info("keyring is not installed; passwords will not be saved")
        _backend_usable = False
        return False

    try:
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        # A "fail" or null backend is what you get on a box with no secret
        # service. It accepts nothing, so treat it as absent.
        _backend_usable = not isinstance(backend, FailKeyring) and "null" not in type(
            backend
        ).__module__.lower()
        if not _backend_usable:
            log.info("no usable keyring backend (%s); passwords will not be saved", backend)
    except Exception:  # noqa: BLE001 - a broken keyring must not stop the app
        log.exception("could not inspect the keyring backend")
        _backend_usable = False
    return _backend_usable


def account_key(url: str, username: str) -> str:
    """One entry per session-and-user, so several logins can coexist."""
    return f"{url.strip()}|{username.strip().lower()}"


def save(url: str, username: str, password: str) -> bool:
    if not is_available() or not password:
        return False
    keyring = _keyring()
    try:
        keyring.set_password(SERVICE, account_key(url, username), password)
        return True
    except Exception:  # noqa: BLE001 - a locked keychain is the user's business
        log.exception("could not save the password")
        return False


def load(url: str, username: str) -> str | None:
    if not is_available() or not username:
        return None
    keyring = _keyring()
    try:
        return keyring.get_password(SERVICE, account_key(url, username))
    except Exception:  # noqa: BLE001
        log.exception("could not read the saved password")
        return None


def forget(url: str, username: str) -> None:
    if not is_available():
        return
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, account_key(url, username))
    except Exception:  # noqa: BLE001 - already gone is the outcome we wanted
        log.debug("no saved password to remove for %s", url)


def has_saved(url: str, username: str) -> bool:
    return bool(load(url, username))
