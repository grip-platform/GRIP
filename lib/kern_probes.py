#!/usr/bin/env python3
"""Live probes of the Claude Code behaviour KERN v2 relies on (phase 1).

KERN v2 routes rules through hooks: several SessionStart hooks carry the
main-session rules, a PreToolUse hook on the Agent tool appends a task's rules to
a subagent's prompt through ``updatedInput``, and rules can ride a command or a
file path. Before any rule's native loading is removed (the plan's first safety
invariant), each probe asks Claude Code whether it really behaves that way.

Every probe runs ``claude -p`` in an empty Claude Code directory, signed in with
a stored token (lib/claude_as.py), with only the probe's own files and hooks:
nothing of GRIP's settings, hooks, CLAUDE.md or rules loads, and the shell
environment is cut to PATH, HOME, LANG and TMPDIR. Every probe has a control. A
NEGATIVE control (the rule withheld) must fail; a POSITIVE control (the same rule
loaded natively) must pass. A probe whose control misbehaves reports INVALID,
because its method, not the behaviour, is then in doubt.

  chunks       three SessionStart hooks, each value under 10,000 characters, all
               reach the model. Negative: a value over 10,000 characters, whose
               secret sits past its first 2,000, does not.
  brief        a PreToolUse hook on Agent appends a rule through updatedInput,
  brief+allow  alone and paired with "allow"; an Explore subagent quotes it and a
  brief-plan   SubagentStart hook confirms its type survived (brief-plan: a Plan
               subagent). Negative: the hook appends nothing.
  two-writers  two PreToolUse hooks on Agent each append a rule; both reach the
               subagent. Negative: the second appends nothing, so only one does.
  command      a rule handed over as PreToolUse additionalContext on Bash shapes the
               FIRST command it is about. Positive: the rule loaded natively does.
  file-write   a rule with ``paths:`` frontmatter shapes the FIRST write of a new
               matching file. Positive: the rule loaded natively does.
  file-edit    the same rule shapes an edit made after reading the matching file.
               Positive: the rule loaded natively does.
  files        given this checkout's CLAUDE.md files, rules and claudeMdExcludes,
               Claude Code's own /context memory-file list equals the set
               bin/grip-lexicon measure counts. Negative: with no excludes it differs.

CLI: grip-kern-probe [PROBE|all] --label LABEL [--model MODEL] [--root CHECKOUT] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import claude_as  # noqa: E402
from lexicon import surfaces  # noqa: E402

MODEL = "claude-haiku-4-5"     # the default; --model probes the model a channel will serve
CHUNK_LIMIT = 10_000          # hook output over this reaches the model as a preview
PREVIEW = 2_000
# The planned main-session tier: the operator-facing rules and the general rules
# that leave the native floor, roughly 70,000 to 90,000 characters in chunks under
# 9,500 (KERN v2 plan, P2). Ten chunks of 9,000 cover it with margin; three would
# not detect a limit on the number or total size of SessionStart values (Codex on
# GRIP#7681). The extended tier stays an index, so it is not part of this fan-out.
CHUNK_COUNT = 10
ENCRYPTED_STAND_IN = "# A stand-in for a git-crypt rule; the probe never copies the real text.\n"
_PADDING = "This line pads a KERN size probe and carries no instruction.\n"
_MEMORY_ROW = re.compile(r"^\|\s*(User|Project|Local|Managed)\s*\|\s*(\S+)\s*\|")


@dataclass
class Outcome:
    probe: str
    passed: bool
    control_ok: bool              # the control behaved as a control must
    detail: str
    evidence: dict = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        if not self.control_ok:
            return "INVALID (the control did not behave as a control)"
        return "PASS" if self.passed else "FAIL"


# --- the sandbox ---------------------------------------------------------------

def _base_env() -> dict:
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}


@contextmanager
def sandbox(label: str):
    """An empty Claude Code directory and work directory, signed in as ``label``."""
    token = claude_as.read_label(label)
    with tempfile.TemporaryDirectory(prefix="kern-probe-") as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "work").mkdir()
        env = claude_as.child_env(_base_env(), token)
        env["CLAUDE_CONFIG_DIR"] = str(root / "config")
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        yield root, env


def run_claude(root: Path, env: dict, prompt: str, settings: dict, extra: tuple = (),
               timeout: int = 300) -> str:
    """One ``claude -p`` run in the sandbox; returns the final result text."""
    argv = [claude_as.find_claude(env), "-p", prompt, "--model", MODEL,
            "--settings", json.dumps(settings), "--output-format", "json",
            "--strict-mcp-config", *extra]
    proc = subprocess.run(argv, cwd=root / "work", env=env, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
    data = json.loads(proc.stdout)
    record = data[-1] if isinstance(data, list) else data
    return str(record.get("result", ""))


def _hook(root: Path, name: str, body: str) -> str:
    """Write a hook script into the sandbox; return the command that runs it."""
    path = root / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    # Both paths double-quoted, as GRIP quotes every hook command: an interpreter or
    # temporary directory with a space in it would otherwise be split by the shell
    # (tests/test_settings_paths_are_space_safe.py; Codex on GRIP#7681).
    return f'"{sys.executable}" "{path}"'


def _settings(event: str, commands: list[str], matcher: str | None = None) -> dict:
    group = {"hooks": [{"type": "command", "command": c} for c in commands]}
    if matcher:
        group["matcher"] = matcher
    return {"hooks": {event: [group]}}


def _jsonl(path: Path) -> list:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# --- chunks --------------------------------------------------------------------

def _padding(room: int) -> str:
    """``room`` characters of padding made of whole lines, each ending in a newline.

    A line cut part-way would fuse onto the marker placed after it, and a model
    asked for lines BEGINNING with the marker could then miss it for a reason that
    has nothing to do with the limit under test.
    """
    if room <= 0:
        return ""
    whole = _PADDING * (room // len(_PADDING))
    rest = room - len(whole)
    return whole + ("." * (rest - 1) + "\n" if rest else "")


def chunk_text(marker: str, secret: str, size: int, secret_last: bool = False) -> str:
    """``size`` characters: the marker line first, or last, and whole padding lines."""
    line = f"{marker}: {secret}\n"
    padding = _padding(size - len(line))
    return padding + line if secret_last else line + padding


def chunk_hook(text: str) -> str:
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    return f"import json\nprint(json.dumps({payload!r}))\n"


def probe_chunks(label: str) -> Outcome:
    seen_secrets = [secrets.token_hex(4) for _ in range(CHUNK_COUNT)]
    hidden = secrets.token_hex(4)
    prompt = ("List every line of your context that begins with KERN-CHUNK, exactly as "
              "written, one per line. Write nothing else.")
    with sandbox(label) as (root, env):
        commands = [_hook(root, f"chunk{i}", chunk_hook(chunk_text(f"KERN-CHUNK-{i}", s, 9_000)))
                    for i, s in enumerate(seen_secrets, 1)]
        # Its own name: written as chunk4.py it overwrote the fourth real chunk once
        # the fan-out passed three (caught by the offline fakes, 2026-10-06).
        commands.append(_hook(root, "chunk-over", chunk_hook(
            chunk_text("KERN-CHUNK-OVER", hidden, CHUNK_LIMIT + 1_000, secret_last=True))))
        answer = run_claude(root, env, prompt, _settings("SessionStart", commands))
    arrived = [s for s in seen_secrets if s in answer]
    return Outcome("chunks", passed=len(arrived) == CHUNK_COUNT, control_ok=hidden not in answer,
                   detail=f"{len(arrived)} of {CHUNK_COUNT} chunks of 9,000 characters arrived "
                          f"({CHUNK_COUNT * 9_000:,} in all); "
                          f"the over-limit chunk's secret {'arrived' if hidden in answer else 'did not'}",
                   evidence={"answer": answer[:600]})


# --- brief ---------------------------------------------------------------------

BRIEF_HOOK = '''import json, sys
data = json.load(sys.stdin)
tool_input = dict(data.get("tool_input") or {{}})
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"keys": sorted(tool_input)}}) + "\\n")
tool_input["prompt"] = str(tool_input.get("prompt", "")) + {append!r}
out = {{"hookEventName": "PreToolUse", "updatedInput": tool_input}}
if {allow!r}:
    out["permissionDecision"] = "allow"
print(json.dumps({{"hookSpecificOutput": out}}))
'''

SUBAGENT_LOG_HOOK = '''import json, sys
data = json.load(sys.stdin)
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"agent_type": data.get("agent_type")}}) + "\\n")
'''

BRIEF_PROMPT = (
    'Call the Agent tool exactly once, with subagent_type "{agent}", description '
    '"probe", and this prompt: "Without using any tools, copy out every line of your '
    'instructions that begins with KERN-PROBE-RULE, exactly as written. If there is '
    'none, reply NONE." Then reply with exactly what the subagent returned and nothing else.')


def _rule_line(word: str) -> str:
    return f"\n\nRules for this task:\nKERN-PROBE-RULE: the probe word is {word}.\n"


def _brief_run(label: str, appends: list[str], allow: bool, agent: str) -> tuple[str, list, list]:
    """One launch through ``len(appends)`` PreToolUse hooks on Agent, each appending its text."""
    with sandbox(label) as (root, env):
        pre_log, sub_log = root / "pre.jsonl", root / "sub.jsonl"
        pres = [_hook(root, f"brief{i}", BRIEF_HOOK.format(log=str(pre_log), append=a, allow=allow))
                for i, a in enumerate(appends)]
        sub = _hook(root, "subagent", SUBAGENT_LOG_HOOK.format(log=str(sub_log)))
        settings = _settings("PreToolUse", pres, matcher="Agent|Task")
        settings["hooks"].update(_settings("SubagentStart", [sub])["hooks"])
        answer = run_claude(root, env, BRIEF_PROMPT.format(agent=agent), settings,
                            extra=("--allowedTools", "Agent"))
        return answer, _jsonl(pre_log), _jsonl(sub_log)


def probe_brief(label: str, allow: bool, agent: str = "Explore") -> Outcome:
    word = secrets.token_hex(4)
    answer, pre, sub = _brief_run(label, [_rule_line(word)], allow, agent)
    control, _, _ = _brief_run(label, [""], allow, agent)
    types = [s.get("agent_type") for s in sub]
    name = "brief-plan" if agent == "Plan" else ("brief+allow" if allow else "brief")
    return Outcome(name, passed=word in answer and bool(pre) and agent in types,
                   control_ok=word not in control,
                   detail=f"hook ran {len(pre)} time(s); subagent types {types}; "
                          f"the appended rule {'reached' if word in answer else 'did not reach'} "
                          f"the {agent} subagent",
                   evidence={"answer": answer[:400], "input_keys": pre[:1], "control": control[:200]})


def probe_two_writers(label: str) -> Outcome:
    first, second = secrets.token_hex(4), secrets.token_hex(4)
    answer, pre, _ = _brief_run(label, [_rule_line(first), _rule_line(second)], False, "Explore")
    control, _, _ = _brief_run(label, [_rule_line(first), ""], False, "Explore")
    both = first in answer and second in answer
    return Outcome("two-writers", passed=both, control_ok=second not in control,
                   detail=f"{len(pre)} hook run(s); first rule {'arrived' if first in answer else 'lost'}, "
                          f"second {'arrived' if second in answer else 'lost'}",
                   evidence={"answer": answer[:400], "control": control[:200]})


# --- command and file timing ---------------------------------------------------

CMD_HOOK = '''import json, sys
data = json.load(sys.stdin)
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"command": (data.get("tool_input") or {{}}).get("command", "")}}) + "\\n")
if {context!r}:
    print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse",
                                              "additionalContext": {context!r}}}}}))
'''

CMD_PROMPT = "Run exactly one Bash command that prints the word hello, then stop. Do not explain."


def _first_command(label: str, rule: str, native: bool) -> str:
    """The first Bash command run when ``rule`` is handed over natively or as hook context."""
    with sandbox(label) as (root, env):
        if native:
            (root / "config" / "CLAUDE.md").write_text(rule, encoding="utf-8")
        log = root / "cmd.jsonl"
        hook = _hook(root, "cmd", CMD_HOOK.format(log=str(log), context="" if native else rule))
        run_claude(root, env, CMD_PROMPT, _settings("PreToolUse", [hook], matcher="Bash"),
                   extra=("--allowedTools", "Bash"))
        commands = [c.get("command", "") for c in _jsonl(log)]
        return commands[0] if commands else ""


def probe_command(label: str) -> Outcome:
    word = secrets.token_hex(4)
    # Additive, never contradicting the request: a rule the model follows when it
    # is in view (a contradicting one was overridden by the request even natively,
    # 2026-10-06), so only its TIMING is measured.
    rule = (f"KERN-CMD-RULE: every Bash command you run must start with the exact text "
            f"`: kern-{word};` followed by the command itself.\n")
    hooked, native = _first_command(label, rule, False), _first_command(label, rule, True)
    return Outcome("command", passed=f"kern-{word}" in hooked, control_ok=f"kern-{word}" in native,
                   detail=f"first command with the rule as hook context: {hooked!r}; "
                          f"with the rule loaded natively: {native!r}",
                   evidence={"hooked": hooked, "native": native})


FILE_RULE = ("KERN-FILE-RULE: every file whose name ends in .kern must begin with the line "
             "kern-{word}, followed by whatever content you were asked to write.\n")
WRITE_PROMPT = ("Create a new file named notes.kern in the current directory containing exactly "
                "the word hello. Do not read any file first. Do not explain.")
EDIT_PROMPT = "Read notes.kern, then change its content to exactly the word goodbye. Do not explain."

WRITE_LOG_HOOK = '''import json, sys
data = json.load(sys.stdin)
tool_input = data.get("tool_input") or {{}}
with open({log!r}, "a") as fh:
    fh.write(json.dumps({{"tool": data.get("tool_name"),
                         "text": tool_input.get("content", tool_input.get("new_string", ""))}}) + "\\n")
'''
_SCOPED = '---\npaths:\n  - "**/*.kern"\n---\n\n'


def _file_content(label: str, rule: str, scoped: bool, edit: bool) -> tuple[str, str]:
    """(the text of the FIRST write or edit, the final file) under ``rule``.

    The first write is what the timing question is about: a rule that arrives only
    when the write is attempted can still end in a compliant file, by a rewrite.
    """
    with sandbox(label) as (root, env):
        (root / "config" / "rules").mkdir()
        (root / "config" / "rules" / "kern-file.md").write_text(
            (_SCOPED if scoped else "") + rule, encoding="utf-8")
        target = root / "work" / "notes.kern"
        if edit:
            target.write_text("hello\n", encoding="utf-8")
        log = root / "writes.jsonl"
        hook = _hook(root, "writes", WRITE_LOG_HOOK.format(log=str(log)))
        run_claude(root, env, EDIT_PROMPT if edit else WRITE_PROMPT,
                   _settings("PreToolUse", [hook], matcher="Write|Edit"),
                   extra=("--allowedTools", "Read,Write,Edit"))
        writes = _jsonl(log)
        final = target.read_text(encoding="utf-8").strip() if target.exists() else ""
        return (writes[0]["text"] if writes else ""), final


def probe_file(label: str, edit: bool) -> Outcome:
    word = secrets.token_hex(4)
    rule = FILE_RULE.format(word=word)
    marker = f"kern-{word}"
    first, final = _file_content(label, rule, scoped=True, edit=edit)
    native_first, _ = _file_content(label, rule, scoped=False, edit=edit)
    return Outcome("file-edit" if edit else "file-write", passed=marker in first,
                   control_ok=marker in native_first,
                   detail=f"with paths frontmatter the first write held {first!r} and the file "
                          f"ended {final!r}; with the rule loaded natively the first write held "
                          f"{native_first!r}",
                   evidence={"first": first, "final": final, "native_first": native_first})


# --- files ---------------------------------------------------------------------

def memory_rows(context_output: str) -> list[tuple[str, str]]:
    """(type, path) for each instruction-file row of a /context report; AutoMem is left out."""
    return [m.groups() for m in map(_MEMORY_ROW.match, context_output.splitlines()) if m]


def stage_checkout(checkout: Path, root: Path, excludes: list[str] | None) -> None:
    """Copy the checkout's instruction surface into the sandbox: the global CLAUDE.md
    and every rule as user files, .claude/CLAUDE.md as the project file. A git-crypt
    rule is replaced by a stand-in, so its text never leaves the repository."""
    config = root / "config"
    (config / "rules").mkdir()
    (config / "CLAUDE.md").write_text((checkout / "CLAUDE.md").read_text(encoding="utf-8"),
                                      encoding="utf-8")
    for rule in surfaces.rules(checkout):
        body = ENCRYPTED_STAND_IN if rule.encrypted else rule.text
        (config / rule.rel).write_text(body, encoding="utf-8")
    (root / "work" / ".claude").mkdir()
    (root / "work" / ".claude" / "CLAUDE.md").write_text(
        (checkout / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"), encoding="utf-8")
    if excludes is not None:
        (config / "settings.json").write_text(json.dumps({"claudeMdExcludes": excludes}),
                                              encoding="utf-8")


def _loaded_set(root: Path, rows: list[tuple[str, str]]) -> set[str]:
    """Paths relative to the checkout layout: user files under config/, project files under work/."""
    out = set()
    for kind, path in rows:
        base = root / ("config" if kind == "User" else "work")
        try:
            out.add(str(Path(path).resolve().relative_to(base.resolve())))
        except ValueError:
            out.add(path)
    return out


def _files_run(label: str, checkout: Path, excludes: list[str] | None) -> set[str]:
    with sandbox(label) as (root, env):
        stage_checkout(checkout, root, excludes)
        argv = [claude_as.find_claude(env), "-p", "/context", "--model", MODEL]
        proc = subprocess.run(argv, cwd=root / "work", env=env, capture_output=True,
                              text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p /context exited {proc.returncode}: {proc.stderr[:300]}")
        return _loaded_set(root, memory_rows(proc.stdout))


def probe_files(label: str, checkout: Path) -> Outcome:
    settings = json.loads((checkout / "settings.json").read_text(encoding="utf-8"))
    expected = {s.rel for s in surfaces.always_loaded(checkout)}
    loaded = _files_run(label, checkout, settings.get("claudeMdExcludes", []))
    control = _files_run(label, checkout, None)
    return Outcome("files", passed=loaded == expected, control_ok=control != expected,
                   detail=f"Claude Code loaded {len(loaded)} instruction files; measure counts "
                          f"{len(expected)}; with no excludes it loaded {len(control)}",
                   evidence={"only_claude_code": sorted(loaded - expected),
                             "only_measure": sorted(expected - loaded)})


# --- CLI -----------------------------------------------------------------------

def probes(label: str, checkout: Path) -> dict:
    """Every probe by name, each returning a list of outcomes."""
    return {
        "chunks": lambda: [probe_chunks(label)],
        "brief": lambda: [probe_brief(label, allow=False), probe_brief(label, allow=True)],
        "brief-plan": lambda: [probe_brief(label, allow=False, agent="Plan")],
        "two-writers": lambda: [probe_two_writers(label)],
        "command": lambda: [probe_command(label)],
        "file-write": lambda: [probe_file(label, edit=False)],
        "file-edit": lambda: [probe_file(label, edit=True)],
        "files": lambda: [probe_files(label, checkout)],
    }


def _use_model(model: str) -> None:
    """Every later run_claude and /context call uses ``model``."""
    global MODEL
    MODEL = model


PROBE_NAMES = ("chunks", "brief", "brief-plan", "two-writers", "command", "file-write",
               "file-edit", "files")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grip-kern-probe", description=__doc__.split("\n\n")[0])
    ap.add_argument("which", nargs="?", default="all", choices=[*PROBE_NAMES, "all"])
    ap.add_argument("--label", required=True, help="stored token label (see: grip-relay-bill list)")
    ap.add_argument("--model", default=MODEL,
                    help="the model each probe runs on: delivery is a property of the harness, "
                         "but whether a delivered rule is followed depends on the model")
    ap.add_argument("--root", default=str(_LIB.parent), help="checkout for the files probe")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    _use_model(args.model)
    table = probes(args.label, Path(args.root).resolve())
    names = PROBE_NAMES if args.which == "all" else (args.which,)
    outcomes = [o for name in names for o in table[name]()]
    if args.json:
        print(json.dumps([{**asdict(o), "verdict": o.verdict} for o in outcomes], indent=2))
    else:
        for o in outcomes:
            print(f"{o.verdict:<8} {o.probe:<12} {o.detail}")
    return 0 if all(o.verdict == "PASS" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
