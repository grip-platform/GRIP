"""grip_tick_plain -- the always-on tick's WhatsApp messages, in plain words.

``lib/grip_tick.py`` decides what is true, and this module only says it. Three messages leave the
tick: the report of new findings, the reminder for a question or an idea list that has waited for
more than a day, and the daily check-up. Each message starts with a bold title and one plain line.
Then comes one block for each kind of finding, the most urgent kind first: a bold heading with the
count, one line for each item, and one line that says what to do.

Nothing is dropped. Every id, number and file name of a finding stays in the message, said in plain
words, and a finding this module cannot read is shown with its own text. The findings, their keys
and the tick's log do not change: only the words the operator reads do. V>> asked for this on
2026-11-24, of the old digest: "This means nothing to me on WhatsApp".

Pure: no I/O, no clock and no model.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple


class FindingLike(Protocol):
    """What the renderer reads of a ``grip_tick.Finding``."""

    key: str
    check: str
    text: str
    age_s: Optional[float]


# The most urgent kind first; a kind this list does not name comes last, in the order it was found.
ORDER = ("error", "hitl", "fleet", "launchd", "spend", "cert", "grant", "hypotheses", "act", "dogfood", "ideation")

# The jobs a signed permission lets the tick do by itself (lib/tick_grant.py KNOWN_ACTS), in plain words.
ACTS = {
    "checkpoint": "keep a copy of a session that runs away",
    "throttle": "slow Claude down near the weekly limit",
    "experiment": "run a test you approved for an overdue prediction",
}

# Each reason lib/tick_grant.py gives for a permission that does not verify, in plain words.
REASONS = {
    "expired": "expired",
    "not yet valid": "not valid yet",
    "bad signature": "not signed correctly",
    "key mismatch": "signed with a different key",
    "malformed": "damaged",
    "unreadable": "cannot be read",
    "unknown act": "names a job GRIP does not know",
    "no verifying key pinned": "no public key to check it",
    "cryptography missing": "cannot be checked here (cryptography missing)",
    "verifying key unreadable": "cannot be checked (the public key cannot be read)",
}

# Idea-list ids on one line of the ideation block: five ids with their hours stay under the 25 words of a sentence.
IDS_PER_LINE = 5

# A stack-dogfood place whose name says nothing to a reader, with what it means.
PLACES = {"tree": "tree (the shared GRIP folder is not on main and up to date)"}

_HOT = re.compile(r"^session (\S+) wrote ([\d.]+) MB in one tick \(hot\)$")
_STUCK = re.compile(r"^session (\S+) repeats one tool error \(digest (\S+)\)$")
_ASK = re.compile(r"^session (\S+) has a question waiting ([\d.]+) h(?: \((.*)\))?$")
_HYP = re.compile(r"^(\S+) is (\d+) days past its deadline with no verdict$")
_SPEND = re.compile(r"^weekly spend \((.*)\) crossed (\d+%) \(now (\d+%)\)$")
_GRANT = re.compile(r"^grant file (\S+) failed verification \((.*)\)$")
_ERROR = re.compile(r"^check (\S+) failed \((\S+)\)$")
_IDEA = re.compile(r"^ideation shortlist (\S+) has waited (\d+) h for a pick$")
_MISSING = re.compile(r"^(\S+): (\d+) cell\(s\) not installed \((.*)\)$")
_AGENT_PREFIX = re.compile(r"^com\.[^.]+\.")


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _listed(heading: str, items: Iterable[str], action: str) -> List[str]:
    """A bold heading, one ``- `` line for each item, then the action line after a blank line.

    ``- `` makes a list on WhatsApp and for lib/ste_lint.py. The blank line ends the list, so the
    action is its own sentence and never runs on from the last item.
    """
    return [f"*{heading}*"] + [f"- {item}" for item in items] + ["", action]


# ----------------------------------------------------------------------------- one item, in plain words

def _error_item(f: FindingLike) -> str:
    m = _ERROR.match(f.text)
    return f"{m[1]} ({m[2]})" if m else f.text


def _hitl_item(f: FindingLike) -> str:
    m = _ASK.match(f.text)
    if not m:
        return f.text
    question = f'"{m[3]}"' if m[3] else "A question"
    return f"{question} (session {m[1]}, waiting {m[2]} h)"


def _fleet_item(f: FindingLike) -> str:
    hot, stuck = _HOT.match(f.text), _STUCK.match(f.text)
    if hot:
        return f"Session {hot[1]} wrote {hot[2]} MB in about five minutes. It may be stuck in a loop."
    if stuck:
        return f"Session {stuck[1]} keeps hitting the same error (error digest {stuck[2]})."
    return f.text


def _job(f: FindingLike) -> Tuple[str, str]:
    """(the job's name in plain words, its exit code) from a ``launchd:<label>:<status>`` key."""
    label, _, status = f.key[len("launchd:"):].rpartition(":")
    if not label or not status:
        return f.text, ""
    return _AGENT_PREFIX.sub("", label).replace("-", " ").replace(".", " "), status


def _launchd_item(job: Tuple[str, str]) -> str:
    name, status = job
    return f"{name} (exit code {status})" if status else name


def _spend_item(f: FindingLike) -> str:
    m = _SPEND.match(f.text)
    if not m:
        return f.text
    return f"Account {m[1]}: {m[3]} of this week's Claude limit is used. It passed {m[2]}."


def _cert_item(f: FindingLike) -> str:
    return f.text[:1].upper() + f.text[1:]


def _reason(reason: str) -> str:
    if reason.startswith("conflicting grants for the same act"):
        return (reason.replace("conflicting grants for the same act", "clashes with another permission for the same job", 1)
                .replace("none is used until one is revoked", "GRIP uses none of them until you revoke one"))
    return REASONS.get(reason, reason)


def _grant_parts(f: FindingLike) -> Optional[Tuple[str, str]]:
    """(file name, reason) of a permission file that did not verify, or None for any other grant finding."""
    m = _GRANT.match(f.text)
    return (m[1], m[2]) if m else None


def _grant_item(f: FindingLike) -> str:
    if f.key == "grant:pin-drift":
        return ("The file grant-verifying.pub in this copy of GRIP is not the one on main. "
                "GRIP checks your permissions against the one on main.")
    parts = _grant_parts(f)
    if parts is None:
        return f.text
    name, reason = parts
    return f"{ACTS.get(name.rsplit('.', 1)[0], 'a permission')} ({name}, {_reason(reason)})"


def _hypothesis_item(f: FindingLike) -> str:
    m = _HYP.match(f.text)
    if not m:
        return f.text
    return f"Prediction {m[1]} is {_count(int(m[2]), 'day', 'days')} past its check date with no result."


def _dogfood_item(f: FindingLike) -> str:
    m = _MISSING.match(f.text)
    if not m:
        return f.text
    places = ", ".join(PLACES.get(p.strip(), p.strip()) for p in m[3].split(","))
    return f"{m[1].upper()} is missing in {_count(int(m[2]), 'place', 'places')}: {places}"


def _hours(h: int) -> str:
    days = round(h / 24)
    return f"{h} h" if days < 1 else f"{h} h (about {_count(days, 'day', 'days')})"


# ----------------------------------------------------------------------------- one block for each kind

def _error(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "GRIP check could not run", "GRIP checks could not run"),
                   [_error_item(f) for f in fs],
                   "Until a check runs again, GRIP cannot see what it watches. "
                   "A GRIP session can read the cause in logs/grip-tick.jsonl.")


def _hitl(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "question waits for your answer", "questions wait for your answer"),
                   [_hitl_item(f) for f in fs], "Open that session and answer it.")


def _fleet(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "session may be stuck", "sessions may be stuck"),
                   [_fleet_item(f) for f in fs],
                   "GRIP never stops a session. Look at it, or ask a GRIP session to check it.")


def _launchd(fs: List[FindingLike]) -> List[str]:
    jobs = [_job(f) for f in fs]
    return _listed(_count(len(fs), "background job failed the last time it ran",
                          "background jobs failed the last time they ran"),
                   [_launchd_item(j) for j in jobs],
                   f"To find out why, ask a GRIP session, for example: why did {jobs[0][0]} fail?")


def _spend(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "weekly Claude limit passed a new 5% mark",
                          "weekly Claude limits passed a new 5% mark"),
                   [_spend_item(f) for f in fs],
                   "GRIP tells you once for each 5% mark. The limit starts again each week.")


def _cert(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "certificate warning", "certificate warnings"), [_cert_item(f) for f in fs],
                   "Ask a GRIP session to renew the certificate before it runs out.")


def _grant(fs: List[FindingLike]) -> List[str]:
    parts = [_grant_parts(f) for f in fs]
    if all(p is not None and p[1] == "expired" for p in parts):
        return _listed(_count(len(fs), "permission you signed for GRIP has expired",
                              "permissions you signed for GRIP have expired"),
                       [_grant_item(f) for f in fs],
                       "GRIP does not do these jobs by itself until you renew them with grip-tick-grant.")
    return _listed(_count(len(fs), "problem with the permissions you signed for GRIP",
                          "problems with the permissions you signed for GRIP"),
                   [_grant_item(f) for f in fs],
                   "A GRIP session can explain each one with grip-tick-grant show.")


def _hypotheses(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "prediction is past its check date", "predictions are past their check date"),
                   [_hypothesis_item(f) for f in fs],
                   "Ask a GRIP session to check each one and record the result.")


def _act(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "note on the jobs you let GRIP do by itself",
                          "notes on the jobs you let GRIP do by itself"),
                   [f.text for f in fs], "Ask a GRIP session if one of these is not clear.")


def _dogfood(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "part of the GRIP stack is missing or does not work",
                          "parts of the GRIP stack are missing or do not work"),
                   [_dogfood_item(f) for f in fs],
                   "The nightly stack check looks again. A GRIP session can fix them with /stack-dogfood.")


def _ideation(fs: List[FindingLike]) -> List[str]:
    """One summary for every idea list: the oldest and newest wait, then each id with its hours, five to a line."""
    found = [m for m in (_IDEA.match(f.text) for f in fs) if m]
    lines = [f"*{_count(len(fs), 'idea list waits for your pick', 'idea lists wait for your pick')}*"]
    if found:
        hours = [int(m[2]) for m in found]
        lines.append(_ages(len(found), max(hours), min(hours)))
        lines += ["- " + ", ".join(f"{m[1]} ({m[2]} h)" for m in found[i:i + IDS_PER_LINE])
                  for i in range(0, len(found), IDS_PER_LINE)]
    lines += [f"- {f.text}" for f in fs if not _IDEA.match(f.text)]
    return lines + ["", "To clear one, record your pick in a session with /ultraideate picks."]


def _ages(n: int, oldest: int, newest: int) -> str:
    if n == 1:
        return f"It comes from /ultraideate and has waited {_hours(oldest)}."
    return f"They come from /ultraideate. The oldest has waited {_hours(oldest)}, the newest {_hours(newest)}."


def _other(fs: List[FindingLike]) -> List[str]:
    return _listed(_count(len(fs), "other thing", "other things"), [f.text for f in fs],
                   "Ask a GRIP session what these mean.")


GROUPS: Dict[str, Callable[[List[FindingLike]], List[str]]] = {
    "error": _error, "hitl": _hitl, "fleet": _fleet, "launchd": _launchd, "spend": _spend, "cert": _cert,
    "grant": _grant, "hypotheses": _hypotheses, "act": _act, "dogfood": _dogfood, "ideation": _ideation,
}


# ----------------------------------------------------------------------------- the three messages

def grouped(findings: Sequence[FindingLike]) -> List[Tuple[str, List[FindingLike]]]:
    """(check, its findings) in ORDER, a check ORDER does not name last; inside a check, the findings keep their order."""
    groups: Dict[str, List[FindingLike]] = {}
    for f in findings:
        groups.setdefault(f.check, []).append(f)
    rank = {check: i for i, check in enumerate(ORDER)}
    return sorted(groups.items(), key=lambda kv: rank.get(kv[0], len(ORDER)))


def _message(head: List[str], findings: Sequence[FindingLike], tail: Sequence[str] = ()) -> str:
    blocks = [head] + [GROUPS.get(check, _other)(items) for check, items in grouped(findings)]
    if tail:
        blocks.append(list(tail))
    return "\n\n".join("\n".join(block) for block in blocks)


def report(findings: Sequence[FindingLike]) -> str:
    """The message for the findings that are new on this tick."""
    head = ["*GRIP check-up*", f"GRIP found {_count(len(findings), 'new thing', 'new things')} to tell you."]
    return _message(head, findings)


def reminder_notice(n: int) -> str:
    """The Mac notification that goes with a reminder."""
    return f"{_count(n, 'thing has', 'things have')} waited for you for more than a day"


def reminder(findings: Sequence[FindingLike]) -> str:
    """The message for a question or an idea list that has waited for more than a day."""
    return _message(["*GRIP reminder*", reminder_notice(len(findings)) + "."], findings)


def _daily_line(open_n: int, grant_n: int) -> str:
    opened = f"{_count(open_n, 'thing is', 'things are')} still open." if open_n else "Nothing is open."
    if not grant_n:
        return opened + " GRIP only reports: no permission you signed lets it act by itself."
    return opened + f" {_count(grant_n, 'permission you signed lets', 'permissions you signed let')} GRIP act by itself."


def _grant_lines(grants: Sequence[Tuple[str, dict]]) -> List[str]:
    if not grants:
        return []
    return _listed(_count(len(grants), "permission is in force", "permissions are in force"),
                   [f"{ACTS.get(g.get('act', ''), 'a permission')} ({g.get('act')}, grant {h}, valid until "
                    f"{g.get('expires')}, first seen {g.get('first_seen')})" for h, g in grants],
                   "To stop one, revoke it with grip-tick-grant.")


def daily(findings: Sequence[FindingLike], grants: Sequence[Tuple[str, dict]]) -> str:
    """The daily check-up: every open finding, then every permission in force (hash, entry) from ``active_grants``."""
    head = ["*GRIP daily check-up*", _daily_line(len(findings), len(grants))]
    return _message(head, findings, _grant_lines(grants))
