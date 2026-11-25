"""
Channel Security — Sender allowlist and rate limiting.

GRIP Channel must verify that incoming messages are from authorised senders.
This is critical: the Channel bridges external platforms to Claude Code
sessions that can execute tools.

Security model:
    1. Sender allowlist: only pre-approved sender IDs can interact
    2. Rate limiting: per-sender message rate cap (prevents abuse)
    3. Permission scope: senders can be granted different permission levels
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# git-crypt pre-flight: fail LOUD if the allowlist on disk is still ciphertext
# (key not unlocked on this host) rather than silently loading an EMPTY allowlist.
_LIB = Path(__file__).resolve().parent.parent  # lib/channel/ -> lib/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from gitcrypt_preflight import assert_decrypted  # noqa: E402

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"
ALLOWLIST_FILE = CLAUDE_DIR / "config" / "channel-allowlist.json"


class PermissionLevel(Enum):
    """What a sender is allowed to do via Channel."""
    READ_ONLY = "read_only"       # Can receive updates, cannot send commands
    COMMANDS = "commands"          # Can send GRIP commands (e.g. /status, /coffee)
    PERMISSION = "permission"     # Can approve/deny tool use (RSI permission relay)
    ADMIN = "admin"               # Full access (same as CLI)


@dataclass
class AllowedSender:
    """A sender authorised to interact with GRIP via Channel."""
    sender_id: str
    name: str = ""
    platform: str = ""  # whatsapp, slack, discord, or * for all
    permission: PermissionLevel = PermissionLevel.READ_ONLY
    rate_limit: int = 30  # messages per minute
    _message_timestamps: list = field(default_factory=list, repr=False)

    def is_rate_limited(self) -> bool:
        """Check if sender has exceeded their rate limit."""
        now = time.time()
        # Prune timestamps older than 60 seconds
        self._message_timestamps = [
            ts for ts in self._message_timestamps if now - ts < 60
        ]
        return len(self._message_timestamps) >= self.rate_limit

    def record_message(self) -> None:
        """Record a message timestamp for rate limiting."""
        self._message_timestamps.append(time.time())


class SenderAllowlist:
    """Manages the set of authorised Channel senders.

    Persists to config/channel-allowlist.json. The allowlist is loaded
    once at startup and can be reloaded on demand.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = path or ALLOWLIST_FILE
        self._senders: dict[str, AllowedSender] = {}
        self._load()

    def _load(self) -> None:
        """Load allowlist from file."""
        if not self._path.exists():
            logger.debug("No channel allowlist at %s", self._path)
            return
        # FAIL-LOUD before parse: a git-crypt-ciphertext allowlist must never be
        # caught below and turned into an empty (deny-all) allowlist silently.
        assert_decrypted(self._path)
        try:
            data = json.loads(self._path.read_text())
            for entry in data.get("senders", []):
                sender = AllowedSender(
                    sender_id=entry["sender_id"],
                    name=entry.get("name", ""),
                    platform=entry.get("platform", "*"),
                    permission=PermissionLevel(entry.get("permission", "read_only")),
                    rate_limit=entry.get("rate_limit", 30),
                )
                self._senders[sender.sender_id] = sender
            logger.debug("Loaded %d allowed senders", len(self._senders))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to load channel allowlist: %s", e)

    def check(self, sender_id: str, platform: str = "") -> Optional[AllowedSender]:
        """Check if a sender is authorised.

        Returns the AllowedSender if authorised, None if denied.
        Also checks rate limiting — returns None if rate-limited.
        """
        sender = self._senders.get(sender_id)
        if sender is None:
            return None
        if sender.platform not in ("*", "") and platform and sender.platform != platform:
            return None
        if sender.is_rate_limited():
            logger.warning("Rate-limited sender %s (%s)", sender_id, sender.name)
            return None
        sender.record_message()
        return sender

    def add(self, sender: AllowedSender) -> None:
        """Add a sender to the allowlist (in-memory only; call save() to persist)."""
        self._senders[sender.sender_id] = sender

    def remove(self, sender_id: str) -> bool:
        """Remove a sender. Returns True if found and removed."""
        return self._senders.pop(sender_id, None) is not None

    def save(self) -> None:
        """Persist the allowlist to file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "senders": [
                {
                    "sender_id": s.sender_id,
                    "name": s.name,
                    "platform": s.platform,
                    "permission": s.permission.value,
                    "rate_limit": s.rate_limit,
                }
                for s in self._senders.values()
            ]
        }
        self._path.write_text(json.dumps(data, indent=2))

    @property
    def count(self) -> int:
        return len(self._senders)
