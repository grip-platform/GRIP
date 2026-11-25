"""What a ``/clear`` cycle must carry across, and the reload that carries it.

ADR-0016 Step 7, W1, and the continuity contract's ``/clear`` row: the thread id,
the sealed checkpoint and the chain survive; prior context comes back from the
checkpoint. ``lib/ultraclear_capture`` already carries the conversation's HOT
turns, verbatim. It cannot carry five things a clear would otherwise drop, so
this module gathers them before the clear and puts them back after it:

* **pending work**: the open tasks in the session's task list, and the newest
  "## Next Step" block the session wrote;
* **decisions**: the ids of the turns the capture pins as directives, so a
  later reader can check each one survived (their text rides in the manifest);
* **tool results**: the newest few, which the capture drops because a tool
  result is not visible text. Each keeps an excerpt for the model, and a sha256
  and length so anyone can check it against the old transcript;
* **the thread id** (Step 4's signed thread store): HAL keys a thread by Claude
  Code's session id, and ``/clear`` gives the conversation a new one. The reload
  binds the new id to the old thread, so the chain continues instead of
  forking. See ``bind_session``;
* **the Remote Control association**: the ``bridgeSessionId`` Claude Code keeps
  per process. A clear must leave it unchanged; the runner checks that it did.

The block is CONTEXT, never an instruction. Tool-result excerpts are untrusted
data that was already in the window before the clear, and the block says so.

The thread binder writes one file into HAL's store, under that store's own
lock and documented layout (``lib/hal/infinite_thread.py``: "threads/index/<k>
.thread, the thread a session writes to (k: sha256 of it)"). It only ever
CREATES a missing binding. A session HAL has already given a thread keeps it,
and the outcome says ``split``, so a forked chain is visible, never hidden.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from _compat import fcntl  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
from infinite_cycle_state import (  # noqa: E402
    config_dir, process_session, read_handoff, read_json, write_handoff,
)
from ultraclear import Turn  # noqa: E402
from ultraclear_capture import _is_directive  # noqa: E402
from ultraclear_session import reload_manifest  # noqa: E402

ACTIVE_TASK_STATES = frozenset({"pending", "in_progress"})
MAX_TASKS = 20
MAX_DECISION_IDS = 12
MAX_TOOL_RESULTS = 6
TOOL_EXCERPT_CHARS = 1_200
NEXT_STEP_CHARS = 1_500
SUBJECT_CHARS = 160
#: A handoff older than this is not this clear's: a manual /clear much later
#: must never adopt it.
HANDOFF_TTL_S = 300.0
#: The cycle's own HOT render, used only when the global warm reload will not
#: serve (see ``_global_reload_serves``), stays inside the same bound.
HOT_TOKEN_CAP = 25_000

_THREAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_NEXT_STEP_RE = re.compile(r"^#{2,3}\s*Next Step\b.*$", re.IGNORECASE | re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,3}\s", re.MULTILINE)
_GLOBAL_RELOAD_HOOK = "ultraclear-reload-sessionstart"


@dataclass(frozen=True)
class ToolResult:
    """One tool result: enough to continue from it, and to check it later."""

    tool_use_id: str
    tool: str
    is_error: bool
    sha256: str
    chars: int
    excerpt: str


@dataclass(frozen=True)
class Continuity:
    """Everything a clear must carry that the HOT manifest does not."""

    cycle_id: str
    from_session: str
    claude_pid: int
    checkpoint_path: str
    tasks: tuple = ()
    next_step: str = ""
    decision_ids: tuple = ()
    tool_results: tuple = ()
    thread_id: Optional[str] = None
    bridge_session: Optional[str] = None
    #: Why this clear actually happened -- ``"w4_move"``, ``"w4_return"`` or
    #: ``None`` (a savings clear, ADR-0016 W1). Set by ``evaluate()`` so the
    #: reload names the real cause (GRIP#7720 Codex 4207339341) instead of
    #: always claiming the clear paid for itself.
    w4_reason: Optional[str] = None
    #: The provider/model label a W4 move named, or ``None``.
    w4_target: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> "Continuity":
        results = tuple(ToolResult(**r) for r in data.get("tool_results") or ())
        tasks = tuple(dict(t) for t in data.get("tasks") or ())
        return cls(
            cycle_id=str(data["cycle_id"]), from_session=str(data["from_session"]),
            claude_pid=int(data["claude_pid"]), checkpoint_path=str(data["checkpoint_path"]),
            tasks=tasks, next_step=str(data.get("next_step") or ""),
            decision_ids=tuple(data.get("decision_ids") or ()), tool_results=results,
            thread_id=data.get("thread_id"), bridge_session=data.get("bridge_session"),
            w4_reason=data.get("w4_reason"), w4_target=data.get("w4_target"),
        )


# ── pending work ──────────────────────────────────────────────────────────

def task_dir(session_id: str, *, env: Optional[Mapping[str, str]] = None,
             config: Optional[Path] = None) -> Path:
    """The session's task list: ``tasks/<CLAUDE_CODE_TASK_LIST_ID or session id>``."""
    environ = os.environ if env is None else env
    list_id = str(environ.get("CLAUDE_CODE_TASK_LIST_ID") or "").strip() or session_id
    return (config if config is not None else config_dir(environ)) / "tasks" / list_id


def _task_entry(path: Path) -> Optional[dict]:
    data = read_json(path)
    if data is None or data.get("status") not in ACTIVE_TASK_STATES:
        return None
    return {"id": str(data.get("id") or path.stem), "status": data["status"],
            "subject": str(data.get("subject") or "")[:SUBJECT_CHARS]}


def _task_order(entry: dict) -> tuple:
    ident = entry["id"]
    return (0, int(ident), ident) if ident.isdigit() else (1, 0, ident)


def pending_tasks(directory: Path, limit: int = MAX_TASKS) -> tuple:
    """Open tasks (pending or in progress), in id order, at most ``limit``."""
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return ()
    entries = [e for e in map(_task_entry, files) if e is not None]
    return tuple(sorted(entries, key=_task_order)[:limit])


def _next_step_in(text: str) -> str:
    matches = list(_NEXT_STEP_RE.finditer(text))
    if not matches:
        return ""
    rest = text[matches[-1].end():]
    following = _HEADING_RE.search(rest)
    body = rest[:following.start()] if following else rest
    return body.strip()[:NEXT_STEP_CHARS]


def next_step(turns: Iterable[Turn]) -> str:
    """The newest "## Next Step" block an assistant turn wrote, or ``""``."""
    for turn in reversed(list(turns)):
        if turn.role == "assistant":
            found = _next_step_in(turn.content)
            if found:
                return found
    return ""


def decision_ids(turns: Iterable[Turn], limit: int = MAX_DECISION_IDS) -> tuple:
    """Ids of the turns the capture pins as directives, newest last.

    Uses the capture's own test (``ultraclear_capture._is_directive``), so this
    list and the manifest's pins cannot disagree about what a decision is.
    """
    ids = [turn.id for turn in turns if turn.pinned and _is_directive(turn.content)]
    return tuple(ids[-limit:])


# ── tool results ──────────────────────────────────────────────────────────

def _block_text(content: object) -> str:
    """The text of a tool result's content: a string, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") if b.get("type") == "text" else f"[{b.get('type', 'block')}]"
             for b in content if isinstance(b, dict)]
    return "\n".join(p for p in parts if p)


def _tool_uses(content: object) -> dict:
    if not isinstance(content, list):
        return {}
    return {str(b.get("id")): str(b.get("name") or "?") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")}


def _tool_result_blocks(content: object) -> list:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _as_result(block: dict, names: Mapping[str, str]) -> ToolResult:
    text = _block_text(block.get("content"))
    ident = str(block.get("tool_use_id") or "")
    return ToolResult(
        tool_use_id=ident, tool=names.get(ident, "?"), is_error=bool(block.get("is_error")),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), chars=len(text),
        excerpt=text[:TOOL_EXCERPT_CHARS],
    )


def _message_content(line: str) -> tuple:
    """(record type, message content) for a main-chain line, else (None, None)."""
    try:
        record = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(record, dict) or record.get("isSidechain") is True:
        return None, None
    message = record.get("message")
    if not isinstance(message, dict):
        return None, None
    return record.get("type"), message.get("content")


def recent_tool_results(transcript: object, limit: int = MAX_TOOL_RESULTS) -> tuple:
    """The newest ``limit`` tool results in the transcript, oldest of them first."""
    names: dict = {}
    kept: deque = deque(maxlen=max(0, limit))
    try:
        with Path(str(transcript)).open(encoding="utf-8", errors="replace") as handle:
            for kind, content in map(_message_content, handle):
                if kind == "assistant":
                    names.update(_tool_uses(content))
                elif kind == "user":
                    kept.extend(_as_result(b, names) for b in _tool_result_blocks(content))
    except OSError:
        return ()
    return tuple(kept)


# ── the thread (Step 4's signed store) ────────────────────────────────────

def hal_thread_root(env: Optional[Mapping[str, str]] = None) -> Path:
    """``$HAL_HOME/state/infinite``, else ``~/.hal/state/infinite`` (HAL's own rule)."""
    home = str((os.environ if env is None else env).get("HAL_HOME") or "").strip()
    return Path(home or (Path.home() / ".hal")) / "state" / "infinite"


def _index_file(root: Path, session_id: str) -> Path:
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return root / "threads" / "index" / f"{key}.thread"


def thread_for_session(session_id: str, root: Path) -> Optional[str]:
    """The thread HAL writes this session's rows to, or ``None`` (not on the wire)."""
    if not session_id:
        return None
    try:
        value = _index_file(root, session_id).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if _THREAD_ID_RE.match(value) else None


def _write_index(index: Path, thread_id: str) -> None:
    tmp = index.with_name(f".{index.name}.{os.getpid()}.tmp")
    tmp.write_bytes(thread_id.encode("ascii"))
    os.replace(tmp, index)


def bind_session(session_id: str, thread_id: Optional[str], root: Path) -> str:
    """Point ``session_id`` at an existing thread, if nothing points it elsewhere.

    Returns ``bound``, ``already`` (it already points there), ``split`` (HAL
    gave this session its own thread first; left untouched), ``no-thread``
    (nothing to carry) or ``refused: <why>``. Holds the same lock HAL's
    ``ThreadStore._append`` holds, so a concurrent first row cannot race it.
    """
    if not (thread_id and _THREAD_ID_RE.match(thread_id) and session_id):
        return "no-thread"
    if not (root / "threads" / f"{thread_id}.jsonl").is_file():
        return "no-thread"
    index = _index_file(root, session_id)
    try:
        index.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(index.with_suffix(".lock")), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError as exc:
        return f"refused: {type(exc).__name__}"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = thread_for_session(session_id, root)
        if current is not None:
            return "already" if current == thread_id else "split"
        _write_index(index, thread_id)
        return "bound"
    except OSError as exc:
        return f"refused: {type(exc).__name__}"
    finally:
        os.close(fd)


# ── gather, before the clear ──────────────────────────────────────────────

@dataclass(frozen=True)
class Sources:
    """Where the gatherer reads from. Injectable, so tests never touch real state.

    ``env=None`` means the process environment AT CALL TIME. A default that
    copied ``os.environ`` would freeze whatever the environment was when this
    module was first imported.
    """

    env: Optional[Mapping[str, str]] = None
    config: Optional[Path] = None
    hal_root: Optional[Path] = None


def gather(*, cycle_id: str, session_id: str, claude_pid: int, transcript: object,
           turns: Iterable[Turn], checkpoint_path: str,
           sources: Optional[Sources] = None) -> Continuity:
    """Collect the five invariants for one cycle. Reads only; writes nothing."""
    src = sources if sources is not None else Sources()
    env = os.environ if src.env is None else src.env
    turns = list(turns)
    proc = process_session(claude_pid, config=src.config) or {}
    root = src.hal_root if src.hal_root is not None else hal_thread_root(env)
    return Continuity(
        cycle_id=cycle_id, from_session=session_id, claude_pid=claude_pid,
        checkpoint_path=str(checkpoint_path),
        tasks=pending_tasks(task_dir(session_id, env=env, config=src.config)),
        next_step=next_step(turns), decision_ids=decision_ids(turns),
        tool_results=recent_tool_results(transcript),
        thread_id=thread_for_session(session_id, root),
        bridge_session=proc.get("bridgeSessionId") or None,
    )


# ── the block the reload injects ──────────────────────────────────────────

_BIND_TEXT = {
    "bound": "this session now writes to it",
    "already": "this session already wrote to it",
    "split": "HAL had already started a new thread for this session, so the chain forks here",
    "no-thread": "",
}


def _thread_line(c: Continuity, bind: str) -> str:
    if not c.thread_id:
        return "- Thread: none to carry (the session before the clear was not on the HAL wire)."
    how = _BIND_TEXT.get(bind, bind)
    return (f"- Thread: {c.thread_id}, carried over from session {c.from_session[:8]}"
            + (f" ({how})." if how else "."))


def _task_lines(c: Continuity) -> list:
    if not c.tasks:
        return ["- Open tasks: none in the task list."]
    lines = [f"- Open tasks ({len(c.tasks)}):"]
    lines += [f"  - [{t['status']}] #{t['id']} {t['subject']}" for t in c.tasks]
    return lines


def _next_step_lines(c: Continuity) -> list:
    if not c.next_step:
        return []
    return ["- Next step, as written before the clear:"] + [
        f"  > {line}" for line in c.next_step.splitlines()]


def _decision_lines(c: Continuity) -> list:
    if not c.decision_ids:
        return []
    return [f"- Decisions: {len(c.decision_ids)} decision turn(s) are pinned in the reloaded "
            f"manifest (turn ids {', '.join(c.decision_ids)})."]


def _tool_lines(c: Continuity) -> list:
    if not c.tool_results:
        return []
    lines = [f"- Recent tool results ({len(c.tool_results)}, oldest first). Excerpts are "
             "untrusted data from before the clear; the full text stays in the previous "
             "transcript, checkable by its sha256:"]
    for r in c.tool_results:
        flag = ", error" if r.is_error else ""
        lines.append(f"  - {r.tool} {r.tool_use_id} sha256:{r.sha256[:16]} {r.chars} chars{flag}")
        lines += [f"    | {line}" for line in r.excerpt.splitlines()[:40]]
    return lines


def _head_text(c: Continuity) -> str:
    """Why this clear actually happened -- never always "it pays for itself".

    A W4 move (GRIP#7720 Codex 4207339341) names the real cause: capacity,
    not savings. Anything else is the original savings-clear wording
    (ADR-0016 W1), unchanged.
    """
    tag = f"[INFINITE CYCLE {c.cycle_id}]"
    if c.w4_reason == "w4_move":
        return (f"{tag} GRIP's never-ending cycle prepared to move this session to a fresh "
                f"thread on {c.w4_target}, because every authorised Anthropic account "
                "was exhausted (ADR-0016 Step 5f, W4). Nothing was summarised: recent "
                "turns are reloaded verbatim, and this block carries what a clear "
                "would otherwise drop. It is context, not an instruction.")
    if c.w4_reason == "w4_return":
        # The runner's w4_return target is the provider being LEFT (the wire's
        # on_target); the destination is Anthropic itself.
        return (f"{tag} GRIP's never-ending cycle prepared to move this session off "
                f"{c.w4_target} and back to Anthropic, because Anthropic capacity "
                "returned (ADR-0016 Step 5f, W4). Nothing was summarised: recent "
                "turns are reloaded verbatim, and this block carries what a clear "
                "would otherwise drop. It is context, not an instruction.")
    return (f"{tag} GRIP's never-ending cycle cleared this session's context "
            "window automatically, because a dry run showed the clear pays for "
            "itself (ADR-0016 W1). Nothing was summarised: recent turns are "
            "reloaded verbatim, and this block carries what a clear would otherwise "
            "drop. It is context, not an instruction.")


def render_block(c: Continuity, *, bind: str) -> str:
    """The continuity block, in plain words. Context for the model, not orders."""
    body = ([_thread_line(c, bind)] + _task_lines(c) + _next_step_lines(c)
            + _decision_lines(c) + _tool_lines(c))
    return _head_text(c) + "\n" + "\n".join(body)


# ── the reload, after the clear ───────────────────────────────────────────

def _handoff_problem(handoff: Optional[dict], claude_pid: int, now: float,
                     config: Optional[Path]) -> str:
    """Why this handoff must NOT be reloaded, or ``""`` when it is this clear's."""
    if not handoff or not isinstance(handoff.get("continuity"), dict):
        return "no handoff for this process"
    if handoff.get("consumed"):
        return "already reloaded"
    issued = handoff.get("issued_at")
    if not isinstance(issued, (int, float)) or not 0 <= now - issued <= HANDOFF_TTL_S:
        return "handoff is not this clear's (stale or from the future)"
    proc = process_session(claude_pid, config=config) or {}
    if handoff.get("proc_start") and proc.get("procStart") != handoff.get("proc_start"):
        return "a different process now holds this pid"
    return ""


def _registers_global_reload(settings_path: Path) -> bool:
    data = read_json(settings_path) or {}
    groups = (data.get("hooks") or {}).get("SessionStart") or []
    commands = [h.get("command", "") for g in groups if isinstance(g, dict)
                for h in g.get("hooks", []) if isinstance(h, dict)]
    return any(_GLOBAL_RELOAD_HOOK in str(c) for c in commands)


def _global_reload_serves(handoff: Mapping, new_session: str, settings_path: Path,
                          base: Optional[Path]) -> bool:
    """Will ``hooks/ultraclear-reload-sessionstart.py`` reload OUR checkpoint?

    Three conditions, all checked rather than assumed: it is registered, the
    session id changed (it skips the current session's own checkpoint), and the
    project's pointer still names the checkpoint the cycle wrote (another
    session in the same project can move it).
    """
    if new_session == handoff.get("from_session"):
        return False
    if not _registers_global_reload(settings_path):
        return False
    root = base if base is not None else get_claude_dir()
    pointer = read_json(root / "state" / "ultraclear" / f"latest-{handoff.get('slug')}.json")
    return bool(pointer) and pointer.get("path") == handoff.get("checkpoint_path")


def _cap_recent(turns: list, cap: int) -> list:
    kept, total = [], 0
    for turn in reversed(turns):
        if kept and total + turn.tokens > cap:
            break
        kept.append(turn)
        total += turn.tokens
    return list(reversed(kept))


def render_hot(checkpoint_path: str) -> str:
    """The checkpoint's HOT turns, verbatim and capped, for when nothing else reloads them."""
    data = read_json(Path(checkpoint_path))
    if data is None:
        return ""
    turns = _cap_recent(reload_manifest(data), HOT_TOKEN_CAP)
    head = (f"[INFINITE CYCLE HOT MANIFEST: {len(turns)} verbatim turn(s) from before the "
            "clear, reloaded by the cycle because the shared warm reload would not serve "
            "this checkpoint.]")
    return head + "\n\n" + "\n\n".join(f"- {t.role}:\n{t.content}" for t in turns)


def _emit(context: str) -> dict:
    return {"continue": True, "suppressOutput": True,
            "hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": context}}


@dataclass(frozen=True)
class ReloadPlaces:
    """Where the reload reads and writes. Injectable for tests."""

    base: Optional[Path] = None
    config: Optional[Path] = None
    hal_root: Optional[Path] = None
    settings_path: Optional[Path] = None


def reload_after_clear(payload: Mapping, *, claude_pid: int, now: float,
                       places: ReloadPlaces = ReloadPlaces()) -> dict:
    """The reload hook's whole job: carry this clear's invariants into the new window.

    Returns the SessionStart output, or ``{}`` when this start is not a clear
    the cycle made (a manual /clear, a startup, a stale handoff). Marks the
    handoff consumed with what it did, which the runner then records.
    """
    if str(payload.get("source") or "") != "clear":
        return {}
    handoff = read_handoff(claude_pid, places.base)
    if _handoff_problem(handoff, claude_pid, now, places.config):
        return {}
    new_session = str(payload.get("session_id") or "")
    continuity = Continuity.from_dict(handoff["continuity"])
    root = places.hal_root if places.hal_root is not None else hal_thread_root()
    bind = bind_session(new_session, continuity.thread_id, root)
    settings = places.settings_path or (config_dir() / "settings.json")
    hot_by = "global" if _global_reload_serves(handoff, new_session, settings, places.base) else "cycle"
    text = render_block(continuity, bind=bind)
    if hot_by == "cycle":
        text = render_hot(continuity.checkpoint_path) + "\n\n" + text
    consumed = {"at": now, "by_session": new_session, "bind": bind, "hot_by": hot_by,
                "chars": len(text), "cycle_id": handoff["cycle_id"],
                "claude_pid": claude_pid, "proc_start": handoff.get("proc_start")}
    write_handoff(claude_pid, {**handoff, "consumed": consumed}, places.base)
    return _emit(text)
