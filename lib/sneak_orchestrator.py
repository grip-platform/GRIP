"""SneakSession orchestrator for /sneak autonomous draft mode.

Pure helper module — no CLI, no side effects on import.
All public state is written to ~/.claude/drafts/ and
~/.claude/state/session/yolo-mode.json (via yolo_mode).

Tests (not written tonight, but the home is clear):
    tests/test_sneak_orchestrator.py

Usage example
-------------
    from lib.sneak_orchestrator import SneakSession, Milestone

    session = SneakSession(goal="Research GRIP pricing models", max_tracks=3)
    session.activate()          # yolo on + writes marker

    session.milestone(Milestone(
        title="[SNEAK] Track A done",
        body="Draft written: drafts/grip-pricing-models/analysis.md",
        subtitle="1 of 3 tracks complete",
    ))

    session.track_done(
        track_name="grip-pricing-models",
        drafts_path="~/.claude/drafts/grip-pricing-models/",
        hypothesis_id="H099",
    )

    session.complete("All 3 tracks done. Review drafts in the morning.")
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
SESSIONS_LOG = CLAUDE_DIR / "drafts" / ".sneak-sessions.jsonl"

# Heartbeat interval in seconds (25 minutes default)
HEARTBEAT_INTERVAL = 25 * 60


@dataclass
class Milestone:
    """A phone-push event for the operator.

    Example::

        Milestone(
            title="[SNEAK] Track A done",
            body="Pricing model draft complete: drafts/grip-pricing-models/",
            subtitle="2 of 3 tracks complete",
            url="file://~/.claude/drafts/grip-pricing-models/analysis.md",
        )
    """

    title: str
    body: str
    subtitle: Optional[str] = None
    url: Optional[str] = None


class SneakSession:
    """Lifecycle manager for a single /sneak invocation.

    Responsibilities:
    - Activate and deactivate yolo mode via lib.gates.yolo_mode
    - Push phone notifications via lib.grip_notify.default_notifier
    - Record track completions and session summary to JSONL log
    - Expose heartbeat_due() so callers can schedule wakeup checks

    Args:
        goal: Natural language description of the /sneak goal.
        max_tracks: Maximum parallel agent tracks (2-5). Clipped to [2, 5].
        ttl_hours: Yolo mode TTL in hours. Default 6h.

    Example::

        session = SneakSession("Draft GRIP commercialisation options", max_tracks=3)
        session.activate()
        # ... spawn agents ...
        session.complete("All tracks done")
    """

    def __init__(
        self,
        goal: str,
        max_tracks: int = 5,
        ttl_hours: int = 6,
    ) -> None:
        self.goal = goal
        self.max_tracks = max(2, min(5, max_tracks))
        self.ttl_hours = ttl_hours
        self._start_time: float = 0.0
        self._last_heartbeat: float = 0.0
        self._completed_tracks: list[str] = []
        self._active = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Activate yolo mode and record session start.

        Call once, immediately after operator consent. Sets the yolo marker
        with the configured TTL so halting gates are bypassed for the
        duration of the sneak session. Quality gates keep firing.

        Example::

            session = SneakSession("Research options")
            session.activate()
            # safe to spawn agents now
        """
        from lib.gates.yolo_mode import activate_yolo  # lazy import — no import side effects

        activate_yolo(
            activator="/sneak",
            ttl_seconds=self.ttl_hours * 3600,
            reason=self.goal,
        )
        self._start_time = time.time()
        self._last_heartbeat = self._start_time
        self._active = True

    def complete(self, summary: str) -> None:
        """Push final phone notification and deactivate yolo mode.

        Writes a session record to drafts/.sneak-sessions.jsonl so the
        operator has an audit trail of what ran and when.

        Args:
            summary: One-paragraph human-readable summary of what was produced.
                     Included verbatim in the phone notification body.

        Example::

            session.complete(
                "3 tracks done in 4h12m. "
                "Drafts in ~/.claude/drafts/grip-pricing-models/, "
                "grip-target-personas/, grip-competitive-moats/."
            )
        """
        from lib.gates.yolo_mode import deactivate_yolo

        elapsed = _format_elapsed(time.time() - self._start_time)
        self.milestone(Milestone(
            title="[SNEAK] Complete",
            body=summary,
            subtitle=f"{len(self._completed_tracks)} tracks done in {elapsed}",
        ))
        deactivate_yolo("/sneak")
        self._active = False
        self._log_session(summary, elapsed)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def milestone(self, m: Milestone) -> None:
        """Push a milestone notification to the operator's phone.

        Uses default_notifier() which fans out to iMessage, macOS
        Notification Center, and Slack as configured in
        docs/restricted/notify-targets.json.

        Args:
            m: Milestone dataclass with title, body, optional subtitle/url.

        Example::

            session.milestone(Milestone(
                title="[SNEAK] Track B done",
                body="Competitive analysis: drafts/grip-competitive-moats/",
                subtitle="2 of 3 tracks complete",
            ))
        """
        from lib.grip_notify import Notification, default_notifier

        notif = Notification(
            title=m.title,
            body=m.body,
            subtitle=m.subtitle,
            url=m.url,
        )
        default_notifier().send(notif)

    # ------------------------------------------------------------------
    # Track management
    # ------------------------------------------------------------------

    def track_done(
        self,
        track_name: str,
        drafts_path: str,
        hypothesis_id: str,
    ) -> None:
        """Record a track completion and push a phone notification.

        Args:
            track_name: Short slug identifying the track (e.g. "grip-pricing-models").
            drafts_path: Absolute or ~-relative path to the track's output directory.
            hypothesis_id: Hypothesis engine ID registered for this track (e.g. "H099").

        Example::

            session.track_done(
                track_name="grip-pricing-models",
                drafts_path="~/.claude/drafts/grip-pricing-models/",
                hypothesis_id="H099",
            )
        """
        self._completed_tracks.append(track_name)
        remaining = self.max_tracks - len(self._completed_tracks)
        subtitle = (
            f"{len(self._completed_tracks)} of {self.max_tracks} tracks complete"
            if remaining > 0
            else "All tracks complete"
        )
        self.milestone(Milestone(
            title=f"[SNEAK] {track_name} done",
            body=f"Drafts: {drafts_path}  |  Hypothesis: {hypothesis_id}",
            subtitle=subtitle,
        ))

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def heartbeat_due(self) -> bool:
        """Return True if 25+ minutes have elapsed since the last heartbeat.

        Call this in a ScheduleWakeup loop to decide whether to check
        agent progress and push a status notification.

        Example::

            while not all_tracks_done():
                if session.heartbeat_due():
                    check_done_markers()
                    send_status_notification()
                time.sleep(60)
        """
        now = time.time()
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            self._last_heartbeat = now
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_session(self, summary: str, elapsed: str) -> None:
        """Append a session record to drafts/.sneak-sessions.jsonl."""
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "goal": self.goal,
            "tracks": self._completed_tracks,
            "elapsed": elapsed,
            "summary": summary,
        }
        SESSIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SESSIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Internal utility
# ---------------------------------------------------------------------------


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as 'Xh Ym' string.

    Example::

        _format_elapsed(5025) == "1h 23m"
        _format_elapsed(180)  == "3m"
    """
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"
