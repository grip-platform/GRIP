"""Join a new Claude Max account to GRIP in one command.

V>> approved a "one-command account add" in the /hitl round of 2026-10-11, when a
third Max account arrived. Joining an account used to take five commands on two
machines. ``grip-account add <label>`` now signs in the account's slot, mints its
one-year token, then makes the steps below. Each step is skipped when it is already
done, so a run that stopped can simply be run again.

1. This machine's relay pool gains the label (``bin/grip-relay-bill pool``). That
   command refuses a token whose organisation is already pooled, because two tokens
   of one organisation add no capacity.
2. The fleet's label list gains it (``claude-fleet.json``, beside the token store).
3. ``grip-claude-fleet converge --apply`` copies the token to every machine in the
   fleet.

Each other machine keeps its own pool order, because each has its own primary
account. The command prints what to run there instead of guessing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import claude_fleet
import claude_token_store
import subscription_room


def join_pool(label: str, bill: Path, run=subprocess.run) -> tuple:
    """``(ok, what happened)`` after adding ``label`` to this machine's pool.
    The helper runs under this interpreter, as grip-account's other calls to it
    do, never under whatever ``python3`` its shebang finds."""
    active = claude_token_store.active_path()
    current = _current_pool(active)
    if current is None:
        return False, f"could not read this machine's pool beside {active}; nothing was changed"
    if label in current:
        return True, f"already in this machine's pool: {' -> '.join(current)}"
    done = run([sys.executable, str(bill), "pool", *current, label])
    order = " -> ".join([*current, label])
    return (done.returncode == 0,
            f"this machine's pool: {order}" if done.returncode == 0
            else f"grip-relay-bill refused the pool {order} (exit {done.returncode})")


def _current_pool(active: Path) -> list | None:
    """This machine's pool in billing order, ``[]`` when it has none, or None when
    its file exists but cannot be read. ``subscription_room.pool`` reads any error as
    an empty pool, which suits the relay, but here it would make the join write a
    pool holding only the new account."""
    try:
        active.with_name(active.name + ".pool").read_bytes()
    except FileNotFoundError:
        return []
    except OSError:
        return None
    return subscription_room.pool(active)


def join_fleet(label: str, path: Path | None = None) -> tuple:
    """``(ok, what happened)`` after adding ``label`` to the fleet's label list.
    A machine with no fleet file is left alone: a file naming labels but no
    machines would not load. Every other key in the file is kept as it was."""
    path = path or claude_fleet.config_path()
    if not path.exists():
        return True, f"no fleet file at {path}; the fleet steps are skipped"
    doc = _read_fleet(path)
    if doc is None:
        return False, f"could not read the fleet file at {path} as JSON; fix it, then run this again"
    labels = list(doc.get("labels") or [])
    if label in labels:
        return True, f"already in the fleet's labels: {', '.join(labels)}"
    doc["labels"] = [*labels, label]
    try:
        _write_private(path, json.dumps(doc, indent=2) + "\n")
    except OSError as exc:
        return False, f"could not write the fleet file at {path} ({type(exc).__name__}); nothing was changed"
    return True, f"the fleet's labels: {', '.join(doc['labels'])}"


def _read_fleet(path: Path) -> dict | None:
    """The fleet file's JSON object, or None when it cannot be read as one."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_private(path: Path, text: str) -> None:
    """Replace ``path`` in one step. ``mkstemp`` creates the file readable and
    writable by its owner only, and the replace keeps that mode."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


#: The fleet tool's own exit code for "not every machine holds every token yet".
#: Any other non-zero code means it could not do the job at all (2 is a machine
#: list that is missing or wrong).
FLEET_NOT_FINISHED = 1
_LOCAL_DONE = "This machine's pool and the fleet's label list are done."


def converge(fleet_bin: Path, run=subprocess.run, path: Path | None = None) -> tuple:
    """``(ok, what happened)`` after copying the fleet's tokens to every machine.

    A machine with no fleet file has no fleet to copy to, so the step is skipped,
    as ``join_fleet`` skips its own. Otherwise the two ways of failing get
    different words, because only one of them is progress. Exit 1 means some
    machine still lacks a token (for example one fenced off until the stray
    tokens in its shell files are removed), and running the command again resumes
    the copy. Any other code means the fleet tool could not run, and running it
    again fails the same way until that is fixed. In both, this machine's own
    steps are done, and the message says so: a bare "stopped" reads as if nothing
    had been joined."""
    if not (path or claude_fleet.config_path()).exists():
        return True, "no fleet file, so there is nothing to copy to other machines"
    code = run([sys.executable, str(fleet_bin), "converge", "--apply"]).returncode
    if code == 0:
        return True, "copied to every fleet machine"
    if code == FLEET_NOT_FINISHED:
        return False, (f"the copy to the other machines is not finished (grip-claude-fleet converge, "
                       f"exit {code}). {_LOCAL_DONE} The lines above say what is left; once that "
                       "is cleared, running this command again resumes the copy")
    return False, (f"grip-claude-fleet converge could not run (exit {code}), so nothing was copied "
                   f"to the other machines. {_LOCAL_DONE} Fix what the lines above name, then run "
                   "this command again")
