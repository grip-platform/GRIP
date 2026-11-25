"""lib/run_it_yourself.py: a `(yours)` closer whose step is only a GRIP command
the session can run is the session's own step.

Origin: V>> 2026-11-16, on reading `RECOMMENDED NEXT STEP (yours)` above
`~/.claude/bin/grip-py ~/.claude/lib/hypothesis_engine.py override --id H949 ...`:
"Why is this the recommended next step for me, if it's just a command that you
can run? We've already had this issue before." rules/canonical-output-style.md
already said a command the agent can run in its own shell is never `(yours)` on
its own, but hooks/next-step-recorder.py reclassified only `gh pr merge`
(lib/merge_yourself.py), so every other GRIP command was parked as his question.

``decide`` reads two things from disk, each once per Stop and as it is at that
moment: the one tool or script the command names, to see whether it asks at
the terminal, and, for a hypothesis override only, the engine's verification
log. It reclassifies only when every check passes, and a step that fails any
check stays `(yours)`:

1. lib/pending_materiality.classify_floor finds no HALT class in the step.
2. The step hands over ONE command: a shell operator, `$(` or a backtick
   inside the command rejects it, and nothing is split to classify half.
3. The command runs a GRIP tool: a name in bin/, a path in a GRIP checkout's
   bin/ or scripts/, or an interpreter (grip-py, py, python, python3) running a
   script in a GRIP checkout's lib/, bin/ or scripts/.
4. The tool is not a switch reserved for the operator (RESERVED_FOR_OPERATOR).
5. It does not override a verdict the hypothesis engine already recorded:
   that overrules the judge, which stays the operator's.
6. The tool or script does not ask at the terminal (a prompt, a shell `read`,
   sudo, the keychain), because a session cannot answer it.

A prose step counts only when a run, execute, type or paste verb opens it, its
one backticked span is the command, and no word of pending_materiality's veto
list follows the verb. Backtick-free prose ("run the override for H949") stays
`(yours)`: the rule text covers it, at writing time.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

try:
    from lib.next_step_router import grip_tools
    from lib.pending_materiality import _VETO, classify_floor
except ImportError:  # imported as a top-level module from lib/
    from next_step_router import grip_tools
    from pending_materiality import _VETO, classify_floor

#: Set to "0" to leave every `(yours)` command closer alone.
ENV_SWITCH = "GRIP_RUN_IT_YOURSELF"

#: Tools that stay the operator's although a session could type them: each is a
#: switch on how GRIP acts for him, or signs with his credential.
RESERVED_FOR_OPERATOR = frozenset({
    "grip-next-step",      # arms continuation: a switch reserved for him (canonical style)
    "grip-afk",            # /afk and /back are his commands (skills/afk)
    "grip-stop",           # his outbound brake (rules/full-power-stop-override.md)
    "grip-wire-brake",     # his egress brake
    "grip-model-default",  # the machine's launch pin, his preference
    "model_pin",           # the module behind grip-model-default
    "grip-relay-bill",     # which subscription the relay bills
    "grip-tick-grant",     # signs with his keychain key
    "tick_grant",          # the module behind grip-tick-grant
})

INTERPRETERS = frozenset({"grip-py", "py", "python", "python3"})
_SCRIPT_DIRS = ("lib", "bin", "scripts")
_DIRECT_DIRS = ("bin", "scripts")
_OPERATOR_CHARS = frozenset("();<>|&")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SPAN = re.compile(r"`([^`]+)`")
_RUN_VERB = re.compile(r"^\s*(?:please\s+)?(?:run|execute|type|paste)\b", re.IGNORECASE)
#: A prompt, a shell `read`, sudo's password or the keychain: input a session
#: cannot give. The replay of 462 real closers on 2026-11-16 found
#: `grip-black-october fix macos-chrome`, which runs sudo behind `read -r`.
_PROMPTS = re.compile(r"(?<![\w.])input\(|\bgetpass\b|/dev/tty|\bread\s+-[a-z]+\b|\bsudo\b"
                      r"|\bsecurity\s+(?:add|find)-generic-password\b")
_MAX_SOURCE_BYTES = 1_000_000
#: Where the hypothesis engine logs each outside review, relative to the install.
_VERIFICATIONS = Path("learning") / "hypothesis_verifications.jsonl"

#: The Stop reason the operator reads: second person, no GRIP-only word
#: (lib/operator_voice_detector.py).
BLOCK_REASON = ("That step is a command this session can run itself, so it runs it now "
                "instead of handing it to you.")


@dataclass(frozen=True)
class Decision:
    """The hook's outcome for a `(yours)` step: block the Stop, or leave it as recorded."""

    block: bool
    reason: str
    log: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """The GRIP tool or script a command runs."""

    name: str
    path: Path


def _no(why: str, tool: str | None = None) -> Decision:
    log = {"runnable": False, "why": why}
    if tool:
        log["tool"] = tool
    return Decision(False, why, log)


def command_in(step_text: str) -> str | None:
    """The command a step hands over, or None when it hands over none.

    A step that opens on a run verb is prose: its one backticked span is the
    command, unless a veto word follows the verb. A step that is only one span
    is that command. A step with no backtick at all is the command form, the
    line below the marker. Any other step with backticks is prose that hands
    over no command."""
    text = (step_text or "").strip()
    spans = _SPAN.findall(text)
    if _RUN_VERB.match(text):
        return _prose_command(text, spans)
    if len(spans) == 1 and _SPAN.fullmatch(text):
        return spans[0].strip()
    return text if text and "`" not in text else None


def _prose_command(text: str, spans: list[str]) -> str | None:
    if len(spans) != 1:
        return None
    after_verb = _RUN_VERB.sub("", _SPAN.sub(" ", text, count=1), count=1)
    return None if _VETO.search(after_verb.lower()) else spans[0].strip()


def split_command(command: str) -> list[str] | None:
    """The command's words without leading VAR=value assignments or an rtk
    prefix, or None when it is not ONE plain command."""
    if "`" in command or "$(" in command:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    if any(word and set(word) <= _OPERATOR_CHARS for word in words):
        return None
    return _strip_prefixes(words)


def _strip_prefixes(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and _ASSIGNMENT.match(words[i]):
        i += 1
    rest = words[i:]
    if rest[:1] == ["rtk"]:
        rest = rest[2:] if rest[1:2] == ["proxy"] else rest[1:]
    return rest


def _stem(word: str) -> str:
    name = Path(word).name.lower()
    for ext in (".py", ".sh"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _expand(word: str, root: Path) -> Path:
    """`~`, `$HOME` and `$CLAUDE_DIR` resolved; `$CLAUDE_DIR` is this install."""
    for var in ("${CLAUDE_DIR}", "$CLAUDE_DIR"):
        if word.startswith(var):
            return Path(str(root) + word[len(var):])
    for var in ("${HOME}", "$HOME"):
        if word.startswith(var):
            word = "~" + word[len(var):]
    return Path(os.path.expanduser(word))


def _checkout_of(real: Path, root: Path) -> Path | None:
    """The GRIP checkout `real` sits in: this install, or a worktree of it."""
    base = root.resolve()
    if real.is_relative_to(base):
        return base
    for parent in real.parents:
        if (parent / "bin" / "grip-py").is_file() and (parent / "lib" / "next_step_router.py").is_file():
            return parent
    return None


def _path_target(word: str, root: Path, dirs: tuple[str, ...]) -> Target | None:
    """The target a path names, when it sits in one of a GRIP checkout's `dirs`.
    A relative path counts only from those folders, read against this install."""
    path = _expand(word, root)
    if not path.is_absolute():
        if not path.parts or path.parts[0] not in dirs:
            return None
        path = root / path
    real = path.resolve()
    base = _checkout_of(real, root)
    if base is None:
        return None
    parts = real.relative_to(base).parts
    return Target(_stem(real.name), real) if parts and parts[0] in dirs else None


def _script_target(args: list[str], root: Path) -> Target | None:
    """The GRIP script an interpreter runs: `-m lib.x`, or the first argument
    that is not a flag, read as a path in lib/, bin/ or scripts/."""
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            return _path_target(args[i + 1].replace(".", "/") + ".py", root, _SCRIPT_DIRS)
        if arg in ("-c", "-"):
            return None
        if not arg.startswith("-"):
            return _path_target(arg, root, _SCRIPT_DIRS)
    return None


def _tool_target(word: str, root: Path, tools: frozenset[str]) -> Target | None:
    name = Path(word).name.lower()
    if name not in tools and _stem(word) not in tools:
        return None
    candidates = [root / "bin" / f"{word}{ext}" for ext in ("", ".py", ".sh")]
    path = next((c for c in candidates if c.is_file()), candidates[0])
    return Target(_stem(word), path)


def resolve(argv: list[str], root: Path, tools: frozenset[str]) -> Target | None:
    """The GRIP tool or script the command runs, or None when it runs neither."""
    if not argv:
        return None
    if Path(argv[0]).name.lower() in INTERPRETERS:
        return _script_target(argv[1:], root)
    if "/" in argv[0] or argv[0].startswith(("~", "$")):
        return _path_target(argv[0], root, _DIRECT_DIRS)
    return _tool_target(argv[0], root, tools)


def _option_value(argv: list[str], flag: str) -> str:
    """The value after `flag` in argv, or "" when the flag or its value is missing."""
    i = argv.index(flag) if flag in argv else -1
    return argv[i + 1] if 0 <= i < len(argv) - 1 else ""


def _json_row(line: str) -> dict:
    try:
        row = json.loads(line)
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def _records_a_verdict(row: dict, hid: str) -> bool:
    """True for a row in which the engine, or its outside reviewer against it,
    recorded a verdict on `hid`. An earlier override row is not a judge's."""
    if row.get("id") != hid:
        return False
    if row.get("kind") == "external_review":
        return row.get("agreed") is False
    return "verified_at" in row and ("status" in row or "verdict" in row)


def overrules_recorded_verdict(argv: list[str], root: Path) -> bool:
    """True when the command overrides a hypothesis the engine already judged.
    Overruling that verdict on the session's own hypothesis would make the
    author the judge, so it stays the operator's (rules/exogenous-anchor-law.md):
    V>> overrode H920's inconclusive verdict himself on 2026-11-11. Grading a
    hypothesis the engine never judged, as H949 was, is the session's step.
    An unreadable log records no verdict, so it reads as False."""
    hid = _option_value(argv, "--id")
    if "override" not in argv or not hid:
        return False
    try:
        lines = (root / _VERIFICATIONS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(_records_a_verdict(_json_row(line), hid) for line in lines)


def asks_at_terminal(path: Path) -> bool:
    """True when the tool or script reads from the person at the terminal, or
    cannot be read to tell: a session cannot answer a prompt."""
    try:
        with open(path, "rb") as fh:
            source = fh.read(_MAX_SOURCE_BYTES).decode("utf-8", "replace")
    except OSError:
        return True
    return _PROMPTS.search(source) is not None


def decide(step_text: str, *, root: Path, tools: frozenset[str] | None = None) -> Decision:
    """The decision for a `(yours)` step: block when it hands over only a GRIP
    command the session can run, otherwise leave it, with the reason logged."""
    command = command_in(step_text)
    if command is None:
        return _no("not-a-command")
    floor = classify_floor(step_text)
    if floor.verdict == "decision":
        return _no(f"halt-class:{floor.halt_class}")
    argv = split_command(command)
    if argv is None:
        return _no("compound")
    target = resolve(argv, Path(root), grip_tools() if tools is None else tools)
    if target is None:
        return _no("not-a-grip-tool")
    if target.name in RESERVED_FOR_OPERATOR:
        return _no("reserved", target.name)
    if target.name == "hypothesis_engine" and overrules_recorded_verdict(argv, Path(root)):
        return _no("overrules-recorded-verdict", target.name)
    if asks_at_terminal(target.path):
        return _no("asks-at-terminal", target.name)
    return Decision(True, BLOCK_REASON, {"runnable": True, "why": "runnable", "tool": target.name})
