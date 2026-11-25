"""Where Claude long-lived tokens live, and what keeps relay sessions off them.

THE PROBLEM, measured 2026-10-01 rather than assumed. A relay-started session
runs with file tools (Read, Grep, Glob), permission prompts bypassed, and its
working directory set to the GRIP directory. That is also where the tokens
lived: ``$CLAUDE_DIR/.cch-oauth-token`` and one ``.cch-oauth-token.<name>`` per
account. Nothing denied those paths and nothing filtered a reply, so an inbound
message could ask the session to read a one-year credential and send it back.
Mode 0600 does not help: the tool runs as the same user.

WHAT THIS MODULE IS. Three things every consumer has to agree on, kept in one
place because three files had each grown their own copy of the path:

  1. where the store is            -> :func:`store_dir`, :func:`active_path`
  2. how a token is read safely    -> :func:`read_active_token`
  3. what a relay session may not
     touch, per harness            -> :func:`deny_read_rules` (Claude Code),
                                      :func:`opencode_secret_rules` (OpenCode)

plus :func:`scrub`, the last-resort filter on outbound relay text.

WHAT IT DOES NOT CLAIM. The deny rules bind a session's built-in file tools.
A session that has a shell (the operator tier, an interactive session) can
still read any file its user can. Only a separate operating-system user or a
relay with no file tools closes that, and neither is built here.
``bin/grip-token-canary`` is the measurement that says whether the rules hold
on a given machine and CLI version; this module is only the rules.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

#: File name of the active token inside the store. Named accounts sit beside it
#: as ``<basename>.<name>``; the failover order as ``<basename>.pool``.
STORE_BASENAME = "cch-oauth-token"
#: The pre-2026-10 name, inside ``$CLAUDE_DIR``. Read only as a migration
#: fallback, and denied to relay sessions for as long as a copy may exist.
LEGACY_BASENAME = ".cch-oauth-token"
#: Explicit override shared by the relay, ``grip-relay-bill`` and
#: ``grip-anthropic-limits``. When set it names the ACTIVE file directly.
PATH_ENV = "GRIP_RELAY_CCH_OAUTH_PATH"

#: Anthropic credential shapes: OAuth tokens (``sk-ant-oat...``) and API keys
#: (``sk-ant-api...``) share this prefix and a base64url-ish body.
TOKEN_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")
SCRUB_PLACEHOLDER = "[withheld: token-shaped text]"

#: What a stored account's label may look like. A label becomes part of a file
#: name, so it is one plain word: nothing that could name a path. The pattern
#: ends in ``\Z`` and not ``$``, because ``$`` also matches just before a
#: trailing newline and would let ``"teammate\n"`` through as a label.
LABEL_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\Z")
#: Suffixes, command words and markers that can never be a label: ``pool`` is the
#: rotation file beside the tokens, ``migrate`` and ``forget`` are commands, and
#: ``unpooled`` is what HAL's ledger calls a token outside the pool, so an account
#: stored under that name could not be told apart from one.
RESERVED_LABELS = ("pool", "migrate", "forget", "unpooled")

_GROUP_OTHER_BITS = 0o077


def _env(env):
    return os.environ if env is None else env


def store_dir(env=None) -> Path:
    """The owner-only directory that holds the tokens.

    Deliberately OUTSIDE the GRIP checkout, any repository and any synced
    folder: ``${XDG_CONFIG_HOME:-$HOME/.config}/grip/claude-tokens``.
    """
    base = (_env(env).get("XDG_CONFIG_HOME") or "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "grip" / "claude-tokens"


def uses_default_store(env=None) -> bool:
    """True unless the operator pinned the active file with :data:`PATH_ENV`."""
    return not (_env(env).get(PATH_ENV) or "").strip()


def active_path(env=None) -> Path:
    """The active token file: the override when set, else inside the store."""
    override = (_env(env).get(PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return store_dir(env) / STORE_BASENAME


#: What a command says when a token was typed as one of its arguments. It never
#: repeats the token: that is the one thing the message must not do.
TOKEN_AS_ARGUMENT = (
    "one of the arguments is a token. A token is only ever read from standard input, never "
    "given as an argument. This one is now in your shell history: remove that line.")


def token_among(args) -> bool:
    """True when any argument carries token-shaped text.

    A token typed where a name goes is an easy slip, and it passes the name
    rule (it is letters, digits and dashes). Taken as a name it would be quoted
    back in messages and become part of a file name. A command asks this
    before it does anything else.
    """
    return any(TOKEN_SHAPE.search(str(arg)) for arg in args)


def label_problem(label: str) -> str | None:
    """Why ``label`` cannot name a stored token, or ``None`` when it can."""
    if token_among([label or ""]):
        return "that is a token, not a label"
    if not LABEL_SHAPE.match(label or ""):
        return "use letters, digits, - or _"
    if label in RESERVED_LABELS:
        return "that name is reserved"
    return None


def labelled_path(label: str, env=None) -> Path:
    """Where the token stored under ``label`` lives: beside the active file,
    as ``<active name>.<label>``. The label is not checked here; callers ask
    :func:`label_problem` first."""
    active = active_path(env)
    return active.with_name(f"{active.name}.{label}")


def legacy_active_path(claude_dir) -> Path:
    """Where the active token lived before the store moved out of the checkout."""
    return Path(claude_dir) / LEGACY_BASENAME


def _owned_by_caller(st: os.stat_result) -> bool:
    getuid = getattr(os, "getuid", None)  # absent on Windows: nothing to compare
    return getuid is None or st.st_uid == getuid()


def privacy_problem(path) -> str | None:
    """Why this token file must not be trusted, or ``None`` when it is private.

    A token is private when neither it nor its directory is a symlink, both
    belong to the caller, and neither grants anything to group or other.
    """
    path = Path(path)
    parent = path.parent
    if parent.is_symlink() or path.is_symlink():
        return "symlink"
    try:
        st_dir, st_file = parent.stat(), path.stat()
    except OSError:
        return "missing"
    if not (_owned_by_caller(st_dir) and _owned_by_caller(st_file)):
        return "foreign-owner"
    if stat.S_IMODE(st_file.st_mode) & _GROUP_OTHER_BITS:
        return "file-mode"
    if stat.S_IMODE(st_dir.st_mode) & _GROUP_OTHER_BITS:
        return "dir-mode"
    return None


def ensure_store_dir(env=None) -> Path:
    """Create the store as an owner-only directory and return it.

    An existing directory that the caller owns is tightened to 0700. A symlink
    or a directory owned by someone else is refused: a store somebody else can
    repoint is not a store.
    """
    path = store_dir(env)
    if path.is_symlink():
        raise PermissionError(f"token store {path} is a symlink; refusing to use it")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if not _owned_by_caller(st):
        raise PermissionError(f"token store {path} is owned by another user")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def read_active_token(path, legacy=None, strict: bool = False) -> tuple[str, str]:
    """Read the active token. Returns ``(token, source)`` and never raises.

    ``source`` is one of:

    - ``"store"``          read from ``path``
    - ``"legacy"``         ``path`` is absent and the pre-move file was used
    - ``"absent"``         no token anywhere; the caller falls back as before
    - ``"unsafe:<why>"``   ``path`` exists but fails :func:`privacy_problem`

    ``strict`` applies the privacy check. Callers pass it for the default
    store, whose 0700 and 0600 modes this module itself creates; an operator
    override keeps its historical read-if-readable behaviour. An unsafe store
    never falls through to the legacy file: a store that has been tampered
    with should be loud, not quietly replaced by an older copy.
    """
    path = Path(path)
    if path.exists() or path.is_symlink():
        problem = privacy_problem(path) if strict else None
        if problem:
            return "", f"unsafe:{problem}"
        return _read_text(path), "store"
    if legacy is not None and Path(legacy).exists():
        return _read_text(Path(legacy)), "legacy"
    return "", "absent"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _with_real(paths) -> list[Path]:
    """Each path plus its symlink-resolved form, in order, without repeats.

    Claude Code applies a deny rule written through a symlinked directory at
    the real location only from 2.1.268, and the GRIP directory is commonly a
    symlink (``~/.claude`` to ``~/.grip``), so both spellings are emitted.
    """
    both: list[Path] = []
    for raw in paths:
        p = Path(raw)
        both += [p, _resolved(p)]
    return list(dict.fromkeys(both))


def secret_dirs(env=None) -> list[Path]:
    """Directories a relay session may read nothing under."""
    return _with_real([store_dir(env)])


def deny_read_rules(claude_dir, env=None, extra_files=()) -> list[str]:
    """Claude Code ``permissions.deny`` rules for a relay-started session.

    The vendor documents that deny rules "block in every mode, including
    ``bypassPermissions``", which is the mode a relay spawn runs in, and that
    ``//path`` is an absolute path. Measured on 2.1.283: with such a rule on
    the command line, Read was refused and Grep and Glob skipped the directory.

    ``extra_files`` names further credential files to fence, such as the
    relay's pay-as-you-go key and the Linux login file.
    """
    rules = [f"Read(/{d}/**)" for d in secret_dirs(env)]
    rules += [f"Read(/{base}/{LEGACY_BASENAME}*)"
              for base in _with_real([Path(claude_dir)])]
    rules += [f"Read(/{f})" for f in _with_real(extra_files)]
    rules.append("Read(//proc/**/environ)")
    return rules


def opencode_secret_rules(env=None, extra_files=()) -> dict:
    """OpenCode ``permission`` entries that fence the same credentials.

    OpenCode matches ``read`` against the file path but ``grep`` and ``glob``
    against the search pattern, so a read rule alone does not stop a content
    search. ``external_directory`` covers every path-taking tool for paths
    outside the working directory, which is where the store now lives; that is
    the rule that actually fences it. The ``read`` map starts from OpenCode's
    own defaults (allow, with ``.env`` files denied) because supplying a map
    replaces them, and the last matching rule wins.
    """
    fenced: list[str] = []
    for d in secret_dirs(env):
        fenced += [str(d), f"{d}/*"]
    files = [str(f) for f in _with_real(extra_files)]
    read = {"*": "allow", "*.env": "deny", "*.env.*": "deny",
            "*.env.example": "allow", f"*{LEGACY_BASENAME}*": "deny",
            "/proc/*": "deny"}
    read.update(dict.fromkeys(fenced + files, "deny"))
    external = {"*": "ask", "/proc": "deny", "/proc/*": "deny"}
    external.update(dict.fromkeys(fenced, "deny"))
    return {"read": read, "external_directory": external}


def scrub(text: str) -> tuple[str, int]:
    """Replace credential-shaped text. Returns ``(clean_text, replacements)``.

    A floor, not a guarantee: it catches a token sent back verbatim, and
    nothing that has been split, encoded or reversed. The deny rules are the
    control; this only narrows what a failure of them can leak into a chat.
    """
    return TOKEN_SHAPE.subn(SCRUB_PLACEHOLDER, text)


def scrubbed(text: str | None, most: int, *, end: bool = True) -> str:
    """``text`` with credential-shaped text replaced, and only then cut to
    ``most`` characters: its last ones, or with ``end=False`` its first.
    ``None``, which a process that captured no output hands over, is nothing
    to say and comes back as the empty string.

    The order is the point. Cut first, a token lying across the cut is no
    longer token-shaped. A cut that removes its beginning leaves the rest of
    it to be shown, which is everything but the prefix; a cut that removes its
    end leaves the prefix and what follows it. Every message that is both
    shortened and shown goes through here, so the order is written once.
    """
    clean = scrub((text or "").strip())[0]
    return clean[-most:] if end else clean[:most]


#: The most lines, and the most of each line, that :func:`besides` passes on.
BESIDES_LINES, BESIDES_WIDTH = 5, 300


def besides(said: str) -> list[str]:
    """What ``grip-relay-bill store`` said after its first line.

    Its first line is "stored token for ...". After it come, one line each,
    the other things it did: kept the token the name had before as a way back,
    moved the relay to the new one. A caller that stored a token for the
    operator passes these on, so a relay that moved is never a surprise. Each
    line is scrubbed, and only a few short ones are passed on: this is a note
    for a person, not a channel.
    """
    lines = [line.strip() for line in said.splitlines() if line.strip()]
    return [scrubbed(line, BESIDES_WIDTH, end=False) for line in lines[1:1 + BESIDES_LINES]]
