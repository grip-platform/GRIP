"""The logic of the STE lint hook (hooks/ste-lint-hook.py).

The hook measures each assistant turn against Simplified Technical English
(lib/ste_lint.py) and shows one line. It only measures and displays. It refuses
nothing.

Two events use it:

  Stop              Score the turn. Append one row of numbers to
                    logs/ste-lint-hook.jsonl. Show one line to the operator
                    (the top-level "systemMessage" key).
  UserPromptSubmit  Hand that line to the model, once ("additionalContext").
                    This half is OFF until GRIP_STE_FEEDBACK=1.

Why two events. On Claude Code 2.1.289 a Stop hook that prints
hookSpecificOutput.additionalContext makes the harness run one more model turn
for each fire. A top-level systemMessage on Stop reaches the operator only, and
costs no turn. The context of a UserPromptSubmit hook reaches the model with no
extra turn. So the operator sees the score when the turn ends, and the model
sees it with the next prompt (measured 2026-10-05).

Why the second half is off. A score that the model reads with each prompt
steers how it writes. The approved plan says two things about that. It says
that the hook ships active. It also says that the plan stops at an opt-in
linter when hypothesis H778 is falsified, and the registered trial of H778 did
not confirm it on 2026-10-05. The session that built this hook read the second
sentence as the one that governs steering. That is a reading, not an order of
the operator, and one switch reverses it: GRIP_STE_FEEDBACK=1. The operator's
line and the log row do not reach the model.

The trial (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, S2 and S3). Every row
carries two arm bits drawn from the session id (arms_of): arm_a for an STE
instruction at session start (hooks/caveman-session-default.py) and arm_b for the
score line with the next prompt. The bits are logged whether or not a treatment
runs, so the rows before the trial are its A/A control. GRIP_STE_TRIAL=1 turns the
treatments on, and GRIP_STE_FEEDBACK=trial hands the line to the model only in arm
B. The row also carries the hidden counters of lib/ste_counters.py, which the model
never reads: scripts/ste-arm-report.py judges the arms by them.

Since 2026-10-18 the row also carries the action-first counters of
lib/action_first_counters.py under their own keys ("action_first",
"action_first_version"): openers, closers, recap narration, sidebars, idioms, empty
hedges, and whether the first and last lines of a substantive turn carry the fact
and the next step. They are row-only. The arm report reads a fixed key set, so a
sibling key cannot move its verdict, and the visible line does not name them
(docs/measurements/2026-10-18-action-first-counters.md registers when it may).

The row never holds turn text. The scorer returns counts. This module adds a
time, a session id, two lengths and two flags. GRIP_STE_VISIBLE=0 silences
both events, and the row is written either way.

The text of the turn comes from the payload only (the last_assistant_message
key on Claude Code 2.1.289). The transcript file is not read: at the time of
the Stop event it held the turn in 1 of 4 probes, so it would give the score of
the turn before, and its size has no limit.

The log does not grow without end. At 8 MB the hook starts a new file and keeps
the old one under a dated name, and it removes the dated files that are older
than 90 days.

This module opens no connection and starts no process. tests/test_ste_lint_hook.py
runs the real hook and checks which modules it loaded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

MAX_TEXT_CHARS = 60000          # the scorer needs 80 ms for this much text (measured)
LINE_MAX_AGE_S = 24 * 3600      # a kept line that no prompt claimed for a day is removed
LOG_MAX_BYTES = 8 * 1024 * 1024         # about 20,000 rows, then a new file starts
LOG_MAX_AGE_S = 90 * 24 * 3600          # a dated log file older than this is removed
#: The rules whose error fails a sentence and so moves the first number of the
#: line. Pinned to lib.ste_lint.SENTENCE_ERROR_RULES by tests/test_ste_lint_hook.py:
#: the two must never drift, because the line says which findings count.
SENTENCE_RULES = ("sentence-length", "semicolon")
SWITCH = "GRIP_STE_VISIBLE"     # "0" silences the line on both events
FEEDBACK = "GRIP_STE_FEEDBACK"  # "1" hands the line to the model with the next prompt; "trial" only in arm B
TRIAL = "GRIP_STE_TRIAL"        # "1" turns the two trial arms on; the bits are logged either way
COUNTERS_VERSION_UNKNOWN = "none"   # written when lib/ste_counters.py could not be loaded
LOG_RELPATH = Path("logs") / "ste-lint-hook.jsonl"
LINES_RELPATH = Path("state") / "ste" / "lines"
TEXT_KEYS = ("last_assistant_message", "assistant_message", "final_message", "message")
SAFE_SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")

# One finding, and more than one finding, in the words of the visible line. Every
# rule that the scorer counts (lib.ste_lint.RULES_CHECKED) has an entry here; a rule
# without one shows as its bare id, which tests/test_ste_lint_hook.py refuses.
PHRASES = {
    "sentence-length": ("sentence too long", "sentences too long"),
    "paragraph-length": ("paragraph too long", "paragraphs too long"),
    "unapproved-word": ("unapproved word", "unapproved words"),
    "determiner-floor": ("paragraph with too few articles", "paragraphs with too few articles"),
    "progressive": ('"is doing" verb form', '"is doing" verb forms'),
    # scorer version 2
    "one-instruction": ("sentence with more than one command", "sentences with more than one command"),
    "passive-instruction": ("passive instruction", "passive instructions"),
    "passive-descriptive": ("passive sentence", "passive sentences"),
    "ing-form": ('"-ing" verb form', '"-ing" verb forms'),
    "semicolon": ("semicolon", "semicolons"),
    "phrasal-verb": ("phrasal verb", "phrasal verbs"),
    "noun-cluster": ("long noun cluster", "long noun clusters"),
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(path) -> str:
    """Claude Code's name for a project directory, in the form this system compares names.

    The rule is lib/path_encoding.py's and is written nowhere else. The formula that
    stood here replaced "/" and "." only, so a Windows path kept its drive colon and
    its backslashes. On Windows a drive letter reaches a session in either case ("C:"
    from the system, "c:" from a Git Bash launch) and both name one folder, so the
    name is case-folded there. os.path.normcase changes nothing on POSIX.
    """
    from path_encoding import encode_project_path  # noqa: PLC0415

    return os.path.normcase(encode_project_path(path))


def in_grip_checkout(cwd, home) -> bool:
    """True when ``cwd`` is a GRIP checkout, by the slug rule of scripts/ste-baseline.py.

    The flag lets a reader of the log take the same sessions as the baseline does.
    A separator of either kind at the end of ``cwd`` is ignored.
    """
    if not isinstance(cwd, str):
        return False
    slug, home = _slug(cwd.rstrip("/\\")), Path(home)
    return (slug in (_slug(home / ".grip"), _slug(home / ".claude"))
            or slug.startswith(_slug(home / ".grip-worktrees") + "-"))


def turn_text(payload: dict) -> str:
    """The text of the turn from the payload, or "" when the payload holds none."""
    texts = (payload.get(key) for key in TEXT_KEYS)
    return next((text for text in texts if isinstance(text, str) and text.strip()), "")


def cut(rate: float, places: int) -> float:
    """A rate cut to ``places`` decimal places and never rounded up. 0.94996 is
    0.9499 at four places and 0.94 at two, so a rate below a target never shows
    as the target."""
    scale = 10 ** places
    return math.floor(rate * scale + 1e-9) / scale


def measured(text: str, scorer) -> dict:
    """The numbers of one turn. The scorer sees at most MAX_TEXT_CHARS characters."""
    score = scorer(text[:MAX_TEXT_CHARS])
    coverage = score.dictionary_coverage
    return {
        "scorer_version": score.scorer_version,
        "lexicon_source": score.lexicon_source,
        "sentences": score.sentences,
        "instructions": score.instructions,
        "structural_errors": score.structural_errors,
        "structural_pass_rate": cut(score.structural_pass_rate, 4),
        "dictionary_coverage": None if coverage is None else cut(coverage, 4),
        "per_rule": {rule: count for rule, count in score.per_rule.items() if isinstance(count, int)},
        "text_chars": len(text),
        "truncated": len(text) > MAX_TEXT_CHARS,
    }


def arms_of(session) -> dict:
    """The session's two trial arms, drawn from its id: ``a`` is the STE instruction
    at session start, ``b`` the score line with the next prompt. Two bits of a
    sha256 digest, so the same session always gets the same arms, two sessions
    get them independently, and nobody chooses. A session without a safe id is
    in neither arm."""
    if not safe_id(session):
        return {"a": False, "b": False}
    first = hashlib.sha256(str(session).encode("utf-8")).digest()[0]
    return {"a": bool(first & 1), "b": bool(first & 2)}


def trial_on(env) -> bool:
    return env.get(TRIAL) == "1"


def feedback_on(env, session) -> bool:
    """Whether the line reaches the model for this session: always under "1", in
    arm B only under "trial" while the trial runs, and never otherwise."""
    setting = env.get(FEEDBACK)
    if setting == "1":
        return True
    return setting == "trial" and trial_on(env) and arms_of(session)["b"]


def counters_of(text: str) -> tuple[dict | None, str]:
    """The hidden counters of lib/ste_counters.py and their version, or (None,
    "none") when that module is absent. Numbers only; the text is not kept."""
    try:
        import ste_counters  # noqa: PLC0415
    except ImportError:
        return None, COUNTERS_VERSION_UNKNOWN
    return ste_counters.count(text[:MAX_TEXT_CHARS]), ste_counters.COUNTERS_VERSION


def action_first_of(text: str) -> tuple[dict | None, str]:
    """The action-first counters of lib/action_first_counters.py and their version,
    or (None, "none") when that module is absent. Numbers only; the text is not kept.

    A fault inside the counters gives (None, "error:<name>"): the sibling key must
    never cost the row its STE numbers or the operator the turn's line, so the fault
    is recorded in the version slot and the row goes on."""
    try:
        import action_first_counters  # noqa: PLC0415
    except ImportError:
        return None, COUNTERS_VERSION_UNKNOWN
    try:
        return action_first_counters.count(text[:MAX_TEXT_CHARS]), action_first_counters.ACTION_FIRST_VERSION
    except Exception as fault:  # noqa: BLE001 -- recorded by name in the row, never raised
        return None, f"error:{type(fault).__name__}"


def _phrase(rule: str, count: int) -> str:
    one, many = PHRASES.get(rule) or (rule.replace("-", " "),) * 2
    return f"{count} {one if count == 1 else many}"


def _percent(rate: float) -> str:
    return f"{round(cut(rate, 2) * 100)}%"


def line_of(row: dict) -> str:
    """The visible line, in plain words. First the share of sentences that pass
    the scorer's sentence rules, with the findings that failed them in brackets:
    those are the only findings that move that number. Then the share of approved
    words when a dictionary is built. Then, apart, the three other rules with the
    most findings, which are warnings or judge a paragraph or a word. Until
    2026-10-10 the line ranked all rules by count alone, and a reader took a warning
    with 630 findings for a third of the score (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, D1)."""
    found = sorted(((count, rule) for rule, count in row["per_rule"].items() if count > 0),
                   key=lambda pair: (-pair[0], pair[1]))
    failing = [_phrase(rule, count) for count, rule in found if rule in SENTENCE_RULES]
    other = [_phrase(rule, count) for count, rule in found if rule not in SENTENCE_RULES]
    rates = f"{_percent(row['structural_pass_rate'])} of sentences pass the sentence rules"
    if failing:
        rates += f" ({', '.join(failing)})"
    if row["dictionary_coverage"] is not None:
        rates += f", {_percent(row['dictionary_coverage'])} of words are approved"
    if other:
        return f"STE: {rates}. Other findings: {', '.join(other[:3])}."
    return f"STE: {rates}. {'No other findings' if failing else 'No findings'}."


def context_of(line: str) -> str:
    """What the model reads with the next prompt: the line, which findings count,
    and the clause that keeps a shorter reply from counting as a better one."""
    return (f"Score of your last reply against Simplified Technical English (lib/ste_lint.py). {line} "
            "Only two rules count against the first number: a descriptive sentence has at most 25 words, "
            "an instruction 20, and prose has no semicolons. Keep every fact and every article: a shorter "
            "reply that drops either is worse, not better. The rules are in skills/clarify/SKILL.md.")


def _quiet(action, *args) -> bool:
    """Do one file action. A file fault must never stop the hook: it gives False."""
    try:
        action(*args)
    except OSError:
        return False
    return True


def _age(entry, now: float) -> float:
    """The age in seconds of a directory entry or a path. A file that is gone has no age."""
    try:
        return now - entry.stat().st_mtime
    except OSError:
        return 0.0


def rotate(log_path: Path, now: float) -> bool:
    """Start a new log when this one is large, and say whether that happened.

    The large file gets a second, dated name (a hard link), and then loses its
    first name. A link is refused when the dated name exists, so a session that
    rotates in the same second as another one changes nothing: an archive is
    never written over. A row that another session appends in between goes to
    the same file, so it is kept too. Dated files older than LOG_MAX_AGE_S are
    removed, so the logs have an end.
    """
    try:
        if log_path.stat().st_size <= LOG_MAX_BYTES:
            return False
        os.link(log_path, log_path.with_name(f"{log_path.stem}.{int(now)}{log_path.suffix}"))
    except OSError:
        return False
    _quiet(log_path.unlink)
    for dated in log_path.parent.glob(f"{log_path.stem}.*{log_path.suffix}"):
        if _age(dated, now) > LOG_MAX_AGE_S:
            _quiet(dated.unlink)
    return True


def _write_row(log_path: Path, row: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotate(log_path, time.time())
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def safe_id(session) -> bool:
    """True when a session id is safe as a file name: no path part, no dot."""
    return isinstance(session, str) and SAFE_SESSION.fullmatch(session) is not None


def _line_path(lines_dir: Path, session) -> Path | None:
    """The file of a session's line. None for an id that is not safe as a file name."""
    return Path(lines_dir) / f"{session}.txt" if safe_id(session) else None


def _write_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n", encoding="utf-8")


def keep_line(lines_dir: Path, session, line: str) -> bool:
    """Keep the line until the session's next prompt. False when it is not kept."""
    path = _line_path(lines_dir, session)
    return path is not None and _quiet(_write_line, path, line)


def claim_line(lines_dir: Path, session) -> str | None:
    """The kept line of a session, given at most once. None when there is none.

    The file is renamed before it is read. Only the caller whose rename works
    reads the line, so a file that cannot be removed is never given twice.
    """
    path = _line_path(lines_dir, session)
    if path is None:
        return None
    taken = path.with_name(f"{session}.{os.getpid()}.claimed.txt")
    try:
        os.replace(path, taken)
        line = taken.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    _quiet(taken.unlink)
    return line or None


def sweep(lines_dir: Path, now: float) -> int:
    """Remove the lines that no prompt claimed for a day, and say how many.

    A session that ended, or a run with no operator, never sends a next prompt.
    Without this, each such session would leave one small file for ever.
    """
    try:
        entries = list(os.scandir(lines_dir))
    except OSError:
        return 0
    old = [entry.path for entry in entries
           if entry.name.endswith(".txt") and _age(entry, now) > LINE_MAX_AGE_S]
    return sum(_quiet(os.unlink, path) for path in old)


def on_stop(payload: dict, env, claude_dir: Path, home: Path, scorer) -> str | None:
    """Score the turn and log its numbers. Give the JSON to print, or None for silence."""
    if payload.get("stop_hook_active") is True:
        return None     # a turn that another Stop hook forced: the turn before it is the scored one
    text = turn_text(payload)
    if not text:
        return None
    session = payload.get("session_id")
    visible = env.get(SWITCH) != "0"
    log_path, lines_dir = Path(claude_dir) / LOG_RELPATH, Path(claude_dir) / LINES_RELPATH
    arms = arms_of(session)
    row = {"ts": _now(), "session": session if safe_id(session) else None,
           "arm_a": arms["a"], "arm_b": arms["b"], "trial": trial_on(env)}
    try:
        row |= measured(text, scorer)
        counters, counters_version = counters_of(text)
        row |= {"counters": counters, "counters_version": counters_version}
        action_first, action_first_version = action_first_of(text)
        row |= {"action_first": action_first, "action_first_version": action_first_version}
    except Exception as fault:  # noqa: BLE001 -- a fault in the scorer is logged by name, never raised
        _quiet(_write_row, log_path, row | {"error": type(fault).__name__, "visible": visible})
        return None
    row |= {"in_grip_checkout": in_grip_checkout(payload.get("cwd"), home), "visible": visible}
    _quiet(_write_row, log_path, row)
    if not visible:
        return None
    line = line_of(row)
    if feedback_on(env, session):
        keep_line(lines_dir, session, line)
    sweep(lines_dir, time.time())
    return json.dumps({"systemMessage": line})


def on_prompt(payload: dict, env, claude_dir: Path) -> str | None:
    """Hand the kept line to the model, once. Give the JSON to print, or None."""
    session = payload.get("session_id")
    line = claim_line(Path(claude_dir) / LINES_RELPATH, session)
    if line is None or env.get(SWITCH) == "0" or not feedback_on(env, session):
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                               "additionalContext": context_of(line)}})
