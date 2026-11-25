"""Parent-context bootstrap loader (Seam B from council verdict 2026-05-03).

On first turn of a thread session, fetch upstream context (parent channel's
last 50 messages for Slack/Discord; sender history for WhatsApp) and format
as ContextSlices for the Commander's bootstrap prompt.

Caching: each session's bootstrap context is loaded ONCE and cached in the
session checkpoint's bootstrap_digest field. Subsequent turns use the cached
context — no repeat loads.

Workspace-graph: writes/updates data/workspace-graph-<teamId>.json on first
load to mirror data/guild-graph-<guildId>.json shape (the Slack equivalent
of the Discord guild crawl signal).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

CLAUDE_DIR = Path.home() / ".claude"
WORKSPACE_GRAPH_DIR = CLAUDE_DIR / "data"
DEFAULT_HISTORY_LIMIT = 50


@dataclass
class ContextSlices:
    parent_channel_id: str
    parent_channel_name: str  # may be empty if not resolved
    messages: list[dict] = field(default_factory=list)  # [{"user", "text", "ts"}, ...]
    workspace_graph_path: Optional[str] = None
    bootstrap_digest: str = ""  # sha256 of canonical-json messages — idempotency guard


def compute_bootstrap_digest(messages: list[dict]) -> str:
    canonical = json.dumps(messages, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def load_parent_context(
    platform: str,
    payload: dict,
    client: Any = None,
    history_fn: Optional[Callable] = None,
    cached_digest: Optional[str] = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> ContextSlices:
    """Load N messages of parent context for a thread.

    Args:
        platform: "slack" | "discord" | "whatsapp"
        payload: platform-specific context (channel id, team id, etc.)
        client: platform client (Slack WebClient / Discord client)
        history_fn: optional injected history fetcher (for tests). Signature:
            history_fn(channel: str, limit: int) -> list[dict]
        cached_digest: if provided and matches the loaded digest, skip the
            full fetch and return ContextSlices with empty messages — caller
            should use its already-cached context.
        limit: how many messages to fetch (default 50).

    Returns:
        ContextSlices with bootstrap_digest set.

    Raises:
        NotImplementedError: for any platform other than "slack" — only the
        slack adapter is supported.
    """
    if platform != "slack":
        raise NotImplementedError(
            f"platform {platform!r} is not supported; only slack is supported."
        )

    channel_id = payload["parent_channel_id"]
    channel_name = payload.get("parent_channel_name", "")
    team_id = payload.get("team_id", "")

    fetch = history_fn if history_fn is not None else _make_slack_history_fn(client)
    messages = fetch(channel=channel_id, limit=limit)
    digest = compute_bootstrap_digest(messages)

    if cached_digest is not None and cached_digest == digest:
        return ContextSlices(
            parent_channel_id=channel_id,
            parent_channel_name=channel_name,
            messages=[],
            bootstrap_digest=digest,
        )

    graph_path: Optional[str] = None
    if team_id:
        written = write_workspace_graph(team_id, channel_id, channel_name)
        graph_path = str(written)

    return ContextSlices(
        parent_channel_id=channel_id,
        parent_channel_name=channel_name,
        messages=messages,
        workspace_graph_path=graph_path,
        bootstrap_digest=digest,
    )


def _make_slack_history_fn(client: Any) -> Callable:
    """Wrap client.conversations_history as a history_fn callable."""

    def _fetch(channel: str, limit: int) -> list[dict]:
        response = client.conversations_history(channel=channel, limit=limit)
        raw = response.get("messages", []) if isinstance(response, dict) else []
        return [
            {"user": m.get("user", ""), "text": m.get("text", ""), "ts": m.get("ts", "")}
            for m in raw
        ]

    return _fetch


def _slack_history(client: Any, channel: str, limit: int) -> list[dict]:
    """Slack default fetch — calls client.conversations_history."""
    return _make_slack_history_fn(client)(channel=channel, limit=limit)


def write_workspace_graph(team_id: str, channel_id: str, channel_name: str = "") -> Path:
    """Write minimal data/workspace-graph-<teamId>.json mirroring guild-graph format.

    Uses atomic write (tempfile + replace) to prevent half-written files under
    concurrent access.
    """
    graph_dir = WORKSPACE_GRAPH_DIR
    graph_dir.mkdir(parents=True, exist_ok=True)

    dest = graph_dir / f"workspace-graph-{team_id}.json"

    existing_channels: list[dict] = []
    if dest.exists():
        try:
            existing = json.loads(dest.read_text())
            existing_channels = existing.get("channels", [])
        except (json.JSONDecodeError, OSError):
            existing_channels = []

    channel_ids = {c["id"] for c in existing_channels}
    if channel_id not in channel_ids:
        existing_channels.append({"id": channel_id, "name": channel_name})

    graph = {"team_id": team_id, "channels": existing_channels, "version": 1}
    payload = json.dumps(graph, indent=2)

    fd, tmp_path = tempfile.mkstemp(dir=graph_dir, prefix=".tmp-workspace-graph-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return dest
