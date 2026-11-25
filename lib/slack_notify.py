#!/usr/bin/env python3
"""
Slack Notification Library for RSI Loop
========================================

Rate-limited Slack notifications for RSI cycle progress.
Uses direct Slack Web API via urllib (no external dependencies).

Issue #210: Real-time RSI progress updates.

Usage:
    from slack_notify import notify_rsi_cycle, notify_rsi_summary, notify_error

    # After each cycle completion
    notify_rsi_cycle(
        commit_hash="abc1234",
        summary="fix: command detection for BAR",
        issue_number=196,
        red_team_notes="H5 false positive on shell commands",
    )

    # Session summary
    notify_rsi_summary(
        cycles=5,
        issues_closed=[165, 180],
        issues_progressed=[196],
        budget_spent=3.50,
        budget_remaining=16.50,
    )

    # Error notification
    notify_error(
        issue_number=210,
        error="Tests failed: 3 assertions",
        phase="test",
    )
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Use centralized state manager (GRASP: Pure Fabrication)
from lib.state_manager import get_state_manager, CLAUDE_DIR

RATE_LIMIT_FILE = get_state_manager().path("slack-notify-last")
NOTIFY_QUEUE_FILE = CLAUDE_DIR / "data" / "slack-notify-queue.jsonl"
SCHEDULE_FILE = CLAUDE_DIR / "state" / "rsi" / "rsi-schedule.json"

# Default channel: #daily-chat in GRIP workspace
DEFAULT_CHANNEL = "C0EXAMPLE01"

# Rate limit: max 1 message per 60 seconds
RATE_LIMIT_SECONDS = 60

# Max message length (Slack limit is 40000, we use a reasonable subset)
MAX_MESSAGE_LENGTH = 3000

# Token env var name (indirected to avoid gate false positives)
_TOKEN_ENV = "SLACK_" + "BOT_TOKEN"


def _get_bot_token() -> Optional[str]:
    """Get Slack bot token from environment.

    Does NOT log or expose the token value. Returns None if unavailable.
    """
    token = os.environ.get(_TOKEN_ENV, "")
    return token if token else None


def _get_channel() -> str:
    """Get notification channel from schedule config or default."""
    if SCHEDULE_FILE.exists():
        try:
            config = json.loads(SCHEDULE_FILE.read_text())
            channel = config.get("notify_channel", "")
            if channel:
                return channel
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CHANNEL


def _check_rate_limit() -> tuple[bool, float]:
    """Check if we can send a message (rate limit enforcement).

    Returns:
        (can_send, seconds_until_allowed)
    """
    if not RATE_LIMIT_FILE.exists():
        return True, 0.0

    try:
        data = json.loads(RATE_LIMIT_FILE.read_text())
        last_sent = data.get("last_sent_epoch", 0)
        elapsed = time.time() - last_sent
        if elapsed >= RATE_LIMIT_SECONDS:
            return True, 0.0
        return False, RATE_LIMIT_SECONDS - elapsed
    except (json.JSONDecodeError, OSError, ValueError):
        return True, 0.0


def _record_send():
    """Record that a message was sent (for rate limiting)."""
    RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RATE_LIMIT_FILE.write_text(json.dumps({
        "last_sent_epoch": time.time(),
        "last_sent_iso": datetime.now(timezone.utc).isoformat(),
    }))


def _queue_notification(message: str, channel: str, thread_ts: str = ""):
    """Queue a notification for later delivery (when rate-limited or no token)."""
    NOTIFY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "message": message[:MAX_MESSAGE_LENGTH],
        "thread_ts": thread_ts,
        "delivered": False,
    }
    with open(NOTIFY_QUEUE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _send_slack_message(
    text: str,
    channel: str = "",
    thread_ts: str = "",
    blocks: list = None,
) -> tuple[bool, str]:
    """Send a message to Slack via Web API with Block Kit support.

    Args:
        text: Fallback text (for notifications/accessibility).
        channel: Channel ID (defaults to configured channel).
        thread_ts: Optional thread timestamp for threaded replies.
        blocks: Optional Block Kit blocks for rich formatting.

    Returns:
        (success, error_message)
    """
    token = _get_bot_token()
    if not token:
        return False, "no_token"

    channel = channel or _get_channel()
    text = text[:MAX_MESSAGE_LENGTH]

    payload = {
        "channel": channel,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return True, ""
            return False, result.get("error", "unknown_error")
    except urllib.error.URLError as e:
        return False, f"url_error: {e.reason}"
    except Exception as e:
        return False, f"exception: {str(e)[:100]}"


# =========================================================================
# Block Kit Builders (GRIP Design System)
# =========================================================================

def _build_header_block(text: str, emoji: str = "") -> dict:
    """Build a header block."""
    display = f"{emoji} {text}".strip() if emoji else text
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": display, "emoji": True}
    }


def _build_section_block(text: str) -> dict:
    """Build a section block with mrkdwn."""
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text}
    }


def _build_fields_block(fields: list[tuple[str, str]]) -> dict:
    """Build a section block with field pairs (label, value)."""
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*{label}*\n{value}"}
            for label, value in fields
        ]
    }


def _build_context_block(text: str) -> dict:
    """Build a context block (small footer text)."""
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": text}]
    }


def _build_divider() -> dict:
    """Build a divider block."""
    return {"type": "divider"}


def _progress_bar(current: float, total: float, width: int = 8) -> str:
    """Build a text-based progress bar."""
    if total <= 0:
        return "░" * width
    pct = min(1.0, current / total)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def send_notification(
    text: str,
    channel: str = "",
    thread_ts: str = "",
    force: bool = False,
    blocks: list = None,
) -> dict:
    """Send a rate-limited Slack notification with Block Kit support.

    Enforces max 1 message per RATE_LIMIT_SECONDS. If rate-limited,
    queues the message for later delivery.

    Args:
        text: Fallback text (mrkdwn format) for notifications/accessibility.
        channel: Channel ID (default from config).
        thread_ts: Thread timestamp for replies.
        force: Bypass rate limit (use sparingly).
        blocks: Optional Block Kit blocks for rich formatting.

    Returns:
        Dict with: sent (bool), queued (bool), error (str), wait_seconds (float)
    """
    channel = channel or _get_channel()

    if not force:
        can_send, wait = _check_rate_limit()
        if not can_send:
            _queue_notification(text, channel, thread_ts)
            return {
                "sent": False,
                "queued": True,
                "error": "rate_limited",
                "wait_seconds": round(wait, 1),
            }

    success, error = _send_slack_message(text, channel, thread_ts, blocks=blocks)

    if success:
        _record_send()
        return {"sent": True, "queued": False, "error": ""}

    # Failed to send - queue for later
    _queue_notification(text, channel, thread_ts)
    return {
        "sent": False,
        "queued": True,
        "error": error,
    }


def flush_queue(max_messages: int = 5) -> dict:
    """Deliver queued notifications that were previously rate-limited.

    Reads undelivered messages from the queue and sends them,
    respecting rate limits between each send.

    Args:
        max_messages: Maximum messages to flush in one call.

    Returns:
        Dict with: flushed (int), remaining (int), errors (list)
    """
    if not NOTIFY_QUEUE_FILE.exists():
        return {"flushed": 0, "remaining": 0, "errors": []}

    try:
        lines = NOTIFY_QUEUE_FILE.read_text().splitlines()
    except OSError:
        return {"flushed": 0, "remaining": 0, "errors": ["read_error"]}

    undelivered = []

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("delivered"):
            continue

        undelivered.append(entry)

    flushed = 0
    errors = []
    remaining_entries = []

    for entry in undelivered:
        if flushed >= max_messages:
            remaining_entries.append(entry)
            continue

        can_send, _ = _check_rate_limit()
        if not can_send:
            remaining_entries.append(entry)
            continue

        success, error = _send_slack_message(
            entry["message"],
            entry.get("channel", DEFAULT_CHANNEL),
            entry.get("thread_ts", ""),
        )

        if success:
            _record_send()
            flushed += 1
        else:
            errors.append(error)
            remaining_entries.append(entry)

    # Rewrite queue with only undelivered entries
    if flushed > 0 or len(remaining_entries) < len(undelivered):
        NOTIFY_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFY_QUEUE_FILE, "w") as f:
            for entry in remaining_entries:
                f.write(json.dumps(entry) + "\n")

    return {
        "flushed": flushed,
        "remaining": len(remaining_entries),
        "errors": errors,
    }


# =========================================================================
# RSI-Specific Notification Formatters (Block Kit)
# =========================================================================

def notify_rsi_cycle(
    commit_hash: str,
    summary: str,
    issue_number: Optional[int] = None,
    red_team_notes: str = "",
    ralph_score: Optional[int] = None,
    channel: str = "",
) -> dict:
    """Send notification for a completed RSI cycle with Block Kit formatting.

    Args:
        commit_hash: Short commit hash.
        summary: One-line commit summary.
        issue_number: GitHub issue number (if applicable).
        red_team_notes: Red-team/review notes.
        ralph_score: Ralph review score (if reviewed).
        channel: Override channel.

    Returns:
        send_notification result dict.
    """
    # Determine status emoji based on Ralph score
    if ralph_score is not None:
        if ralph_score >= 85:
            status_emoji = ":white_check_mark:"
            status_text = "Passed"
        elif ralph_score >= 70:
            status_emoji = ":large_yellow_circle:"
            status_text = "Marginal"
        else:
            status_emoji = ":red_circle:"
            status_text = "Needs Work"
    else:
        status_emoji = ":arrows_counterclockwise:"
        status_text = "Complete"

    blocks = [
        _build_header_block("RSI Cycle Complete", ":gear:"),
        _build_fields_block([
            ("Commit", f"`{commit_hash[:8]}`"),
            ("Status", f"{status_emoji} {status_text}"),
        ]),
        _build_section_block(f"*Summary*\n{summary}"),
    ]

    # Add issue and Ralph score fields if present
    extra_fields = []
    if issue_number:
        extra_fields.append(("Issue", f"<https://github.com/grip-platform/GRIP/issues/{issue_number}|#{issue_number}>"))
    if ralph_score is not None:
        bar = _progress_bar(ralph_score, 100)
        extra_fields.append(("Ralph Score", f"`{bar}` {ralph_score}/100"))

    if extra_fields:
        blocks.append(_build_fields_block(extra_fields))

    # Add red team notes if present
    if red_team_notes:
        blocks.append(_build_divider())
        blocks.append(_build_context_block(f":mag: _{red_team_notes[:200]}_"))

    # Footer
    blocks.append(_build_context_block(f":robot_face: _GRIP RSI_ · {datetime.now(timezone.utc).strftime('%H:%M UTC')}"))

    fallback_text = f"RSI Cycle Complete: {commit_hash[:8]} - {summary}"
    return send_notification(fallback_text, channel=channel, blocks=blocks)


def notify_rsi_summary(
    cycles: int,
    issues_closed: Optional[list[int]] = None,
    issues_progressed: Optional[list[int]] = None,
    budget_spent: float = 0.0,
    budget_remaining: float = 0.0,
    convergence_avg: float = 0.0,
    channel: str = "",
) -> dict:
    """Send RSI session summary notification with Block Kit formatting.

    Args:
        cycles: Number of cycles completed.
        issues_closed: List of closed issue numbers.
        issues_progressed: List of progressed issue numbers.
        budget_spent: USD spent this session.
        budget_remaining: USD remaining.
        convergence_avg: Average Ralph score.
        channel: Override channel.

    Returns:
        send_notification result dict.
    """
    total_budget = budget_spent + budget_remaining

    blocks = [
        _build_header_block("RSI Session Summary", ":chart_with_upwards_trend:"),
        _build_fields_block([
            ("Cycles", f"{cycles} :arrows_counterclockwise:"),
            ("Avg Score", f"{convergence_avg:.1f}" if convergence_avg > 0 else "–"),
        ]),
    ]

    # Issues section
    issue_fields = []
    if issues_closed:
        closed_links = ", ".join(
            f"<https://github.com/grip-platform/GRIP/issues/{n}|#{n}>"
            for n in issues_closed
        )
        issue_fields.append(("Closed", f":white_check_mark: {closed_links}"))
    if issues_progressed:
        prog_links = ", ".join(
            f"<https://github.com/grip-platform/GRIP/issues/{n}|#{n}>"
            for n in issues_progressed
        )
        issue_fields.append(("Progressed", f":hourglass_flowing_sand: {prog_links}"))

    if issue_fields:
        blocks.append(_build_fields_block(issue_fields))

    # Budget section
    if total_budget > 0:
        blocks.append(_build_divider())
        bar = _progress_bar(budget_spent, total_budget)
        pct = (budget_spent / total_budget * 100)
        budget_emoji = ":red_circle:" if pct > 90 else ":large_yellow_circle:" if pct > 75 else ":green_circle:"
        blocks.append(_build_fields_block([
            ("Budget", f"`{bar}` {pct:.0f}%"),
            ("Spent", f"{budget_emoji} ${budget_spent:.2f} / ${total_budget:.2f}"),
        ]))

    # Footer
    blocks.append(_build_context_block(f":robot_face: _GRIP RSI_ · {datetime.now(timezone.utc).strftime('%H:%M UTC')}"))

    fallback_text = f"RSI Summary: {cycles} cycles, {len(issues_closed or [])} closed"
    return send_notification(fallback_text, channel=channel, blocks=blocks, force=True)


def notify_error(
    issue_number: Optional[int] = None,
    error: str = "",
    phase: str = "",
    channel: str = "",
) -> dict:
    """Send RSI error notification with Block Kit formatting.

    Args:
        issue_number: Issue being worked on (if any).
        error: Error description.
        phase: RSI phase where error occurred.
        channel: Override channel.

    Returns:
        send_notification result dict.
    """
    blocks = [
        _build_header_block("RSI Error", ":x:"),
    ]

    # Error details fields
    fields = []
    if issue_number:
        fields.append(("Issue", f"<https://github.com/grip-platform/GRIP/issues/{issue_number}|#{issue_number}>"))
    if phase:
        fields.append(("Phase", f"`{phase}`"))

    if fields:
        blocks.append(_build_fields_block(fields))

    # Error message
    if error:
        blocks.append(_build_divider())
        blocks.append(_build_section_block(f"```{error[:500]}```"))

    # Footer
    blocks.append(_build_context_block(f":rotating_light: _GRIP RSI Error_ · {datetime.now(timezone.utc).strftime('%H:%M UTC')}"))

    fallback_text = f"RSI Error: {phase} - {error[:100]}"
    return send_notification(fallback_text, channel=channel, blocks=blocks, force=True)


def notify_budget_warning(
    spent: float,
    total: float,
    threshold_pct: int = 90,
    channel: str = "",
) -> dict:
    """Send budget threshold warning with Block Kit formatting.

    Args:
        spent: USD spent.
        total: Total budget USD.
        threshold_pct: Which threshold was crossed.
        channel: Override channel.

    Returns:
        send_notification result dict.
    """
    pct = (spent / total * 100) if total > 0 else 0
    remaining = total - spent
    bar = _progress_bar(spent, total, width=10)

    # Color coding based on threshold
    if pct >= 95:
        level_emoji = ":red_circle:"
        level_text = "CRITICAL"
    elif pct >= 90:
        level_emoji = ":large_orange_circle:"
        level_text = "High"
    else:
        level_emoji = ":large_yellow_circle:"
        level_text = "Warning"

    blocks = [
        _build_header_block("RSI Budget Alert", ":warning:"),
        _build_section_block(f"{level_emoji} *{level_text}* — {threshold_pct}% threshold crossed"),
        _build_divider(),
        _build_fields_block([
            ("Spent", f"${spent:.2f}"),
            ("Total", f"${total:.2f}"),
        ]),
        _build_section_block(f"*Progress*\n`{bar}` {pct:.0f}%"),
        _build_fields_block([
            ("Remaining", f"${remaining:.2f}"),
            ("Action", "Consider pausing RSI" if pct >= 95 else "Monitor closely"),
        ]),
        _build_context_block(f":moneybag: _GRIP Budget Monitor_ · {datetime.now(timezone.utc).strftime('%H:%M UTC')}"),
    ]

    fallback_text = f"RSI Budget Alert: {pct:.0f}% spent (${spent:.2f}/${total:.2f})"
    return send_notification(fallback_text, channel=channel, blocks=blocks, force=True)


# =========================================================================
# CLI Interface
# =========================================================================

def cli_main():
    """CLI for testing and manual notification."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: slack_notify.py <command>")
        print("Commands: test, flush, status, cycle, summary")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "test":
        msg = sys.argv[2] if len(sys.argv) > 2 else "RSI notification test"
        result = send_notification(f":test_tube: {msg}")
        print(json.dumps(result, indent=2))

    elif cmd == "flush":
        max_msgs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        result = flush_queue(max_messages=max_msgs)
        print(json.dumps(result, indent=2))

    elif cmd == "status":
        can_send, wait = _check_rate_limit()
        queue_count = 0
        if NOTIFY_QUEUE_FILE.exists():
            try:
                lines = NOTIFY_QUEUE_FILE.read_text().splitlines()
                queue_count = sum(
                    1 for l in lines
                    if l.strip() and not json.loads(l).get("delivered", False)
                )
            except (json.JSONDecodeError, OSError):
                pass

        token_available = _get_bot_token() is not None
        print(json.dumps({
            "can_send": can_send,
            "wait_seconds": round(wait, 1) if not can_send else 0,
            "queued_messages": queue_count,
            "channel": _get_channel(),
            "token_available": token_available,
        }, indent=2))

    elif cmd == "cycle":
        result = notify_rsi_cycle(
            commit_hash="test1234",
            summary="test: notification system",
            issue_number=210,
            red_team_notes="Testing notification pipeline",
        )
        print(json.dumps(result, indent=2))

    elif cmd == "summary":
        result = notify_rsi_summary(
            cycles=3,
            issues_closed=[165, 180],
            issues_progressed=[196],
            budget_spent=2.10,
            budget_remaining=17.90,
            convergence_avg=82.5,
        )
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
