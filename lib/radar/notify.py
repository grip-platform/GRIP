"""Alert lines (at most 160 characters) and the one WhatsApp send per tick.

Each change becomes one line, prefixed ``radar <source>:``. A tick's lines go
out as ONE WhatsApp message, one line each. The URL is the load-bearing part of
a GitHub line, so the title is cut to whatever room the URL leaves, and dropped
outright when fewer than twelve characters remain, rather than the line being
cut at the end.

The stop marker (``state/session/outbound-stop.json``, armed by ``grip-stop on``)
is checked here before any send, and ``lib/whatsapp_transport.post`` refuses on
the same marker: lines held back stay in ``pending_alerts`` for the next tick.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

MAX_LEN = 160
MIN_TITLE = 12
MIN_HEAD = 24


def clip(text: str, limit: int = MAX_LEN) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def _fit(prefix: str, title: str, suffix: str) -> str:
    """``prefix "title" suffix`` within MAX_LEN. The title gives way first: cut
    to the room left, or left out when that room is under MIN_TITLE."""
    room = MAX_LEN - len(prefix) - len(suffix) - 2
    if room < MIN_TITLE:  # the fixed fields give way next; the suffix (the URL) never does
        return clip(prefix.rstrip(), max(MAX_LEN - len(suffix), MIN_HEAD)) + suffix
    return f'{prefix}"{clip(title, room)}"{suffix}'


def _reply(c: dict) -> str:
    return _fit(f'radar mail: reply from {c["from"]} {c["when"]} on ', c["subject"],
                f' (thread {c["thread_id"]})')


def _bounce(c: dict) -> str:
    return _fit(f'radar mail: BOUNCE {c["when"]} {c["from"]} on ', c["subject"], ", check the recipient")


def _mail_failing(c: dict) -> str:
    return f'radar: mail check failing since {c["since"]} ({c["reason"]})'


def _issue(c: dict) -> str:
    what = "PR" if c["kind"] == "pr" else "issue"
    return _fit(f'radar github: new {what} {c["repo_short"]}#{c["number"]} by @{c["login"]} ',
                c["title"], f' {c["url"]}')


def _mention(c: dict) -> str:
    return _fit(f'radar github: mention in {c["repo"]} ', c["title"], f' {c["url"]}')


def _rollup(c: dict) -> str:
    return f'radar 08:00: {c["summary"]}'


def _rev(c: dict) -> str:
    by = f' by {clip(c["by"], 30)}' if c.get("by") else ""
    return (f'radar ietf: {c["draft"]}-{int(c["new_rev"]):02d} posted {c["date"]}{by} '
            f'(rev {int(c["old_rev"]):02d} to {int(c["new_rev"]):02d}) {c["url"]}')


def _rats(c: dict) -> str:
    return _fit(f'radar ietf: rats list mentions {c["term"]} {c["date"]} ', c["subject"], f' {c["url"]}')


def _web(c: dict) -> str:
    return _fit(f'radar web: {c["channel"]} {c["what"]} mentions ', c["term"], f' {c["url"]}')


FORMATTERS: dict[str, Callable[[dict], str]] = {
    "reply": _reply,
    "bounce": _bounce,
    "mail_failing": _mail_failing,
    "issue": _issue,
    "pr": _issue,
    "mention": _mention,
    "rollup": _rollup,
    "rev": _rev,
    "rats": _rats,
    "web": _web,
}


def format_line(change: dict) -> str:
    try:
        fmt = FORMATTERS[change["kind"]]
    except KeyError:
        raise ValueError(f"no alert line for change kind {change.get('kind')!r}") from None
    return clip(fmt(change))


def send_lines(lines: Sequence[str], send_fn: Callable[[str], dict], *, stop_marker: Path) -> dict:
    """Send all lines as one message. ``{"sent": [...], "held": [...], "reason"}``;
    held lines are the caller's to keep for the next tick."""
    lines = [ln for ln in lines if ln]
    if not lines:
        return {"sent": [], "held": [], "reason": None, "verified": None}
    if stop_marker.exists():
        return {"sent": [], "held": lines, "reason": "suppressed: outbound-stop marker present", "verified": None}
    try:
        res = send_fn("\n".join(lines))
    except Exception as exc:  # noqa: BLE001 -- a transport failure holds the lines, never crashes the tick
        return {"sent": [], "held": lines, "reason": f"send failed: {type(exc).__name__}: {str(exc)[:80]}",
                "verified": None}
    if isinstance(res, dict) and res.get("ok"):
        # ok means the transport accepted the message. ``verified`` (the delivery
        # check) is reported for the log and never used to hold the lines: on a host
        # with no delivery history it is always False, and holding would resend the
        # same line every tick (Codex on GRIP#8633, tested).
        return {"sent": lines, "held": [], "reason": None, "verified": res.get("verified")}
    err = res.get("error") if isinstance(res, dict) else repr(res)
    return {"sent": [], "held": lines, "reason": f"send failed: {str(err)[:80]}", "verified": None}


def default_send_fn() -> Callable[[str], dict]:
    """WhatsApp to the operator through the verified transport. Imports are lazy
    so a test never loads the transport's environment."""
    def _send(text: str) -> dict:
        from wa_identities import MISSING_HINT, operator_jid
        jid = operator_jid("GRIP_RADAR_RECIPIENT")
        if not jid:
            return {"ok": False, "error": MISSING_HINT}
        from wa_delivery_verify import post_verified
        return post_verified(jid, text)
    return _send
