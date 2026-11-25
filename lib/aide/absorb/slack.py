"""Slack absorber.

Reads from a list of subscribed channel IDs and a list of DM channel IDs,
captures recent activity into the knowledge log. Wraps the canonical
``lib.slack_transport`` shim — same syscall doctrine that already proved
guardrail-safe (see ``rules/syscall-doctrine.md``).
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

from .base import AbsorbRecord, coalesce_actors, make_id, utc_from_epoch, utc_now, write_record


def _transport():
    """Lazy import — Slack transport is optional in test environments."""
    try:
        return importlib.import_module("lib.slack_transport")
    except ImportError:
        return None


def absorb_channel(channel_id: str, *, workspace: str = "codetonight",
                   hours_back: int = 24) -> int:
    """Absorb messages from one channel. Returns count kept."""
    transport = _transport()
    if transport is None or not hasattr(transport, "history"):
        return 0
    try:
        messages: List[Dict[str, Any]] = transport.history(
            workspace=workspace, channel_id=channel_id, hours_back=hours_back,
        )
    except Exception:  # pragma: no cover — transport-specific failures
        return 0
    return _absorb_messages(messages, channel_id=channel_id, workspace=workspace)


def absorb_dm(user_id: str, *, workspace: str = "codetonight",
              hours_back: int = 24) -> int:
    """Absorb DM messages with a specific user (channel_id == user_id)."""
    return absorb_channel(user_id, workspace=workspace, hours_back=hours_back)


def absorb_all(channels: List[str], dms: Optional[List[str]] = None, *,
               workspace: str = "codetonight", hours_back: int = 24) -> Dict[str, int]:
    """Run the full Slack sweep across subscribed channels and DMs."""
    out: Dict[str, int] = {}
    for ch in channels:
        out[f"channel:{ch}"] = absorb_channel(ch, workspace=workspace, hours_back=hours_back)
    for user in dms or []:
        out[f"dm:{user}"] = absorb_dm(user, workspace=workspace, hours_back=hours_back)
    return out


def _absorb_messages(messages: List[Dict[str, Any]], *, channel_id: str,
                     workspace: str) -> int:
    kept = 0
    for msg in messages:
        ts = msg.get("ts")
        if not ts:
            continue
        rec = AbsorbRecord(
            record_id=make_id("slack", f"{channel_id}:{ts}"),
            source="slack",
            captured_utc=utc_now(),
            occurred_utc=utc_from_epoch(float(ts)),
            text=msg.get("text", ""),
            actors=coalesce_actors(msg.get("user"), *(msg.get("mentions") or [])),
            refs=[msg.get("permalink", "")] if msg.get("permalink") else [],
            extra={
                "workspace": workspace,
                "channel_id": channel_id,
                "thread_ts": msg.get("thread_ts"),
                "reactions": msg.get("reactions") or [],
            },
        )
        if write_record(rec):
            kept += 1
    return kept


def detect_decisions(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Heuristic decision detector — returns messages that look load-bearing.

    A "decision" message contains a verdict verb (``decided``, ``approved``,
    ``ship``, ``reject``, ``go with``) AND mentions someone or links to a
    PR/issue. Conservative — caller can review and confirm.
    """
    verdicts = ("decided", "approved", "ship it", "shipping", "reject", "go with",
                "we'll do", "let's do", "agreed")
    out: List[Dict[str, Any]] = []
    for msg in messages:
        text = (msg.get("text") or "").lower()
        if not any(v in text for v in verdicts):
            continue
        if not (msg.get("mentions") or "github.com" in text):
            continue
        out.append(msg)
    return out
