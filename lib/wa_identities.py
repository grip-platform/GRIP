"""WhatsApp identities that running code needs, read from one private local file.

Real numbers do not belong in the repository (rules/personal-matters-stay-private.md,
rules/operator-anonymity.md). Running code asks this module instead, and each value
resolves in this order:

1. an environment variable the caller names, so existing overrides keep working;
2. the private file: $GRIP_WA_IDENTITIES if set (an absolute path: a relative one
   resolves against the working directory), else
   <CLAUDE_DIR>/config/wa-identities.local.json, which git ignores;
3. nothing. The function returns None, and the caller must not send.

The file's shape is config/wa-identities.example.json, which holds obvious fakes.
Each lookup accepts only its own kind of ID: a person (the operator or a contact)
is 6-20 digits then @s.whatsapp.net, @lid or @c.us, and a group is 8-30 digits then
@g.us, the ranges lib/wa_control_state.py accepts. Anything else counts as missing,
so a typo, or a group ID filed as a person, fails closed instead of sending to the
wrong audience. An override that is set at all decides the answer, even when it is
empty or malformed: it then returns None rather than falling back to the file, so
an explicit override is never silently replaced (an empty one disables the sender).
A sender that refuses for want of a value reports ``MISSING_HINT``, which says
where to add it.

Values are read from the file on every call, but most callers copy one into a
module constant when they are imported. A long-running process (the answer-watch
daemon, the relay drain) therefore sees an edited file only after it restarts,
just as a number typed into the code needed a restart before.

The relay reads two more tables from the same file: "relay"."full_capability"
({person ID: label}) and "relay"."reply" ({allowlist name: person ID}), plus the
optional top-level "operator_lid". See relay_full_capability() and relay_reply_map().

``python -m lib.wa_identities --check`` reports which values are set on this
machine without printing any of them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

FILE_ENV = "GRIP_WA_IDENTITIES"
PERSON_RE = re.compile(r"\d{6,20}@(?:s\.whatsapp\.net|lid|c\.us)")
GROUP_RE = re.compile(r"\d{8,30}@g\.us")
# What a sender reports when it refuses because nothing is configured.
MISSING_HINT = ("no WhatsApp ID configured: add it to config/wa-identities.local.json "
                "(shape: config/wa-identities.example.json)")


def identities_path() -> Path:
    """Where the private file is read from on this machine."""
    override = os.environ.get(FILE_ENV, "").strip()
    return Path(override) if override else get_claude_dir() / "config" / "wa-identities.local.json"


def load() -> dict:
    """The private file's contents, or {} when it is missing, unreadable or not an object."""
    try:
        data = json.loads(identities_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid(value, kind: re.Pattern) -> str | None:
    return value if isinstance(value, str) and kind.fullmatch(value) else None


def _resolve(env: str | None, value, kind: re.Pattern) -> str | None:
    override = os.environ.get(env) if env else None
    return _valid(override.strip(), kind) if override is not None else _valid(value, kind)


def _table(key: str) -> dict:
    table = load().get(key)
    return table if isinstance(table, dict) else {}


def operator_jid(env: str | None = None) -> str | None:
    """The operator's own WhatsApp ID (a person), or None when none is configured."""
    return _resolve(env, load().get("operator"), PERSON_RE)


def contact_jid(name: str, env: str | None = None) -> str | None:
    """A named contact's WhatsApp ID (a person) from the "contacts" table, or None."""
    return _resolve(env, _table("contacts").get(name), PERSON_RE)


def group_jid(name: str, env: str | None = None) -> str | None:
    """A named group's WhatsApp ID (a group) from the "groups" table, or None."""
    return _resolve(env, _table("groups").get(name), GROUP_RE)


def operator_lid(env: str | None = None) -> str | None:
    """The operator's WhatsApp LID (the per-account ID a group delivers), or None.

    Read from the top-level "operator_lid" key. It is optional: without it the
    operator is still recognised by phone ID, just not in groups that deliver LIDs.
    """
    return _resolve(env, load().get("operator_lid"), PERSON_RE)


def _relay_table(key: str) -> dict:
    relay = _table("relay")
    table = relay.get(key)
    return table if isinstance(table, dict) else {}


def _labelled(table: dict, id_is_key: bool) -> dict:
    """The table's entries whose ID passes PERSON_RE and whose label is a non-empty string."""
    out = {}
    for key, value in table.items():
        jid, label = (key, value) if id_is_key else (value, key)
        if _valid(jid, PERSON_RE) and isinstance(label, str) and label.strip():
            out[key] = value
    return out


def relay_full_capability() -> dict:
    """{person ID: label} for the senders the relay may give its full tool surface.

    The operator's phone ID and LID come first, labelled "V>>", followed by the
    "relay"."full_capability" table. An entry that is not a person's WhatsApp ID,
    or has no label, is dropped. Empty when nothing is configured, so nobody is
    widened by default.
    """
    out = {jid: "V>>" for jid in (operator_jid(), operator_lid()) if jid}
    for jid, label in _labelled(_relay_table("full_capability"), id_is_key=True).items():
        out.setdefault(jid, label)
    return out


def relay_reply_map() -> dict:
    """{allowlist name: person ID} the relay drain replies to, keyed by sender name.

    The operator's phone ID comes first under "V>>", followed by the
    "relay"."reply" table. An entry whose ID is not a person's WhatsApp ID is
    dropped. Without the operator there is no "V>>" entry, and the drain idles.
    """
    operator = operator_jid()
    out = {"V>>": operator} if operator else {}
    for name, jid in _labelled(_relay_table("reply"), id_is_key=False).items():
        out.setdefault(name, jid)
    return out


def check_report() -> str:
    """Which values are set on this machine, never the values themselves."""
    operator = "set" if operator_jid() else "missing"
    lid = "set" if operator_lid() else "missing"
    full = _labelled(_relay_table("full_capability"), id_is_key=True)
    reply = _labelled(_relay_table("reply"), id_is_key=False)
    return (f"path: {identities_path()}\noperator: {operator}\noperator_lid: {lid}\n"
            f"contacts: {', '.join(sorted(k for k in _table('contacts') if contact_jid(k))) or 'none'}\n"
            f"groups: {', '.join(sorted(k for k in _table('groups') if group_jid(k))) or 'none'}\n"
            f"relay full_capability entries: {len(full)}\nrelay reply entries: {len(reply)}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        sys.exit("usage: python -m lib.wa_identities --check")
    print(check_report())
