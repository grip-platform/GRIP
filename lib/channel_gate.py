"""Channel outbound gate — fail-closed write gating per platform.

Each platform registers a policy: (session_key, target_channel_id) -> verdict.
Verdict is Allow or Deny(reason). Unregistered platforms fail closed (deny).

Origin: council verdict 2026-05-03 (commit 4103db49), Seam D unification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


Verdict = Allow | Deny  # type alias

# Policy signature: (session_key: str, target_channel_id: str, owner_dm_id: Optional[str]) -> Verdict
Policy = Callable[[str, str, Optional[str]], Verdict]


class OutboundGate:
    """Fail-closed outbound write gate keyed by platform.

    Platforms must register a policy before any writes are evaluated.
    An unregistered platform always yields Deny — there is no implicit allow.
    """

    def __init__(self) -> None:
        self._policies: dict[str, Policy] = {}

    def register_policy(self, platform: str, policy: Policy) -> None:
        """Register a write policy for the given platform.

        Overwrites any previously registered policy for the same platform.
        """
        self._policies[platform] = policy

    def evaluate(
        self,
        platform: str,
        session_key: str,
        target_channel_id: str,
        owner_dm_id: Optional[str] = None,
    ) -> Verdict:
        """Evaluate whether a write to target_channel_id is allowed.

        Looks up the platform policy. If no policy is registered for the
        platform, returns Deny (fail-closed). Otherwise delegates to the
        registered policy.

        Args:
            platform: Platform identifier (e.g. "slack", "discord").
            session_key: The session's canonical key (e.g. composite thread key).
            target_channel_id: The channel the agent wants to write to.
            owner_dm_id: Optional DM channel of the session owner.

        Returns:
            Allow if the write is permitted, Deny with a reason otherwise.
        """
        policy = self._policies.get(platform)
        if policy is None:
            return Deny(
                f"no policy registered for platform {platform!r} — write to "
                f"{target_channel_id!r} denied (fail-closed)"
            )
        return policy(session_key, target_channel_id, owner_dm_id)

    def has_policy(self, platform: str) -> bool:
        """Return True iff a policy is registered for the given platform."""
        return platform in self._policies


def thread_session_policy(
    session_key: str,
    target_channel_id: str,
    owner_dm_id: Optional[str] = None,
) -> Verdict:
    """Default policy for thread-bound sessions.

    Allows writes to:
    - The thread itself (target == session_key)
    - The thread owner's DM channel (target == owner_dm_id, when provided)

    Denies everything else with a clear reason string.

    Note on Slack composite keys: session_key for Slack sessions is the
    composite ``{channelId}:{threadTs}`` form (see thread_session_router.py).
    For Discord, bare thread IDs are used directly. The policy is
    platform-agnostic — it compares by equality regardless of key shape.
    """
    if target_channel_id == session_key:
        return Allow()
    if owner_dm_id is not None and target_channel_id == owner_dm_id:
        return Allow()
    return Deny(
        f"thread-bound session {session_key!r} cannot write to "
        f"channel {target_channel_id!r} (only the thread and owner DM are allowed)"
    )
