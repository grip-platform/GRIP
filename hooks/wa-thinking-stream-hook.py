#!/usr/bin/env python3
"""WhatsApp thinking-stream hook — Stop event.

Streams ★ Insight blocks from each assistant turn to V>>'s WhatsApp via the
local grip-channel daemon. Opt-in via GRIP_WA_THINKING_STREAM=on env var.

Design
------
- Fires on Stop event after every assistant turn.
- Reads the most recent assistant message from the session transcript.
- Extracts ★ Insight blocks (the Explanatory output style's reasoning marker).
- Computes a content hash for each insight; checks dedup file before posting.
- Posts new insights via lib.wa_1to1.send() to V>>'s JID (retry + audit).
- Advisory: never blocks session close. Exit code 0.

Toggle
------
Default: OFF (no posts). Enable per-session:

    export GRIP_WA_THINKING_STREAM=on
    grip

Or globally via ~/.zshrc.

Recipient
---------
The operator's own WhatsApp ID, from the private file
config/wa-identities.local.json (lib/wa_identities.py), is the default. Override
via GRIP_WA_THINKING_STREAM_JID env var. With neither, nothing is sent.

Performance budget
------------------
< 1 second. Fast-paths when no ★ substring in transcript or env var off.

Origin: V>> directive 2026-05-04 — "we need a way for you to output
streamable WhatsApp messages showing your thinking block."
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def _operator_default() -> str | None:
    """The operator's ID from the private file, or None. A hook never breaks a session."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from wa_identities import operator_jid
        return operator_jid()
    except Exception:
        return None


CLAUDE_DIR = Path(os.environ.get("HOME", "/tmp")) / ".claude"
DEDUP_DIR = CLAUDE_DIR / "state" / "wa-thinking-stream"
DEDUP_FILE = DEDUP_DIR / "sent-hashes.txt"
DEFAULT_JID = _operator_default()
ENV_TOGGLE = "GRIP_WA_THINKING_STREAM"
ENV_JID = "GRIP_WA_THINKING_STREAM_JID"
MAX_MESSAGE_CHARS = 1500  # WhatsApp practical limit before truncation
MAX_INSIGHTS_PER_TURN = 3  # don't spam the channel

# Match ★ Insight blocks: opening "★ Insight" line + content + closing ─── line
_INSIGHT_RE = re.compile(
    r"★\s*Insight\s*[─\-]+\s*\n(.*?)\n\s*[─\-]{5,}",
    re.DOTALL,
)


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _is_enabled() -> bool:
    val = os.environ.get(ENV_TOGGLE, "").strip().lower()
    return val in ("on", "1", "true", "yes")


def _recipient_jid() -> str | None:
    return os.environ.get(ENV_JID, "").strip() or DEFAULT_JID


def _load_sent_hashes() -> set[str]:
    if not DEDUP_FILE.exists():
        return set()
    try:
        return {line.strip() for line in DEDUP_FILE.read_text().splitlines() if line.strip()}
    except OSError:
        return set()


def _record_sent(hashes: set[str]) -> None:
    if not hashes:
        return
    try:
        DEDUP_DIR.mkdir(parents=True, exist_ok=True)
        with DEDUP_FILE.open("a") as fh:
            for h in sorted(hashes):
                fh.write(h + "\n")
    except OSError:
        pass


def _hash_insight(text: str) -> str:
    """Stable content hash for dedup. First 16 chars of sha256 — collision-resistant enough."""
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def _extract_assistant_text(turn: dict) -> str:
    """Pull the human-readable text from an assistant turn dict."""
    msg = turn.get("message") or turn
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
        return "\n".join(out)
    return ""


def _latest_assistant_text(transcript_path: Path) -> str:
    """Read the transcript JSONL and return text of the LAST assistant turn."""
    if not transcript_path.exists():
        return ""
    last_text = ""
    try:
        for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = turn.get("role") or (turn.get("message") or {}).get("role")
            if role != "assistant":
                continue
            text = _extract_assistant_text(turn)
            if text:
                last_text = text  # keep overwriting; final survivor is the most recent
    except OSError:
        return ""
    return last_text


def _extract_insights(text: str) -> list[str]:
    """Return all ★ Insight blocks' inner content from text."""
    if "★" not in text:
        return []
    return [m.group(1).strip() for m in _INSIGHT_RE.finditer(text)]


def _format_for_wa(insight: str, idx: int, total: int) -> str:
    """Format an insight for WhatsApp: small header + body, truncated if huge."""
    body = insight if len(insight) <= MAX_MESSAGE_CHARS else (insight[: MAX_MESSAGE_CHARS - 50] + "\n\n…[truncated]")
    header = f"💭 *thinking* ({idx + 1}/{total})\n\n" if total > 1 else "💭 *thinking*\n\n"
    return header + body


def _redact(text: str) -> str | None:
    """Scrub an insight before it leaves this machine, or None if it cannot be.

    FAILS CLOSED, deliberately unlike lib/comms_update.py:50-53 and
    lib/aurora_report.py:68-71, which return un-redacted text when the shield is
    unavailable so a broken shield can never block a post or a render. The trade
    is different here. This is an opt-in convenience stream, so a dropped insight
    costs nothing, whereas an un-redacted one puts a client name or a credential
    onto Meta's infrastructure and cannot be recalled. `_send` already returns
    False silently on any failure and never blocks the Stop hook, so dropping the
    message is its existing contract rather than a new behaviour.

    PROFILE_LLM_EGRESS is the right profile: this text leaves the machine to a
    third party, which is the same threat model as sending it to a provider.
    """
    try:
        if str(CLAUDE_DIR) not in sys.path:
            sys.path.insert(0, str(CLAUDE_DIR))
        from lib.redaction_shield import (  # type: ignore
            PROFILE_LLM_EGRESS, Shield, load_denylist,
        )
        return Shield(load_denylist(), profile=PROFILE_LLM_EGRESS).redact(text).text
    except Exception as exc:  # noqa: BLE001 — an unusable shield must not leak, and must not raise
        # Say WHY on stderr (the hook log) before dropping. Silence here would be
        # indistinguishable from "no insights this turn", so an offline shield
        # could suppress the stream indefinitely with nobody able to tell.
        print(f"wa-thinking-stream: redaction shield unavailable, dropping "
              f"insight ({type(exc).__name__}: {exc})", file=sys.stderr)
        return None


def _send(insight: str, idx: int, total: int, jid: str) -> bool:
    """Post a single insight to WhatsApp via lib.wa_1to1 (retry + audit).

    Migrated from lib.whatsapp_transport.post to lib.wa_1to1.send — gains
    3-attempt exponential backoff (1s, 2s) and per-attempt audit logging
    to ``data/wa-audit.jsonl``. The Stop-hook <1s budget is preserved on the
    happy path (one attempt, zero backoff); a bridge-down event adds up to
    3s of backoff before returning False.

    Returns True on ok, False on failure (silent — never blocks Stop hook).
    """
    try:
        if str(CLAUDE_DIR) not in sys.path:
            sys.path.insert(0, str(CLAUDE_DIR))
        from lib.wa_1to1 import send  # type: ignore
    except ImportError:
        return False
    scrubbed = _redact(insight)
    if scrubbed is None:
        # Shield unavailable -> drop rather than send un-redacted. See _redact.
        return False
    try:
        result = send(recipient=jid, text=_format_for_wa(scrubbed, idx, total))
        return bool(result.get("ok"))
    except Exception:  # transport failures must not break Stop hook
        return False


def main() -> int:
    if not _is_enabled():
        print("{}")
        return 0

    hook_input = _parse_stdin()
    transcript_path_str = hook_input.get("transcript_path", "")
    if not transcript_path_str:
        print("{}")
        return 0

    text = _latest_assistant_text(Path(transcript_path_str))
    insights = _extract_insights(text)
    if not insights:
        print("{}")
        return 0

    sent = _load_sent_hashes()
    new_insights: list[tuple[str, str]] = []  # (hash, text)
    for insight in insights[:MAX_INSIGHTS_PER_TURN]:
        h = _hash_insight(insight)
        if h not in sent:
            new_insights.append((h, insight))

    if not new_insights:
        print("{}")
        return 0

    jid = _recipient_jid()
    if not jid:  # no operator configured on this machine: nothing to stream to
        print("{}")
        return 0
    posted_hashes: set[str] = set()
    total = len(new_insights)
    for idx, (h, insight) in enumerate(new_insights):
        if _send(insight, idx, total, jid):
            posted_hashes.add(h)

    _record_sent(posted_hashes)
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
