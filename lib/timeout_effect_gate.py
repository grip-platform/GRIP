"""What a timed-out continuation is allowed to CAUSE.

When a turn ends `RECOMMENDED NEXT STEP (yours)` and the operator does not
answer, the 20-minute ceiling lets the agent carry on. Measured over 82,446
transcripts and 95,842 reply pairs, that ceiling fires on **24.2%** of such
questions — nearly one in four — with a p90 reply latency of 157 minutes. No
tolerable ceiling catches that tail, so the question is not how long to wait. It
is what the continuation may DO, and that is the entire safety story.

WHY AN EFFECT ALLOWLIST AND NOT AGENT JUDGEMENT

The obvious design asks the agent whether the pending question was dangerous.
Three independent reviews rejected that, and an UltraJudge seat put it plainly
(RETHINK, 0.90): a parked question "is a soft, conversational control relying on
the agent's interpretation". The agent that mislabels a question is, by
construction, the agent confident it did not. GRIP already holds self-reports to
be the least reliable claims a model makes, under council seal `b32af619`.

So nothing here reads the question. It reads the EFFECT.

WHY IT IS AN ALLOWLIST

Denylists rot: the next transport anyone adds is permitted by default, silently.
`rules/sync-mirror-safety.md` settles this — allowlist, never denylist, so a new
dangerous capability is excluded by construction rather than by remembering.

WHY QUEUE WRITES ARE THEIR OWN CLASS — the lesson that cost a real brake

Take an agent's sending tool away and it does not stop; it QUEUES. Earlier the
same night this module was written, `grip-stop` was found to be honoured by nine
Python modules and zero TypeScript ones: a queued WhatsApp send sat in
`.wa-pending-sends.json` and flushed on reconnect, in another process, on a
timer, with no human in the loop. A capability lock scoped to tools would have
been defeated by one file write.

Therefore a write to any path an asynchronous executor drains is treated as the
effect that executor will eventually perform. The queue is the send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["EffectClass", "Verdict", "classify", "may_proceed_on_timeout"]


class EffectClass(Enum):
    """What an action causes, judged mechanically."""

    LOCAL_WORK = "local_work"       # reads, edits in-tree, tests — allowed
    OUTWARD = "outward"             # reaches a human who is not the operator
    PUBLISH = "publish"             # leaves the machine for a durable public place
    QUEUE_WRITE = "queue_write"     # an async executor will perform it later
    DESTRUCTIVE = "destructive"     # not recoverable from the working tree
    UNKNOWN = "unknown"             # not on the allowlist — therefore refused


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    effect: EffectClass
    reason: str


# Tools whose effects stay inside the working tree. Anything absent is UNKNOWN
# and therefore refused — that is the allowlist property, and it is why this set
# is deliberately small.
_LOCAL_TOOLS = frozenset({
    "read", "grep", "glob", "edit", "write", "notebookedit",
    "todowrite", "task", "agent",
})

# Shell verbs that stay local. Same rule: absence means refusal.
_LOCAL_SHELL = frozenset({
    "cat", "ls", "rg", "fd", "head", "tail", "sed", "awk", "wc", "diff",
    "pytest", "python", "python3", "node", "npm", "npx", "tsc", "jq", "sort",
    "uniq", "cut", "mkdir", "cp", "mv", "touch", "stat", "which", "echo",
})

# Git subcommands that do not leave the machine.
_LOCAL_GIT = frozenset({
    "status", "diff", "log", "show", "add", "commit", "branch", "checkout",
    "switch", "stash", "rev-parse", "ls-files", "blame", "fetch",
})

# Paths an asynchronous executor drains. A write here IS the effect it performs.
# Extend this whenever a new queue is added — and note that forgetting to is the
# failure this list exists to make loud rather than silent.
_QUEUE_PATHS = (
    ".wa-pending-sends.json",
    "state/relay/",
    "slack-notify-queue.jsonl",
    "outbox",
    "pending-sends",
)

_OUTWARD_TOOLS = re.compile(
    r"(channel_reply|channel_post|channel_send|send_message|send_voice|"
    r"whatsapp|slack|discord|mail|gmail|smtp|twilio|pushnotification)",
    re.IGNORECASE,
)

_PUBLISH_SHELL = re.compile(
    r"\b(push|publish|deploy|release)\b|gh\s+(pr|release|issue)\s+(create|merge|edit|comment)",
    re.IGNORECASE,
)

_DESTRUCTIVE_SHELL = re.compile(
    r"rm\s+-[rf]|--force|--hard|drop\s+table|truncate|shred|:\s*>\s*/", re.IGNORECASE
)


def _touches_queue(text: str) -> bool:
    return any(q in text for q in _QUEUE_PATHS)


def classify(tool: str, payload: str = "") -> EffectClass:
    """Classify an action by what it CAUSES. Never reads intent.

    `tool` is the tool name; `payload` is the command, path or argument blob.
    Ordering matters: the dangerous classes are tested BEFORE the local
    allowlist, so a local-looking tool writing into a send queue is caught.
    """
    t = (tool or "").strip().lower()
    blob = f"{t} {payload}"

    # A queue write is the effect the drainer will perform. Checked first,
    # because the tool doing it is usually an innocuous file write.
    if _touches_queue(blob):
        return EffectClass.QUEUE_WRITE
    if _OUTWARD_TOOLS.search(blob):
        return EffectClass.OUTWARD
    if _DESTRUCTIVE_SHELL.search(payload):
        return EffectClass.DESTRUCTIVE
    if _PUBLISH_SHELL.search(payload):
        return EffectClass.PUBLISH

    if t in _LOCAL_TOOLS:
        return EffectClass.LOCAL_WORK

    if t in ("bash", "shell"):
        head = payload.strip().split()
        if not head:
            return EffectClass.UNKNOWN
        verb = Path(head[0]).name
        if verb == "git":
            sub = head[1] if len(head) > 1 else ""
            return EffectClass.LOCAL_WORK if sub in _LOCAL_GIT else EffectClass.UNKNOWN
        return EffectClass.LOCAL_WORK if verb in _LOCAL_SHELL else EffectClass.UNKNOWN

    return EffectClass.UNKNOWN


def may_proceed_on_timeout(tool: str, payload: str = "") -> Verdict:
    """May a continuation past the 20-minute ceiling perform this?

    Only LOCAL_WORK passes. UNKNOWN is REFUSED rather than allowed, which is the
    whole point: an effect nobody has classified is not thereby safe, and the
    cost of wrongly refusing is a delayed task while the cost of wrongly allowing
    is an act taken in the operator's name that he never sanctioned.
    """
    effect = classify(tool, payload)
    if effect is EffectClass.LOCAL_WORK:
        return Verdict(True, effect, "stays inside the working tree")
    if effect is EffectClass.UNKNOWN:
        return Verdict(
            False, effect,
            "not on the allowlist — an unclassified effect is refused, never assumed safe",
        )
    return Verdict(
        False, effect,
        f"{effect.value}: the operator never answered, so this is not sanctioned",
    )
