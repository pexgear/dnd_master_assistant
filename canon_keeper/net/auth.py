"""Passwords, without putting them on the wire.

A LAN session has no TLS, so sending ``username + password`` would broadcast the
password to anyone on the same network -- and people reuse passwords, so that is
worse than having no login at all.

Instead, login is a challenge/response in the shape of SCRAM:

1. the client says who it is;
2. the server replies with that account's **salt** and a fresh random **nonce**;
3. the client derives ``verifier = scrypt(password, salt)`` locally and sends
   ``HMAC(verifier, nonce)``;
4. the server computes the same thing from its stored verifier and compares.

The password never leaves the client, and the nonce means a recorded login
cannot be replayed. Stdlib only.

The honest limit: the stored verifier is login-equivalent, so someone who steals
the campaign file can impersonate a player. Defending against that needs the
server to hold a value it cannot itself use, which is more machinery than a
home game warrants -- and anyone holding the campaign file already has the DM's
secrets, which is the thing actually worth protecting.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: scrypt cost. ~16 MB and a few tenths of a second, which is plenty against
#: someone guessing at a stolen campaign file and unnoticeable when logging in.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32

SALT_BYTES = 16
NONCE_BYTES = 32

MIN_PASSWORD_LENGTH = 4


class AuthError(ValueError):
    """Login failed. The message is deliberately vague; see :func:`explain`."""


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def new_nonce() -> bytes:
    return secrets.token_bytes(NONCE_BYTES)


def derive_verifier(password: str, salt: bytes) -> bytes:
    """scrypt the password. Runs on both sides; the result is never sent."""
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
        maxmem=64 * 1024 * 1024,
    )


def make_credentials(password: str) -> tuple[bytes, bytes]:
    """Return ``(salt, verifier)`` for a new or changed password."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"passwords need at least {MIN_PASSWORD_LENGTH} characters")
    salt = new_salt()
    return salt, derive_verifier(password, salt)


def proof(verifier: bytes, nonce: bytes) -> str:
    """What the client sends: proof it knows the password, tied to this nonce."""
    return hmac.new(verifier, nonce, hashlib.sha256).hexdigest()


def verify(verifier: bytes, nonce: bytes, offered: str) -> bool:
    return hmac.compare_digest(proof(verifier, nonce), str(offered or ""))


def explain(_reason: str = "") -> str:
    """The single message shown for every login failure.

    One wording for "no such user", "wrong password" and "account disabled", so
    the login screen cannot be used to enumerate who plays in the campaign.
    """
    return "That username and password did not match."
