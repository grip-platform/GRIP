#!/usr/bin/env python3
"""continue_card — the GOAL/STATE/NEXT card the `/continue` skill injects.

The operator types "continue. what was the goal/task again? /recall" after a
compaction or an interrupt rather than re-explaining the session. This module
answers that question from the transcript itself: the newest real REQUEST (not
the "continue" prompt that triggered this card), whether a later short reply
approved a specific next step, whether that step's own referents (a PR, a
path, a URL) already show it done, and the worktree's own git state — never by
asking the operator what the goal was.

One streaming pass over the transcript (files reach 80 MB; this never loads
the whole thing). Operator rows are META / NOISE / SHORT_APPROVAL / REQUEST.
The newest operator row is this card's own invocation only when it IS a
continue trigger (META); otherwise the trigger is not in the file yet, or the
card is reading another session, and every kept row is a GOAL candidate.
GOAL is the newest REQUEST, overridden by a newer SHORT_APPROVAL's preceding
closer; NEXT is the closer the invocation row answered, or, with no
invocation row, the newest closer in the file. STATE probes every referent in
both texts via `task_reality_reconcile` (reused, not reimplemented) plus the
worktree's own git state via `next_move`/`shared_tree_state` (reused);
CONFIDENCE and SOURCES say plainly what was read and what was not.

Privacy: the card quotes only operator rows and assistant CLOSERS (the
`RECOMMENDED NEXT STEP (...)` line `next_step_router.parse` extracts) — never
tool output, hook output, or a memory body. A tool-result-bearing row or a
hook `attachment` row is skipped before any text is ever read out of it.

ALWAYS exits 0 (see `main`): the skill injects this with `!`...`` and a
failing command aborts the whole skill, so an internal error here becomes a
LOW-confidence card whose SOURCES line names the error, never a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from next_step_router import Owner, parse as parse_next_step  # noqa: E402
from task_reality_reconcile import (  # noqa: E402
    Probes,
    ProbeFact,
    Verdict,
    classify_task,
    probe_task,
)
from transcript_text import _flatten_content, is_human  # noqa: E402
import next_move  # noqa: E402
import shared_tree_state  # noqa: E402
import transcript_text  # noqa: E402

__all__ = ["build_card", "render_text", "main"]

#: How many of the newest operator rows the card keeps in memory, per the
#: engine contract. Older rows are evicted as the stream advances — a
#: 30-entry window, not a 30-line read limit; the whole file is still scanned.
OPERATOR_ROW_WINDOW = 30
DEFAULT_MAX_QUOTE = 300
SUMMARY_NEXT_CAP = 800
RECALL_TIMEOUT_S = 3.0
LABEL_WIDTH = 12

# --------------------------------------------------------------------------- #
# Operator-row classification
# --------------------------------------------------------------------------- #
#: A "continue" trigger in any of its known wordings, including a
#: message built from several of them at once ("continue. what was the
#: goal/task again? /recall") — checked per clause, not as one long substring.
_META_CLAUSE_EXACT = frozenset({"continue", "/continue", "/recall"})
#: The skill's own invocation with its documented switches ("/continue
#: --deep", "/continue ultracode") is the trigger too, never a REQUEST.
_CONTINUE_INVOCATION_RE = re.compile(r"^/continue(?:\s+(?:--deep|deep|ultracode))*$")
_META_CLAUSE_CONTAINS = ("what was the goal", "what was the task", "where were we")
#: META is only for short trigger-shaped messages — a long REQUEST that merely
#: mentions "where were we" in passing must stay a REQUEST (R0 scope guard).
_META_MAX_CHARS = 200

_APPROVAL_WORDS = frozenset({
    "yes", "yep", "yeah", "ok", "okay", "sure", "lfg", "go", "proceed",
    "do it", "merge it", "ship it", "approved", "lgtm", "sounds good",
})
_SLASH_TOKEN_RE = re.compile(r"/\S+")
_NOISE_EXACT = frozenset({"[request interrupted by user]"})
_NOISE_PREFIXES = ("<bash-input>", "<bash-stdout>")


#: How much may trail a matched trigger phrase inside its clause and still
#: count as "just the trigger" — enough for a stray slash-joined word
#: ("goal/task") or a trailing "?", never enough for a second request riding
#: on the same clause with no sentence break ("where were we ... , and also
#: implement feature X" must stay a REQUEST, not become META).
_META_CLAUSE_SLACK = 10


def _is_meta_clause(part: str) -> bool:
    part = re.sub(r"\bagain\b", "", part).strip()
    if part in _META_CLAUSE_EXACT or _CONTINUE_INVOCATION_RE.match(part):
        return True
    for trigger in _META_CLAUSE_CONTAINS:
        if trigger in part:
            remainder = part.replace(trigger, "", 1).strip(" ?/")
            if len(remainder) <= _META_CLAUSE_SLACK:
                return True
    return False


def _is_meta(norm: str) -> bool:
    if not norm or len(norm) > _META_MAX_CHARS:
        return False
    parts = [p.strip() for p in re.split(r"[.?!]", norm) if p.strip()]
    return bool(parts) and all(_is_meta_clause(p) for p in parts)


def _is_short_approval(norm: str) -> bool:
    if not norm:
        return False
    without_slash = _SLASH_TOKEN_RE.sub("", norm).strip()
    body = without_slash or norm.lstrip("/")
    return body in _APPROVAL_WORDS


def _is_noise(norm: str, raw: str) -> bool:
    if not raw.strip():
        return True
    if norm in _NOISE_EXACT:
        return True
    return norm.startswith(_NOISE_PREFIXES)


_COMMAND_NAME_RE = re.compile(r"<command-name>\s*([^<]+?)\s*</command-name>")
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def readable_operator_text(text: str) -> str:
    """A typed slash command reaches the transcript wrapped in tags
    (`<command-message>recall</command-message>`, then `<command-name>/recall
    </command-name>`, then `<command-args>...</command-args>`). Return it as
    the operator typed it, "/recall ..."; any other text comes back as is."""
    if not text.lstrip().startswith("<command-"):
        return text
    name = _COMMAND_NAME_RE.search(text)
    if name is None:
        return text
    command = name.group(1) if name.group(1).startswith("/") else "/" + name.group(1)
    args = _COMMAND_ARGS_RE.search(text)
    return f"{command} {args.group(1).strip() if args else ''}".strip()


def classify_operator_row(text: str) -> str:
    """META / SHORT_APPROVAL / NOISE / REQUEST for one operator row's text."""
    norm = text.strip().lower().rstrip(".!")
    if _is_meta(norm):
        return "META"
    if _is_short_approval(norm):
        return "SHORT_APPROVAL"
    if _is_noise(norm, text):
        return "NOISE"
    return "REQUEST"


# --------------------------------------------------------------------------- #
# Transcript scanning — one streaming pass
# --------------------------------------------------------------------------- #
@dataclass
class _OpRow:
    seq: int
    ts: str
    text: str
    kind: str
    closer_before: Any  # NextStep | None — the newest non-ABSENT closer seen
    #: so far, snapshotted at the moment this row was read.
    closer_seq: "int | None" = None


def _row_content(entry: dict):
    message = entry.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _row_text(entry: dict) -> str:
    return _flatten_content(_row_content(entry))


def _has_tool_result(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _scan_user_row(entry: dict, seq: int, state: dict, max_quote: int) -> None:
    """Fold one `type: "user"` transcript row into the running scan state."""
    if entry.get("isCompactSummary"):
        state["summary_count"] += 1
        state["summary_text"] = _row_text(entry)
        state["summary_ts"] = str(entry.get("timestamp") or "")
        return
    if entry.get("isMeta"):
        return
    content = _row_content(entry)
    if _has_tool_result(content):
        return
    if not is_human(entry):
        return
    text = readable_operator_text(_row_text(entry))
    row = _OpRow(
        seq=seq, ts=str(entry.get("timestamp") or ""), text=text[:max_quote],
        kind=classify_operator_row(text),
        closer_before=state["last_closer"][1] if state["last_closer"] else None,
        closer_seq=state["last_closer"][0] if state["last_closer"] else None,
    )
    state["op_rows"].append(row)
    if len(state["op_rows"]) > OPERATOR_ROW_WINDOW:
        state["op_rows"].pop(0)


def _scan_assistant_row(entry: dict, seq: int, state: dict) -> None:
    text = _row_text(entry)
    if not text.strip():
        return
    step = parse_next_step(text)
    if step.owner is not Owner.ABSENT:
        state["last_closer"] = (seq, step)


def _scan_transcript(path: Path, max_quote: int) -> dict:
    """One forward pass: bounded operator-row window + the newest closer and
    compaction summary seen anywhere in the file. Never raises — a missing or
    unreadable transcript, or a malformed line, degrades the result rather
    than failing the whole card."""
    state: dict = {
        "op_rows": [], "last_closer": None, "summary_count": 0,
        "summary_text": "", "summary_ts": "", "lines": 0, "malformed": 0,
    }
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"cannot open transcript {path}: {exc}"}
    with fh:
        for seq, raw in enumerate(fh):
            state["lines"] += 1
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, TypeError):
                state["malformed"] += 1
                continue
            if not isinstance(entry, dict):
                continue
            etype = entry.get("type")
            if etype == "attachment":
                continue
            if etype == "assistant":
                _scan_assistant_row(entry, seq, state)
            elif etype == "user":
                _scan_user_row(entry, seq, state, max_quote)
    state["ok"] = True
    return state


# --------------------------------------------------------------------------- #
# GOAL — the newest REQUEST, overridable by a newer SHORT_APPROVAL whose
# preceding closer names the real goal.
# --------------------------------------------------------------------------- #
def _newest(rows: list, kind: str):
    for row in reversed(rows):
        if row.kind == kind:
            return row
    return None


def _split_invocation(op_rows: "list[_OpRow]"):
    """(invocation | None, prior rows). The newest operator row is this
    card's own invocation only when it IS a continue trigger (META). When it
    is anything else -- a request, a "yes" -- the trigger is not in the file
    (the harness may run the injection before it writes the row, or the card
    is reading another session), and that row is a GOAL candidate like the
    rest. Excluding it unconditionally hid an approval that was the newest
    row, and a request that was."""
    if op_rows and op_rows[-1].kind == "META":
        return op_rows[-1], op_rows[:-1]
    return None, list(op_rows)


def _goal_from_session(op_rows: "list[_OpRow]"):
    """(goal_text, source, approved_dict | None, invocation | None) from THIS
    session's own kept operator rows, or (None, None, None, invocation) when
    no REQUEST is in the window."""
    invocation, prior = _split_invocation(op_rows)
    req = _newest(prior, "REQUEST")
    if req is None:
        return None, None, None, invocation
    approvals_after = [r for r in prior if r.seq > req.seq and r.kind == "SHORT_APPROVAL"]
    if approvals_after:
        newest_approval = max(approvals_after, key=lambda r: r.seq)
        if newest_approval.closer_before is not None:
            return (
                newest_approval.closer_before.text, "approval-closer",
                {"reply": newest_approval.text, "step": newest_approval.closer_before.text},
                invocation,
            )
    return req.text, "session-request", None, invocation


def _goal_from_previous_session(cwd: str, exclude: "Path | None", max_quote: int,
                                 claude_dir: "str | None" = None):
    """The newest OTHER transcript in this cwd's slug dir, scanned the same
    way. MEDIUM at best — it is not this session's own record.

    `claude_dir` threads through to `path_encoding.project_dir` exactly as it
    does for `transcript_for_session`, so a test can point this at a tmp_path
    projects root instead of the real `~/.claude`.

    Plain imports, not the lib./bare dance: `_LIB` (this file's own `lib/`
    directory) is already on `sys.path` by the time any function below runs,
    because the module-level `sys.path.insert` at the top of this file ran
    first regardless of how `continue_card` itself was imported."""
    from claude_dir import get_claude_dir
    from path_encoding import project_dir
    root = Path(claude_dir) if claude_dir else get_claude_dir()
    try:
        slug_dir = project_dir(cwd, claude_dir=root)
    except (OSError, ValueError):
        return None, None
    try:
        candidates = sorted(
            (p for p in slug_dir.glob("*.jsonl") if p != exclude),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
    except OSError:
        return None, None
    if not candidates:
        return None, None
    scan = _scan_transcript(candidates[0], max_quote)
    if not scan.get("ok"):
        return None, None
    text, _source, _approved, _invocation = _goal_from_session(scan["op_rows"])
    return (text, candidates[0]) if text else (None, None)


def _goal_from_recall(cwd: str):
    """(text | None, timed_out): `grip_recall.recall_all(..., quick=True)`'s
    `instance.next_up` / the cached plan's name, time-boxed to
    RECALL_TIMEOUT_S. The call runs on a daemon thread: an executor's
    shutdown waits for a hung call, and the interpreter joins executor
    workers at exit, so neither could keep the timebox."""
    import threading
    try:
        import grip_recall
    except ImportError:
        return None, False
    box: dict = {}

    def _run():
        try:
            box["results"] = grip_recall.recall_all(project_path=cwd, quick=True)
        except Exception:  # noqa: BLE001 — recall must never break the card
            box["results"] = None

    worker = threading.Thread(target=_run, name="continue-card-recall", daemon=True)
    worker.start()
    worker.join(RECALL_TIMEOUT_S)
    if worker.is_alive():
        return None, True
    return _recall_text(box.get("results")), False


def _recall_text(results) -> "str | None":
    if not isinstance(results, dict):
        return None
    instance = results.get("instance") or {}
    if instance.get("ok") and instance.get("next_up"):
        return str(instance["next_up"])
    last_plan = results.get("last_plan") or {}
    if last_plan.get("ok"):
        return (
            f"cached plan: {last_plan.get('name', 'unnamed')} "
            f"({last_plan.get('step_count', 0)} steps, {last_plan.get('age_hours', 0)}h old)"
        )
    return None


def resolve_goal(op_rows: "list[_OpRow]", cwd: str, transcript_path: "Path | None",
                  max_quote: int, claude_dir: "str | None" = None,
                  summary_text: str = "") -> dict:
    """GOAL resolution across the full fallback chain, in order: this
    session's rows, this session's compaction summary, the previous session,
    recall. Returns a dict naming the text, its source, any APPROVED pair,
    and which fallback tiers were actually reached (for SOURCES)."""
    trail = {"previous_session": False, "recall": False}
    text, source, approved, invocation = _goal_from_session(op_rows)
    if text:
        return {"text": text, "source": source, "approved": approved,
                "invocation": invocation, "trail": trail}

    # No REQUEST in the kept window. This session's own compaction summary
    # still states its request, so it outranks any other session.
    text = _summary_section(summary_text, _PRIMARY_REQUEST_RE)
    if text:
        return {"text": text, "source": "compaction-summary", "approved": None,
                "invocation": invocation, "trail": trail}

    trail["previous_session"] = True
    text, _other_path = _goal_from_previous_session(cwd, transcript_path, max_quote, claude_dir)
    if text:
        return {"text": text, "source": "previous-session", "approved": None,
                "invocation": invocation, "trail": trail}

    trail["recall"] = True
    text, timed_out = _goal_from_recall(cwd)
    if timed_out:
        trail["recall"] = f"timed out after {RECALL_TIMEOUT_S:g}s"
    if text:
        return {"text": text, "source": "recall", "approved": None,
                "invocation": invocation, "trail": trail}

    return {"text": None, "source": None, "approved": None,
            "invocation": invocation, "trail": trail}


# --------------------------------------------------------------------------- #
# STATE — probe every referent in GOAL + NEXT, plus the worktree's own state.
# --------------------------------------------------------------------------- #
def _no_network_probes(root: str = "") -> Probes:
    """gh/network refused; a path referent still resolves against `root`
    (`cwd`, never the ambient process cwd) -- '--no-probe skips gh and
    network, keeps git', and a local file check is neither."""
    def _skip_http(_url):
        raise RuntimeError("--no-probe: network disabled")

    def _skip_gh(_args):
        return 1, "", "--no-probe: gh disabled"

    return Probes(http_get=_skip_http, gh=_skip_gh, root=root)


_FACT_DONE_STATES = frozenset({"up", "exists", "merged"})
_FACT_OPEN_STATES = frozenset({"down", "absent", "open", "closed"})
_PROBE_NAME = {"pr": "gh pr view", "issue": "gh issue view", "named_pr": "gh pr/issue view",
               "repo": "gh repo view", "url": "HTTP GET", "path": "file check"}
#: A '<Name>#<N>' that could not be resolved is either a PR or an issue.
_KIND_LABEL = {"named_pr": "PR/ISSUE"}


def _fact_label(fact: ProbeFact) -> str:
    if not fact.ok:
        return "UNKNOWN"
    if fact.state in _FACT_DONE_STATES:
        return "DONE"
    if fact.referent.kind == "issue" and fact.state == "closed":
        return "DONE"  # an issue's closed state is its done state; a PR's is not
    if fact.state in _FACT_OPEN_STATES:
        return "OPEN"
    return "UNKNOWN"


def _fact_name(referent) -> str:
    """The referent as the reader knows it: its own text ('GRIP#9069') when
    that is one token, else the canonical value -- a whole gh command found
    in a closer reads as 'grip-platform/GRIP#9069', and a bare number as
    '#9069'."""
    raw = referent.raw
    if raw and not any(ch.isspace() for ch in raw):
        return raw
    return f"#{referent.value}" if referent.value.isdigit() else referent.value


def _render_fact(fact: ProbeFact) -> str:
    kind = _KIND_LABEL.get(fact.referent.kind, fact.referent.kind.upper())
    probe = _PROBE_NAME.get(fact.referent.kind, "probe")
    return f"{_fact_name(fact.referent)} {kind} {fact.state.upper()} ({probe})"


def _count_from_title(candidates, candidate_id: str) -> "int | None":
    for c in candidates:
        if c.id == candidate_id:
            m = re.search(r"\d+", c.title)
            return int(m.group()) if m else None
    return None


def _worktree_line(cwd: str) -> str:
    try:
        candidates = next_move.session_candidates(cwd)
    except Exception:  # noqa: BLE001 — git state must never break the card
        candidates = []
    dirty = _count_from_title(candidates, "session:dirty") or 0
    unpushed = _count_from_title(candidates, "session:unpushed") or 0
    try:
        tree = shared_tree_state.tree_state(Path(cwd), "origin/main")
        branch = tree.branch or "?"
        behind = tree.behind
    except Exception:  # noqa: BLE001
        branch, behind = "?", None
    behind_text = f"{behind} behind origin/main" if behind is not None else "behind unknown"
    return f"worktree {branch}: {dirty} dirty, {unpushed} unpushed, {behind_text} (vs last fetch)"


def render_state(goal_facts: "list[ProbeFact]", next_facts: "list[ProbeFact]", cwd: str) -> str:
    seen: dict = {}
    for fact in (*goal_facts, *next_facts):
        seen.setdefault((fact.referent.kind, fact.referent.value), fact)
    parts = [_render_fact(fact) for fact in seen.values()]
    parts.append(_worktree_line(cwd))
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# NEXT — the closer the invocation row answered, or the newest in the file.
# --------------------------------------------------------------------------- #
def _one_line(text: str) -> str:
    """Every run of whitespace, newlines included, as one space: the card is
    one field per line, and a multi-line quote would break that."""
    return " ".join(text.split())


def _quote(text: str, max_quote: int) -> str:
    """Words in double quotes, on one line, cut to `max_quote`. Text that
    already opens with a double quote is shown as it stands, never doubled."""
    cut = _one_line(text)[:max_quote]
    return cut if cut.startswith('"') else f'"{cut}"'


def _next_closer(invocation, scan: dict):
    """(NextStep | None, its row seq | None): the closer the invocation row
    answered, or, with no invocation row in the file, the newest closer."""
    if invocation is not None:
        return invocation.closer_before, invocation.closer_seq
    last = scan.get("last_closer")
    return (last[1], last[0]) if last else (None, None)


def _next_already_done(next_facts: "list[ProbeFact]", next_verdict) -> bool:
    if not next_facts or next_verdict.verdict != Verdict.DONE:
        return False
    return all(f.ok and _fact_label(f) == "DONE" for f in next_facts)


def _after_next(op_rows: "list[_OpRow]", invocation, closer_seq: "int | None",
                 max_quote: int) -> str:
    if closer_seq is None:
        return "nothing recorded after it"
    after = [r for r in op_rows if r is not invocation and r.seq > closer_seq]
    if not after:
        return "nothing recorded after it"
    return f"next said: {_quote(after[-1].text, max_quote)}"


def render_next(op_rows: "list[_OpRow]", invocation, closer, next_facts: "list[ProbeFact]",
                 next_verdict, max_quote: int) -> str:
    step, step_seq = closer
    if step is None:
        return "no closer recorded in this session"
    owner = f"({step.owner.value if step.owner is not Owner.THIRD_PARTY else step.party})"
    base = f"{owner} {_one_line(step.text)[:max_quote]}".strip()
    if _next_already_done(next_facts, next_verdict):
        done_fact = next((f for f in next_facts if _fact_label(f) == "DONE"), None)
        evidence = f"{_fact_name(done_fact.referent)} {done_fact.state.upper()}" if done_fact else "done"
        after = _after_next(op_rows, invocation, step_seq, max_quote)
        return f"{base} -- already done ({evidence}); {after}"
    return base


# --------------------------------------------------------------------------- #
# SUMMARY, CONFIDENCE, SOURCES
# --------------------------------------------------------------------------- #
#: The "next" heading of a compaction summary: "Next:", "**Next:**", and the
#: numbered "9. Optional Next Step:" section Claude Code's own summaries use.
_NEXT_PARA_RE = re.compile(
    r"(?im)^[ \t]*(?:\d+\.[ \t]*)?(?:\*\*)?(?:optional[ \t]+)?next(?:[ \t]+steps?)?:"
    r"(?:\*\*)?[ \t]*(.*)$"
)


#: The request heading of a compaction summary: "1. Primary Request and
#: Intent:" in Claude Code's own summaries, or a plain "Primary request:".
_PRIMARY_REQUEST_RE = re.compile(
    r"(?im)^[ \t]*(?:\d+\.[ \t]*)?(?:\*\*)?primary[ \t]+request(?:[ \t]+and[ \t]+intent)?:"
    r"(?:\*\*)?[ \t]*(.*)$"
)


def _summary_section(text: str, heading: "re.Pattern[str]") -> str:
    """The paragraph under `heading` in a compaction summary, on one line,
    capped; "" when the summary has no such heading."""
    m = heading.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    if not m.group(1).strip():  # a heading line: the paragraph starts below it
        rest = rest.lstrip("\n")
    # up to the next blank line or markdown heading, capped, on one line.
    stop = len(rest)
    for marker in ("\n\n", "\n#"):
        idx = rest.find(marker)
        if idx != -1:
            stop = min(stop, idx)
    return _one_line(f"{m.group(1)} {rest[:stop]}")[:SUMMARY_NEXT_CAP]


def render_summary(scan: dict) -> str:
    if not scan.get("summary_count"):
        return "no compaction summary in this transcript"
    para = _summary_section(scan["summary_text"], _NEXT_PARA_RE)
    body = f'Next: {para}' if para else "(no Next: paragraph in the summary)"
    return f"{body} (compaction summary {scan['summary_count']} of {scan['summary_count']}, {scan['summary_ts']})"


def _probe_note(next_facts: "list[ProbeFact]") -> str:
    """What the NEXT probes establish -- never "every probe answered" when
    no probe ran, which would claim a check that never happened."""
    if not next_facts:
        return "the next step names nothing a probe can check"
    return f"{len(next_facts)} of {len(next_facts)} NEXT probes answered"


def resolve_confidence(goal: dict, closer_step, next_facts: "list[ProbeFact]") -> str:
    if not goal.get("text"):
        return "LOW -- no goal found in this session, a previous session, or recall"
    if goal.get("source") not in ("session-request", "approval-closer"):
        return f"MEDIUM -- goal came from {goal.get('source')}, not this session's own request"
    unknown_next = any(not f.ok for f in next_facts)
    if closer_step is not None and not unknown_next:
        via = "via an approval " if goal.get("source") == "approval-closer" else ""
        return (f"HIGH -- goal {via}and next step both come from this session; "
                f"{_probe_note(next_facts)}")
    bits = []
    if closer_step is None:
        bits.append("no closer recorded for the next step")
    if unknown_next:
        bits.append("a NEXT probe came back unknown")
    return "MEDIUM -- " + "; ".join(bits)


def render_sources(scan: dict, goal: dict, no_probe: bool, sid_display: str) -> str:
    label = "transcript" if not goal["trail"].get("no_session_id") else \
        "transcript (no session id -- newest in the cwd slug dir)"
    read = [f"{label} {sid_display} ({scan.get('lines', 0):,} lines)", "closers", "git"]
    if scan.get("summary_count"):
        read.append("compaction summary")
    if goal["trail"].get("previous_session"):
        read.append("previous session transcript")
    recall = goal["trail"].get("recall")
    if recall is True:
        read.append("recall")
    not_read = ["ultragoal (no public call discovers an armed goal without its path)"]
    if isinstance(recall, str):
        not_read.append(f"recall ({recall})")
    if no_probe:
        not_read.append("gh/network (--no-probe)")
    return f"read: {', '.join(read)}; not read: {', '.join(not_read)}"


def render_goal(goal: dict, max_quote: int) -> str:
    if not goal.get("text"):
        return "not found -- ask the operator"
    quoted = _quote(goal["text"], max_quote)
    source = {
        "session-request": "this session",
        "approval-closer": "this session, via an approval",
        "compaction-summary": "this session's compaction summary -- MEDIUM at best",
        "previous-session": "a previous session -- MEDIUM at best",
        "recall": "recall",
    }.get(goal.get("source"), goal.get("source") or "unknown")
    return f"{quoted} ({source})"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _resolve_transcript_path(transcript_path: "str | None", session_id: "str | None",
                              cwd: str, claude_dir: "str | None" = None) -> "tuple[Path | None, bool]":
    """(path, used_no_session_fallback). Never raises."""
    if transcript_path:
        p = Path(transcript_path)
        return (p if p.is_file() else None), False
    found = transcript_text.transcript_for_session(
        session_id or None, cwd=cwd, claude_dir=claude_dir)
    return found, not bool(session_id)


def _low_confidence_card(reason: str) -> dict:
    """The card for every path that cannot build one at all -- a missing or
    unreadable transcript, or a genuine internal error. `reason` says which,
    verbatim, in both SOURCES and CONFIDENCE -- never a generic phrase that
    would call a simple absence of data "an internal error"."""
    return {
        "goal": "not found -- ask the operator",
        "state": "unknown -- see SOURCES",
        "next": "no closer recorded in this session",
        "summary": "no compaction summary in this transcript",
        "sources": f"read: nothing; not read: everything -- {reason}",
        "confidence": f"LOW -- {reason}",
    }


def build_card(
    *,
    session_id: "str | None" = None,
    transcript_path: "str | None" = None,
    cwd: "str | None" = None,
    no_probe: bool = False,
    max_quote: int = DEFAULT_MAX_QUOTE,
    probes: "Probes | None" = None,
    claude_dir: "str | None" = None,
) -> dict:
    """The whole card, as a dict with lowercase field keys. Never raises —
    any internal failure degrades to `_low_confidence_card` instead.

    `probes` is the DIP seam `task_reality_reconcile.Probes` already defines —
    pass one to test a specific DONE/OPEN/UNKNOWN outcome hermetically,
    without a real `gh` or network call. Left as `None` (the normal case),
    the real backends are used, or a network-refusing stand-in under
    `no_probe`. `claude_dir` overrides where the transcript/recall/previous-
    session fallbacks look for `projects/` — real `~/.claude` when left
    `None`; a test passes a tmp_path.
    """
    cwd = cwd or os.getcwd()
    try:
        resolved, no_session = _resolve_transcript_path(
            transcript_path, session_id, cwd, claude_dir)
        if resolved is None:
            return _low_confidence_card(f"no transcript found for cwd {cwd}")

        scan = _scan_transcript(resolved, max_quote)
        if not scan.get("ok"):
            return _low_confidence_card(scan.get("error", "transcript scan failed"))

        goal = resolve_goal(scan["op_rows"], cwd, resolved, max_quote, claude_dir,
                            summary_text=scan.get("summary_text", ""))
        if no_session:
            goal["trail"]["no_session_id"] = True
        invocation = goal.get("invocation")
        closer = _next_closer(invocation, scan)
        next_step = closer[0]

        if probes is None:
            probes = _no_network_probes(cwd) if no_probe else Probes(root=cwd)
        goal_facts = probe_task(goal.get("text") or "", probes)
        next_facts = probe_task(next_step.text, probes) if next_step else []
        next_verdict = classify_task("next", next_step.text if next_step else "", next_facts)

        card: dict = {"goal": render_goal(goal, max_quote)}
        if goal.get("approved"):
            card["approved"] = (
                f'{_quote(goal["approved"]["reply"], max_quote)} approved: '
                f'{_one_line(goal["approved"]["step"])[:max_quote]}'
            )
        card["state"] = render_state(goal_facts, next_facts, cwd)
        card["next"] = render_next(
            scan["op_rows"], invocation, closer, next_facts, next_verdict, max_quote)
        card["summary"] = render_summary(scan)
        # Name the file actually read, never an ambient session id: with
        # --transcript the two differ, and SOURCES must say what was read.
        card["sources"] = render_sources(scan, goal, no_probe, resolved.stem[:8] or "unknown")
        card["confidence"] = resolve_confidence(goal, next_step, next_facts)
        return card
    except Exception as exc:  # noqa: BLE001 — the whole point: never crash the skill
        return _low_confidence_card(f"{type(exc).__name__}: {exc}")


def render_text(card: dict) -> str:
    return "\n".join(f"{key.upper():<{LABEL_WIDTH}}{value}" for key, value in card.items())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
class _ArgumentError(Exception):
    """A bad command line, turned into a LOW card instead of an exit 2."""


class _CardArgumentParser(argparse.ArgumentParser):
    def error(self, message):  # argparse's own error() calls sys.exit(2)
        raise _ArgumentError(message)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _build_parser() -> argparse.ArgumentParser:
    p = _CardArgumentParser(description=__doc__)
    p.add_argument("--session")
    p.add_argument("--transcript")
    p.add_argument("--cwd")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-probe", action="store_true")
    p.add_argument("--max-quote", type=_positive_int, default=DEFAULT_MAX_QUOTE)
    return p


def main(argv: "list[str] | None" = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentError as exc:
        print(render_text(_low_confidence_card(f"bad arguments: {exc}")))
        return 0  # ALWAYS -- a typo in the skill's command must not abort it
    session_id = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID") or \
        os.environ.get("CLAUDE_SESSION_ID")
    card = build_card(
        session_id=session_id, transcript_path=args.transcript,
        cwd=args.cwd or os.getcwd(), no_probe=args.no_probe, max_quote=args.max_quote,
    )
    if args.json:
        print(json.dumps(card, indent=2))
    else:
        print(render_text(card))
    return 0  # ALWAYS — see the module docstring.


if __name__ == "__main__":
    sys.exit(main())
