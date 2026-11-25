"""Side-by-side Claude Code logins: different terminals, different accounts.

THE PROBLEM. `claude` stores one login per CONFIG DIRECTORY, and the default
directory is shared by every terminal. So `/login` as a second account logs the
first one out everywhere, which is unusable when many sessions are running --
and it makes `claude setup-token` for a second account a chicken-and-egg:
minting the token requires a login that disrupts every session already open.

THE MECHANISM, measured 2026-09-18 rather than assumed. Claude Code namespaces
its keychain credentials by the config directory:

    sha256("~/.grip")[:8] == "f6b914ba"
    keychain service "Claude Code-credentials-f6b914ba"

On the operator's machine, the same hash of his real config directory matched
the keychain service name exactly.

Verified end-to-end: with CLAUDE_CONFIG_DIR pointed at an empty directory,
`claude -p` returned "Not logged in - Please run /login" while the default
account stayed logged in and every running session kept working. The isolation
is real, it is per-directory, and it already ships.

WHAT THIS MODULE IS. The path and naming rules for that isolation, kept pure so
they can be tested without touching a keychain or spawning a login. The CLI that
uses them is `bin/grip-account`.

WHAT IT REFUSES. It will not hand out the GRIP directories as an account slot.
`$CLAUDE_DIR` IS the GRIP repository and carries the live login that every
running session depends on; pointing an "account" at it would mean a second
`/login` silently replacing the credentials of the very sessions this exists to
protect. That is the failure the module was written to prevent, so it raises.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

ACCOUNTS_ROOT = Path.home() / ".claude-accounts"

# A slot name becomes a directory name and a shell word. Keep it boring.
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class UnsafeAccount(ValueError):
    """The requested slot would collide with a live, shared login."""


def _protected_dirs() -> set:
    """Directories whose login is shared by running sessions.

    Resolved, so a symlink cannot smuggle one past the check: `~/.claude` is a
    symlink to `~/.grip` on this machine, and comparing unresolved strings would
    have let `--dir ~/.claude` through while `~/.grip` was refused.
    """
    out = set()
    for cand in (os.environ.get("CLAUDE_DIR"), "~/.claude", "~/.grip"):
        if not cand:
            continue
        p = Path(cand).expanduser()
        out.add(p)
        try:
            out.add(p.resolve())
        except OSError:
            pass
    return out


def validate_name(name: str) -> str:
    """A slot name, or raise. Rejects traversal, spaces and empties."""
    if not _VALID_NAME.match(name or ""):
        raise ValueError(
            f"invalid account name {name!r}: use lowercase letters, digits, "
            "dot, dash or underscore (max 64)"
        )
    return name


def config_dir(name: str, root: Path = ACCOUNTS_ROOT) -> Path:
    """The isolated CLAUDE_CONFIG_DIR for one account slot."""
    return root / validate_name(name)


def assert_safe(path: Path) -> Path:
    """Raise UnsafeAccount if this directory carries a shared live login."""
    candidates = {path}
    try:
        candidates.add(path.resolve())
    except OSError:
        pass
    clash = candidates & _protected_dirs()
    if clash:
        raise UnsafeAccount(
            f"refusing to use {path} as an account slot: it is a live shared "
            "config dir, and logging in there would replace the credentials "
            "every running session is using"
        )
    return path


def keychain_service(config_path: Path) -> str:
    """The keychain service name Claude Code uses for that config dir.

    Derived, never guessed: sha256 of the path string, first 8 hex characters,
    appended to the base service name. Confirmed against a live entry.
    """
    digest = hashlib.sha256(str(config_path).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


def _grip_settings() -> Path:
    """GRIP's own settings.json, through the canonical CLAUDE_DIR resolver.

    get_claude_dir() returns an absolute path for a relative $CLAUDE_DIR. That
    matters here: a relative symlink target is read relative to the SLOT, not
    the caller's working directory, so it would dangle (GRIP#8300 review).
    """
    from claude_dir import get_claude_dir
    # .absolute() too: with no $CLAUDE_DIR the default is $HOME/.claude, and a
    # relative $HOME would otherwise give a relative target (GRIP#8303).
    return (get_claude_dir() / "settings.json").absolute()


def link_shared_settings(slot: Path, target: Path | None = None) -> str:
    """Make the slot read GRIP's settings.json. Returns what it did.

    A slot is a separate CLAUDE_CONFIG_DIR, and Claude Code reads its USER
    settings from that dir. A slot that holds its own settings.json therefore
    runs with none of GRIP's hooks (the PreToolUse safety gates among them)
    and none of its claudeMdExcludes, so every rules/*.md loads natively.
    Measured 2026-10-15 on 2.1.296: 86 instruction files, 796.7k characters,
    in a slot whose settings.json held only a theme (GRIP#8299).

    Only the settings file is shared. The login stays per slot, because the
    keychain service is derived from the slot path, not from this file.

    Outcomes: "linked" (new link), "kept" (already the right link),
    "replaced" (a slot-local file was moved aside to settings.json.slot-bak
    and linked), "absent" (GRIP has no settings.json, so nothing to share).
    A link that does not resolve to the target is never reported as success:
    it raises OSError instead.

    The check-and-move runs under a lock beside the slot (<slot>.lock), so two
    terminals migrating one slot at once cannot both choose the same free
    backup name: the second waits, then finds the link and keeps it. Without
    the lock its rename() would replace the first's backup -- the original
    file -- with the new link (Codex review on GRIP#8300).
    """
    target = Path(target).expanduser().absolute() if target else _grip_settings()
    if not target.exists():
        return "absent"
    from file_lock import FileLock
    with FileLock(slot, sidecar=True, timeout=10):
        return _link_locked(slot, target)


def _link_locked(slot: Path, target: Path) -> str:
    """link_shared_settings' body, run while the slot's lock is held."""
    link = slot / "settings.json"
    if link.is_symlink() and link.exists() and link.resolve() == target.resolve():
        return "kept"
    outcome = "linked"
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        backup = slot / "settings.json.slot-bak"
        n = 1
        while backup.exists():
            n += 1
            backup = slot / f"settings.json.slot-bak{n}"
        link.rename(backup)
        outcome = "replaced"
    slot.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    if not (link.exists() and link.resolve() == target.resolve()):
        raise OSError(f"{link} was created but does not resolve to {target}; "
                      "the slot would run without GRIP's settings")
    return outcome


def _grip_projects() -> Path:
    """GRIP's own projects/ folder: session transcripts and the memory store."""
    from claude_dir import get_claude_dir
    return (get_claude_dir() / "projects").absolute()


def _process_envs(proc_root: Path = Path("/proc")) -> list:
    """(command name, environment text) for every running `claude` this user can see.

    Linux reads /proc and keeps each environment's NUL separators; macOS asks
    `ps -E`, which appends a process's environment to its command line. Raises
    when a running claude's environment cannot be read, or when processes
    cannot be listed at all, so the caller can fail safe (Codex review on
    GRIP#8341).
    """
    if proc_root.is_dir() and any(proc_root.glob("[0-9]*/comm")):
        out = []
        for comm_file in proc_root.glob("[0-9]*/comm"):
            try:
                comm = comm_file.read_text().strip()
            except OSError:
                continue  # the process exited between listing and reading
            if comm == "claude":
                out.append((comm, (comm_file.parent / "environ").read_bytes()
                            .decode("utf-8", "replace")))
        return out
    import subprocess
    res = subprocess.run(["ps", "-axwwE", "-o", "comm=,command="],
                         capture_output=True, text=True, timeout=10, check=True)
    return [(line.split(None, 1)[0], line) for line in res.stdout.splitlines() if line.strip()]


def slot_in_use(slot: Path, envs=None) -> bool:
    """True when a running `claude` has this slot as its CLAUDE_CONFIG_DIR.

    Claude Code reopens a transcript by path for every write (lsof finds no
    open handle on a live session's file, measured 2026-10-15), so moving a
    live slot's files could split a running session's history. Any doubt
    therefore counts as in use: a failure to list or read returns True.
    Each CLAUDE_CONFIG_DIR value is compared as a resolved path, so another
    spelling of the slot (a trailing slash, a .. segment, a symlinked folder)
    still counts (Codex review on GRIP#8341). A relative value cannot be
    resolved from outside that process, so it counts as in use too.
    """
    try:
        envs = _process_envs() if envs is None else envs
    except Exception:
        return True
    want = os.path.realpath(slot)
    for comm, env in envs:
        if Path(comm).name != "claude":
            continue
        for value in _config_dir_values(env):
            value = os.path.expanduser(value)
            if not os.path.isabs(value) or os.path.realpath(value) == want:
                return True
    return False


_CONFIG_DIR_VALUE = re.compile(
    r"(?:^|[\s\x00])CLAUDE_CONFIG_DIR=(.*?)(?=[\s\x00][A-Za-z_][A-Za-z0-9_]*=|\x00|$)")


def _config_dir_values(env: str) -> list:
    """Every CLAUDE_CONFIG_DIR value in one process's environment text.

    /proc separates entries with NULs. `ps -E` separates them with spaces,
    and a value may itself contain spaces, so a value runs until the next
    NAME= entry, a NUL, or the end of the text.
    """
    return [m.group(1).rstrip() for m in _CONFIG_DIR_VALUE.finditer(env)]


def _same_filesystem(a: Path, b: Path) -> bool:
    """True when a and b live on one filesystem, so a move needs no copy."""
    return os.stat(a).st_dev == os.stat(b).st_dev


def _move_no_replace(entry: Path, dest: Path) -> bool:
    """Move a file or a link to dest unless something is already there.

    os.link and os.symlink fail when the name exists, so two migrations racing
    for one name cannot silently replace each other, as rename() would
    (Codex review on GRIP#8341). Returns False when dest was taken.
    """
    try:
        if entry.is_symlink():
            os.symlink(os.readlink(entry), dest)
        else:
            os.link(entry, dest)
    except FileExistsError:
        return False
    entry.unlink()
    return True


def _quarantine_file(entry: Path, quarantine: Path) -> None:
    """Move entry under quarantine with the first free name: name, name-2, ..."""
    quarantine.mkdir(parents=True, exist_ok=True)
    n = 1
    while not _move_no_replace(entry, quarantine / (entry.name if n == 1 else f"{entry.name}-{n}")):
        n += 1


def _claim_dir(dest: Path, quarantine: Path) -> Path:
    """A real directory to move a folder's contents into, made atomically.

    mkdir fails when the name exists: an existing real folder is merged into;
    a file or link of that name sends the folder to the quarantine instead.
    """
    try:
        dest.mkdir()
        return dest
    except FileExistsError:
        if dest.is_dir() and not dest.is_symlink():
            return dest
    quarantine.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        candidate = quarantine / (dest.name if n == 1 else f"{dest.name}-{n}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            n += 1


def _merge_move(src: Path, dst: Path, quarantine: Path) -> None:
    """Move everything under src into dst without overwriting anything.

    A name dst already has goes to the same relative place under quarantine.
    Symlinked folders move as links and are not entered.
    """
    for entry in sorted(src.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            _merge_move(entry, _claim_dir(dst / entry.name, quarantine),
                        quarantine / entry.name)
        elif not _move_no_replace(entry, dst / entry.name):
            _quarantine_file(entry, quarantine)


def _remove_empty_dirs(root: Path) -> bool:
    """rmdir every empty folder under root, root included. True when it is gone."""
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            _remove_empty_dirs(entry)
    try:
        root.rmdir()
        return True
    except OSError:
        return False


def link_shared_projects(slot: Path, target: Path | None = None,
                         in_use=slot_in_use) -> str:
    """Make the slot's projects/ a link to GRIP's (GRIP#8313). Returns what it did.

    A slot keeps its own projects/, so its sessions see none of GRIP's memory
    (the MEMORY.md index and its pinned instructions) and what they save stays
    in the slot. Measured 2026-10-15: GRIP's store held 1,035 records, the
    slot's none. V>> chose to link the whole folder, so a slot behaves like
    the default config dir, sessions from every account included.

    Outcomes: "linked" (new link), "kept" (already the right link),
    "migrated" (the slot's own files were moved into GRIP's projects/, any
    name GRIP already had going to projects/.slot-merge-<slot>/, nothing
    deleted, then linked), "in-use" (a live session uses the slot, so nothing
    changed; the next login or env retries), "cross-device" (GRIP's projects/
    is on another filesystem, so nothing was moved), "absent" (GRIP has no
    projects/). The link is installed only after every file has moved, so a
    failure part-way leaves the slot's own folder, and the next call resumes.
    """
    target = Path(target).expanduser().absolute() if target else _grip_projects()
    if not target.is_dir():
        return "absent"
    from file_lock import FileLock
    with FileLock(slot, sidecar=True, timeout=10):
        return _link_projects_locked(slot, target, in_use)


def _link_projects_locked(slot: Path, target: Path, in_use) -> str:
    link = slot / "projects"
    if link.is_symlink() and link.exists() and link.resolve() == target.resolve():
        return "kept"
    if (link.exists() or link.is_symlink()) and in_use(slot):
        return "in-use"
    outcome = "linked"
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        if not _same_filesystem(link, target):
            return "cross-device"
        _merge_move(link, target, target / f".slot-merge-{slot.name}")
        if not _remove_empty_dirs(link):
            raise OSError(f"{link} still holds files after the merge; nothing "
                          "was deleted and the link was not installed")
        outcome = "migrated"
    slot.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    if not (link.exists() and link.resolve() == target.resolve()):
        raise OSError(f"{link} was created but does not resolve to {target}")
    return outcome


def env_for(name: str, root: Path = ACCOUNTS_ROOT) -> dict:
    """The environment overlay that pins a process to one account.

    ANTHROPIC_BASE_URL is deliberately CLEARED. A gateway in front of the API
    makes `claude` report "not available with a custom ANTHROPIC_BASE_URL" for
    managed settings and org policy, and a login flow has no reason to route
    through a proxy. Clearing it keeps the auth read clean.
    """
    path = assert_safe(config_dir(name, root))
    return {"CLAUDE_CONFIG_DIR": str(path), "ANTHROPIC_BASE_URL": ""}
