"""Joining a campaign for the first time, without putting a secret on the wire.

A campaign starts with characters and no players. The DM makes an invite **for a
character** and sends the code to the person who will play them; that person's
app then makes the account. The DM never types somebody else's password, and
never learns it.

The problem this module solves: the new account's password material has to reach
the host, and a LAN session has no TLS. :mod:`canon_keeper_protocol.auth` keeps
the *password* off the wire at login by sending only a proof -- but enrolment
cannot do that, because the host has nothing stored yet. It needs the verifier
itself, and a verifier is login-equivalent: anyone who sniffs it can be that
player forever.

So the invite code does the work. It is known only to the DM and the person they
sent it to, it never crosses the wire either, and it is what the verifier is
sealed with:

1. the host sends a fresh **nonce**;
2. both sides derive ``pad || mac_key = scrypt(code, salt=nonce)``;
3. the client sends ``verifier XOR pad``, and an HMAC over everything the host
   is about to act on;
4. the host tries the codes it has live. The one whose HMAC checks out is the
   invite being answered -- so the tag proves knowledge of the code *and*
   identifies the invite, and there is no separate proof to get wrong.

**Why XOR and not a cipher.** A verifier is exactly 32 bytes and the pad is 32
bytes of KDF output used exactly once, because the nonce is fresh for every
attempt. That is a one-time pad over a fixed-length secret, not a stream cipher:
there is no counter, no mode, and no way to reuse a keystream by accident. The
alternative was hand-rolling CTR out of HMAC, which is the kind of thing that
looks fine and is not.

**Why scrypt on the code.** A code read aloud across a table is about thirty
bits. Somebody who recorded the exchange could otherwise try all of them offline
in an afternoon and recover the verifier. Running each guess through scrypt
makes that cost years instead, and costs the person joining a fifth of a second
once.

**The honest limits.** The host stores live codes in the campaign file, so
somebody holding that file holds any invite still outstanding -- but they also
hold every verifier in it, so this adds nothing to that. And an attacker who
learns a code before the player uses it can take the character: an invite *is*
the authority, which is why they expire, why making a new one kills the old, and
why they should be sent the way you would send a password.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from canon_keeper_protocol.auth import (
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    VERIFIER_BYTES,
)

#: No O/0 or I/1: these get read aloud across a table. Thirty bits, which is
#: only safe because guesses cost a scrypt and are capped per connection.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
#: Written as XXXXX-XXXXX. Longer than the six-character join code that was
#: never used, because this one is worth stealing.
_GROUP = 5

#: How long an invite is good for. Long enough to send it and have somebody act
#: on it that evening; short enough that a code in a chat log from last month is
#: already dead.
INVITE_LIFETIME_SECONDS = 24 * 60 * 60

#: Attempts allowed on one connection before it is closed. The scrypt is the
#: real cost of guessing; this is what stops somebody paying it in parallel.
MAX_ATTEMPTS = 3

_PAD_BYTES = VERIFIER_BYTES
_MAC_BYTES = 32


class EnrolError(ValueError):
    """The enrolment failed. The message is safe to show; see :func:`explain`."""


def new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + _GROUP] for i in range(0, len(raw), _GROUP))


def clean_code(raw: str) -> str:
    """What somebody typed, in the form the codes are made in.

    People retype these from a message. Case, spaces and the dash are all
    noise, so none of them are a reason to refuse somebody.
    """
    kept = [c for c in str(raw or "").upper() if c in _CODE_ALPHABET]
    return "-".join(
        "".join(kept)[i:i + _GROUP] for i in range(0, len(kept), _GROUP)
    )


#: What separates the address from the code in a whole invite. A fragment
#: marker, because that is exactly what it is: the part of a URL that is never
#: sent to the server, which is also true of the code.
_JOIN = "#"


def wrap(address: str, code: str) -> str:
    """One string holding both halves: where to connect, and the code.

    Two things to copy is two things to get wrong, and the address is the one
    people mistype. This is the thing a DM actually sends.
    """
    address = str(address or "").strip()
    code = clean_code(code)
    if not address:
        return code
    return f"{address}{_JOIN}{code}"


def unwrap(text: str) -> tuple[str, str]:
    """``(address, code)`` from whatever somebody pasted.

    Tolerant on purpose. It arrives through a chat app, an email, or a piece of
    paper, and any of those may have added a scheme, a stray space or a
    trailing full stop. Either half may come back empty: a code with no address
    is what a DM who was not hosting yet would have sent, and an address with
    no code is somebody who already has an account.
    """
    raw = str(text or "").strip().strip(".")
    if raw.lower().startswith("canonkeeper:"):
        raw = raw[len("canonkeeper:"):].lstrip("/")

    address, _, tail = raw.partition(_JOIN)
    if tail:
        return address.strip(), clean_code(tail)

    # No marker. Either it is only a code, or only an address -- and a code is
    # letters and digits from one small alphabet, which an address never is.
    if "://" in raw or ":" in raw or "/" in raw:
        return raw, ""
    return "", clean_code(raw)


def _material(code: str, nonce: bytes) -> tuple[bytes, bytes]:
    """``(pad, mac_key)``, derived from the code and the host's nonce.

    Deliberately the same cost as a login: a guess should not be cheaper than
    the thing it is guessing at.
    """
    both = hashlib.scrypt(
        clean_code(code).encode("utf-8"),
        salt=nonce,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=_PAD_BYTES + _MAC_BYTES,
        maxmem=64 * 1024 * 1024,
    )
    return both[:_PAD_BYTES], both[_PAD_BYTES:]


def _transcript(nonce: bytes, username: str, salt: bytes, sealed: bytes) -> bytes:
    """Everything the host is about to act on, in one string to sign.

    The username is in here on purpose. Without it somebody who recorded an
    enrolment could replay the sealed verifier under a different name, and the
    host would happily make *them* an account with that password.
    """
    parts = [nonce, username.encode("utf-8"), salt, sealed]
    joined = b""
    for part in parts:
        joined += len(part).to_bytes(4, "big") + part
    return joined


def seal(code: str, nonce: bytes, username: str, salt: bytes, verifier: bytes) -> dict:
    """What the joining player's app sends. The password is not in it."""
    if len(verifier) != _PAD_BYTES:
        raise EnrolError("that verifier is the wrong length")
    pad, mac_key = _material(code, nonce)
    sealed = bytes(a ^ b for a, b in zip(verifier, pad))
    return {
        "username": username,
        "salt": salt.hex(),
        "sealed": sealed.hex(),
        "tag": hmac.new(
            mac_key, _transcript(nonce, username, salt, sealed), hashlib.sha256
        ).hexdigest(),
    }


def unseal(
    code: str, nonce: bytes, username: str, salt: bytes, sealed: bytes, tag: str
) -> bytes:
    """The verifier, if this code is the one the sealing was done with.

    Raises :class:`EnrolError` otherwise, and the caller must not be able to
    tell "wrong code" from "not an invite" -- see :func:`explain`.
    """
    if len(sealed) != _PAD_BYTES:
        raise EnrolError(explain())
    pad, mac_key = _material(code, nonce)
    expected = hmac.new(
        mac_key, _transcript(nonce, username, salt, sealed), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, str(tag or "")):
        raise EnrolError(explain())
    return bytes(a ^ b for a, b in zip(sealed, pad))


def explain() -> str:
    """The single message for every failed enrolment.

    One wording for "no such invite", "wrong code", "expired", "already used"
    and "revoked". Otherwise somebody with a guess could learn which of those it
    was, and "expired" tells them a real code once existed for that campaign.
    """
    return "That invite is not valid. Ask your DM for a new one."
