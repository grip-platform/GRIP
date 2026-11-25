"""WhatsApp absorber — V>>'s personal chats marked work-relevant only.

WhatsApp messages flow in through grip-channel's MCP bridge. This absorber
reads from a JSONL log produced by the bridge and applies the relevance
filter conservatively. We never absorb V>>'s spouse/family chats, just
work-relevant threads (clients, team, contractors).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple

from ...wa_identities import operator_jid
from .. import jsonl_io
from .base import AbsorbRecord, coalesce_actors, make_id, utc_from_epoch, utc_now, write_record


# JIDs explicitly opted in for absorption. Anything not in this allowlist is
# dropped before reaching the relevance filter — defence in depth. V>>'s own ID
# (self-message tests) comes from the private file (lib/wa_identities.py); with
# none configured the allowlist is empty and nothing is absorbed.
WORK_JID_ALLOWLIST: Tuple[str, ...] = tuple(j for j in (operator_jid(),) if j)


def absorb_log(log_path: Path, *, allowlist: Optional[Tuple[str, ...]] = None,
               hours_back: int = 24) -> int:
    """Absorb a grip-channel WhatsApp inbound log. Returns count kept."""
    allow = allowlist if allowlist is not None else WORK_JID_ALLOWLIST
    if not log_path.exists():
        return 0
    cutoff_ts = time.time() - (hours_back * 3600)
    kept = 0
    for entry in jsonl_io.stream(log_path):
        jid = entry.get("from_jid") or entry.get("jid") or ""
        if jid not in allow:
            continue
        ts_raw = entry.get("ts") or entry.get("epoch")
        if ts_raw is None or float(ts_raw) < cutoff_ts:
            continue
        rec = AbsorbRecord(
            record_id=make_id("whatsapp", f"{jid}:{ts_raw}"),
            source="whatsapp",
            captured_utc=utc_now(),
            occurred_utc=utc_from_epoch(float(ts_raw)),
            text=entry.get("text") or entry.get("body") or "",
            actors=coalesce_actors(jid, entry.get("display_name")),
            extra={"is_group": entry.get("is_group", False)},
        )
        if write_record(rec):
            kept += 1
    return kept


def opt_in(jid: str, allowlist: List[str]) -> List[str]:
    """Add a JID to the allowlist (returns the new list)."""
    if jid not in allowlist:
        allowlist.append(jid)
    return allowlist
