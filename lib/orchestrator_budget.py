#!/usr/bin/env python3
"""Orchestrator budget — where did the MAIN session's tool-driven context go?

Reads one Claude Code session transcript (JSONL) and reports, for the main
session only, how much context the orchestrator spent on two things:

  reading   — Read / Grep / Glob, plus Bash commands that only read
              (cat, sed -n, rg, git log, gh pr view, ...). This is the
              orchestrator reading the world directly.
  dispatch  — Agent / Workflow / TaskOutput: the briefs it wrote, the launch
              stubs and reports that came back, and the <task-notification>
              messages that deliver a background agent's result.

Everything else a tool call produced (Edit, Write, mutating Bash, SendMessage,
...) is reported under `other` so nothing is hidden, but it is not part of the
ratio. Subagent records (`isSidechain: true`) are skipped: a subagent's own
reads are exactly the reads this measurement wants moved OUT of the main
window. System-prompt and hook injections (`attachment` records) are out of
scope here; `/context-forensics` decomposes those.

This is a MEASUREMENT, not a gate. It exists so that
`rules/orchestrator-context-is-planning-capacity.md` has a number in its
falsification clause. Per `rules/knowledge-maturation-functor.md` a shim
waits for N=3, and per `rules/gate-resolution-escape-hatch.md` a gate on
reading would deny the operations that resolve almost any blocked condition.

Estimation: tokens = characters / CHARS_PER_TOKEN (default 4.0). The API's
usage figures are per request, not per tool result, so per-tool attribution
has to come from content size. The RATIO between the two buckets is what the
rule tracks, and the constant cancels out of it (tested). Absolute figures are
estimates and are labelled as such in the report.

Usage:
    python3 lib/orchestrator_budget.py <session.jsonl> [--json]
                                       [--chars-per-token 4.0]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

CHARS_PER_TOKEN = 4.0

READING_TOOLS = frozenset({"Read", "Grep", "Glob"})
DISPATCH_TOOLS = frozenset({"Agent", "Workflow", "TaskOutput"})

BUCKET_READING = "reading"
BUCKET_DISPATCH = "dispatch"
BUCKET_OTHER = "other"

# --- Bash classification -----------------------------------------------------
# A command is "read" only when EVERY pipeline segment's verb is read-shaped
# and nothing is redirected to a file. Unknown verbs are "other", so the
# reading bucket is a LOWER bound; Bash(other) is reported so the undercount is
# visible rather than silent.
_READ_VERBS = frozenset({
    "cat", "head", "tail", "sed", "awk", "grep", "egrep", "fgrep", "rg", "ag",
    "ls", "tree", "wc", "find", "fd", "stat", "file", "du", "df", "which",
    "type", "echo", "printf", "jq", "yq", "diff", "sort", "uniq", "cut", "tr",
    "column", "nl", "bat", "less", "more", "pwd", "env", "printenv", "date",
    "whoami", "uname", "test", "[", "true", "realpath", "readlink", "dirname",
    "basename", "shasum", "sha256sum", "md5", "md5sum", "cd", "strings", "od",
    "xxd", "hexdump", "cmp", "comm", "tac", "rev", "seq", "expr", "cal",
})
_WRAPPERS = frozenset({"rtk", "time", "nice", "command", "builtin"})
_WRAPPERS_WITH_ARG = frozenset({"timeout", "gtimeout"})
_GIT_READ = frozenset({
    "status", "log", "diff", "show", "rev-parse", "rev-list", "ls-files",
    "ls-remote", "ls-tree", "blame", "describe", "cat-file", "merge-base",
    "name-rev", "shortlog", "grep", "check-ignore", "reflog", "count-objects",
    "var", "whatchanged", "for-each-ref", "show-ref", "symbolic-ref",
})
_GIT_BRANCH_MUTATORS = frozenset({"-d", "-D", "-m", "-M", "-c", "-C", "--delete",
                                  "--move", "--copy", "-u", "--set-upstream-to",
                                  "--unset-upstream", "-f", "--force"})
# Bare `git stash` pushes and bare `git worktree` errors; only the listed
# sub-arguments are reads. Bare `git remote` / `git tag` list, so "" is a read.
_GIT_SUB_READ_ARGS = {
    "stash": frozenset({"list", "show"}),
    "worktree": frozenset({"list"}),
    "remote": frozenset({"", "-v", "show", "get-url"}),
    "tag": frozenset({"", "-l", "--list"}),
}
_GH_READ = frozenset({
    "pr view", "pr checks", "pr list", "pr diff", "pr status",
    "run list", "run view", "issue view", "issue list", "repo view",
    "release list", "release view", "search", "auth status", "api",
})
_GH_API_BODY_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
_FIND_MUTATORS = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir"})
_NULL_REDIRECTS = re.compile(r"(?:\d?>>?|&>)\s*/dev/null|2>&1")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_pipeline(cmd: str) -> tuple[list[str], bool]:
    """Split on UNQUOTED |, ||, &&, ;, & — return (segments, saw_file_redirect).

    Quote-aware so `rg 'a|b'` is one segment. `>` outside quotes marks a
    write; `/dev/null` and `2>&1` forms are scrubbed before the scan.
    """
    scrubbed = _NULL_REDIRECTS.sub(" ", cmd)
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    redirect = False
    i = 0
    while i < len(scrubbed):
        ch = scrubbed[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"':
                buf.append(ch)
                i += 1
                ch = scrubbed[i] if i < len(scrubbed) else ""
            buf.append(ch)
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ">":
            redirect = True
        elif ch in "|;&":
            segments.append("".join(buf))
            buf = []
            if i + 1 < len(scrubbed) and scrubbed[i + 1] in "|&":
                i += 1
        else:
            buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()], redirect


def _argv(segment: str) -> list[str]:
    """Tokens of one segment with env assignments and wrappers stripped."""
    tokens = segment.split()
    while tokens and _ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    while tokens:
        verb = os.path.basename(tokens[0])
        if verb in _WRAPPERS:
            tokens = tokens[1:]
        elif verb in _WRAPPERS_WITH_ARG:
            tokens = tokens[2:]
        else:
            break
    return tokens


def _git_is_read(args: list[str]) -> bool:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
    if not rest:
        return False
    sub, tail = rest[0], rest[1:]
    if sub in _GIT_READ:
        return True
    if sub == "branch":
        return all(t.startswith("-") for t in tail) and not (set(tail) & _GIT_BRANCH_MUTATORS)
    if sub in _GIT_SUB_READ_ARGS:
        return (tail[:1] or [""])[0] in _GIT_SUB_READ_ARGS[sub]
    if sub == "config":
        positional = [t for t in tail if not t.startswith("-")]
        flags = {t for t in tail if t.startswith("-")}
        return bool(flags & {"--get", "--list", "-l", "--get-all"}) or (
            len(positional) == 1 and not flags)
    return False


def _gh_api_is_read(args: list[str]) -> bool:
    """`gh api` is a read unless it names a non-GET method or sends a body."""
    for i, tok in enumerate(args):
        if tok in ("-X", "--method"):
            if i + 1 >= len(args) or args[i + 1].upper() != "GET":
                return False
        elif tok in _GH_API_BODY_FLAGS:
            return False
    return True


def _gh_is_read(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] == "api":
        return _gh_api_is_read(args[1:])
    if args[0] == "search":
        return True
    return " ".join(args[:2]) in _GH_READ


def _segment_is_read(segment: str) -> bool:
    argv = _argv(segment)
    if not argv:
        return False
    verb = os.path.basename(argv[0])
    if verb == "git":
        return _git_is_read(argv[1:])
    if verb == "gh":
        return _gh_is_read(argv[1:])
    if verb == "sed":
        return not any(a == "-i" or a.startswith("-i") for a in argv[1:])
    if verb == "find":
        return not (set(argv[1:]) & _FIND_MUTATORS)
    return verb in _READ_VERBS


def classify_bash(command: str) -> str:
    """Return "read" for a command that only reads, else "other".

    Heredocs are scripts of unknown effect and classify as "other".
    """
    cmd = (command or "").strip()
    if not cmd or "<<" in cmd:
        return BUCKET_OTHER
    segments, redirect = _split_pipeline(cmd)
    if redirect or not segments:
        return BUCKET_OTHER
    return "read" if all(_segment_is_read(s) for s in segments) else BUCKET_OTHER


# --- transcript walk ---------------------------------------------------------

@dataclass
class _Call:
    tool_id: str
    name: str
    label: str
    bucket: str
    call_chars: int
    result_chars: int = 0
    in_flight_at_call: int = 0
    inflight_ids: frozenset = frozenset()
    task_id: str | None = None
    is_async: bool = False


@dataclass
class _Walk:
    calls: dict[str, _Call] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    inflight: set[str] = field(default_factory=set)
    notification_chars: int = 0
    notifications: int = 0
    usage_by_request: dict[str, dict] = field(default_factory=dict)
    main_records: int = 0
    sidechain_records: int = 0
    malformed: int = 0
    session_id: str | None = None


def _iter_records(path: Path) -> Iterator[dict | None]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                yield None
                continue
            yield obj if isinstance(obj, dict) else None


def _content_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _label_and_bucket(name: str, tool_input: dict) -> tuple[str, str]:
    if name in READING_TOOLS:
        return name, BUCKET_READING
    if name in DISPATCH_TOOLS:
        return name, BUCKET_DISPATCH
    if name == "Bash":
        kind = classify_bash(str(tool_input.get("command", "")))
        return f"Bash({kind})", BUCKET_READING if kind == "read" else BUCKET_OTHER
    return name, BUCKET_OTHER


def _is_async_launch(tur, text: str) -> bool:
    if isinstance(tur, dict):
        if tur.get("isAsync") is True or tur.get("status") == "async_launched":
            return True
    low = text[:200].lower()
    return "launched in background" in low or "async agent launched" in low


def _on_assistant(walk: _Walk, rec: dict) -> None:
    msg = rec.get("message") or {}
    req = rec.get("requestId")
    usage = msg.get("usage")
    if req and isinstance(usage, dict):
        walk.usage_by_request[req] = usage
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name", ""))
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        label, bucket = _label_and_bucket(name, tool_input)
        call = _Call(tool_id=str(block.get("id")), name=name, label=label, bucket=bucket,
                     call_chars=len(json.dumps(tool_input, ensure_ascii=False)),
                     in_flight_at_call=len(walk.inflight),
                     inflight_ids=frozenset(walk.inflight))
        walk.calls[call.tool_id] = call
        walk.order.append(call.tool_id)


_TASK_ID_RE = re.compile(r"<task[-_]id>\s*([^<\s]+)\s*</task[-_]id>")
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
_STATUS_RE = re.compile(r"<status>\s*([^<\s]+)\s*</status>")
_TERMINAL = frozenset({"completed", "failed", "killed", "cancelled", "canceled", "error"})


def _resolve_task(walk: _Walk, task_id: str | None) -> None:
    for tid in list(walk.inflight):
        if walk.calls[tid].task_id == task_id:
            walk.inflight.discard(tid)


def _on_tool_result(walk: _Walk, rec: dict, block: dict) -> None:
    call = walk.calls.get(str(block.get("tool_use_id")))
    if call is None:
        return
    text = _content_text(block.get("content"))
    call.result_chars += len(text)
    tur = rec.get("toolUseResult")
    if call.name in ("Agent", "Workflow") and _is_async_launch(tur, text):
        call.is_async = True
        if isinstance(tur, dict):
            call.task_id = tur.get("agentId") or tur.get("taskId")
        walk.inflight.add(call.tool_id)
    elif call.name == "TaskOutput":
        status = _STATUS_RE.search(text)
        if status and status.group(1) in _TERMINAL:
            m = _TASK_ID_RE.search(text)
            _resolve_task(walk, m.group(1) if m else None)


def _on_notification(walk: _Walk, text: str) -> None:
    walk.notifications += 1
    walk.notification_chars += len(text)
    m = _TOOL_USE_ID_RE.search(text)
    if m and m.group(1) in walk.inflight:
        walk.inflight.discard(m.group(1))
        return
    m = _TASK_ID_RE.search(text)
    if m:
        _resolve_task(walk, m.group(1))


def _on_user(walk: _Walk, rec: dict) -> None:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            _on_tool_result(walk, rec, block)
        elif block.get("type") == "text" and block.get("text", "").lstrip().startswith(
                "<task-notification>"):
            _on_notification(walk, block["text"])


def _walk(path: Path) -> _Walk:
    walk = _Walk()
    for rec in _iter_records(path):
        if rec is None:
            walk.malformed += 1
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        if rec.get("isSidechain") is True:
            walk.sidechain_records += 1
            continue
        walk.main_records += 1
        walk.session_id = walk.session_id or rec.get("sessionId")
        if rec["type"] == "assistant":
            _on_assistant(walk, rec)
        else:
            _on_user(walk, rec)
    return walk


# --- report ------------------------------------------------------------------

def _bucket_totals(walk: _Walk, cpt: float) -> tuple[dict, list[dict]]:
    buckets = {b: {"calls": 0, "call_tokens": 0.0, "result_tokens": 0.0}
               for b in (BUCKET_READING, BUCKET_DISPATCH, BUCKET_OTHER)}
    per_tool: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "call_tokens": 0.0, "result_tokens": 0.0})
    for tid in walk.order:
        c = walk.calls[tid]
        for row in (buckets[c.bucket], per_tool[c.label]):
            row["calls"] += 1
            row["call_tokens"] += c.call_chars / cpt
            row["result_tokens"] += c.result_chars / cpt
        per_tool[c.label]["bucket"] = c.bucket
    buckets[BUCKET_DISPATCH]["result_tokens"] += walk.notification_chars / cpt
    buckets[BUCKET_DISPATCH]["notifications"] = walk.notifications
    for row in buckets.values():
        row["tokens"] = row["call_tokens"] + row["result_tokens"]
    tools = [{"tool": k, **v, "tokens": v["call_tokens"] + v["result_tokens"]}
             for k, v in per_tool.items()]
    tools.sort(key=lambda r: -r["tokens"])
    return buckets, tools


def _posture(walk: _Walk, cpt: float) -> dict:
    """Reads split by whether a dispatch was out at the time.

    `reads_while_a_later_resolved_dispatch_in_flight` is the truncation-robust
    variant: it credits a read only when a dispatch that DID return its report
    later in the transcript was in flight. A dispatch that never resolves (a
    killed agent, a truncated transcript, a completion shape this parser does
    not recognise) would otherwise tag every later read as in-flight.
    """
    busy = {"calls": 0, "result_tokens": 0.0}
    strict = {"calls": 0, "result_tokens": 0.0}
    idle = {"calls": 0, "result_tokens": 0.0}
    resolved = {tid for tid, c in walk.calls.items() if c.is_async and tid not in walk.inflight}
    dispatches = async_n = 0
    for tid in walk.order:
        c = walk.calls[tid]
        if c.bucket == BUCKET_DISPATCH and c.name in ("Agent", "Workflow"):
            dispatches += 1
            async_n += int(c.is_async)
        if c.bucket != BUCKET_READING:
            continue
        rows = [busy] if c.in_flight_at_call > 0 else [idle]
        if c.inflight_ids & resolved:
            rows.append(strict)
        for row in rows:
            row["calls"] += 1
            row["result_tokens"] += c.result_chars / cpt
    return {"dispatches": dispatches, "async_dispatches": async_n,
            "reads_while_dispatch_in_flight": busy,
            "reads_while_a_later_resolved_dispatch_in_flight": strict,
            "reads_with_nothing_in_flight": idle,
            "unresolved_at_end": len(walk.inflight)}


def _api_usage(walk: _Walk) -> dict:
    peak = 0
    out = 0
    for u in walk.usage_by_request.values():
        ctx = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                 "cache_read_input_tokens"))
        peak = max(peak, ctx)
        out += int(u.get("output_tokens") or 0)
    return {"requests": len(walk.usage_by_request), "peak_context_tokens": peak,
            "output_tokens": out}


def measure(path: str | Path, chars_per_token: float = CHARS_PER_TOKEN) -> dict:
    """Measure one transcript. Returns a JSON-serialisable report."""
    path = Path(path)
    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    walk = _walk(path)
    buckets, tools = _bucket_totals(walk, chars_per_token)
    reading = buckets[BUCKET_READING]["tokens"]
    dispatch = buckets[BUCKET_DISPATCH]["tokens"]
    total = reading + dispatch + buckets[BUCKET_OTHER]["tokens"]
    return {
        "transcript": str(path),
        "session_id": walk.session_id,
        "main_session_records": walk.main_records,
        "sidechain_records_skipped": walk.sidechain_records,
        "malformed_lines": walk.malformed,
        "chars_per_token": chars_per_token,
        "estimate_note": "token figures are chars/chars_per_token estimates; "
                         "the ratio is independent of the constant",
        "buckets": buckets,
        "tools": tools,
        "ratio": {
            "reading_to_dispatch": (reading / dispatch) if dispatch else None,
            "reading_share_of_tool_context": (reading / total) if total else None,
        },
        "posture": _posture(walk, chars_per_token),
        "api_usage": _api_usage(walk),
    }


def format_report(rep: dict) -> str:
    b, r, p, u = rep["buckets"], rep["ratio"], rep["posture"], rep["api_usage"]
    ratio = "n/a (no dispatch)" if r["reading_to_dispatch"] is None else f"{r['reading_to_dispatch']:.2f}"
    share = "n/a" if r["reading_share_of_tool_context"] is None else f"{r['reading_share_of_tool_context']:.0%}"
    lines = [
        f"orchestrator budget — {rep['transcript']}",
        f"session {rep['session_id']}  main records {rep['main_session_records']}  "
        f"sidechain skipped {rep['sidechain_records_skipped']}  "
        f"malformed {rep['malformed_lines']}",
        f"estimate: chars/{rep['chars_per_token']:g} per token (the ratio does not depend on it)",
        "",
        f"{'bucket':<10}{'calls':>7}{'wrote':>12}{'read back':>12}{'total':>12}",
    ]
    for name in (BUCKET_READING, BUCKET_DISPATCH, BUCKET_OTHER):
        row = b[name]
        lines.append(f"{name:<10}{row['calls']:>7}{row['call_tokens']:>12,.0f}"
                     f"{row['result_tokens']:>12,.0f}{row['tokens']:>12,.0f}")
    lines += [
        "",
        f"reading : dispatch = {ratio}    reading share of tool context = {share}",
        f"dispatches {p['dispatches']} ({p['async_dispatches']} background), "
        f"notifications read {b[BUCKET_DISPATCH].get('notifications', 0)}, "
        f"unresolved at end {p['unresolved_at_end']}",
        f"reads while a dispatch was in flight: {p['reads_while_dispatch_in_flight']['calls']} calls, "
        f"{p['reads_while_dispatch_in_flight']['result_tokens']:,.0f} tokens read back",
        f"  of which while a dispatch that later returned was in flight: "
        f"{p['reads_while_a_later_resolved_dispatch_in_flight']['calls']} calls, "
        f"{p['reads_while_a_later_resolved_dispatch_in_flight']['result_tokens']:,.0f} tokens read back",
        f"reads with nothing in flight:        {p['reads_with_nothing_in_flight']['calls']} calls, "
        f"{p['reads_with_nothing_in_flight']['result_tokens']:,.0f} tokens read back",
        f"api: {u['requests']} requests, peak context {u['peak_context_tokens']:,} tokens, "
        f"{u['output_tokens']:,} output tokens",
        "",
        f"{'tool':<16}{'bucket':<10}{'calls':>7}{'total tokens':>14}",
    ]
    for row in rep["tools"]:
        lines.append(f"{row['tool']:<16}{row['bucket']:<10}{row['calls']:>7}{row['tokens']:>14,.0f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transcript", help="session transcript .jsonl")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--chars-per-token", type=float, default=CHARS_PER_TOKEN,
                    help=f"estimator constant (default {CHARS_PER_TOKEN})")
    args = ap.parse_args(argv)
    path = Path(args.transcript)
    if not path.is_file():
        print(f"error: no such transcript: {path}", file=sys.stderr)
        return 2
    rep = measure(path, chars_per_token=args.chars_per_token)
    print(json.dumps(rep, indent=2) if args.json else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
