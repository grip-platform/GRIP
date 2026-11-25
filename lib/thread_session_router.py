#!/usr/bin/env python3
"""Unified thread/conversation session router.

PlatformAdapter interface — Slack/Discord/WhatsApp each provide one.
Composes session keys, resolves owners, gates inbound + outbound.

Origin: council verdict 2026-05-03 (commit 4103db49), Seam A unification.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from lib.session_checkpoint import (
    Checkpoint,
    ResumeContext,
    checkpoint,
    evict as evict_checkpoint,
    resume_or_bootstrap,
)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    session_key: str
    is_owner: bool
    is_short_circuit: bool   # DM short-circuit — caller uses existing per-user flow
    is_fresh: bool            # True → no prior checkpoint, caller must bootstrap
    decline_reason: Optional[str] = None  # populated when is_owner is False (polite-refusal)


# ---------------------------------------------------------------------------
# PlatformAdapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: str  # "slack" | "discord" | "whatsapp"

    def compose_key(self, payload: dict) -> str: ...
    def resolve_owner(self, payload: dict, client: Any = None) -> str: ...
    def should_short_circuit(self, payload: dict) -> bool: ...


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ThreadSessionRouter:
    """Routes inbound platform messages to per-thread sessions."""

    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}
        # In-memory cache — avoids re-reading disk on every message in a thread.
        self._owner_cache: dict[str, str] = {}

    # --- Registration ---

    def register_adapter(self, adapter: PlatformAdapter) -> None:
        """Register a platform adapter. Replaces any prior adapter for same platform."""
        self._adapters[adapter.platform] = adapter

    def adapter_for(self, platform: str) -> PlatformAdapter:
        """Return adapter for *platform* or raise KeyError with a clear message."""
        if platform not in self._adapters:
            registered = list(self._adapters.keys())
            raise KeyError(
                f"No adapter registered for platform '{platform}'. "
                f"Registered: {registered}"
            )
        return self._adapters[platform]

    # --- Routing ---

    def route(
        self,
        platform: str,
        payload: dict,
        sender_id: str,
        client: Any = None,
    ) -> RouteResult:
        """Route an inbound message to its session.

        Returns RouteResult describing ownership, freshness, and short-circuit status.
        Raises KeyError for unregistered platforms and propagates adapter errors.
        """
        adapter = self.adapter_for(platform)

        # DM short-circuit: no thread context needed — caller handles via per-user flow.
        if adapter.should_short_circuit(payload):
            return RouteResult(
                session_key="",
                is_owner=True,
                is_short_circuit=True,
                is_fresh=True,
            )

        session_key = adapter.compose_key(payload)
        ctx: ResumeContext = resume_or_bootstrap(session_key)

        if ctx.is_fresh:
            # First message in this thread — resolve owner and persist checkpoint.
            owner_id = adapter.resolve_owner(payload, client=client)
            self._owner_cache[session_key] = owner_id
            cp = Checkpoint(
                key=session_key,
                owner_id=owner_id,
                parent_id=payload.get("channel", ""),
                platform=platform,
                last_activity_at=_utcnow(),
            )
            checkpoint(session_key, cp)
        else:
            # Existing thread — owner already stored; update cache from disk if missing.
            assert ctx.checkpoint is not None
            owner_id = ctx.checkpoint.owner_id
            self._owner_cache.setdefault(session_key, owner_id)

        owned = self.is_owner(session_key, sender_id)
        decline: Optional[str] = None
        if not owned:
            decline = _decline_reason(session_key, owner_id, sender_id)

        return RouteResult(
            session_key=session_key,
            is_owner=owned,
            is_short_circuit=False,
            is_fresh=ctx.is_fresh,
            decline_reason=decline,
        )

    def is_owner(self, session_key: str, sender_id: str) -> bool:
        """Return True if *sender_id* is the thread owner."""
        cached = self._owner_cache.get(session_key)
        if cached is not None:
            return cached == sender_id
        # Cache miss — read from disk.
        ctx = resume_or_bootstrap(session_key)
        if ctx.checkpoint is None:
            return False
        self._owner_cache[session_key] = ctx.checkpoint.owner_id
        return ctx.checkpoint.owner_id == sender_id

    def evict(self, session_key: str) -> None:
        """Remove session from disk and in-memory cache."""
        self._owner_cache.pop(session_key, None)
        evict_checkpoint(session_key)


# ---------------------------------------------------------------------------
# Built-in Slack adapter
# ---------------------------------------------------------------------------


class SlackThreadAdapter:
    """Slack-specific adapter.

    Composite key: channelId:threadTs (Slack ts values are per-channel, not global).
    Owner resolved via conversations.replies parent message.
    DMs short-circuit (no thread_ts).
    """

    platform = "slack"

    def __init__(self, conversations_replies: Optional[Callable] = None) -> None:
        # conversations_replies(channel, ts) -> {"messages": [{"user": ...}, ...]}
        # Default: None — tests mock this; production passes the Slack SDK call.
        self._conv_replies = conversations_replies

    def compose_key(self, payload: dict) -> str:
        ch = payload["channel"]
        ts = payload["thread_ts"]
        return f"{ch}:{ts}"

    def resolve_owner(self, payload: dict, client: Any = None) -> str:
        replies_fn = self._conv_replies or (
            client.conversations_replies if client else None
        )
        if replies_fn is None:
            raise RuntimeError(
                "SlackThreadAdapter.resolve_owner requires a conversations_replies "
                "callable (pass via constructor or client keyword arg)."
            )
        result = replies_fn(channel=payload["channel"], ts=payload["thread_ts"], limit=1)
        msgs = result.get("messages", [])
        if not msgs:
            raise RuntimeError(
                f"No parent message found for thread "
                f"{payload['channel']}:{payload['thread_ts']}"
            )
        return msgs[0]["user"]

    def should_short_circuit(self, payload: dict) -> bool:
        """Return True for DMs (no thread_ts) — caller handles via per-user flow."""
        return not payload.get("thread_ts")


# ---------------------------------------------------------------------------
# Built-in Discord adapter
# ---------------------------------------------------------------------------


class DiscordThreadAdapter:
    """Discord adapter for ThreadSessionRouter.

    Differences from SlackThreadAdapter (per council verdict 2026-05-03):
    - Bare thread_id session key (Discord IDs are globally unique).
    - owner_id resolved from Thread object metadata, no API call.
    - Lifecycle driven by Events.ThreadUpdate archive event, not timer.
    """

    platform = "discord"

    def compose_key(self, payload: dict) -> str:
        return payload["thread_id"]

    def resolve_owner(self, payload: dict, client: Any = None) -> str:
        # Discord thread metadata has owner_id directly on the Thread object.
        # Caller must populate payload["owner_id"] from Thread.owner_id.
        owner_id = payload.get("owner_id")
        if not owner_id:
            raise RuntimeError(
                f"Discord thread {payload.get('thread_id', '?')!r} payload "
                "missing owner_id (caller must populate from Thread.owner_id)"
            )
        return owner_id

    def should_short_circuit(self, payload: dict) -> bool:
        # Short-circuit when no thread_id — regular channel message, not a thread reply.
        return not payload.get("thread_id")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _decline_reason(session_key: str, owner_id: str, sender_id: str) -> Optional[str]:
    """Build polite-refusal message, or None when silent mode is active."""
    if os.environ.get("GRIP_THREAD_DENY") == "silent":
        return None
    return (
        f"This thread session belongs to user {owner_id}. "
        f"Your message ({sender_id}) cannot be processed in this context."
    )


# ---------------------------------------------------------------------------
# CLI entrypoint — called by thread-router.ts subprocess
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Read JSON payload from stdin, route it, write JSON verdict to stdout.

    Input:  {"platform": str, "payload": dict, "sender_id": str}
    Output: {"session_key": str, "is_owner": bool, "is_short_circuit": bool,
             "is_fresh": bool, "decline_reason": str|null}
    Errors: exit 1 with {"error": str} on JSON/key failures.
    """
    import json
    import sys

    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        platform: str = req["platform"]
        payload: dict = req["payload"]
        sender_id: str = req["sender_id"]
    except (json.JSONDecodeError, KeyError) as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}) + "\n")
        sys.exit(1)

    router = ThreadSessionRouter()
    if platform == "slack":
        router.register_adapter(SlackThreadAdapter())
    else:
        sys.stdout.write(json.dumps({"error": f"unsupported platform: {platform}"}) + "\n")
        sys.exit(1)

    result = router.route(platform=platform, payload=payload, sender_id=sender_id)
    sys.stdout.write(
        json.dumps({
            "session_key": result.session_key,
            "is_owner": result.is_owner,
            "is_short_circuit": result.is_short_circuit,
            "is_fresh": result.is_fresh,
            "decline_reason": result.decline_reason,
        }) + "\n"
    )


if __name__ == "__main__":
    _cli_main()
