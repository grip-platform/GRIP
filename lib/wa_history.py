"""WhatsApp per-JID history reader — the GRIP half of the history store.

The grip-channel daemon (mcp-servers/grip-channel/src/history-store.ts)
appends every message it sees to ``state/whatsapp-history/<jid>.jsonl``.
This module reads those files so "GRIP, read X's chats" is a real verb.

    from lib.wa_history import read_chat, list_chats
    rows = read_chat("27000000000")            # digits, full JID, or known name
    rows = read_chat("v", limit=50)

CLI: ``bin/grip-wa-chats <who> [-n 50] [--since 2026-06-09] [--history-dir D]``.

Known-contact names resolve via the small registry below (operational
contacts only; extend as contacts earn memory files).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from itertools import chain as _chain
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
import wa_export as _wx  # noqa: E402
from wa_identities import group_jid, operator_jid  # noqa: E402

CLAUDE_DIR = get_claude_dir()
HISTORY_DIR = CLAUDE_DIR / "state" / "whatsapp-history"

# Known operational contacts (name -> digits or full JID). Group chats MUST
# carry their explicit @g.us suffix here — groups are never inferred from
# digits. The IDs come from the private config/wa-identities.local.json
# (lib/wa_identities.py); a name whose ID is not configured is left out.
# Names are matched case-insensitively.
_OPERATOR = operator_jid()
_TEAM_GROUP = group_jid("team-group")
KNOWN_CONTACTS = {
    "v": _OPERATOR,
    "laurie": _OPERATOR,
    "team-group": _TEAM_GROUP,
    "group": _TEAM_GROUP,
}
KNOWN_CONTACTS = {name: jid for name, jid in KNOWN_CONTACTS.items() if jid}

SINCE_FORMAT_HINT = (
    "expected an ISO date or datetime, e.g. 2026-06-09 or 2026-06-09T14:30 "
    "(naive values are read as local time)"
)


def _sanitise(jid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", jid)


def _store_dir(history_dir: Path | str | None) -> Path:
    """The store to read: explicit override or the default HISTORY_DIR."""
    return Path(history_dir) if history_dir else HISTORY_DIR


def resolve_jid(who: str) -> str:
    """Resolve a name / digits / full JID to a full JID — explicit rules only.

    - Input already carrying a domain (``@g.us`` / ``@s.whatsapp.net`` /
      ``@lid``) is kept verbatim.
    - Bare digits default to ``@s.whatsapp.net`` regardless of length.
    - ``@g.us`` is NEVER inferred: a group JID must be explicit, either in
      the input or in the KNOWN_CONTACTS registry.
    """
    w = who.strip()
    if "@" in w:
        return w
    known = KNOWN_CONTACTS.get(w.lower())
    if known:
        return known if "@" in known else f"{known}@s.whatsapp.net"
    digits = re.sub(r"\D", "", w)
    if not digits:
        raise ValueError(f"Cannot resolve {who!r} to a JID — pass digits or a full JID")
    return f"{digits}@s.whatsapp.net"


def chat_file(who: str, history_dir: Path | str | None = None) -> Path:
    return _store_dir(history_dir) / f"{_sanitise(resolve_jid(who))}.jsonl"


def since_cutoff(since: str) -> float:
    """Unix timestamp for an ISO date or datetime string.

    Accepts anything ``datetime.fromisoformat`` parses (date or datetime).
    Naive values are assumed local time; ``.astimezone()`` normalises both
    naive and aware values before the epoch conversion.

    Raises ValueError with a plain-language format hint on bad input.
    """
    try:
        parsed = _dt.datetime.fromisoformat(since)
    except ValueError as exc:
        raise ValueError(f"Invalid since value {since!r}: {SINCE_FORMAT_HINT}") from exc
    return parsed.astimezone().timestamp()


def _iter_rows(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate a torn final line


#: A rotated generation of a thread file: ``<thread>.<n>.jsonl``.
_ROTATED_SUFFIX = re.compile(r"\.(\d+)\.jsonl$")


def rotation_slot(name: str) -> int | None:
    """The generation number if ``name`` is a rotated sibling, else ``None``.

    Real thread filenames end in the JID's domain part (``…_s.whatsapp.net``,
    ``…_g.us``, ``…_lid``), so a live file never matches a numeric slot.
    """
    m = _ROTATED_SUFFIX.search(name)
    return int(m.group(1)) if m else None


def rotated_siblings(main: Path) -> list[Path]:
    """Every rotated generation of ``main``, OLDEST first.

    Slot numbers ascend with age — ``.1`` is the most recent rotation — so
    oldest-first is descending slot order.

    Depth comes from what is on DISK, never from ``GRIP_WA_HISTORY_KEEP``: this
    process may not share the daemon's environment, and lowering the retention
    depth strands higher slots rather than deleting them. Stranded history is
    still history, so reading by disk state means a configuration change can
    never make already-captured messages vanish from a chronology.
    """
    parent = main.parent
    if not parent.is_dir():
        return []
    prefix = main.name[: -len(".jsonl")] + "."
    found: list[tuple[int, Path]] = []
    for f in parent.iterdir():
        if not f.name.startswith(prefix):
            continue
        slot = rotation_slot(f.name)
        if slot is not None and f.name == f"{prefix}{slot}.jsonl":
            found.append((slot, f))
    found.sort(key=lambda pair: -pair[0])
    return [f for _, f in found]


def read_chat(who: str, limit: int = 100, since: str | None = None,
              history_dir: Path | str | None = None) -> list[dict]:
    """Rows for a chat, oldest-first, across EVERY retained generation.

    Reading only ``.1`` would silently hide history that a raised
    ``GRIP_WA_HISTORY_KEEP`` deliberately retained (GRIP#6025).
    """
    main = chat_file(who, history_dir)
    rows: list[dict] = []
    for sibling in rotated_siblings(main):   # oldest generation first
        rows.extend(_iter_rows(sibling))
    rows.extend(_iter_rows(main))
    if since:
        cutoff = since_cutoff(since)
        rows = [r for r in rows if r.get("ts", 0) >= cutoff]
    return rows[-limit:]


def _index_generations(files: list[Path]) -> dict[str, list[Path]]:
    """Map each live thread filename to its rotated generations, OLDEST first.

    Built in ONE pass over the directory listing. Calling ``rotated_siblings``
    per thread instead would re-scan the directory once per file and make
    ``list_chats`` quadratic in the thread count.
    """
    by_base: dict[str, list[tuple[int, Path]]] = {}
    for f in files:
        slot = rotation_slot(f.name)
        if slot is None:
            continue
        base = f.name[: -len(f".{slot}.jsonl")] + ".jsonl"
        by_base.setdefault(base, []).append((slot, f))
    return {base: [p for _, p in sorted(pairs, key=lambda pair: -pair[0])]
            for base, pairs in by_base.items()}


def list_chats(history_dir: Path | str | None = None) -> list[dict]:
    """All chats with row counts and last-activity timestamps.

    Excludes EVERY rotated generation, not just ``.1``. Excluding only ``.1``
    made ``.2`` and deeper surface as separate conversations once
    ``GRIP_WA_HISTORY_KEEP`` could raise the ring depth (GRIP#6025), which
    duplicated JIDs here and made ``grip-wa-chronology --all`` process the same
    thread repeatedly.

    Row counts span every generation, so a rotated thread is not under-reported.
    """
    out = []
    files = sorted(_store_dir(history_dir).glob("*.jsonl"))
    generations = _index_generations(files)
    for f in files:
        if rotation_slot(f.name) is not None:
            continue  # a rotated generation of another file, not its own chat
        sources = (*generations.get(f.name, ()), f)  # oldest generation first
        rows = list(_chain.from_iterable(_iter_rows(p) for p in sources))
        if not rows:
            continue
        out.append({
            "file": f.name,
            "chat": rows[-1].get("chat", f.stem),
            "rows": len(rows),
            "last_ts": rows[-1].get("ts", 0),
            "last_text": str(rows[-1].get("text", ""))[:60],
        })
    return sorted(out, key=lambda r: r["last_ts"], reverse=True)


def render(rows: list[dict]) -> str:
    """Readable transcript rendering for terminal / session use."""
    lines = []
    for r in rows:
        stamp = _wx.matter_dt(r.get("ts", 0)).strftime("%m-%d %H:%M")
        who = {"in": r.get("pushName") or r.get("sender", "?").split("@")[0],
               "out-phone": "V>> (phone)",
               "out-bot": "GRIP"}.get(r.get("direction", ""), "?")
        lines.append(f"[{stamp}] {who}: {r.get('text', '')}")
    return "\n".join(lines) if lines else "(no history captured yet)"
