#!/usr/bin/env python3
"""The never-ending cycle's watcher: idle, dry run, clear, reload, seal.

ADR-0016 Step 7, W1. The pieces existed and were not joined: an idle signal
(``hooks/stop-idle-marker.py``), the decision (``lib/infinite_cycle.decide``),
the /ultraclear planner and checkpoint, and a warm reload. This module is the
join, and it is OFF unless a launch opts in: ``bin/grip`` adds the cycle's two
hooks to one session's own ``--settings`` when ``GRIP_INFINITE_CYCLE=1``
(``lib/infinite_cycle_launch.py``), never to any shared settings file.

ONE WATCHER PER CLAUDE CODE PROCESS. The Stop hook arms it; it holds a kernel
lock for its lifetime, so another Stop finds it running and does nothing. It
follows the process, not the conversation: ``/clear`` gives the conversation a
new session id (measured 2026-10-05: about 10-20 s after the keystroke, while
the SessionStart hooks run) and the watcher carries on with the new id. It
exits when the process ends, when the brake is engaged, or after
``MAX_LIFETIME_S``.

Each idle period is evaluated once, in this order:

1. The brake (``bin/grip-infinite-stop``) is engaged: stop, and never act.
2. Wait until Claude Code itself reports the process ``idle`` and the last Stop
   is at least ``min_idle_s`` old (``GRIP_INFINITE_CYCLE_MIN_IDLE_S``, default
   ``infinite_cycle.DEFAULT_MIN_IDLE_S``).
3. **Dry run first** (``lib/infinite_cycle_econ.py``): measure the window, plan
   the HOT manifest, gather the continuity block, and price the clear. It
   writes nothing. Alongside it, ask HAL's wire whether THIS session should
   move providers for capacity (W4, ADR-0016 Step 5f, GRIP#7720) through the
   injectable ``Deps.w4_status``: paused with a fresh target (``w4_move``),
   or already moved with Anthropic capacity back (``w4_return``) — never
   inside an open tool loop (``ends_mid_tool_loop``), because a provider
   switch there would need a thinking signature to travel unmodified, which
   this step never attempts. No measurable saving AND no W4 relief: stop
   here. Either kind of W4 relief fires even with no saving — the reason to
   clear is capacity, not tokens.
4. Write a fresh checkpoint, so the warm reload reloads THIS moment.
5. ``infinite_cycle.decide``, fed the dry run's — or, for a W4 move, the
   wire's — relief marker.
6. The driver proves the pane is this session's, Claude Code's input box is
   empty (an operator's half-typed message is never submitted with a
   ``/clear`` appended), and keystrokes can be delivered at all.
7. Sign and write the decision to clear, paired with a belief-chain node that
   cites it. Only when both exist: look at the pane once more (signing takes
   time, and the operator may have started typing), hand off to the reload
   hook and send ``/clear``. Otherwise the cycle fails as ``unsigned`` and
   nothing is touched. Wait for the new session id and the reload to report
   back.
8. Record the outcome (digests and numbers only, plus the W4 reason and target
   label when relief came from the wire), referring to the prior decision;
   seal it as a ``hal/infinite`` outcome record when a clear was sent.

Anything short of every gate passing leaves the session exactly as it was, and
says why in ``state/hal-infinite/cycle.jsonl``. A degraded outcome means relief
was warranted and this session could not receive it: the same honest-degradation
rule ``lib/infinite_cycle.py`` states.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

_LIB = Path(__file__).resolve().parent
_REPO = _LIB.parent
for _path in (str(_REPO), str(_LIB)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import infinite_cycle_continuity as continuity  # noqa: E402
import infinite_cycle_econ as econ  # noqa: E402
import infinite_cycle_state as state  # noqa: E402
from infinite_cycle import ACTUATOR_NONE, ACTUATOR_OTTY, ACTUATOR_TMUX, actuator_kind, decide  # noqa: E402
from infinite_cycle_econ import CycleConfig  # noqa: E402
from infinite_cycle_seal import seal as seal_cycle, sign_clear  # noqa: E402
from ultraclear_capture import build_turns_from_transcript, project_slug, write_checkpoint  # noqa: E402

RECORD_VERSION = "hal-infinite-cycle/1"
SCREEN_LINES = 14


# ── Claude Code's input box, read from a pane capture ────────────────────

_RULE_RE = re.compile(r"^\s*[─━]{10,}\s*$")
_PLACEHOLDER_RE = re.compile(r'^Try "[^"]*"$')
_PROMPT_MARK = "❯"


def input_box_empty(screen: str) -> Optional[bool]:
    """Is Claude Code's input box empty in this capture? ``None`` when not found.

    The box is the LAST line starting with ``❯`` that sits directly under a
    horizontal rule, up to the next rule (measured on Claude Code 2.1.289 in
    Otty, 2026-10-05). A selection menu also uses ``❯`` but has no rule above
    it. Empty means nothing after the mark, or exactly the dimmed placeholder
    Claude Code shows in an empty box (``Try "..."``); a capture carries no
    styling, so the placeholder is recognised by its fixed shape. Any other
    text, or any line between the prompt and the bottom rule, is the
    operator's.
    """
    lines = screen.splitlines()
    tops = [i for i in range(1, len(lines))
            if lines[i].lstrip().startswith(_PROMPT_MARK) and _RULE_RE.match(lines[i - 1])]
    if not tops:
        return None
    top = tops[-1]
    bottom = next((j for j in range(top + 1, len(lines)) if _RULE_RE.match(lines[j])), None)
    if bottom is None:
        return None
    if any(line.strip() for line in lines[top + 1:bottom]):
        return False
    first = lines[top].lstrip()[len(_PROMPT_MARK):].replace("\xa0", " ").strip()
    return not first or bool(_PLACEHOLDER_RE.match(first))


# ── drivers: how a keystroke reaches THIS session's pane, and nowhere else ─

def _same_dir(a: object, b: object) -> bool:
    try:
        return bool(a) and bool(b) and os.path.realpath(str(a)) == os.path.realpath(str(b))
    except (OSError, ValueError):
        return False


def otty_pane_id(env: Mapping[str, str]) -> str:
    """This process's Otty pane, as ``pane list`` names it (``p_`` + ``$OTTY_PANE_ID``)."""
    raw = str(env.get("OTTY_PANE_ID") or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("p_") else f"p_{raw}"


class OttyDriver:
    """Otty: the pane named by the session's OWN ``$OTTY_PANE_ID``, verified first."""

    kind = ACTUATOR_OTTY

    def __init__(self, pane: str, transport) -> None:
        self.pane = pane
        self._t = transport

    def check(self, session_id: str, cwd: str, claude_pid: int) -> tuple:
        if not self._t.send_keys_enabled():
            return False, "Otty send-keys is disabled (run `grip-otty doctor --fix` once)"
        pane = next((p for p in self._t.pane_list() if p.get("id") == self.pane), None)
        if pane is None:
            return False, f"Otty lists no pane {self.pane}"
        if not self._t.agent_panes([pane]):
            return False, f"pane {self.pane} is not running an agent"
        agent = str(pane.get("agent_session_id") or "")
        if agent and agent != session_id:
            return False, f"pane {self.pane} belongs to another session"
        if not agent and not _same_dir(pane.get("cwd"), cwd):
            return False, f"pane {self.pane} is in another directory"
        return _box_verdict(self.screen())

    def screen(self) -> str:
        return self._t.capture(self.pane, lines=SCREEN_LINES)

    def send_clear(self) -> None:
        self._t.send_prompt(self.pane, "/clear", submit=True, bracketed=False)


Run = Callable[..., "subprocess.CompletedProcess"]


def _parent_of(pid: int, run: Run) -> int:
    out = run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    try:
        return int((out.stdout or "").strip())
    except ValueError:
        return 0


def descends_from(pid: int, ancestor: int, run: Run, depth: int = 12) -> bool:
    """Is ``ancestor`` among ``pid``'s parents (or ``pid`` itself)?"""
    current = pid
    for _ in range(depth):
        if current == ancestor:
            return True
        if current <= 1:
            return False
        current = _parent_of(current, run)
    return False


class TmuxDriver:
    """tmux: ``$TMUX_PANE`` on ``$TMUX``'s socket, and only if Claude Code runs in it."""

    kind = ACTUATOR_TMUX

    def __init__(self, pane: str, socket: str, run: Run = subprocess.run) -> None:
        self.pane = pane
        self.socket = socket
        self._run = run

    def _tmux(self, *args: str) -> "subprocess.CompletedProcess":
        return self._run(["tmux", "-S", self.socket, *args],
                         capture_output=True, text=True, timeout=10)

    def check(self, session_id: str, cwd: str, claude_pid: int) -> tuple:
        out = self._tmux("display-message", "-p", "-t", self.pane, "#{pane_pid}")
        try:
            pane_pid = int((out.stdout or "").strip())
        except ValueError:
            return False, f"tmux knows no pane {self.pane}"
        if not descends_from(claude_pid, pane_pid, self._run):
            return False, f"Claude Code does not run in tmux pane {self.pane}"
        return _box_verdict(self.screen())

    def screen(self) -> str:
        return self._tmux("capture-pane", "-p", "-t", self.pane).stdout or ""

    def send_clear(self) -> None:
        self._tmux("send-keys", "-t", self.pane, "-l", "/clear")
        self._tmux("send-keys", "-t", self.pane, "Enter")


class NoDriver:
    """Nowhere to deliver a keystroke: ``decide`` reports this as degraded."""

    kind = ACTUATOR_NONE

    def check(self, session_id: str, cwd: str, claude_pid: int) -> tuple:
        return False, "no Otty pane and no tmux session to deliver /clear to"

    def screen(self) -> str:
        return ""

    def send_clear(self) -> None:
        raise RuntimeError("NoDriver cannot deliver a keystroke")


def _box_verdict(screen: str) -> tuple:
    empty = input_box_empty(screen)
    if empty is None:
        return False, "Claude Code's input box was not found on screen, so it cannot be confirmed empty"
    if not empty:
        return False, "the input box holds unsent text; a /clear would submit it"
    return True, "pane verified, input box empty"


def resolve_driver(env: Mapping[str, str], *, otty=None, run: Run = subprocess.run,
                   which: Callable[[str], Optional[str]] = shutil.which):
    """The driver ``infinite_cycle.actuator_kind`` picks for this session's environment."""
    if otty is None:
        import otty_transport as otty
    pane = otty_pane_id(env)
    tmux_pane = str(env.get("TMUX_PANE") or "")
    socket = str(env.get("TMUX") or "").split(",")[0]
    kind = actuator_kind(otty_available=bool(pane) and otty.is_available(),
                         tmux_available=bool(tmux_pane and socket and which("tmux")), env=env)
    if kind == ACTUATOR_OTTY:
        return OttyDriver(pane, otty)
    if kind == ACTUATOR_TMUX:
        return TmuxDriver(tmux_pane, socket, run)
    return NoDriver()


# ── W4: a capacity-driven clear, asked of HAL's wire (GRIP#7720) ─────────

W4_STATUS_PATH = "/w4/status"
#: The wire answers within this budget or this evaluation gets no relief —
#: loopback only (see ``fetch_w4_status``), so no DNS wait is possible.
W4_STATUS_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class W4Status:
    """One validated answer from the wire's W4 status endpoint (GRIP#7720)."""

    paused: bool
    anthropic_available: bool
    on_target: Optional[str] = None
    fresh_thread_target: Optional[str] = None


def _parse_w4_status(doc: object) -> Optional[W4Status]:
    """``doc`` as a validated ``W4Status``, or ``None`` when its shape cannot
    be trusted — never a guess (ADR-0016 Step 5f).

    Every field is type-checked before anything is read from it, so a
    malformed or partial body reads exactly like an unreachable wire, never
    like an empty, trustworthy answer.
    """
    if not isinstance(doc, dict):
        return None
    paused, available = doc.get("paused"), doc.get("anthropic_available")
    on_target, fresh = doc.get("on_target"), doc.get("fresh_thread_target")
    if not (isinstance(paused, bool) and isinstance(available, bool)):
        return None
    if not all(v is None or isinstance(v, str) for v in (on_target, fresh)):
        return None
    return W4Status(paused=paused, anthropic_available=available,
                    on_target=on_target or None, fresh_thread_target=fresh or None)


def fetch_w4_status(session_id: str, *, timeout: float = W4_STATUS_TIMEOUT_S) -> Optional[W4Status]:
    """GET HAL's wire for this session's W4 status. Never raises.

    Loopback only — the wire IS a loopback proxy by construction (ADR-0016
    Step 2), so a ``GRIP_WIRE_URL`` pointed anywhere else gets no relief
    rather than a probe. An unreachable wire, a reply slower than
    ``timeout``, a non-200, or a body that does not match the contract all
    read the same way: ``None`` — no W4 relief, and the savings path decides
    as it always has. Never move on a guess.
    """
    import http.client
    from urllib.parse import urlencode, urlsplit

    from grip_infinite import wire_base
    parts = urlsplit(wire_base())
    if parts.scheme != "http" or parts.hostname != "127.0.0.1":
        return None
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", f"{W4_STATUS_PATH}?{urlencode({'session': session_id})}")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        return _parse_w4_status(json.loads(body.decode("utf-8")))
    except Exception:
        return None
    finally:
        conn.close()


def _w4_relief(status: Optional[W4Status]) -> Optional[tuple]:
    """``(reason, target)`` when the wire names capacity relief, else ``None``.

    ``w4_move`` only when this session is reported paused AND a fresh-thread
    target is named; ``w4_return`` only when it is already on a target AND
    Anthropic capacity is back. Either one fires even when the dry run shows
    no saving — the reason to clear is capacity, not tokens.
    """
    if status is None:
        return None
    if status.paused and status.fresh_thread_target:
        return "w4_move", status.fresh_thread_target
    if status.on_target and status.anthropic_available:
        return "w4_return", status.on_target
    return None


def _is_error_notice(kind: object, line: str) -> bool:
    """Is this main-chain line Claude Code's own error notice, not a turn?

    When a request fails (the usage-limit pause a W4 move answers among
    them), Claude Code appends an assistant message flagged
    ``isApiErrorMessage`` ("You've hit your session limit ..."). It is not
    part of the conversation, so it must never hide the tool loop the failed
    request was in the middle of. The substring test keeps large transcripts
    to one parse per line.
    """
    if kind != "assistant" or "isApiErrorMessage" not in line:
        return False
    try:
        record = json.loads(line)
    except ValueError:
        return False
    return isinstance(record, dict) and record.get("isApiErrorMessage") is True


def ends_mid_tool_loop(transcript: object) -> bool:
    """Does the transcript end awaiting a tool's turn, not at a turn boundary?

    True only for the newest main-chain message: an assistant turn that
    opened a ``tool_use`` with no ``tool_result`` for it yet, or a user turn
    carrying a ``tool_result`` the assistant has not replied to yet. Claude
    Code's own error notices (``_is_error_notice``) are skipped, because the
    usage-limit pause that a W4 move answers always ends the transcript with
    one. A W4 move never crosses one of these open exchanges (ADR-0016 Step
    5f): inside a tool loop the thinking blocks must travel unmodified, which
    this step never attempts. An unreadable transcript refuses too — never
    guess at a safe boundary. Reuses ``infinite_cycle_continuity``'s own
    main-chain/tool-block readers, so this can never disagree with what the
    continuity gatherer itself calls a tool result.
    """
    kind, content = None, None
    try:
        with Path(str(transcript)).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                found_kind, found_content = continuity._message_content(line)
                if found_kind is not None and not _is_error_notice(found_kind, line):
                    kind, content = found_kind, found_content
    except OSError:
        return True
    if kind == "assistant":
        return bool(continuity._tool_uses(content))
    if kind == "user":
        return bool(continuity._tool_result_blocks(content))
    return False


# ── one evaluation ────────────────────────────────────────────────────────

@dataclass
class Deps:
    """Everything the evaluation touches outside itself. Injectable for tests."""

    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    seal: Callable[[Mapping], dict] = seal_cycle
    sign_clear: Callable[[Mapping], dict] = sign_clear
    checkpoint: Callable[..., Optional[Path]] = write_checkpoint
    w4_status: Callable[[str], Optional[W4Status]] = fetch_w4_status
    base: Optional[Path] = None
    config_dir: Optional[Path] = None
    sources: continuity.Sources = field(default_factory=continuity.Sources)


@dataclass(frozen=True)
class Facts:
    """What the watcher knows about the session when an idle period is ripe."""

    claude_pid: int
    proc: Mapping
    marker: Mapping
    last_stop_at: float
    now: float

    @property
    def session_id(self) -> str:
        return str(self.proc.get("sessionId") or "")

    @property
    def cwd(self) -> str:
        return str(self.marker.get("cwd") or self.proc.get("cwd") or "")

    @property
    def transcript(self) -> str:
        return str(self.marker.get("transcript_path") or "")


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec="seconds")


def _digest_or_none(value: object) -> Optional[str]:
    return state.digest(value) if value else None


def _file_digest(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _base_record(facts: Facts, config: CycleConfig, driver) -> dict:
    return {
        "v": RECORD_VERSION, "ts": _iso(facts.now), "cycle_id": "c" + uuid.uuid4().hex[:16],
        "claude_pid": facts.claude_pid, "session": state.digest(facts.session_id),
        "actuator": driver.kind, "idle_s": round(facts.now - facts.last_stop_at, 1),
        "thresholds": {"min_idle_s": config.min_idle_s,
                       "max_payback_turns": config.max_payback_turns},
    }


def _continuity_counts(c: continuity.Continuity) -> dict:
    return {"tasks": len(c.tasks), "decisions": len(c.decision_ids),
            "tool_results": len(c.tool_results), "next_step": bool(c.next_step),
            "thread": _digest_or_none(c.thread_id), "bridge": _digest_or_none(c.bridge_session)}


def _finish(rec: dict, outcome: str, reason: str, deps: Deps) -> dict:
    """Record the outcome; seal it whenever a clear was sent or a decision was signed.

    A signed decision always gets its outcome leaf, including "nothing was
    sent", so no decision is left on the chain without what came of it.
    """
    rec["outcome"], rec["reason"] = outcome, reason
    if rec.get("clear_sent") or rec.get("decision"):
        rec["seal"] = deps.seal(rec)
    state.append_cycle_log(rec, deps.base)
    return rec


def _reload_ack_matches(consumed: object, rec: Mapping, facts: Facts, proc: Mapping) -> bool:
    """The hook receipt must name this cycle, process and new session."""
    if not isinstance(consumed, dict):
        return False
    new_session = proc.get("sessionId")
    return (bool(new_session) and bool(facts.proc.get("procStart"))
            and new_session != facts.session_id
            and consumed.get("cycle_id") == rec["cycle_id"]
            and consumed.get("claude_pid") == facts.claude_pid
            and consumed.get("proc_start") == facts.proc.get("procStart")
            and proc.get("procStart") == facts.proc.get("procStart")
            and consumed.get("by_session") == new_session)


def _await_reload(rec: Mapping, facts: Facts, config: CycleConfig, deps: Deps) -> tuple:
    """Poll until the process shows a new session id and the reload reported back."""
    deadline = deps.clock() + config.clear_timeout_s
    proc, handoff = {}, {}
    while deps.clock() < deadline:
        proc = state.process_session(facts.claude_pid, config=deps.config_dir) or {}
        handoff = state.read_handoff(facts.claude_pid, deps.base) or {}
        consumed = handoff.get("consumed")
        if _reload_ack_matches(consumed, rec, facts, proc):
            return proc, consumed
        deps.sleep(1.0)
    return proc, {}


def _write_clear_handoff(rec: dict, facts: Facts, cont, checkpoint: Path, deps: Deps) -> None:
    """Prepare the reload hook's hand-off for this one clear."""
    cont = replace(cont, checkpoint_path=str(checkpoint))
    state.write_handoff(facts.claude_pid, {
        "cycle_id": rec["cycle_id"], "issued_at": deps.clock(), "claude_pid": facts.claude_pid,
        "proc_start": facts.proc.get("procStart"), "from_session": facts.session_id,
        "checkpoint_path": str(checkpoint), "slug": project_slug(facts.cwd),
        "continuity": cont.to_dict()}, deps.base)


def _sign_before_clear(rec: dict, deps: Deps) -> bool:
    """No signed, written decision record means no hand-off and no ``/clear``.

    It runs before the hand-off is written, so a refusal, or a crash right
    after it, can never leave a hand-off behind for a later manual ``/clear``
    to be mistaken for this cycle's reload.
    """
    try:
        signed = deps.sign_clear(rec)
    except Exception as exc:  # noqa: BLE001 - an injected signer must also fail closed
        signed = {"ok": False, "reason": type(exc).__name__}
    if signed.get("idr_id") and signed.get("content_addr"):
        # A leaf that reached the chain is named even when its pairing failed,
        # so the outcome this cycle records is tied to that leaf.
        rec["decision"] = {"idr_id": signed["idr_id"], "content_addr": signed["content_addr"]}
    if signed.get("ok") and rec.get("decision"):
        return True
    # Only the cause's leading label: an exception's message may hold a path.
    cause = str(signed.get("reason") or "no record written").split(":")[0][:60]
    _finish(rec, "failed", f"unsigned: {cause}", deps)
    return False


def _clear_and_reload(rec: dict, facts: Facts, cont, checkpoint: Path,
                      config: CycleConfig, driver, deps: Deps) -> dict:
    """Steps 7 and 8: sign, look at the pane again, hand off, clear, judge the reload."""
    if not _sign_before_clear(rec, deps):
        return rec
    # Signing takes time. The operator may have started typing or changed
    # session meanwhile, and ``/clear`` plus Enter would submit that text.
    ok, why = driver.check(facts.session_id, facts.cwd, facts.claude_pid)
    if not ok:
        return _finish(rec, "degraded", f"after signing: {why}", deps)
    try:
        _write_clear_handoff(rec, facts, cont, checkpoint, deps)
        driver.send_clear()
    except Exception as exc:  # noqa: BLE001 - the signed cycle keeps its own outcome
        # A hand-off already written stays: if the keystroke did land, the
        # reload it needs is there.
        return _finish(rec, "failed", "the clear could not be delivered after signing: "
                                      f"{type(exc).__name__}", deps)
    rec["clear_sent"] = True
    return _reload_result(rec, facts, cont, config, deps)


def _reload_result(rec: dict, facts: Facts, cont, config: CycleConfig, deps: Deps) -> dict:
    """Record what actually happened after the clear was sent."""
    proc, consumed = _await_reload(rec, facts, config, deps)
    new_session = proc.get("sessionId") if proc.get("sessionId") != facts.session_id else None
    before, after = cont.bridge_session, proc.get("bridgeSessionId")
    rec["next_session"] = _digest_or_none(new_session)
    rec["reload"] = {key: consumed.get(key) for key in ("bind", "hot_by", "chars")}
    rec["remote_control"] = {"before": _digest_or_none(before), "after": _digest_or_none(after),
                             "preserved": (before == after) if before else None}
    if not new_session:
        return _finish(rec, "failed", f"/clear was sent but the session id did not change "
                                      f"within {config.clear_timeout_s:g}s", deps)
    if not consumed:
        return _finish(rec, "failed", "the session cleared but the cycle's reload did not "
                                      "run, so its continuity block was not delivered", deps)
    return _finish(rec, "cleared", "cleared and reloaded", deps)


def _hot_turns_in(checkpoint_path: Path) -> int:
    """How many turns landed HOT in a just-written checkpoint.

    Read from the checkpoint itself, never recomputed: when the window could
    not be measured there is no ``DryRun`` to read ``hot_turns`` from, and
    the checkpoint is the one artefact that already carries it
    (``ultraclear_session.checkpoint``'s own ``hot`` list) -- reading it is
    verification, not a second planning pass that could drift from the one
    ``deps.checkpoint`` already ran.
    """
    data = state.read_json(checkpoint_path)
    return len((data or {}).get("hot") or [])


def _w4_check(facts: Facts, deps: Deps) -> tuple:
    """This evaluation's W4 status and relief.

    Returns ``(status, relief)``. ``relief`` is ``(reason, target)`` or
    ``None``, never across an open tool loop (ADR-0016 Step 5f). ``status``
    is the raw ``W4Status`` (``None`` if the wire did not answer), kept so a
    declined evaluation can tell the watcher whether this idle period is
    worth a cheap repoll later (``_worth_w4_repoll``, GRIP#7720 Codex
    4207339316) -- it is never published past the local cycle record.
    """
    status = deps.w4_status(facts.session_id)
    w4 = _w4_relief(status)
    if w4 is not None and ends_mid_tool_loop(facts.transcript):
        w4 = None
    return status, w4


def _w4_probe_record(status: Optional[W4Status]) -> dict:
    """The local-only (never-leaf) trace of one W4 status check."""
    return {"answered": status is not None,
           "paused": status.paused if status else None,
           "on_target": status.on_target if status else None}


def _worth_w4_repoll(rec: dict) -> bool:
    """Should a declined idle period stay eligible for a cheap W4 repoll?

    Only when the wire did not answer at all, or answered saying this
    session is paused or already on a target -- each can change without a
    new Stop. A healthy "nothing pending" answer, or any non-declined
    outcome, is not worth repolling.
    """
    if rec.get("outcome") != "declined":
        return False
    probe = rec.get("w4_probe") or {}
    return not probe.get("answered") or bool(probe.get("paused")) or bool(probe.get("on_target"))


def _measure_or_mark_unmeasured(turns, window, config: CycleConfig, cont) -> tuple:
    """The savings dry run and its record, or an honest "not measured".

    ``window is None`` only reaches here when W4 relief already covers for
    it (see ``evaluate``) -- never invent a window to get a verdict from.
    """
    if window is None:
        return None, {"measured": False}
    size = len(continuity.render_block(cont, bind="bound")) // 4
    dry = econ.dry_run(turns, window, max_payback_turns=config.max_payback_turns,
                       continuity_tokens=size)
    return dry, dry.record()


def evaluate(facts: Facts, config: CycleConfig, driver, deps: Deps) -> dict:
    """Run steps 3-8 for one ripe idle period. Returns the record it appended."""
    rec = _base_record(facts, config, driver)
    status, w4 = _w4_check(facts, deps)
    rec["w4_probe"] = _w4_probe_record(status)
    if w4 is not None:
        rec["w4"] = {"reason": w4[0], "target": w4[1]}
    window = econ.measure_window(facts.transcript)
    if window is None and w4 is None:
        return _finish(rec, "declined", "cannot measure the window: the transcript has "
                                        "no usage record", deps)
    turns, _kernel = build_turns_from_transcript(facts.transcript)
    cont = continuity.gather(cycle_id=rec["cycle_id"], session_id=facts.session_id,
                             claude_pid=facts.claude_pid, transcript=facts.transcript,
                             turns=turns, checkpoint_path="", sources=deps.sources)
    if w4 is not None:
        # The reload must name the real cause (GRIP#7720 Codex 4207339341):
        # capacity, not a false claim that the clear paid for itself. Set
        # before render_block is called at all, including the size estimate
        # below.
        cont = replace(cont, w4_reason=w4[0], w4_target=w4[1])
    rec["continuity"] = _continuity_counts(cont)
    dry, rec["dry_run"] = _measure_or_mark_unmeasured(turns, window, config, cont)
    if (dry is None or not dry.saves) and w4 is None:
        reason = (dry.reason if dry is not None else
                  "cannot measure the window: the transcript has no usage record")
        return _finish(rec, "declined", reason, deps)
    checkpoint = deps.checkpoint(turns, 0, facts.session_id, _iso(deps.clock()),
                                 slug=project_slug(facts.cwd))
    if checkpoint is None:
        return _finish(rec, "failed", "the checkpoint could not be written", deps)
    hot_turns = dry.hot_turns if dry is not None else _hot_turns_in(Path(checkpoint))
    rec["checkpoint"] = {"sha256": _file_digest(Path(checkpoint)), "hot_turns": hot_turns}
    relief = {"status": "pending", "reason": w4[0] if w4 else dry.reason}
    verdict = decide(relief, now=deps.clock(), last_stop_at=facts.last_stop_at,
                     checkpoint_at=deps.clock(), actuator=driver.kind,
                     min_idle_s=config.min_idle_s)
    rec["verdict"] = {"fire": verdict.fire, "degraded": verdict.degraded}
    if not verdict.fire:
        return _finish(rec, "degraded" if verdict.degraded else "declined", verdict.reason, deps)
    ok, why = driver.check(facts.session_id, facts.cwd, facts.claude_pid)
    if not ok:
        return _finish(rec, "degraded", why, deps)
    return _clear_and_reload(rec, facts, cont, Path(checkpoint), config, driver, deps)


# ── the watcher ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """One look at the session: stop, wait, or evaluate these facts."""

    stop: bool = False
    wait: float = 0.0
    facts: Optional[Facts] = None
    why: str = ""


def _brake_engaged() -> bool:
    from grip_infinite import brake_state
    return brake_state().engaged


def observe(pid: int, proc_start: object, evaluated: Optional[float],
            config: CycleConfig, deps: Deps, brake: Callable[[], bool] = _brake_engaged) -> Observation:
    """Decide what the watcher does next. Pure apart from reading small files."""
    proc = state.process_session(pid, config=deps.config_dir)
    if proc is None or not state.pid_alive(pid) or proc.get("procStart") != proc_start:
        return Observation(stop=True, why="the Claude Code process ended")
    if brake():
        return Observation(stop=True, why="the brake is engaged")
    marker = state.read_marker(state.marker_path(base=deps.base, sid=str(proc["sessionId"])))
    last = state.stop_time(marker)
    if marker is None or last is None or last == evaluated:
        return Observation(wait=config.poll_s)
    now = deps.clock()
    remaining = last + config.min_idle_s - now
    if remaining > 0:
        return Observation(wait=min(remaining, config.poll_s))
    if proc.get("status") != "idle":
        return Observation(wait=config.poll_s)
    return Observation(facts=Facts(claude_pid=pid, proc=proc, marker=marker,
                                   last_stop_at=last, now=now))


def _safe_evaluate(facts: Facts, config: CycleConfig, driver, deps: Deps) -> dict:
    """``evaluate``, but an unexpected error becomes a record, never a dead watcher.

    Returns the record either way, so a caller can decide whether this idle
    period is worth a W4 repoll (``_worth_w4_repoll``).
    """
    try:
        return evaluate(facts, config, driver, deps)
    except Exception as exc:  # noqa: BLE001 - one bad evaluation must not end the cycle
        rec = _base_record(facts, config, driver)
        return _finish(rec, "failed",
                      f"evaluation raised {type(exc).__name__}: {str(exc)[:200]}", deps)


def _log_stopped(claude_pid: int, reason: str, deps: Deps) -> None:
    state.append_cycle_log({"v": RECORD_VERSION, "ts": _iso(deps.clock()),
                            "claude_pid": claude_pid, "outcome": "stopped",
                            "reason": reason}, deps.base)


def _repoll_facts(facts: Facts, proc_start: object, config: CycleConfig,
                  deps: Deps, brake: Callable[[], bool]) -> Optional[Facts]:
    """Apply the normal watcher gates and keep this same idle period."""
    fresh = observe(facts.claude_pid, proc_start, None, config, deps, brake).facts
    if (fresh is None or fresh.session_id != facts.session_id
            or fresh.last_stop_at != facts.last_stop_at):
        return None
    return fresh


class _RepollDriver:
    """Recheck live watcher gates immediately before a repoll can clear."""

    def __init__(self, driver, facts, proc_start, config, deps, brake):
        self.driver, self.facts, self.proc_start = driver, facts, proc_start
        self.config, self.deps, self.brake = config, deps, brake
        self.kind = driver.kind

    def check(self, session_id, cwd, claude_pid):
        if _repoll_facts(self.facts, self.proc_start, self.config,
                         self.deps, self.brake) is None:
            return False, "the session changed or is no longer idle"
        ok, why = self.driver.check(session_id, cwd, claude_pid)
        if ok and _repoll_facts(self.facts, self.proc_start, self.config,
                                self.deps, self.brake) is None:
            return False, "the session changed or is no longer idle"
        return ok, why

    def send_clear(self):
        self.driver.send_clear()


def _w4_repoll(facts: Facts, proc_start: object, config: CycleConfig, driver,
              deps: Deps, brake: Callable[[], bool], born: float) -> bool:
    """Repoll one idle period until relief, a new Stop, or the lifetime limit.

    True stops the watcher for the brake or process exit. Evaluate only with
    relief: transcripts can reach 40 MB.
    """
    interval = config.w4_repoll_start_s
    while deps.clock() - born < config.max_lifetime_s:
        deps.sleep(interval)
        if brake():
            _log_stopped(facts.claude_pid, "the brake is engaged", deps)
            return True
        fresh = _repoll_facts(facts, proc_start, config, deps, lambda: False)
        if fresh is None:
            return observe(facts.claude_pid, proc_start, None, config, deps, brake).stop
        _, w4 = _w4_check(fresh, deps)
        if w4 is not None:
            fresh = _repoll_facts(facts, proc_start, config, deps, brake)
            if fresh is not None:
                guarded = _RepollDriver(driver, facts, proc_start, config, deps, brake)
                _safe_evaluate(fresh, config, guarded, deps)
            return False
        interval = min(interval * 2, config.w4_repoll_max_s)
    return False


def watch(pid: int, *, config: CycleConfig, deps: Deps, driver=None,
          brake: Callable[[], bool] = _brake_engaged) -> int:
    """The watcher's whole life. Returns 0; every outcome is in the cycle log."""
    fd = state.try_lock(state.watch_lock_path(pid, deps.base))
    if fd is None:
        return 0
    try:
        start = state.process_session(pid, config=deps.config_dir) or {}
        drv = driver if driver is not None else resolve_driver(os.environ)
        born, evaluated = deps.clock(), None
        while deps.clock() - born < config.max_lifetime_s:
            seen = observe(pid, start.get("procStart"), evaluated, config, deps, brake)
            if seen.stop:
                if seen.why == "the brake is engaged":
                    _log_stopped(pid, seen.why, deps)
                break
            if seen.facts is not None:
                rec = _safe_evaluate(seen.facts, config, drv, deps)
                evaluated = seen.facts.last_stop_at
                if _worth_w4_repoll(rec) and _w4_repoll(
                        seen.facts, start.get("procStart"), config, drv, deps, brake, born):
                    break
            deps.sleep(max(seen.wait, 0.5))
        return 0
    finally:
        os.close(fd)


# ── the command line ──────────────────────────────────────────────────────

def _cmd_watch(args: argparse.Namespace) -> int:
    return watch(args.claude_pid, config=CycleConfig.from_env(), deps=Deps())


def _cmd_status(args: argparse.Namespace) -> int:
    rows = state.read_cycle_log()
    if args.claude_pid:
        rows = [r for r in rows if r.get("claude_pid") == args.claude_pid]
    for row in rows[-args.last:]:
        print(json.dumps({k: row.get(k) for k in ("ts", "cycle_id", "outcome", "reason")}))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="infinite_cycle_runner",
                                     description="The never-ending cycle's watcher (ADR-0016 W1).")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="watch one Claude Code process until it ends")
    w.add_argument("--claude-pid", type=int, required=True)
    w.set_defaults(handler=_cmd_watch)
    s = sub.add_parser("status", help="print the newest cycle records")
    s.add_argument("--claude-pid", type=int, default=0)
    s.add_argument("--last", type=int, default=10)
    s.set_defaults(handler=_cmd_status)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    from pq_reexec import reexec_if_incapable
    reexec_if_incapable(Path(__file__))
    raise SystemExit(main())
