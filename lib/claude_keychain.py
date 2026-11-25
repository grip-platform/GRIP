"""Keychain items that hold a Claude token for other parts of GRIP and HAL.

The token store (``lib/claude_token_store.py``) is where a long-lived Claude
token is kept and fenced. On macOS two keychain items hold such a token as
well, because other code reads them from there:

- ``grip-claude-oauth``: HAL bills the subscription with it, and the harness
  launchers hand it to the HAL gateway as the caller's key.
- ``grip-remote-claude-oauth``: an older resting place. Nothing in GRIP's
  commands reads it today, and it still holds a working token.

``READ_BY`` says the same in a form the fleet tool can put into words, and
``tests/test_claude_keychain.py`` checks it against the checkout: an item said
to be read is named by some program, and one said to be read by nothing is
named by none.

WHY THIS MODULE EXISTS. Minting a fresh token does not change what these items
hold. When the older tokens are then removed on the vendor's side, whatever
reads the first item stops working, and the second is left holding a dead
token. A fresh token is never shown, so it cannot be typed into the keychain
by hand either. :func:`put` copies a stored token into an item, and
:func:`holds` says what each item holds, as a digest.

HOW THE TOKEN TRAVELS. ``security -i`` reads its commands from standard input,
so the token is written to the keychain without ever being an argument of a
process. Measured on 2026-10-02 with a made-up value on a throwaway item: the
command was accepted, the value read back the same, and the item was removed.

Only the items named in ``ITEMS`` can be written: this is not a general way to
put things in the keychain.

A LOCKED KEYCHAIN LOOKS EMPTY. Measured on 2026-10-02 on a Mac reached over a
remote connection (macOS 26.3.1): looking an item up exits 44, "the specified
item could not be found", whether the item exists or not; ``security
show-keychain-info`` exits 36, "User interaction is not allowed"; and adding
an item exits 36 too. On the same day a Mac with its keychain open answered
``show-keychain-info`` with exit 0. So :func:`holds` asks that question first,
and where the keychain is locked it says of every item that it could not be
read, instead of saying the item is not there.

NOT COVERED, and said rather than solved. An item on a Mac whose keychain is
locked for this connection has to be set on that machine itself. A machine
without the ``security`` command has no such items. One Linux machine was
found to have a small script of that name that answers look-ups only: it does
not know the question about the lock, is treated as open, and cannot be
written. An item is replaced in two steps (remove, then add), so a failure
between them leaves the item absent, and that is reported.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

import claude_token_store as store

#: The keychain items that hold a Claude subscription token, and what reads
#: each one. ``None``: nothing in GRIP does.
READ_BY = {"grip-claude-oauth": "HAL and the harness launchers", "grip-remote-claude-oauth": None}

ITEMS = tuple(READ_BY)

#: The account every item is stored under, as ``bin/grip-keyring`` stores them.
ACCOUNT = "grip"

#: What ``security`` exits with when no such item exists.
NOT_FOUND = 44

#: What ``security`` exits with when the keychain is locked and nobody can be
#: asked to unlock it.
LOCKED = 36

LOCKED_HERE = ("the keychain could not be read: it is locked for this connection, so it has to be "
               "looked at on that machine itself")

#: An item can exist once per account. This many removals clear any pile-up.
MOST_COPIES = 10

SECONDS = 30


class KeychainError(Exception):
    """The keychain could not be read or written, in plain words."""


def digest(token: str) -> str:
    """Twelve characters that identify a token without revealing it."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def tool(env=None) -> str | None:
    """Where the ``security`` command is, or None on a machine without one."""
    return shutil.which("security", path=None if env is None else env.get("PATH"))


def _run(argv: list[str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=SECONDS)


def _read(security: str, item: str) -> tuple[str | None, str | None]:
    """The value of one item, and why it could not be read. ``(None, None)``
    means there is no such item."""
    try:
        done = _run([security, "find-generic-password", "-s", item, "-w"])
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not be read ({type(exc).__name__})"
    if done.returncode == NOT_FOUND:
        return None, None
    if done.returncode != 0:
        return None, f"could not be read (exit {done.returncode})"
    return done.stdout.strip(), None


def _entry(security: str, item: str) -> dict:
    value, problem = _read(security, item)
    shaped = bool(value and store.TOKEN_SHAPE.fullmatch(value))
    return {"present": value is not None, "digest": digest(value) if shaped else None,
            "problem": problem if problem else (None if shaped or value is None
                                                else "what it holds is not a token")}


def locked(security: str) -> bool:
    """True when the keychain cannot be read from here. A locked keychain
    answers "no such item" to every look-up, so this is asked first."""
    try:
        return _run([security, "show-keychain-info"]).returncode == LOCKED
    except (OSError, subprocess.SubprocessError):
        return False


def holds(env=None) -> dict:
    """What each item holds on this machine: whether it is there, the digest
    of its token, and why it could not be read. Empty on a machine without a
    keychain. Never the token itself."""
    security = tool(env)
    if not security:
        return {}
    if locked(security):
        return {item: {"present": False, "digest": None, "problem": LOCKED_HERE} for item in ITEMS}
    return {item: _entry(security, item) for item in ITEMS}


def token_in(item: str, env=None) -> str | None:
    """The token one item holds, for a live check made on this machine. None
    when there is no keychain, no such item, or no token in it."""
    security = tool(env)
    value = _read(security, item)[0] if security and item in ITEMS else None
    return value if value and store.TOKEN_SHAPE.fullmatch(value) else None


def _remove_all(security: str, item: str) -> None:
    """Remove every copy of one item, whichever account each was stored under."""
    for _ in range(MOST_COPIES):
        if _run([security, "delete-generic-password", "-s", item]).returncode != 0:
            return
    raise KeychainError(f"keychain item {item!r} still exists after {MOST_COPIES} removals")


def put(item: str, token: str, env=None) -> str:
    """Make one item hold ``token``, and nothing else. Returns the digest the
    item holds afterwards, read back from the keychain. Raises
    ``KeychainError`` in plain words, with the token never in the message.

    What decides is the item read back afterwards, never the tool's exit
    code: an item that holds the token was set, and one that does not was not.
    """
    if item not in ITEMS:
        raise KeychainError(f"{item!r} is not a keychain item that holds a Claude token; "
                            f"those are: {', '.join(ITEMS)}")
    if not store.TOKEN_SHAPE.fullmatch(token):
        raise KeychainError("what was given is not a token")
    security = tool(env)
    if not security:
        raise KeychainError("this machine has no keychain (no `security` command)")
    if locked(security):
        raise KeychainError(f"keychain item {item!r} was not set: the keychain is locked for this "
                            "connection. Run this on that machine itself")
    _remove_all(security, item)
    done = _run([security, "-i"], f"add-generic-password -U -a {ACCOUNT} -s {item} -w {token}\n")
    held = _entry(security, item)["digest"]
    if held != digest(token):
        said = store.scrubbed(done.stderr or done.stdout, 160)
        raise KeychainError(f"keychain item {item!r} was not set (exit {done.returncode}): {said}. "
                            "It may now be absent; the token is still in the store, so run this again")
    return held
