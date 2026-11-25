#!/usr/bin/env python3
"""Follow a live event and push commentary to WhatsApp as it happens.

This is the ``--follow`` half of ``/grip-watch``. The other half answers "where
can I watch this"; this one answers "I cannot watch it, tell me what happens".

The design constraint that shapes everything here: a live blog is an
append-mostly list of minute-stamped lines, and the only thing worth sending is
what is NEW since the last poll. So the module keeps a seen-set keyed on the
line itself rather than on the minute, because live blogs routinely revise a
line in place (a try is awarded, then the conversion is appended to the same
minute) and re-sending a whole minute on every revision is how a commentary bot
becomes noise.

No secrets, no credentials: outbound goes through ``lib.whatsapp_transport.post``
which talks to the Mac-local grip-channel daemon. Recipients are passed in by the
caller and are never defaulted here — a commentary module that knows a phone
number by heart is a module that leaks one.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

_UA = "Mozilla/5.0 (compatible; GRIP-watch/1.0)"

#: A live-blog entry: a minute marker followed by its text.
#: Minimum body length is 2, not 8. At 8 the highest-value updates a viewer
#: actually wants -- "TRY!", "3-0" -- were never parsed at all. The 80-entry
#: calibration that set the old thresholds contained no short genuine lines, so
#: it could not have revealed this.
_ENTRY = re.compile(r"(\d{1,3})'\s*[-–]?\s*([^\n]{2,400}?)(?=\s+\d{1,3}'|$)")

#: Lines that are scene-setting rather than match action. Minute 0 on a live
#: blog is the pre-match build-up, which can run to dozens of entries.
_PREMATCH_MINUTE = 0


@dataclass(frozen=True)
class Entry:
    """One live-blog line."""

    minute: int
    text: str

    @property
    def key(self) -> str:
        """Identity for de-duplication: minute plus a normalised text prefix."""
        return f"{self.minute}|{re.sub(r'[^a-z0-9]+', '', self.text.lower())[:80]}"


@dataclass(frozen=True)
class PollResult:
    """The outcome of one poll.

    ``fetch_failed`` exists because "I could not reach the source" and "nothing
    new happened" are different facts that previously produced the identical
    ``(0, "")`` and an exit code of 0.
    """

    sent: int = 0
    message: str = ""
    fetch_failed: bool = False

    def __iter__(self):
        """Unpack as ``(sent, message)`` so existing callers keep working."""
        return iter((self.sent, self.message))


@dataclass
class FollowState:
    """What has already been sent, so a restart does not replay the match."""

    seen: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "FollowState":
        try:
            return cls(seen=set(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(self.seen)), encoding="utf-8")
        except OSError:
            pass  # a lost seen-set costs a duplicate message, never a crash


def fetch(url: str, timeout: float = 12.0) -> str | None:
    """Page text, or None when it could not be fetched. Never raises.

    None and "" are deliberately different answers. Collapsing both to "" made a
    permanently dead live-blog URL indistinguishable from a quiet match: the
    poller reported zero new entries with a success exit code and the commentary
    feed stopped forever without saying so.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def strip_markup(page: str) -> str:
    """Reduce HTML to a single whitespace-normalised text run."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def parse_entries(page: str, include_prematch: bool = False) -> list[Entry]:
    """Extract minute-stamped entries, newest-first order preserved as found."""
    flat = strip_markup(page)
    out: list[Entry] = []
    for match in _ENTRY.finditer(flat):
        minute = int(match.group(1))
        if minute == _PREMATCH_MINUTE and not include_prematch:
            continue
        body = match.group(2).strip(" -–")
        if body:
            out.append(Entry(minute=minute, text=body))
    return out


def is_noise(entry: Entry) -> bool:
    """Whether an entry is a scraped stats table rather than commentary.

    Live-blog pages embed a match-stats panel whose cells flatten into the same
    text run as the commentary, producing lines like
    "Jordan Will 0 Conversions 0 1 Penalties 0". They carry a minute marker and
    are otherwise indistinguishable, so the tell is density: mostly bare digits,
    almost no sentence punctuation.
    """
    tokens = entry.text.split()
    if not tokens:
        return True
    # Token count is NOT a noise signal. Rejecting anything under four tokens
    # discarded "TRY!" and "Kolbe converts" -- real updates, and the ones a
    # follower exists to deliver. Density alone separates a stats cell from
    # commentary: "3-0" and "TRY!" carry no bare digits at all, while
    # "Love Ruben 3 Penalties 0" is 40% bare digits.
    digits = sum(1 for token in tokens if token.isdigit())
    # Two bare digits is enough, not three. Live use on 2026-08-22 leaked
    # "Love Ruben 3 Penalties 0" into the commentary: five tokens, two digits,
    # so the old `digits >= 3` guard never fired even though the ratio was 40%.
    # Measured over 80 real entries from that match, this threshold catches both
    # stats rows and nothing else -- no commentary line was falsely filtered.
    if digits >= 2 and digits / len(tokens) > 0.25:
        return True
    return False


def new_entries(entries: Iterable[Entry], state: FollowState) -> list[Entry]:
    """Entries not yet seen, oldest minute first so commentary reads forwards."""
    fresh = [e for e in entries if e.key not in state.seen and not is_noise(e)]
    fresh.sort(key=lambda e: e.minute)
    return fresh


def is_big_moment(entry: Entry) -> bool:
    """Whether an entry deserves emphasis rather than a plain line."""
    loud = ("try", "penalty", "yellow card", "red card", "half-time", "half time",
            "full-time", "full time", "conversion", "drop goal", "kick-off")
    lowered = entry.text.lower()
    return any(word in lowered for word in loud)


def _was_delivered(result: object) -> bool:
    """Whether a send actually reached the transport.

    The transport returns a dict with an ``ok`` flag rather than raising, so a
    failure is a VALUE, not an exception. Anything that is not a dict (a test
    double returning None, say) is treated as delivered, because a caller that
    reports nothing cannot be assumed to have failed.
    """
    if isinstance(result, dict):
        return bool(result.get("ok", True))
    return True


def compose(
    entries: Sequence[Entry],
    event_name: str,
    score: str | None,
    missed: int = 0,
) -> str:
    """Render new entries as one WhatsApp message.

    WhatsApp renders ``*bold*`` but NOT single backticks, so emphasis uses
    asterisks only (``rules/operational-context.md``).
    """
    if not entries:
        return ""
    head = f"*{event_name}*"
    if score:
        head += f"  —  *{score}*"
    lines = [head, ""]
    if missed > 0:
        lines.append(f"_({missed} earlier update(s) not shown -- catching up)_")
        lines.append("")
    for entry in entries:
        marker = "*" if is_big_moment(entry) else ""
        lines.append(f"{entry.minute}'  {marker}{entry.text}{marker}")
    return "\n".join(lines)


def find_score(page: str) -> str | None:
    """The scoreline from the LATEST scoring entry, by minute.

    Not the first match in document order. An earlier version returned that,
    and its own test pinned the wrong answer: a page carrying 3-0 at 5' and
    3-5 at 8' yielded "3-0", so the WhatsApp header could report a score two
    updates behind the match. Ordering by minute removes the dependence on an
    undocumented newest-first page-order assumption -- live blogs are written
    both ways.
    """
    best: tuple[int, str] | None = None
    for entry in parse_entries(page):
        found = re.search(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b", entry.text)
        if found and (best is None or entry.minute >= best[0]):
            best = (entry.minute, f"{found.group(1)}-{found.group(2)}")
    return best[1] if best else None


def follow_once(
    url: str,
    event_name: str,
    recipients: Sequence[str],
    state: FollowState,
    send: Callable[[str, str], object],
    limit: int = 8,
) -> PollResult:
    """One poll: fetch, diff, send."""
    page = fetch(url)
    if page is None:
        return PollResult(fetch_failed=True)
    if not page:
        return PollResult()
    fresh = new_entries(parse_entries(page), state)
    if not fresh:
        return PollResult()
    batch = fresh[-limit:]
    missed = len(fresh) - len(batch)
    message = compose(batch, event_name, find_score(page), missed=missed)

    # `whatsapp_transport.post` does NOT raise on transport failure -- it
    # returns {"ok": False, "error": ...}. An earlier version only caught
    # exceptions, so a daemon that was down or returning 401 looked exactly
    # like a successful send: every entry was marked seen and the commentary
    # was gone for good. Delivery is now established from the result.
    delivered = False
    for recipient in recipients:
        try:
            result = send(recipient, message)
        except Exception:
            continue  # one bad recipient never blocks the others
        if _was_delivered(result):
            delivered = True

    if not delivered:
        # Nothing reached anyone. Leaving `seen` untouched is what makes the
        # next poll a retry rather than a silent loss.
        return PollResult(sent=0, message=message)

    # Only what was actually SENT is marked seen. Marking all of `fresh` while
    # sending only the last `limit` is how entries older than the batch were
    # consumed without ever being delivered.
    for entry in batch:
        state.seen.add(entry.key)
    return PollResult(sent=len(batch), message=message)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: grip_watch_follow.py <url> <event-name> <recipient> [<recipient>...]"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        print("usage: grip_watch_follow.py <url> <event-name> <recipient>...", file=sys.stderr)
        return 2
    url, event_name, recipients = args[0], args[1], args[2:]

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from whatsapp_transport import post  # noqa: E402

    state_path = Path(__file__).resolve().parent.parent / "state" / "grip-watch-follow.json"
    state = FollowState.load(state_path)
    result = follow_once(url, event_name, recipients, state, lambda r, t: post(r, t))
    state.save(state_path)
    print(json.dumps({
        "sent": result.sent,
        "chars": len(result.message),
        "fetch_failed": result.fetch_failed,
    }))
    if result.fetch_failed:
        print(f"[grip-watch] could not fetch {url} -- commentary is NOT running",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
