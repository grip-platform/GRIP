"""Route turn-end behaviour from the RECOMMENDED NEXT STEP owner token.

GRIP has emitted `RECOMMENDED NEXT STEP (mine|yours|<named party>)` at the end of every
substantive turn for months, and until now NOTHING READ IT. A declared output
with no actor at the other end is the mirror image of the defect
`rules/declared-input-must-reach-the-actor.md` exists to prevent: it looks wired
and is not. This module is the missing consumer.

THE RULE (V>> 2026-09-20, with his 20-minute correction):

    (mine)          -> continue working, immediately.
    (yours)         -> hold briefly. Silence for 5 minutes earns ONE alert.
                       At 20 minutes total, stop waiting and get on with it.
    (named party)   -> neither. The work is blocked on someone who is not in
                       this loop and no amount of waiting or working changes it.
    (a GRIP tool)   -> read as (mine). An executable in bin/, a com.grip.*
                       launchd job, the merge sweep or AGORA is something this
                       session can run or watch itself, so naming it names no
                       one outside the loop (GRIP#9073, V>> 2026-11-10).
    (no line)       -> stop normally. Absence is NOT permission.

WHAT THE TIMEOUT PATH MAY AND MAY NOT DO — the safety core.

V>>'s correction says "continue autonomously" after 20 minutes. Taken literally
that converts silence into consent on a timer, and GRIP would proceed to decide
a thing it had itself just declared was not its to decide. Writing `(yours)` IS
that declaration; twenty minutes of quiet does not transfer the authority.

So the timeout does not answer the question. It PARKS it:

  * the pending question stays pending, recorded, and is surfaced on return;
  * GRIP continues on work that does NOT depend on the answer;
  * if nothing independent remains, GRIP STOPS rather than inventing work or
    quietly doing the parked thing.

That preserves the whole of his intent — do not waste the session waiting for
me — while never once executing an outward act on his behalf because he
happened to be driving, asleep, or three timezones away.

No keyword classifier decides this. A heuristic that tries to sort "irreversible"
from "safe" fails open on the first phrasing it has not seen, and the cost of one
false negative here is a message sent to a child's teacher in a contested
family-law matter. Parking is correct-by-construction instead.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "Owner",
    "Disposition",
    "NextStep",
    "parse",
    "disposition",
    "grip_tool_named",
    "grip_tools",
    "SILENT_WAIT_SECONDS",
    "RELEASE_SECONDS",
]

# The two thresholds, both V>>'s numbers. Named, not scattered as literals.
SILENT_WAIT_SECONDS = 5 * 60
RELEASE_SECONDS = 20 * 60


class Owner(Enum):
    """Whose move the turn ended on."""

    MINE = "mine"
    YOURS = "yours"
    THIRD_PARTY = "third_party"
    ABSENT = "absent"


class Disposition(Enum):
    """What the turn-end machinery should therefore do."""

    CONTINUE = "continue"   # (mine) — work on, now
    HOLD = "hold"           # (yours) — the turn ends; the question waits, then parks
    BLOCKED = "blocked"     # named party — record it; neither wait nor invent
    STOP = "stop"           # no line — end the turn normally


# `RECOMMENDED NEXT STEP (owner)` optionally followed by an em-dash sentence.
# Case-insensitive because the canonical form is shouted but humans are not.
# The separator is an em dash, the operator's ` -- ` rendering of one
# (rules/em-dash-double-hyphen.md), a hyphen or a colon. `--` is matched whole,
# so the step text does not start with a stray "- " and both renderings give
# the same question_id.
_MARKER = re.compile(
    r"RECOMMENDED\s+NEXT\s+STEP\s*\(\s*(?P<owner>[^)]{1,60}?)\s*\)"
    r"(?:\s*(?:—|--?|:)\s*(?P<tail>.*))?$",
    re.IGNORECASE | re.MULTILINE,
)

_FORBIDDEN_NULL = ("none", "nothing further", "n/a", "nil", "nothing")


@dataclass(frozen=True)
class NextStep:
    """One parsed closer line.

    `party` is the owner as written whenever it was not `mine` or `yours`.
    `tool` is set only when that owner named one of GRIP's own tools, which is
    why such a step's owner is MINE (see "GRIP's own tools" below).
    """

    owner: Owner
    text: str
    raw: str
    party: str | None = None
    tool: str | None = None

    @property
    def question_id(self) -> str:
        """Stable identity for the ALERT LATCH.

        Keyed on the step text, never on a counter or a timestamp, so the same
        pending question cannot earn a second alert by being re-rendered, by the
        session restarting, or by a loop re-entering. This is the single
        property standing between this feature and the advisor that sent 1,120
        notifications: `alert_once` must be a latch, not a hope.
        """
        basis = f"{self.owner.value}:{' '.join(self.text.split()).lower()}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def is_null(self) -> bool:
        """True when the step says nothing — forbidden by the canonical style."""
        return self.text.strip().lower().rstrip(".") in _FORBIDDEN_NULL


# ------------------------------------------------------------ GRIP's own tools
#
# A GRIP tool is not a party outside this loop (GRIP#9073). On 2026-11-09 a
# session ended on `RECOMMENDED NEXT STEP (grip-tree-sync) -- the worktrees pick
# up b65980979 on their next sync`. The router read that as an outside party,
# so the turn ended, although the session could run the tool itself and the
# timer it named levels only the shared tree, never a worktree. The operator
# had to type "Continue". V>> chose the design on 2026-11-10 through /hitl:
#
#   * such an owner is read as (mine), so an armed machine carries on under
#     the brakes in lib/continuation_brake.py, and an unarmed one only logs it;
#   * GRIP's own tools are the executables in this checkout's bin/ (read by
#     every hook run, so a new tool counts from the next Stop), any com.grip.*
#     launchd label, the merge sweep and AGORA. rules/canonical-output-style.md
#     already said that naming the last two as the owner drops the convergence
#     duty.
#
# GitHub, Codex, people, vendors, background agents and fleet orchestrators
# never match: none of them is GRIP's own tool. An owner that names one of
# those roles beside a tool keeps the role ("the grip-fleet orchestrator" is
# the orchestrator, not bin/grip-fleet). AGORA is GRIP's own council by V>>'s
# choice, so its own council and seats do not count as outside roles: "the
# AGORA council" counts, "the AGORA background agent" does not. Nor does an
# owner that also names the operator ("you via grip-relay-bill"). Reading that
# as (mine) would carry on with a step the session meant for him, so it stays a
# named party, as before.
# An owner spread over two lines is malformed and keeps its old routing too,
# so no stray line can reach the continuation reason (Codex on GRIP#9116).
#
# A plain-word tool (grip, mesh, holistic, anamnesis) matches only as the whole
# owner, so "the GRIP repo's CI" stays an outside party. A hyphenated name such
# as grip-tree-sync is distinctive enough to match as one word of a longer
# owner, as in "the grip-tree-sync timer". An owner of several parts ("X and
# Y", "X, Y") counts only when every part names a GRIP tool; AGORA alone keeps
# the mixed form V>> chose, so "AGORA and Codex" counts.

_SPLIT_PARTIES = re.compile(r"\s*(?:,|;|&|\band\b|\bor\b)\s*")
# An owner that names AGORA is also split at "/", at "with" and at any run of
# "+", as in "AGORA/Codex", "AGORA with Codex" or "AGORA++GitHub". A tool whose
# own name holds "+", such as bin/gg++, is set aside first, so it stays whole
# (_agora_parts; GRIP#9117, GRIP#9130).
_SPLIT_AGORA_PARTIES = re.compile(r"\s*(?:,|;|&|/|\++|\band\b|\bor\b|\bwith\b)\s*")
# A launchd label of its own, never inside an address such as
# "support@com.grip.worker" or "https://com.grip.example/status".
_LAUNCHD_LABEL = re.compile(
    r"(?<![\w.@/\\:-])com\.grip\.[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?(?![\w@-])")
_MERGE_SWEEP = re.compile(r"(?:merge\s+)?sweep|.*\bmerge\s+sweep\b.*")
# AGORA as a word of its own, never inside an address: "agora.io",
# "support@agora.io" and "https://agora.example/status" are outside services.
_AGORA = re.compile(r"(?<![\w.@/\\:-])agora(?![\w@-]|\.\w)")
# What may stand beside AGORA: V>> chose "AGORA alone or with Codex or CI".
_AGORA_COMPANION = re.compile(r"codex(?:\s+review(?:er|s)?)?|ci(?:\s+checks?)?")
_NON_LETTERS = re.compile(r"[^a-z]+")
# Never a tool on Windows, where no file carries an executable bit.
_DATA_EXTENSIONS = frozenset({
    "plist", "json", "md", "txt", "yaml", "yml", "toml", "cfg", "ini", "csv",
    "log", "lock",
})
# Each word that names the operator counts only as a word, never as a segment
# of a path: the lookarounds below skip a word with a slash or a backslash on
# either side, as in a macOS, Linux or Windows home folder.
_NAMES_THE_OPERATOR = re.compile(
    r"(?<![\w/\\])(?:you(?:rs?|rself)?|operator|users?)\b(?![/\\])|[a-z]>>")
_WRAPPING = "`'\"*"
# Role nouns that keep an owner outside the loop even when it names a tool.
_OUTSIDE_ROLES = frozenset({
    "agent", "agents", "orchestrator", "orchestrators", "vendor", "vendors",
    "maintainer", "maintainers", "council", "councils", "seat", "seats",
    "session", "sessions", "team", "teams", "reviewer", "reviewers",
    "author", "authors", "person", "people",
})
# Beside AGORA these name its own council and seats, or Codex as its fellow
# reviewer, not an outsider. Every other part must still be Codex, CI or a tool.
_AGORA_PARTS = frozenset({"council", "councils", "seat", "seats", "reviewer", "reviewers"})
# The only words that may share one part with AGORA: its own nouns and "the".
# Any other word there, as in "AGORA with GitHub" or "AGORA++GitHub", makes the
# part someone else's (Codex on GRIP#9122).
_AGORA_WORDS = frozenset({
    "the", "agora", "council", "councils", "seat", "seats", "judge", "verdict",
    "sitting", "review", "reviewer", "reviewers", "run",
})


def _list_tools(bin_dir: Path, *, windows: bool | None = None) -> frozenset[str]:
    """The executables in `bin_dir`, lower-cased; `x.py` and `x.sh` also as `x`.

    A file that is not executable (a plist, a sourced helper) is not a tool.
    Windows has no executable bit, so there every file counts except a data
    file such as a .plist or a .json. An unreadable folder gives the empty set,
    so the check falls back to the launchd, sweep and AGORA rules instead of
    raising inside a Stop hook.
    """
    windows = os.name == "nt" if windows is None else windows
    names: set[str] = set()
    try:
        entries = list(bin_dir.iterdir())
    except OSError:
        return frozenset()
    for path in entries:
        if path.name.startswith(".") or not path.is_file() or not _runnable(path, windows):
            continue
        name = path.name.lower()
        names.add(name)
        stem, dot, ext = name.rpartition(".")
        if dot and stem and ext in ("py", "sh"):
            names.add(stem)
    return frozenset(names)


def _runnable(path: Path, windows: bool) -> bool:
    """True for a file this platform can run: the executable bit, or on
    Windows, where no file carries one, any file that is not a data file."""
    if windows:
        return path.suffix.lower().lstrip(".") not in _DATA_EXTENSIONS
    return os.access(path, os.X_OK)


@functools.lru_cache(maxsize=1)
def grip_tools() -> frozenset[str]:
    """The tools in this checkout's bin/, read once per process.

    The Stop hook runs as a new process at every Stop, so a tool added to bin/
    counts from the next Stop. A long-lived importer keeps its first reading.
    """
    return _list_tools(Path(__file__).resolve().parent.parent / "bin")


def _words(text: str) -> list[str]:
    """The whitespace-separated words of `text`, unwrapped, without a trailing 's."""
    return [w.strip(_WRAPPING + ".,:").removesuffix("'s") for w in text.split()]


def _names_an_outside_role(text: str, known: frozenset[str],
                           roles: frozenset[str] = _OUTSIDE_ROLES) -> bool:
    """True when a word of `text` is, or holds, an outside role, so
    "background-agent" and "agent!" count (Codex on GRIP#9116, fifth round).
    A GRIP tool's own name or label is skipped, whatever words it holds."""
    for word in _words(text):
        if word in known or _LAUNCHD_LABEL.fullmatch(word):
            continue
        if roles.intersection(_NON_LETTERS.split(word)):
            return True
    return False


def _bare(part: str) -> str:
    """One lower-cased owner part without wrapping, a leading "the" or a path
    through bin/, which may contain spaces ("~ Example/.grip/bin/")
    or use Windows' backslashes ("C:\\Users\\Jane\\.grip\\bin\\", GRIP#9117)."""
    text = part.strip().strip(_WRAPPING).strip()
    text = re.sub(r"^the\s+", "", text)
    text = re.sub(r"^.*?\bbin[/\\]", "", text)
    return text.strip(_WRAPPING).strip()


def _tool_in_part(part: str, known: frozenset[str]) -> str | None:
    """The GRIP tool one lower-cased owner part names, or None.

    An outside role named anywhere in the part keeps the owner outside, checked
    before any path is stripped: "the grip-fleet orchestrator" stays the
    orchestrator, and "the com.grip.acme vendor" stays the vendor. A part that
    is only a tool's name has no role word, because a name holds no space.
    """
    if _names_an_outside_role(part, known):
        return None
    bare = _bare(part)
    if bare in known:
        return bare
    label = _LAUNCHD_LABEL.search(bare)
    if label:
        return label.group(0)
    if _MERGE_SWEEP.fullmatch(bare):
        return "the merge sweep"
    return next((w for w in _words(bare) if w in known and ("-" in w or "_" in w)), None)


def _never_a_tool(low: str) -> bool:
    """True for an owner that cannot name a GRIP tool: an empty one, one spread
    over lines (malformed, so it keeps the routing it had before GRIP#9073), or
    one that names the operator."""
    return not low or "\n" in low or "\r" in low or bool(_NAMES_THE_OPERATOR.search(low))


def grip_tool_named(token: str, tools: frozenset[str] | None = None) -> str | None:
    """The GRIP tool an owner token names, or None for a party outside the loop.

    `tools` defaults to the live bin/ listing; the tests pass their own set.
    """
    low = (token or "").strip().lower()
    if _never_a_tool(low):
        return None
    known = grip_tools() if tools is None else tools
    if _AGORA.search(low):
        return _agora_owner(low, known)
    # Every part must name a GRIP tool, so "the vendor and grip-tree-sync" stays
    # with the vendor (Codex on GRIP#9116, fourth round). AGORA keeps the mixed
    # form V>> chose: "AGORA and Codex" counts (_agora_owner).
    found = [_tool_in_part(part, known) for part in _SPLIT_PARTIES.split(low) if part.strip()]
    return found[0] if found and all(found) else None


def _agora_owner(low: str, known: frozenset[str]) -> str | None:
    """AGORA, when every part of the owner is AGORA, Codex, CI or another GRIP
    tool, and no outside role is named.

    V>> chose AGORA alone or with Codex or CI, so "AGORA and Codex" counts while
    "AGORA and GitHub" stays with GitHub (Codex on GRIP#9116, fifth round).
    AGORA's own council and seats are GRIP's, so "the AGORA council" counts,
    while "the AGORA background agent" is that agent.
    """
    if _names_an_outside_role(low, known, _OUTSIDE_ROLES - _AGORA_PARTS):
        return None
    saw_agora = False
    for part in _agora_parts(low, known):
        if not part.strip() or _AGORA_COMPANION.fullmatch(_bare(part)):
            continue
        if _is_agora_part(part):
            saw_agora = True
        elif _tool_in_part(part, known) is None:
            return None
    return "AGORA" if saw_agora else None


def _agora_parts(low: str, known: frozenset[str]) -> list[str]:
    """The parts of an owner that names AGORA. A tool whose own name holds "+"
    is set aside before the split and put back after it, so "gg+++AGORA" gives
    gg++ and AGORA, not "gg" and an empty part (GRIP#9130)."""
    kept: dict[str, str] = {}
    plus_tools = sorted((t for t in known if "+" in t), key=len, reverse=True)
    for i, name in enumerate(plus_tools):
        if name in low:
            mark = f"\x00{i}\x00"
            low = low.replace(name, mark)
            kept[mark] = name
    parts = _SPLIT_AGORA_PARTIES.split(low)
    for mark, name in kept.items():
        parts = [p.replace(mark, name) for p in parts]
    return parts


def _is_agora_part(part: str) -> bool:
    """True for a part that is AGORA alone or with its own nouns, never a part
    that also holds another word, as in "AGORA with GitHub" (GRIP#9122)."""
    words = _words(part)
    return "agora" in words and set(words) <= _AGORA_WORDS


def _command_below(after: str) -> str:
    """The command on the line below the marker, with any lines a trailing
    backslash continues joined to it, so a long command arrives whole."""
    parts: list[str] = []
    for line in after.split("\n"):
        stripped = line.strip()
        parts.append(stripped.removesuffix("\\").strip())
        if not stripped.endswith("\\"):
            break
    return " ".join(part for part in parts if part)


def parse(message: str, *, tools: frozenset[str] | None = None) -> NextStep:
    """Read the closer out of one assistant message.

    Takes the LAST match, not the first. A turn that explains the format — this
    session has written several — contains the marker in prose well before its
    own real closer, and matching the first would route on documentation. The
    operative line is always the final one, because the canonical style makes it
    the last element on screen.

    An owner that names one of GRIP's own tools is read as MINE, with the tool
    on the step (GRIP#9073). `tools` replaces the live bin/ listing in tests.
    """
    if not message:
        return NextStep(owner=Owner.ABSENT, text="", raw="")

    matches = list(_MARKER.finditer(message))
    if not matches:
        return NextStep(owner=Owner.ABSENT, text="", raw="")

    m = matches[-1]
    token = (m.group("owner") or "").strip()
    tail = (m.group("tail") or "").strip()

    # The command form puts the command alone on the NEXT line so it pastes
    # cleanly, so an empty tail means "look below", never "no step".
    if not tail:
        tail = _command_below(message[m.end():].lstrip("\n"))

    low = token.lower()
    if low == "mine":
        return NextStep(owner=Owner.MINE, text=tail, raw=m.group(0))
    if low == "yours":
        return NextStep(owner=Owner.YOURS, text=tail, raw=m.group(0))
    tool = grip_tool_named(token, tools)
    if tool is not None:
        return NextStep(owner=Owner.MINE, text=tail, raw=m.group(0),
                        party=token, tool=tool)
    return NextStep(
        owner=Owner.THIRD_PARTY, text=tail, raw=m.group(0), party=token
    )


def disposition(step: NextStep) -> Disposition:
    """Map an owner to what the machinery does about it.

    ABSENT maps to STOP and emphatically NOT to CONTINUE. A missing closer is
    ambiguous — a trivial reply that legitimately carries none, a crash, a
    truncated render — and reading ambiguity as licence to run unattended is
    how a default comes to mean the opposite of what it says.
    """
    if step.owner is Owner.MINE:
        return Disposition.CONTINUE
    if step.owner is Owner.YOURS:
        return Disposition.HOLD
    if step.owner is Owner.THIRD_PARTY:
        return Disposition.BLOCKED
    return Disposition.STOP


# ----------------------------------------------------------------- arming

# The rule is BUILT but DISARMED. Arming is the operator's decision and
# deliberately not the agent's, for one reason: arming changes how every
# session on this machine terminates, and at the time of writing there were
# 73 claude processes running here. An agent that can widen its own autonomy
# by deciding it feels confident is the shape `rules/exogenous-anchor-law.md`
# exists to refuse — so the switch lives outside the agent, and the agent's
# job is to make flipping it a one-liner rather than a build.
#
#   arm:      bin/grip-next-step arm      (this machine, from its next Stop)
#   disarm:   bin/grip-next-step disarm
#   status:   bin/grip-next-step status
#   one process: GRIP_NEXT_STEP_ARMED=1 or =0 overrides the file either way.
#
# WHERE THE SWITCH LIVES, and why it moved (2026-10-02). It used to live only
# in the `env` block of the tracked settings.json. The session-start merge
# (lib/settings_merge.py) replaces `env` from config/settings.reference.json,
# which never carried it, so each merge silently disarmed the machine. Putting
# it in the reference would arm every install; exempting it from the merge
# would arm every checkout of the tracked file. A file under the machine's own
# state directory is per machine by construction, ignored by git
# (`state/**/*.json`), untouched by the merge, and read at every Stop, so
# arming and disarming reach sessions that are already running.
#
# WHAT BOUNDS AN ARMED CONTINUATION. Its length: lib/continuation_brake.py ends
# a chain on stall, on a wall-time budget and at an outer cap, and the hook
# enforces that. Not its effects: lib/timeout_effect_gate.py classifies effects
# but nothing calls it at runtime, so a continuation may do whatever the
# session's normal rules and gates allow. This comment used to say a wrong
# (mine) "can only do local work"; measured 2026-10-02, three outward actions
# followed the gate's refusal within 24 forced turns. A mechanical effect check
# during continuations would need its own decision, not least about pushes.

ARM_ENV = "GRIP_NEXT_STEP_ARMED"
ARM_FILE = "next-step-armed.json"   # under <claude dir>/state/

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def arm_path() -> "Path":
    """Where this machine's switch lives: <claude dir>/state/next-step-armed.json."""
    from pathlib import Path

    try:
        from lib.claude_dir import get_claude_dir
    except ImportError:  # imported as a top-level module from lib/
        from claude_dir import get_claude_dir
    return Path(get_claude_dir()) / "state" / ARM_FILE


def _file_says_armed(path) -> bool:
    """True only when the file parses as JSON carrying "armed": true."""
    import json

    try:
        return json.loads(path.read_text()).get("armed") is True
    except (OSError, ValueError, AttributeError):
        return False


def is_armed(env: dict[str, str] | None = None, *, path=None) -> bool:
    """True only on an explicit opt-in. Absence is NEVER permission.

    A recognised value of GRIP_NEXT_STEP_ARMED decides for this process: a
    truthy one arms, a falsy one disarms. Anything else, including unset,
    empty, "maybe" or "2", leaves the decision to this machine's switch file,
    which arms only when it says so in valid JSON. So a half-written export or
    a half-written file cannot arm the machine by accident.
    """
    import os

    raw = str((env if env is not None else os.environ).get(ARM_ENV, "")).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return _file_says_armed(path if path is not None else arm_path())


def plan(step: NextStep, *, armed: bool | None = None) -> dict:
    """What the turn-end machinery WOULD do — the whole rule in one place.

    Returns a plan rather than performing it, so the decision is inspectable and
    testable without a live session, and so the disarmed path can log exactly
    what it would have done. That log is the evidence for whether arming is
    safe, which is the question that has been unanswerable because nothing ever
    recorded it.
    """
    armed = is_armed() if armed is None else armed
    d = disposition(step)
    base = {
        "owner": step.owner.value,
        "disposition": d.value,
        "question_id": step.question_id,
        "armed": armed,
    }
    if d is Disposition.CONTINUE:
        # Bounded in length by the brakes, which the hook enforces. It used to
        # say "timeout_effect_gate", which nothing calls (see "arming" above).
        cont = {**base, "action": "continue_now",
                "bounded_by": "continuation_brake", "hold_seconds": 0}
        if step.tool:
            # The log row that shows a closer named a GRIP tool (GRIP#9073).
            cont.update(rerouted_from=step.party, tool=step.tool)
        return cont
    if d is Disposition.HOLD:
        # The turn is no longer held (2026-10-02, hooks/next-step-recorder.py):
        # it ends, the question waits in the store, and the answer watcher parks
        # it at the ceiling. The disposition keeps its value "hold" so the
        # existing log stays comparable; the action says what now happens.
        return {**base, "action": "wait_then_park",
                "alert_after_s": SILENT_WAIT_SECONDS,
                "release_after_s": RELEASE_SECONDS,
                "parked_by": "answer_watcher",
                "on_release": "the answer watcher parks it and sends at most one "
                              "notice; nothing is decided by the wait"}
    if d is Disposition.BLOCKED:
        return {**base, "action": "record_external_block", "party": step.party,
                "note": "neither waiting nor working changes a block on someone else"}
    return {**base, "action": "stop", "note": "no closer — absence is not permission"}
