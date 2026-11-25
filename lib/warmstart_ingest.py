"""warmstart_ingest — parse a pasted questionnaire reply, land it structured.

The Warm Start questionnaire goes out as plain text (WhatsApp or email). The
reply comes back as free text. This module parses that reply into per-question
answers and lands them through the ultralead funnel (POST /api/submit,
``firm_slug=warmstart``) so the completed questionnaire renders as structured
Q&A in the dedicated private Slack channel (#warmstart-leads). When the funnel
is unreachable, the direct-Slack fallback posts the same structured message via
``lib.slack_transport`` — a reply is never lost to an infra outage.

Pure parsing/composition is separated from transport; both transports are
injectable, so tests run with zero network.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

WARMSTART_SLUG = "warmstart"
WARMSTART_CHANNEL_ID = "C0EXAMPLE01"  # #warmstart-leads (private) — see rules/operational-context.md
DEFAULT_FUNNEL_BASE = "https://ultralead-codetonight.vercel.app"
# ``MAX_FREETEXT`` mirrors the funnel's SERVER-side clamp (ultralead
# api/submit MAX_FREETEXT): the client clamps to the same bound so it never
# sends preamble the server would silently drop. The direct-Slack fallback is
# NOT bound by the funnel, so it preserves more (``MAX_SLACK_FREETEXT``) —
# silent preamble loss on the fallback path was the AGORA PR #4065 concern
# (task #519). parse_reply keeps up to the Slack bound; build_payload re-clamps
# to the funnel bound for the funnel payload.
MAX_FREETEXT = 280        # funnel server cap — honoured on the funnel payload
MAX_SLACK_FREETEXT = 500  # direct-Slack fallback bound (Slack, not the funnel)
MAX_ANSWER_CHARS = 1200  # mirror of ultralead lib/notify.js MAX_ANSWER_CHARS
QUESTION_COUNT = 10

# Display labels for the direct-Slack fallback. Source of truth is
# firms/warmstart/questions.json in the ultralead repo; these are the short
# forms reviewers need to recognise which question an answer belongs to.
QUESTION_LABELS = {
    "q1": "The three-year dream",
    "q2": "Who is it really for, and what does it save them?",
    "q3": "Where is it today — anything built we could look at?",
    "q4": "Who is in this with you?",
    "q5": "How does it make money?",
    "q6": "How is it funded — is raising part of the plan?",
    "q7": "What pace feels right?",
    "q8": "What would help most right now?",
    "q9": "How much does the covered/safe feeling matter?",
    "q10": "Onboarding into a culture that does not exist yet",
}

# Answer markers tolerated in a pasted reply: "1." / "1)" / "1 -" / "Q1:" /
# WhatsApp-bold variants like "*1.*". Number captured; the rest of the line
# starts the answer; following lines continue it until the next marker.
# The dot separator requires a NON-digit next (``\.(?!\d)``) so a decimal at
# line start ("1.5x growth", "1.0 rating") stays preamble instead of a
# mis-parsed q1 — while "1. 5 people" (space after dot) and "1)answer" (paren)
# still parse. AGORA PR #4065 parser-edge-case follow-up (task #519).
_MARKER_RE = re.compile(
    r"^\s*[*_]{0,2}\s*(?:[Qq]\s*)?(\d{1,2})\s*[*_]{0,2}\s*(?:\.(?!\d)|[):\-])\s*[*_]{0,2}\s*(.*)$"
)


def parse_reply(text: str) -> dict:
    """Parse a pasted reply into ``{"answers": {...}, "freetext": str}``.

    Lines before the first question marker become freetext preamble. Skipped
    questions are simply absent — the questionnaire explicitly allows skipping.
    Markers outside 1..QUESTION_COUNT are treated as ordinary text.
    """
    answers: dict[str, list[str]] = {}
    preamble: list[str] = []
    current: str | None = None
    for line in (text or "").splitlines():
        m = _MARKER_RE.match(line)
        if m and 1 <= int(m.group(1)) <= QUESTION_COUNT:
            current = f"q{int(m.group(1))}"
            answers.setdefault(current, [])
            first = m.group(2).strip()
            if first:
                answers[current].append(first)
        elif current is not None:
            answers[current].append(line.rstrip())
        elif line.strip():
            preamble.append(line.strip())
    clean = {
        qid: "\n".join(parts).strip()[:MAX_ANSWER_CHARS]
        for qid, parts in answers.items()
        if "\n".join(parts).strip()
    }
    # Preserve up to the Slack bound; build_payload re-clamps to the funnel cap
    # for the funnel payload, so the fallback path is not silently truncated.
    return {"answers": clean, "freetext": " ".join(preamble).strip()[:MAX_SLACK_FREETEXT]}


def build_payload(email: str, answers: dict, freetext: str = "", rating: int | None = None) -> dict:
    payload = {
        "firm_slug": WARMSTART_SLUG,
        "email": (email or "").strip().lower(),
        "answers": answers or {},
        "freetext": (freetext or "")[:MAX_FREETEXT],
        "chain": [],
    }
    if rating is not None:
        payload["rating"] = rating
    return payload


def post_to_funnel(payload: dict, base_url: str = DEFAULT_FUNNEL_BASE, timeout: int = 20) -> dict:
    """POST the payload to the funnel. Returns ``{ok, ...response|reason}``.

    A rejection (HTTP 4xx/5xx with a response) is labelled ``funnel_rejected``
    so telemetry never mislabels a validation reject as an outage; only a
    transport-level failure reads ``funnel_unreachable``. Known limit: when a
    response is LOST after the funnel accepted (ambiguous network failure),
    the fallback may duplicate the post — the funnel's server-side 5-minute
    email idempotency and the human channel readers absorb that.
    """
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/submit",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = json.loads(res.read().decode() or "{}")
            return {"ok": bool(body.get("ok")), **body}
    except urllib.error.HTTPError as err:
        detail = err.read()[:200].decode(errors="replace") if err.fp else ""
        return {"ok": False, "reason": f"funnel_rejected_http_{err.code}: {detail}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        return {"ok": False, "reason": f"funnel_unreachable: {err}"}


def escape_mrkdwn(s: str) -> str:
    """Slack's documented mrkdwn escaping (& < >) — submitted text can never
    inject mentions (<!channel>, <@U...>) or markup into the channel."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_blocks(email: str, answers: dict, freetext: str = "") -> list[dict]:
    """Mirror of ultralead lib/notify.js block shape for the fallback path."""
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "WARM START · questionnaire received", "emoji": False}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": "*Container*\nwarmstart (direct ingest)"},
            {"type": "mrkdwn", "text": f"*Email*\n{escape_mrkdwn(email)[:200] or '—'}"},
        ]},
    ]
    if freetext:
        quoted = escape_mrkdwn(freetext).replace("\n", "\n>")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Their words*\n>{quoted[:MAX_SLACK_FREETEXT]}"}})
    for idx in range(1, QUESTION_COUNT + 1):
        qid = f"q{idx}"
        answer = (answers or {}).get(qid, "").strip()
        if not answer:
            continue
        quoted = escape_mrkdwn(answer).replace("\n", "\n>")[:MAX_ANSWER_CHARS]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{QUESTION_LABELS[qid]}*\n>{quoted}"}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": "via warmstart-ingest fallback (funnel unreachable) · cc <@U0EXAMPLE01> <@U0EXAMPLE01>"},
    ]})
    return blocks


def post_direct_slack(email: str, answers: dict, freetext: str = "") -> dict:
    from lib.slack_transport import post  # runtime boundary — keychain token stays in the shim

    fallback = f"WARM START questionnaire (direct ingest): {email or 'no email'}"
    return post(
        workspace="codetonight",
        channel_id=WARMSTART_CHANNEL_ID,
        text=fallback,
        blocks=build_slack_blocks(email, answers, freetext),
    )


def spool_reply(payload: dict, spool_dir: Path | None = None) -> Path:
    """Last-resort local capture when BOTH transports fail — never lose a reply.

    Writes the parsed payload to a gitignored spool file so the operator can
    re-run the ingest once transports recover (finding from the sovereign
    council review seal 9547f9bc: without this, a double transport failure
    left the reply only in the operator's paste buffer).
    """
    base = spool_dir or (Path(__file__).resolve().parent.parent / "state" / "warmstart-leads")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"reply-{int(time.time())}-{abs(hash(payload.get('email', ''))) % 10000}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_ingest(
    text: str,
    email: str,
    rating: int | None = None,
    base_url: str = DEFAULT_FUNNEL_BASE,
    dry_run: bool = False,
    force_direct_slack: bool = False,
    funnel_poster: Callable[..., dict] = post_to_funnel,
    slack_poster: Callable[..., dict] = post_direct_slack,
    spool_dir: Path | None = None,
) -> dict:
    """Parse → funnel → direct-Slack fallback → local spool (last resort).

    Returns ``{path, parsed, payload, result}`` where ``path`` is one of
    ``dry_run`` / ``funnel`` / ``direct_slack`` / ``spooled``.
    """
    parsed = parse_reply(text)
    payload = build_payload(email, parsed["answers"], parsed["freetext"], rating)
    if dry_run:
        return {"path": "dry_run", "parsed": parsed, "payload": payload, "result": None}
    if not force_direct_slack:
        result = funnel_poster(payload, base_url=base_url)
        if result.get("ok"):
            return {"path": "funnel", "parsed": parsed, "payload": payload, "result": result}
        fallback_reason = result.get("reason", "funnel_rejected")
    else:
        fallback_reason = "forced"
    try:
        # Funnel payload is clamped to MAX_FREETEXT (server cap); the direct-Slack
        # fallback is not funnel-bound, so pass the richer parsed preamble (#519).
        slack_result = slack_poster(payload["email"], payload["answers"], parsed["freetext"])
    except Exception as err:  # transport shim may raise (e.g. keychain missing)
        slack_result = {"ok": False, "reason": f"slack_transport_error: {err}"}
    slack_result["fallback_reason"] = fallback_reason
    if slack_result.get("ok"):
        return {"path": "direct_slack", "parsed": parsed, "payload": payload, "result": slack_result}
    spooled = spool_reply(payload, spool_dir=spool_dir)
    return {
        "path": "spooled",
        "parsed": parsed,
        "payload": payload,
        "result": {**slack_result, "ok": False, "spooled": str(spooled)},
    }
