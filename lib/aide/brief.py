"""Copilot morning + afternoon brief generator.

Reads absorbed knowledge (state/copilot/knowledge.jsonl), recent git
activity, and any registered predictions, and produces a Slack-ready
markdown brief. Posts to V>>'s self-DM via lib.slack_transport.

Falsifier: if it fails to deliver the brief on three consecutive weekdays,
the cron loop or the Slack delivery path is broken — disable until fixed.

CLI:
    python3 -m lib.aide.brief morning [--dry-run] [--post]
    python3 -m lib.aide.brief afternoon [--dry-run] [--post]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from lib.aide import jsonl_io, paths
from lib.wa_identities import operator_jid

# V>>'s Slack workspace + self-DM target. user_id is durable; DM channel ID is not.
SLACK_WORKSPACE = "codetonight"
SLACK_USER_ID = "U0EXAMPLE01"

# V>>'s WhatsApp ID, from the private file (lib/wa_identities.py); None when unset.
WA_JID = operator_jid()

KNOWLEDGE_PATH = paths.KNOWLEDGE_LOG
PREDICTIONS_PATH = paths.PREDICTIONS


@dataclass
class BriefSection:
    title: str
    bullets: List[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.bullets:
            return ""
        body = "\n".join(f"  - {b}" for b in self.bullets)
        return f"*{self.title}*\n{body}"


@dataclass
class Brief:
    kind: str  # "morning" or "afternoon"
    when: datetime
    sections: List[BriefSection] = field(default_factory=list)

    def render(self) -> str:
        header = f":coffee: *Copilot {self.kind} brief* — {self.when.strftime('%a %d %b %H:%M %Z')}"
        body = "\n\n".join(s.render() for s in self.sections if s.bullets)
        if not body:
            return f"{header}\n\n_No brief content available — knowledge sources are empty or unreachable._"
        return f"{header}\n\n{body}"


def _git_yesterday_commits() -> List[str]:
    """Read yesterday's commits in ~/.claude. Plain text, no jargon."""
    try:
        out = subprocess.run(
            ["git", "log", "--since=yesterday.midnight", "--until=midnight",
             "--pretty=format:%s", "--no-merges"],
            cwd=str(Path.home() / ".claude"),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return []
        return [line for line in out.stdout.splitlines() if line.strip()][:5]
    except Exception:
        return []


def _read_recent_knowledge(limit: int = 5) -> List[str]:
    if not KNOWLEDGE_PATH.exists():
        return []
    try:
        records = jsonl_io.read(KNOWLEDGE_PATH, limit=limit, reverse=True)
    except Exception:
        return []
    return [
        f"{r.get('source', '?')}: {r.get('summary') or r.get('text', '')[:120]}"
        for r in records
    ]


def _read_predictions_due() -> List[str]:
    if not PREDICTIONS_PATH.exists():
        return []
    try:
        records = jsonl_io.read(PREDICTIONS_PATH, limit=20, reverse=True)
    except Exception:
        return []
    today = datetime.now(timezone.utc).date()
    due = []
    for r in records:
        check_at = r.get("check_at_utc", "")
        try:
            d = datetime.fromisoformat(check_at.split("T")[0]).date()
        except Exception:
            continue
        if d <= today and not r.get("outcome"):
            due.append(f"{r.get('claim', '?')[:120]} (due {check_at})")
    return due[:5]


def _meeting_decisions_pending(limit: int = 5) -> List[str]:
    """Meeting decisions awaiting V>>'s tap-to-ratify (meet → IDR bridge).

    Each card carries the decision, the why captured at the moment it was made,
    and the "reply: yes <id>" hint. Empty list on ANY failure — this section
    must never break the brief.
    """
    try:
        from lib.meet_idr_bridge import pending_ratifications
        cards = pending_ratifications()[:limit]
        return [
            f"{c['title']} — {c['why'] or 'no reason recorded'} ({c['ratify_hint']})"
            for c in cards
        ]
    except Exception as e:  # noqa: BLE001 — section is best-effort by contract
        print(f"meeting-decisions section unavailable: {e}", file=sys.stderr)
        return []


def build_brief(kind: str, now: Optional[datetime] = None) -> Brief:
    """Compose the brief from available sources. Empty sources gracefully omitted."""
    when = now or datetime.now().astimezone()
    brief = Brief(kind=kind, when=when)
    if kind == "morning":
        brief.sections.append(BriefSection("Yesterday's commits", _git_yesterday_commits()))
        brief.sections.append(BriefSection("Knowledge absorbed overnight", _read_recent_knowledge()))
        brief.sections.append(BriefSection("Predictions due today", _read_predictions_due()))
        brief.sections.append(BriefSection(
            "Meeting decisions awaiting ratification", _meeting_decisions_pending()))
    elif kind == "afternoon":
        brief.sections.append(BriefSection("Predictions still due today", _read_predictions_due()))
        brief.sections.append(BriefSection("New since morning", _read_recent_knowledge(limit=3)))
    else:
        raise ValueError(f"unknown brief kind: {kind!r} — use 'morning' or 'afternoon'")
    return brief


def post_to_slack(text: str) -> dict:
    """Post brief to V>>'s self-DM via lib.slack_transport. Returns response dict."""
    from lib.slack_transport import post  # imported lazily so dry-run doesn't load it
    return post(workspace=SLACK_WORKSPACE, channel_id=SLACK_USER_ID, text=text)


def _wa_format(text: str) -> str:
    """Transform Slack-rendered brief for WhatsApp display.

    Replaces Slack emoji shortcodes (e.g. ``:coffee:``) with their unicode
    equivalents. ``*bold*`` already works on both surfaces; triple backticks
    render fine; the only visible drift is shortcode literals.
    """
    return text.replace(":coffee:", "☕")


def post_to_whatsapp(text: str) -> dict:
    """Post brief to V>>'s WhatsApp via lib.wa_1to1 (retry + audit). Best-effort.

    Migrated from lib.whatsapp_transport.post to lib.wa_1to1.send on PR
    "enable wa_1to1" — gains 3-attempt exponential backoff (1s, 2s) and
    per-attempt audit logging to ``data/wa-audit.jsonl``. WhatsApp delivery
    stays best-effort — Slack remains the canonical surface; this function's
    return contract (dict with ``ok`` key) is preserved by ``wa_1to1.send``.

    Returns the wa_1to1.send response dict: ``{ok, message_id, error,
    recipient, attempts}``. The added ``attempts`` field surfaces the retry
    count to callers that want it; existing ``ok``-only callers are unaffected.
    """
    if not WA_JID:  # no operator configured on this machine
        from lib.wa_identities import MISSING_HINT
        return {"ok": False, "message_id": None, "error": MISSING_HINT, "recipient": None, "attempts": 0}
    from lib.wa_1to1 import send  # imported lazily so dry-run doesn't load it
    return send(recipient=WA_JID, text=_wa_format(text))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Copilot brief generator")
    parser.add_argument("kind", choices=["morning", "afternoon"])
    parser.add_argument("--post", action="store_true",
                        help="post the brief to V>> Slack DM and WhatsApp")
    parser.add_argument("--dry-run", action="store_true",
                        help="render the brief to stdout, don't post")
    args = parser.parse_args(argv)

    brief = build_brief(args.kind)
    rendered = brief.render()

    if args.dry_run or not args.post:
        print(rendered)
        return 0

    # One render, two posts, isolated failure semantics.
    # Slack is the canonical surface — its failure is the exit-non-zero condition.
    # WhatsApp is best-effort: log outcome but never block on it.
    slack_resp = post_to_slack(rendered)
    if slack_resp.get("ok"):
        print(f"posted to Slack DM (ts={slack_resp.get('ts')})")
    else:
        print(f"Slack post failed: {slack_resp}", file=sys.stderr)

    try:
        wa_resp = post_to_whatsapp(rendered)
    except Exception as e:  # noqa: BLE001  — best-effort: never let WA explode the run
        wa_resp = {"ok": False, "error": f"unexpected: {str(e)[:120]}"}
    if wa_resp.get("ok"):
        print(f"posted to WhatsApp (message_id={wa_resp.get('message_id')})")
    else:
        print(f"WhatsApp post failed (best-effort, not fatal): {wa_resp}", file=sys.stderr)

    return 0 if slack_resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
