"""Measured-claim gate: a count in outbound text must come from a run this session read.

The failure this closes was recorded eleven times in operator memory before it
became mechanical (``feedback_verification_claims_written_before_the_verification_ran``,
N=11 on 2026-10-12): a commit message, PR body or PR comment carried a test
count such as "12 passed" typed from expectation, while the run that was read
said 9. The number then travelled, into the merge commit, the changelog and the
next session's memory. V>>: "11 times? Learn!!" A record that is violated
eleven times is not the fix; a gate is.

What it checks. For a Bash command that publishes text, ``git commit`` (-m,
-F), ``gh pr create|edit|close|comment|review`` and ``gh issue comment|close``
(--body, --body-file, -F, --comment, -m), the text is taken from the quoted
argument, from a heredoc the same command writes to that path, from a heredoc
that feeds ``-F -`` on stdin in the same command, or from the file on disk.
Every count in the test-runner vocabulary in that text ("12 passed", "3
failed", "fails 12 tests") is compared with the counts that appear
in test-runner SUMMARY lines inside tool results of this session's transcript,
the lines pytest prints as "9 passed, 1 failed in 0.23s" and the lines vitest
prints as "Tests  9 passed (9)" (GRIP#9128). Prose in the
transcript never counts: the wrong number is usually already in the session's
own earlier text, and a raw scan of the transcript would count it as evidence.

Whose transcript. A worker dispatched with the Agent tool shares its parent's
session_id, and the ``transcript_path`` in its PreToolUse payload is the
parent's transcript; only ``agent_id`` names the worker. So a payload that
carries ``agent_id`` is checked against the worker's own transcript, which
Claude Code keeps at ``<session>/subagents/agent-<agent_id>.jsonl``, and the
parent's is read only when that file is not on disk. Every reason says which
transcript was read. Until GRIP#8331 the parent's was read for every worker, so
a worker's true count was denied with the parent's numbers.

Verdicts. A count that no summary reports is denied, and the reason names the
counts the transcript did hold. A transcript with no summary at all cannot
check, so it warns. A command carrying ``GRIP_MEASURED_CLAIM_GATE=0`` is
allowed and logged: the documented override for a count that came from outside
the transcript the gate reads (a worker's run quoted by its parent, a run from
before ``/clear``). The denied set is one command; the resolution is to correct
the number in the text, which no gate refuses
(``rules/gate-resolution-escape-hatch.md``, Invariant 1).

Fails open: any error inside the gate allows the command and is logged,
because a publish gate that crashes must not become a block on every commit.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

ENV_OVERRIDE = "GRIP_MEASURED_CLAIM_GATE"

#: Commands that publish text somewhere a wrong number travels from.
_PUBLISH_RE = re.compile(
    r"\bgit\s+commit\b|\bgh\s+(?:pr|issue)\s+(?:create|edit|comment|close|review)\b")

#: The words a test-runner summary uses. The summary and count patterns share
#: this one vocabulary so a keyword added to one cannot drift from the other.
#: "subtests passed" comes first so it wins over the bare "passed": a line such
#: as "162 passed, 174 warnings, 120 subtests passed in 1.39s" was skipped
#: whole without it, and the gate refused the true 162 (measured 2026-10-12,
#: the first live run after GRIP#8139 merged). "rerun" is pytest-rerunfailures;
#: "todo" is vitest's count of tests marked todo.
_RUNNER_WORDS = (r"subtests? passed|passed|failed|skipped|xfailed|xpassed|errors?|warnings?"
                 r"|deselected|rerun|todo")
#: The subset a prose claim is checked for: a sentence claims passes and
#: failures, not warning counts.
_CLAIM_WORDS = r"passed|failed|skipped|xfailed"

#: pytest's summary line: counts separated by commas, closing with "in 0.23s".
_SUMMARY_RE = re.compile(r"((?:\d+ (?:" + _RUNNER_WORDS + r")(?:, )?)+) in [\d.]+s")
#: vitest's summary lines: "Test Files  1 failed | 61 passed (62)" and
#: "Tests  5 failed | 707 passed (712)", counts separated by " | " and closing
#: with the total in brackets. The label must start its line and be followed by
#: two or more spaces, as vitest prints it, so a sentence that quotes such a line
#: is not read as a summary. Until GRIP#9128 only pytest's shape was read, and the
#: gate refused a true count copied from vitest's own line.
_VITEST_SUMMARY_RE = re.compile(
    r"^[ \t]*(?:Test Files|Tests)[ \t]{2,}((?:\d+ (?:" + _RUNNER_WORDS + r")(?: \| )?)+) \(\d+\)",
    re.M)
_SUMMARY_PATTERNS = (_SUMMARY_RE, _VITEST_SUMMARY_RE)
_COUNT_RE = re.compile(r"(\d+) (" + _RUNNER_WORDS + r")")
#: A terminal escape code. vitest and pytest colour their summary lines whenever
#: they think they write to a terminal, and the codes sit between the label and
#: the counts, so they are removed before matching. Measured 2026-11-25: the gate
#: refused a true "16 passed" copied from vitest's coloured line
#: "\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[32m16 passed...".
#: Covered: CSI sequences with any ECMA-48 parameter bytes (colours and styles,
#: 256-colour and true-colour in both the ";" and the ":" form, cursor and erase
#: codes) and OSC sequences (terminal hyperlinks and titles, ended by BEL or
#: ESC \ on the same line, so a malformed one cannot remove the lines after it,
#: GRIP#9700). Not covered: rarer two-byte ESC codes, which no test runner is
#: known to put in a summary line.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b\n]*(?:\x07|\x1b\\))")

#: A count claimed in prose: "12 passed", "3 tests failed", "fails 12 tests".
#: The number must not end a version or a name ("fable-5.2 failed", "v1.2
#: passed", "gpt-4 failed"): on 2026-11-25 "the Fable 5.2 seat ... fable-5.2
#: failed" was refused as a claim of "2 failed".
_CLAIM_RE = re.compile(
    r"(?<![\w.\-])(\d+)\s+(?:tests?\s+)?(" + _CLAIM_WORDS + r")\b"
    r"|\bfails?\s+(\d+)\s+tests?\b",
    re.I,
)

_QUOTED = r"\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(\S+)"
_TEXT_FLAG_RE = re.compile(
    r"(?:^|\s)(?:-m|--message|--body|-b|--comment)\s+(?:" + _QUOTED + ")", re.S)
_FILE_FLAG_RE = re.compile(r"(?:^|\s)(?:-F|--file|--body-file)\s+(?:" + _QUOTED + ")")
_HEREDOC_START_RE = re.compile(
    r"cat\s*>>?\s*(\"[^\"]+\"|'[^']+'|\S+)\s*<<-?\s*(['\"]?)(\w+)\2[^\n]*\n")
#: A heredoc opened later in the same command as ``-F -``, feeding the message on
#: stdin. No separator (; & |) or input redirect (<) may come between them, so the
#: heredoc of a later command on the same line is never taken (AGORA on GRIP#9675).
_STDIN_HEREDOC_RE = re.compile(r"[^\n;&|<]*?<<-?\s*(['\"]?)(\w+)\1[^\n]*\n")
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)=(?:\"([^\"]*)\"|'([^']*)'|(\S*))\s*$", re.M)
_VAR_RE = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
_OVERRIDE_RE = re.compile(r"(?:^|\s)" + ENV_OVERRIDE + r"=0(?:\s|$)")

_PLURAL = {"errors": "error", "warnings": "warning",
           "subtests passed": "subtest", "subtest passed": "subtest"}

#: The folder beside ``<session>.jsonl`` that holds each worker's own transcript.
#: The Claude Code hooks reference names it for SubagentStop's
#: ``agent_transcript_path``: "the subagent's own transcript stored in a nested
#: subagents/ folder". Nested workers are kept flat in the same folder (counted
#: 2026-10-15 on one machine: 1,322 depth-2 workers, no nested subagents/ folder).
_SUBAGENTS_DIR = "subagents"


@dataclass(frozen=True)
class Decision:
    """What the gate decided and why, the counts on both sides, and the transcript read."""

    action: str  # "allow" | "warn" | "deny"
    reason: str
    claims: tuple
    observed: tuple
    transcript: str = ""  # which transcript the observed counts came from, in words
    transcript_file: str = ""  # that transcript's path, for the audit log


# -- text extraction -----------------------------------------------------------

def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _first(groups) -> str:
    return next((g for g in groups if g is not None), "")


def _heredoc_body(command: str, start: int, tag: str) -> str:
    """The lines after ``start`` up to the line that is exactly ``tag``."""
    body: list = []
    for line in command[start:].split("\n"):
        if line.strip() == tag:
            break
        body.append(line)
    return "\n".join(body)


def heredocs(command: str) -> dict:
    """``{path text: body}`` for every ``cat > path <<TAG`` block in ``command``."""
    return {_unquote(m.group(1)): _heredoc_body(command, m.end(), m.group(3))
            for m in _HEREDOC_START_RE.finditer(command)}


def assignments(command: str) -> dict:
    """Plain ``NAME=value`` lines in the command, for resolving ``$NAME`` in a path."""
    return {m.group(1): _first(m.groups()[1:]) for m in _ASSIGN_RE.finditer(command)}


def _resolve_path(raw: str, command: str, environ, cwd) -> Path | None:
    env = {**dict(environ), **assignments(command)}
    expanded = os.path.expanduser(_VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), raw))
    if "$" in expanded:
        return None
    path = Path(expanded)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path


def _file_text(raw: str, docs: dict, command: str, environ, cwd):
    """``(source, text)`` for one ``-F``/``--body-file`` argument, or None."""
    if raw in docs:
        return f"heredoc {raw}", docs[raw]
    path = _resolve_path(raw, command, environ, cwd)
    if path is None or not path.is_file():
        return None
    try:
        return f"file {path}", path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _stdin_text(command: str, start: int):
    """``(source, text)`` for a ``-F -`` whose message a heredoc in the same command
    feeds on stdin, or None. Before GRIP#9675, ``git commit -F - <<'EOF'`` was read
    as a file named ``-`` that does not exist, so its counts were never checked. A
    heredoc whose closing tag never appears is not read: its tag was probably not a
    heredoc tag at all, and reading on would take the rest of the command."""
    m = _STDIN_HEREDOC_RE.match(command, start)
    if m is None or not re.search(rf"^\s*{re.escape(m.group(2))}\s*$", command[m.end():], re.M):
        return None
    return "heredoc on stdin", _heredoc_body(command, m.end(), m.group(2))


def _flag_text(m, command: str, docs: dict, environ, cwd):
    """``(source, text)`` for one file-flag match: stdin when its argument is ``-``."""
    raw = _unquote(_first(m.groups()))
    if raw == "-":
        return _stdin_text(command, m.end())
    return _file_text(raw, docs, command, environ, cwd)


def outbound_texts(command: str, environ=None, cwd=None) -> list:
    """``[(source, text)]`` for every message or body the command publishes."""
    environ = os.environ if environ is None else environ
    found = [("argument", _first(m.groups())) for m in _TEXT_FLAG_RE.finditer(command)]
    docs = heredocs(command)
    files = (_flag_text(m, command, docs, environ, cwd) for m in _FILE_FLAG_RE.finditer(command))
    found.extend(item for item in files if item is not None)
    return found


# -- counts on both sides -------------------------------------------------------

def _norm(word: str) -> str:
    word = word.lower()
    return _PLURAL.get(word, word)


def _claim_pair(m) -> tuple:
    if m.group(3) is not None:
        return int(m.group(3)), "failed"
    return int(m.group(1)), _norm(m.group(2))


def claimed_counts(text: str) -> list:
    """Counts the text asserts, as ``(n, word)`` pairs, first occurrence order."""
    return list(dict.fromkeys(_claim_pair(m) for m in _CLAIM_RE.finditer(text or "")))


def _summary_pairs(summary: str) -> set:
    return {(int(n), _norm(word)) for n, word in _COUNT_RE.findall(summary)}


def summary_counts(text: str) -> set:
    """Counts from every pytest or vitest summary line in ``text``; prose is ignored."""
    plain = _ANSI_RE.sub("", text or "")
    matches = chain.from_iterable(p.finditer(plain) for p in _SUMMARY_PATTERNS)
    return set().union(*(_summary_pairs(m.group(1)) for m in matches))


def _block_texts(block) -> list:
    """The text of one tool-result content block, or nothing for any other block."""
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return []
    inner = block.get("content")
    if isinstance(inner, str):
        return [inner]
    if isinstance(inner, list):
        return [part["text"] for part in inner
                if isinstance(part, dict) and isinstance(part.get("text"), str)]
    return []


def _tool_result_texts(obj) -> list:
    """Every tool-result text in one transcript line; nothing the model wrote."""
    if not isinstance(obj, dict):
        return []
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    texts = list(chain.from_iterable(map(_block_texts, content))) if isinstance(content, list) else []
    extra = obj.get("toolUseResult")
    if extra is not None:
        texts.append(extra if isinstance(extra, str) else json.dumps(extra))
    return texts


def _line_counts(line: str) -> set:
    # No leading space in the words: a colour code may sit between the count and
    # the word ("16 \x1b[32mpassed"), and such a line must still be read.
    if "passed" not in line and "failed" not in line:
        return set()
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return set()
    return set().union(*map(summary_counts, _tool_result_texts(obj)))


def observed_counts(transcript_path) -> set:
    """Every ``(n, word)`` a test-runner summary reported in this session's tool results."""
    if not transcript_path:
        return set()
    path = Path(transcript_path)
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", errors="replace") as fh:
        return set().union(*map(_line_counts, fh))


def agent_transcript(transcript_path, agent_id: str) -> Path | None:
    """The worker's own transcript beside the session's, or None when it is not on disk.

    ``transcript_path`` is normally the session's file, ``<session>.jsonl``, and
    the worker's is ``<session>/subagents/agent-<agent_id>.jsonl``. When
    ``transcript_path`` is already a file in ``subagents/``, the worker's file is
    its sibling. An id that is not a bare file name is never joined into a path.
    """
    if not transcript_path or not agent_id or Path(agent_id).name != agent_id:
        return None
    name = agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}"
    session = Path(transcript_path)
    folder = (session.parent if session.parent.name == _SUBAGENTS_DIR
              else session.with_suffix("") / _SUBAGENTS_DIR)
    candidate = folder / f"{name}.jsonl"
    return candidate if candidate.is_file() else None


def _choose_transcript(transcript_path, agent_id: str) -> tuple:
    """``(path, words)``: the transcript holding the publishing actor's own runs."""
    if not agent_id:
        return transcript_path, "this session's transcript"
    own = agent_transcript(transcript_path, agent_id)
    if own is not None:
        return own, f"this worker's own transcript (agent {agent_id})"
    return transcript_path, f"the parent's transcript (no transcript for agent {agent_id} was found)"


def override_requested(command: str, environ=None) -> bool:
    """``GRIP_MEASURED_CLAIM_GATE=0`` on the command or in the environment."""
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_OVERRIDE, "")).strip() == "0" or bool(_OVERRIDE_RE.search(command or ""))


# -- the decision ---------------------------------------------------------------

def _fmt(pairs) -> str:
    return ", ".join(f"{n} {word}" for n, word in pairs) or "nothing"


def decide(command: str, transcript_path, environ=None, cwd=None, agent_id: str = "") -> Decision:
    """Allow, warn or deny one Bash command; see the module docstring.

    ``agent_id`` is the PreToolUse payload's field of that name: empty for the
    session itself, set for a dispatched worker, whose own transcript is read.
    """
    command = command or ""
    if not _PUBLISH_RE.search(command):
        return Decision("allow", "not a publishing command", (), ())
    if override_requested(command, environ):
        return Decision("allow", f"overridden by {ENV_OVERRIDE}=0", (), ())
    texts = outbound_texts(command, environ, cwd)
    claimed = tuple(dict.fromkeys(chain.from_iterable(claimed_counts(t) for _, t in texts)))
    if not claimed:
        return Decision("allow", "no test counts in the outbound text", (), ())
    path, where = _choose_transcript(transcript_path, agent_id)
    observed = tuple(sorted(observed_counts(path)))
    read = {"transcript": where, "transcript_file": str(path or "")}
    if not observed:
        return Decision(
            "warn",
            f"the text claims {_fmt(claimed)}, and {where} holds no test-runner summary "
            "to check it against. Read the summary line, then write the number.",
            claimed, observed, **read)
    missing = tuple(pair for pair in claimed if pair not in observed)
    if missing:
        return Decision(
            "deny",
            f"[GRIP measured-claim gate] the text claims {_fmt(missing)}, and no test-runner "
            f"summary in {where} reports it (seen: {_fmt(observed)}). Read the summary "
            "line, then write the number. For a count that came from outside that "
            f"transcript, prefix the command with {ENV_OVERRIDE}=0 (logged).",
            claimed, observed, **read)
    return Decision("allow", f"every claimed count was read in {where}: {_fmt(claimed)}",
                    claimed, observed, **read)
