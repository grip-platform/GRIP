"""What one machine says about its Claude tokens, asked where the tokens are.

This module runs ON the machine being asked. ``lib/claude_fleet.py`` sends it,
together with the modules it imports, down the connection it has opened, so the
machine needs nothing but Python: it runs the code that was tested in this
checkout, whichever version of GRIP is installed there.

Nothing it returns contains a token. A token is reported as twelve characters
of its SHA-256, which says "the same token" or "a different one" and nothing
else. Two functions here use a token, and both use it on the machine it is on:
:func:`identify` hands a stored one to one child process, and :func:`lives`
asks the service about one that is lying in a shell file, through one child
process or, for an API key, in one request header sent to the vendor.

TOKENS LYING IN SHELL FILES. A token that was pasted into a terminal or
exported from a profile stays in that file. :func:`strays` says where, by file
and line number, and :func:`remove_strays` deletes exactly those lines.

TOKENS LYING IN WHAT A HARNESS KEEPS. A token that was pasted into a prompt,
printed by a command, or exported into a session's environment stays in that
harness's records: prompt history, session files, shell snapshots, a database.
:func:`records` finds those by reading every file under the places named in
``RECORDS``, and says what it looked in. Nothing there can be cut out, so it
has no counterpart to :func:`remove_strays`: such a copy is settled by
switching the token off where it was made.

Measured on 2026-10-02 and built on here:

- On one machine seven working credentials lay outside the token store: in
  Claude Code's prompt history and session records, in Codex shell snapshots,
  in OpenCode's database, and in two draft files. The shell files held one.
- Plain Python reads those records at about a gigabyte a second (11.7 GB in
  11 seconds), so no other tool is needed on the machine.
- Each of ten real credentials was 108 characters long. The many shorter
  strings of the same shape were fragments and examples.

- macOS Terminal keeps a history file per window under ``~/.zsh_sessions``,
  and each starts as a copy of the whole shared history
  (``/etc/zshrc_Apple_Terminal``: "If the session history doesn't exist yet,
  copy the shared history"). One exported token was in the shared file and in
  five session files, so those are read too.
- The vendor's model list (``GET /v1/models``) answers 401 to a made-up key
  and 404 on a made-up path. It calls no model, so asking it whether an API
  key is still accepted costs nothing. A key that is accepted answers 200:
  :func:`lives` got that for a key lying in one machine's records, which it
  read from the file and never showed.

NOT COVERED, and said rather than solved. Only the places named below, and
those a caller adds, are read: a token anywhere else is not seen, and neither
is one inside a compressed file. A string that says of itself that it is made
up is left out, so a real token that happened to carry one of those words
would be too. A line is removed whole and alone, so a command that was typed
over several lines loses only the line that carried the token. A shell that is
open while its history file is cleaned still holds its own lines in memory and
can write them back when it exits.
"""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import platform
import re
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import claude_as
import claude_keychain as keychain
import claude_token_store as store

#: Shell start-up and history files: where a token that was once pasted into a
#: terminal, or exported from a profile, is still lying.
SHELL_FILES = (".zsh_history", ".bash_history", ".zshrc", ".zprofile", ".zshenv",
               ".bashrc", ".bash_profile", ".profile")

#: History that macOS Terminal keeps per window, beside the shared file.
SESSION_HISTORY = (".zsh_sessions/*.history*", ".bash_sessions/*.history*")

#: The GRIP commands a machine needs for a token to be put there and fenced.
TOOLS = ("grip-relay-bill", "grip-token-canary", "grip-claude-as")

#: Asked of the vendor to learn whether an API key is still accepted. It lists
#: one model: no model is called and nothing is billed.
API_MODELS = "https://api.anthropic.com/v1/models?limit=1"

#: The status the vendor gives a key it does not accept.
REFUSED = 401

#: The two kinds of credential that share the token shape, by how each begins.
KINDS = {"sk-ant-oat": "oauth", "sk-ant-api": "api-key"}

#: How often a file is read again when it changes while it is being cleaned.
ATTEMPTS = 3

#: Where a harness keeps what was typed and what came back, under the home
#: directory: prompt history, session records, shell snapshots, its own
#: database, and GRIP's logs, scratch and drafts.
RECORDS = (".claude/history.jsonl", ".claude/projects", ".claude/shell-snapshots",
           ".claude/logs", ".claude/tmp", ".claude/drafts",
           ".codex/history.jsonl", ".codex/sessions", ".codex/shell_snapshots",
           ".local/share/opencode", ".config/opencode")

#: Folders that are never opened: other people's code, and one private mirror
#: of messages.
NEVER_OPENED = frozenset({"node_modules", ".git", "venv", "__pycache__", "whatsapp-history"})

#: Shorter than this, a string of the shape is a fragment or an example. Each
#: of the ten real credentials measured on 2026-10-02 was 108 characters long.
REAL_LENGTH = 100

#: Words that only a made-up token carries. Each is too long to turn up by
#: chance in the random text of a real one.
MADE_UP = re.compile(r"example|fixture|placeholder|made-up|not-a-real", re.IGNORECASE)

#: A big file is read this many bytes at a time, and each read is looked at
#: together with the end of the one before, so a token on the edge is whole.
CHUNK, OVERLAP = 8 * 1024 * 1024, 512

#: At most this many files are named for one token. The rest are counted.
MOST_FILES = 20

_TOKEN_BYTES = re.compile(store.TOKEN_SHAPE.pattern.encode())


def digest(token: str) -> str:
    """Twelve characters that identify a token without revealing it."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _digest_of(path: Path) -> str | None:
    """The digest of the token in ``path``, or None when it holds no token."""
    token, _ = store.read_active_token(path)
    return digest(token) if store.TOKEN_SHAPE.fullmatch(token) else None


def _labels(active: Path) -> dict:
    """Each stored label: its token's digest, and why its file is not private."""
    prefix = active.name + "."
    checked = store.uses_default_store()
    found = {}
    for path in sorted(active.parent.glob(prefix + "*")):
        label = path.name[len(prefix):]
        if not store.label_problem(label):
            found[label] = {"digest": _digest_of(path),
                            "problem": store.privacy_problem(path) if checked else None}
    return found


def _active(active: Path, labels: dict) -> dict:
    """The token the relay bills on now, and which label it is a copy of."""
    mine = _digest_of(active)
    names = [label for label, entry in labels.items() if mine and entry["digest"] == mine]
    return {"digest": mine, "label": names[0] if names else None}


def _claude() -> dict:
    """Where the ``claude`` command is on this machine and what version it is."""
    try:
        path = claude_as.find_claude(os.environ)
        done = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
    except (claude_as.Refused, OSError, subprocess.SubprocessError):
        return {"path": None, "version": None}
    return {"path": path, "version": (done.stdout.strip().splitlines() or [None])[0]}


def _shell_files(home: Path) -> list[str]:
    """Every file to look in, as a name under ``home``: the start-up and
    history files, then whatever per-window history is there."""
    windows = itertools.chain.from_iterable(sorted(home.glob(pattern)) for pattern in SESSION_HISTORY)
    return [*SHELL_FILES, *(str(path.relative_to(home)) for path in windows)]


def _raw_lines(path: Path) -> list[bytes]:
    """A file's lines with their endings, split at newlines and nowhere else:
    the way an editor and ``sed -n`` number them. Empty when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return handle.readlines()
    except OSError:
        return []


def _hits_in(home: Path, name: str) -> list[tuple[str, int, str]]:
    """``(file name, line number, token)`` for each token-shaped string in one
    shell file. Line numbers start at 1, as an editor shows them."""
    numbered = enumerate(_raw_lines(home / name), start=1)
    return [(name, number, found.decode()) for number, line in numbered
            for found in _TOKEN_BYTES.findall(line)]


def _hits(home: Path) -> list[tuple[str, int, str]]:
    """Every token-shaped string in every shell file, with where it lies."""
    return [hit for name in _shell_files(home) for hit in _hits_in(home, name)]


def _strays(home: Path) -> dict:
    """Token-shaped strings in shell start-up and history files, as digests per
    file. The fence around the store does not cover these files, so a token
    left in one of them is readable by anything that can read the file."""
    found = {}
    for name, _, token in _hits(home):
        found.setdefault(name, set()).add(digest(token))
    return {name: sorted(marks) for name, marks in found.items()}


def survey(grip_dir: str) -> dict:
    """Everything that can be said about this machine without using a token or
    making a call: what is stored, how private it is, and what is installed."""
    active = store.active_path()
    labels = _labels(active)
    tools = Path(grip_dir).expanduser() / "bin"
    return {
        "python": platform.python_version(),
        "system": platform.system(),
        "store": {"present": active.parent.is_dir(), "labels": labels,
                  "active": _active(active, labels)},
        "claude": _claude(),
        "tools": {name: os.access(tools / name, os.X_OK) for name in TOOLS},
        "strays": _strays(Path.home()),
        "keychain": keychain.holds(),
    }


def _one_call(token: str) -> dict:
    """One tiny call with an OAuth token, from this machine, with no other
    login in reach. Says whether the service refused the token, why the call
    failed if it did, and which usage windows it reported."""
    import claude_token_mint as mint

    with tempfile.TemporaryDirectory(prefix="grip-fleet-") as scratch:
        env = claude_as.child_env(os.environ, token)
        env["CLAUDE_CONFIG_DIR"] = scratch
        stream = mint.live_call(claude_as.find_claude(env), env, scratch)
    return {"refused": mint.token_refused(stream), "failure": mint.call_failure(stream),
            "fingerprint": mint.fingerprint(stream)}


NOT_A_SUBSCRIPTION_TOKEN = "this label holds an API key, which a subscription call cannot check"


def identify(label: str) -> dict:
    """Make one tiny call with the token stored under ``label``. Says whether
    the service accepted it and which usage windows it reported.

    The call is a subscription call. Made with an API key it is refused
    whether the key works or not, so for a label that holds anything but a
    subscription token no call is made and nothing is called refused."""
    token = claude_as.read_label(label)
    if kind_of(token) != "oauth":
        return {"digest": digest(token), "refused": False, "failure": NOT_A_SUBSCRIPTION_TOKEN,
                "fingerprint": None}
    return {"digest": digest(token), **_one_call(token)}


# --- tokens lying in shell files -------------------------------------------------

def kind_of(token: str) -> str:
    """``oauth``, ``api-key``, or ``other`` for a string that only has the shape."""
    return next((kind for start, kind in KINDS.items() if token.startswith(start)), "other")


def _oauth_lives(token: str) -> dict:
    try:
        seen = _one_call(token)
    except (claude_as.Refused, OSError, subprocess.SubprocessError) as exc:
        why = store.scrub(str(exc))[0] if isinstance(exc, claude_as.Refused) else type(exc).__name__
        return {"works": None, "why": f"could not be asked ({why})", "windows": None}
    if seen["refused"]:
        return {"works": False, "why": seen["failure"], "windows": None}
    # A usage record comes from the service for the account the token belongs
    # to, so it says the token is accepted even when the call got no further.
    accepted = seen["failure"] is None or seen["fingerprint"] is not None
    return {"works": True if accepted else None, "why": seen["failure"],
            "windows": seen["fingerprint"]}


def _api_key_lives(key: str) -> dict:
    """Ask the vendor whether an API key is still accepted, by listing one
    model. The key goes in a header, to the vendor and nowhere else. Only a
    401 is called a refusal: any other failure says nothing about the key."""
    request = urllib.request.Request(API_MODELS, headers={"x-api-key": key,
                                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return {"works": True, "why": f"HTTP {answer.status}", "windows": None}
    except urllib.error.HTTPError as refused:
        return {"works": False if refused.code == REFUSED else None, "why": f"HTTP {refused.code}",
                "windows": None}
    except OSError as exc:
        return {"works": None, "why": f"could not be asked ({type(exc).__name__})", "windows": None}


_ASK = {"oauth": _oauth_lives, "api-key": _api_key_lives}


def lives(token: str) -> dict:
    """Whether the service still accepts one credential: ``works`` is True,
    False, or None when that could not be learned, with the reason."""
    ask = _ASK.get(kind_of(token))
    if ask is None:
        return {"works": None, "why": "not a kind of token this can test", "windows": None}
    return ask(token)


def stored_lives(label: str) -> dict:
    """Whether the service still accepts the credential stored under ``label``
    on this machine, asked the way its kind needs. This is the question behind
    forgetting a kept copy, where a wrong "refused" would lose a credential
    that works."""
    return lives(claude_as.read_label(label))


def strays(live: bool = False) -> dict:
    """Every different token-shaped string lying in a shell file, by digest:
    what kind it is and on which lines of which files it lies. With ``live``,
    also whether the service still accepts it. Never the string itself.

    One entry, after a live check, looks like this::

        {"0a1b2c3d4e5f": {"kind": "oauth",
                          "where": {".zsh_history": [171], ".zshrc": [4]},
                          "live": {"works": True, "why": None,
                                   "windows": {"weekly_reset": 1791075600,
                                               "extra_usage_off": "out_of_credits"}}}}

    Without one, ``live`` is None. ``works`` is False only when the service
    refused the credential, and None when that could not be learned.
    """
    found, tokens = {}, {}
    for name, number, token in _hits(Path.home()):
        mark = digest(token)
        entry = found.setdefault(mark, {"kind": kind_of(token), "where": {}, "live": None})
        numbers = entry["where"].setdefault(name, [])
        numbers += [number] if number not in numbers else []
        tokens[mark] = token
    if live:
        for mark, token in tokens.items():
            found[mark]["live"] = lives(token)
    return found


def _same_file(one: os.stat_result, other: os.stat_result) -> bool:
    return ((one.st_ino, one.st_size, one.st_mtime_ns)
            == (other.st_ino, other.st_size, other.st_mtime_ns))


def _swap(target: Path, data: bytes, before: os.stat_result) -> bool:
    """Put ``data`` in place of ``target`` in one step, keeping its mode,
    unless the file changed after ``before`` was taken: a shell may have added
    a line in the meantime, and that line must not be lost. Says whether it
    did the swap. Nothing is left behind either way."""
    handle, scratch = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".")
    try:
        with os.fdopen(handle, "wb") as out:
            os.fchmod(out.fileno(), stat.S_IMODE(before.st_mode))
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if not _same_file(target.stat(), before):
            return False
        os.replace(scratch, target)
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(scratch)


def _clean(path: Path) -> int:
    """Remove from one file every line that carries a token-shaped string, and
    return how many went. Every other byte stays as it was. A link is
    followed: the file it points at is rewritten and the link stays a link."""
    target = path.resolve()
    for _ in range(ATTEMPTS):
        before = target.stat()
        lines = _raw_lines(target)
        kept = [line for line in lines if not _TOKEN_BYTES.search(line)]
        if len(kept) == len(lines) or _swap(target, b"".join(kept), before):
            return len(lines) - len(kept)
    raise OSError(f"it kept changing while it was being cleaned ({ATTEMPTS} attempts); run this again")


def _clean_or_say(path: Path) -> int | str:
    """How many lines were removed from one file, or why it was left alone."""
    try:
        return _clean(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc.strerror or exc}"


def remove_strays() -> dict:
    """Delete, from this machine's shell files, every line that carries a
    token-shaped string. Returns the lines ``removed`` per file, and for a
    file that could not be cleaned, why, under ``failed``.

    No copy of a removed line is kept: a copy would be the same secret in a
    new place.
    """
    home = Path.home()
    present = [name for name in _shell_files(home) if (home / name).exists()]
    results = {name: _clean_or_say(home / name) for name in present}
    return {"removed": {name: count for name, count in results.items() if count and isinstance(count, int)},
            "failed": {name: why for name, why in results.items() if isinstance(why, str)}}


# --- tokens lying in what a harness keeps ----------------------------------------

def credible(token: str) -> bool:
    """True for a string that could be a real credential: long enough, of a
    known kind, and not saying of itself that it is made up."""
    return len(token) >= REAL_LENGTH and kind_of(token) != "other" and not MADE_UP.search(token)


def _pieces(handle):
    """A file's content in pieces, each joined to the end of the one before,
    and each with whether it is the last."""
    tail, piece = b"", handle.read(CHUNK)
    while piece:
        following = handle.read(CHUNK)
        yield tail + piece, not following
        tail, piece = (tail + piece)[-OVERLAP:], following


def _whole_tokens(text: bytes, last: bool) -> set[bytes]:
    """The token-shaped strings in one piece of a file. One that touches the
    end of a piece may go on in the next, so it is left to the next piece,
    which begins before it."""
    return {found.group() for found in _TOKEN_BYTES.finditer(text) if last or found.end() < len(text)}


def _tokens_in(path: Path) -> set[str]:
    """Every token-shaped string in one file, whatever the file is. Empty
    when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            found = set().union(*(_whole_tokens(text, last) for text, last in _pieces(handle)))
    except OSError:
        return set()
    return {token.decode() for token in found}


def _kept(folder: str, names: list[str], files: list[str]) -> list[Path]:
    """The files of one folder that are not links. The folders that are never
    opened are struck from the walk that is still to come."""
    names[:] = [name for name in names if name not in NEVER_OPENED]
    return [path for path in (Path(folder, name) for name in files) if not path.is_symlink()]


def _files_under(place: Path) -> list[Path]:
    """Every file under one place. A link below it is not followed: it could
    lead anywhere, and round in a circle."""
    if place.is_file():
        return [place]
    return list(itertools.chain.from_iterable(_kept(*step) for step in os.walk(place)))


def _place(home: Path, root: str) -> Path:
    return Path(root) if os.path.isabs(root) else home / root


def _name(home: Path, path: Path) -> str:
    """How a file is named in the answer: below the home directory, by the
    part below it."""
    return str(path.relative_to(home)) if home in path.parents else str(path)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def records(live: bool = False, extra=()) -> dict:
    """Every credible token lying in what a harness keeps on this machine, by
    digest: its kind and the files that hold it. ``extra`` names more places
    to look in, below the home directory or anywhere. With ``live``, each is
    asked of the service. Never the token itself.

    The answer also says what was looked in, so that finding nothing has a
    scope, and how many strings of the shape were left out for being too
    short or plainly made up.
    """
    home = Path.home()
    roots = [root for root in dict.fromkeys((*RECORDS, *extra)) if _place(home, root).exists()]
    files = list(dict.fromkeys(itertools.chain.from_iterable(
        _files_under(_place(home, root)) for root in roots)))
    where: dict = {}
    for token, name in ((token, _name(home, path)) for path in files for token in _tokens_in(path)):
        where.setdefault(token, set()).add(name)
    kept = {token: sorted(names) for token, names in where.items() if credible(token)}
    found = {digest(token): {"kind": kind_of(token), "files": names[:MOST_FILES],
                             "more": max(0, len(names) - MOST_FILES),
                             "live": lives(token) if live else None}
             for token, names in kept.items()}
    return {"found": found, "left_out": len(where) - len(kept),
            "scanned": {"roots": roots, "files": len(files), "bytes": sum(map(_size, files))}}


# --- the keychain items that hold a token ------------------------------------------

def keychain_lives() -> dict:
    """Whether the service still accepts the token in each keychain item that
    holds one, by item name. An item with no token in it is left out."""
    held = {item: keychain.token_in(item) for item in keychain.ITEMS}
    return {item: lives(token) for item, token in held.items() if token}


def keychain_put(item: str, label: str) -> dict:
    """Make one keychain item hold the token stored under ``label`` on this
    machine. Says which item, and the digest it holds afterwards."""
    return {"item": item, "digest": keychain.put(item, claude_as.read_label(label))}


ACTIONS = {"survey": survey, "identify": identify, "strays": strays,
           "remove_strays": remove_strays, "records": records,
           "keychain_lives": keychain_lives, "keychain_put": keychain_put,
           "stored_lives": stored_lives}


def run(action: str, args: list) -> dict:
    """Carry out one action and never raise: whatever goes wrong on a machine
    is that machine's answer, in words, without a traceback to parse."""
    try:
        return {"ok": True, "data": ACTIONS[action](*args)}
    except (claude_as.Refused, keychain.KeychainError) as why:
        return {"ok": False, "why": str(why)}
    except Exception as exc:  # noqa: BLE001 - a machine's failure is its answer
        return {"ok": False, "why": store.scrub(f"{type(exc).__name__}: {exc}")[0][:300]}
