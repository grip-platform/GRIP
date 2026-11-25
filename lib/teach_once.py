"""Teach-once: deliver a command-moment rule at the first command it is about.

WHY. KERN v2 (GRIP#6658) is taking rules out of the text every session loads at
start-up, because Claude Code warns once that text passes 150,000 characters and
GRIP's always-loaded floor alone is over it. V>>'s ruling 1(a) of 2026-10-18
moves three rules to the moment they matter: commit-standards,
gpt-on-the-subscription-never-credits and gh-repo-resolution are each about one
kind of command, so each is taught at the first such command instead. This
module is that mechanism. It changes no rule's loading: that is a later pull
request, made once this channel is live, because a rule may name a channel only
once the channel works.

HOW. hooks/teach-once-gate.py runs this on every Bash command (PreToolUse). The
FIRST time a command triggers a rule in a recipient context, the gate refuses
that one command, and the refusal reason is a one-line note followed by the
rule's full text. The model reads the rule in the refusal and runs the command
again, which passes, as does every later command that triggers the same rule.
A context is the main session or one subagent: (session_id, agent_id or
"main"). A rule's version is the sha256 of its text, so an edited rule is taught
again (ruling 5: once per recipient context and rule version). Compaction
summarises the refusal away while the session id stays, so a PreCompact hook
(the same script with --renew) clears the session's record and every rule is
taught again after it (ruling 5: renewed after compaction). /clear and /resume
need nothing: each gives the conversation a new session id (measured 2026-10-05,
lib/infinite_cycle_runner.py), and a new id starts with an empty record.

TRIGGERS. The command is split into simple commands (quotes respected,
here-document bodies dropped), and each one's program is found past leading
variable assignments and wrappers (rtk, env, timeout, xargs, sudo and the
like). So `git -C <dir> commit`, `/usr/bin/git commit`, `cd x && git commit`
and `bash -c "codex exec ..."` all count, while `echo "git commit"` and
`cat bin/grip-codex-ladder` do not.

  rules/commit-standards.md                       git commit; gh pr create; gh pr edit
  rules/gpt-on-the-subscription-never-credits.md  codex, any subcommand; grip-codex-ladder
  rules/gh-repo-resolution.md                     a gh call that acts on the current
                                                  repository, with no -R/--repo and no
                                                  GH_REPO set for it

The gh trigger names the calls that act on the current repository when none is
given: the groups whose help lists -R/--repo (pr, issue, run, ...), `gh repo`
except list, clone and create, and `gh api` with an {owner}, {repo} or {branch}
placeholder. Every other gh command (gist, org, search, auth, an extension, ...)
resolves no repository that a default could misdirect. GH_REPO is tracked as the
shell would track it: unset, `export -n` or an empty value clears it, `env -u` or
`env -i` clears it for one program, and a subshell keeps its changes to itself.
It only teaches: hooks/gh-default-repo-gate.py still refuses a call
bound for the wrong repository, and none of its remote-resolution logic is
repeated here. One command can trigger several rules (`gh pr create` with no -R
triggers two). Every untaught one goes in ONE refusal, so the note's promise
holds: the command, run again, passes.

THE RECORD. state/teach-once/<session_id>.json under CLAUDE_DIR, written
atomically: {"contexts": {"main": {"rules/commit-standards.md": "<sha256>"},
"<agent_id>": {...}}}. Records last written over seven days ago are removed
whenever a lesson is recorded; a session that outlives that is taught again,
which is the safe direction. Two hooks racing on one session can both refuse,
or one can drop the other's mark: each costs one extra teaching and never skips
one, so there is no lock.

ESCAPE HATCH (rules/gate-resolution-escape-hatch.md, Invariant 1). The denied
set is one command, once per context and rule version, and the resolution is to
run the command again, which lies outside the denied set. That holds only if
the record survives, so the gate writes the lesson's record BEFORE it refuses,
and does not refuse when the record cannot be written: a refusal is only ever
issued with the mark that lets the re-run through already on disk. The price is
one rare loss: a refusal that cannot be printed after its record was written
leaves the rule marked but untaught until the next renewal. A rule file that
cannot be read, a payload with no session_id, or any error inside the gate lets
the command run (fail open), and each such outcome is logged to
logs/teach-once-gate.jsonl. So the gate cannot deadlock.

REASON LENGTH, measured 2026-10-18 on Claude Code 2.1.301 (claude -p in an
empty config directory, claude-haiku-4-5): refusal reasons of 9,000, 10,600,
21,000 and 60,000 characters each reached the model's tool result whole, as
"PreToolUse:Bash hook error: " followed by the reason (the stream-json output
and the session transcript agree, and nothing was spilled to a file). The
longest refusal here, all three rules at once, is about 20,500 characters.
SessionStart additionalContext behaves differently: over 10,000 characters it
reaches the model only as a preview (lib/kern_probes.py).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

COMMIT_RULE = "rules/commit-standards.md"
GPT_RULE = "rules/gpt-on-the-subscription-never-credits.md"
GH_RULE = "rules/gh-repo-resolution.md"
#: Every rule this gate teaches, in the order a refusal presents them.
RULES = (COMMIT_RULE, GPT_RULE, GH_RULE)

#: The record's folder, relative to CLAUDE_DIR. ``state/**/*.json`` is gitignored.
STORE_DIR = Path("state") / "teach-once"
MAX_AGE_S = 7 * 24 * 3600
MAIN = "main"

_PUNCT = "();<>|&`\n"
_PUNCT_CHARS = frozenset(_PUNCT)
#: Characters that end a simple command even inside a punctuation run such as `);`.
_BREAKS = frozenset(";()`\n")
_REDIRECT_CHARS = frozenset("<>")
_FALLBACK_TOKEN_RE = re.compile(r"[();<>|&`\n]+|[^\s();<>|&`]+")
#: A here-document operator at the scan position, `<<` or `<<-`, and its delimiter
#: word, quoted or bare. A here-string (`<<<`) is stepped over before this is tried.
_HEREDOC_OP_RE = re.compile(r"<<(-?)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([A-Za-z_][\w.-]*))")
#: A `#` starts a comment only at the start of a word.
_COMMENT_AFTER = frozenset(" \t;&|()")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_DEPTH = 3

#: Words that run the word after them as the program: wrappers, and the shell
#: keywords that can open a simple command.
_WRAPPERS = frozenset({
    "rtk", "proxy", "env", "command", "builtin", "exec", "nohup", "time", "nice",
    "sudo", "noglob", "caffeinate", "timeout", "gtimeout", "xargs", "npx", "bunx",
    "!", "{", "if", "then", "elif", "else", "do", "while", "until",
})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                "--super-prefix", "--config-env", "--attr-source"})
_GH_VALUE_OPTIONS = frozenset({"-R", "--repo"})
#: gh groups that act on the current repository unless -R/--repo names one: the
#: groups whose help lists `-R, --repo` (measured on gh 2.96.0, 2026-10-18). Every
#: other group (gist, org, search, ssh-key, gpg-key, status, codespace, project,
#: attestation, auth, an extension, ...) resolves no default, so it teaches nothing.
_GH_REPO_GROUPS = frozenset({"pr", "issue", "release", "run", "workflow", "cache", "secret",
                             "variable", "label", "ruleset", "browse"})
#: `gh repo` takes its repository as an argument and defaults to the current one,
#: except in these actions, which never use the current repository.
_GH_REPO_FREE_ACTIONS = frozenset({"list", "clone", "create"})
#: `gh api` uses the current repository only through these placeholders.
_GH_API_PLACEHOLDERS = ("{owner}", "{repo}", "{branch}")
_CODEX_PROGRAMS = frozenset({"codex", "grip-codex-ladder"})
_NO_OPTIONS: frozenset = frozenset()
_TIMEOUT_OPTIONS = frozenset({"-s", "--signal", "-k", "--kill-after"})
#: Wrapper options whose next word is their value, so that word is not the program.
_WRAPPER_VALUE_OPTIONS = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-U", "--other-user", "-C",
                       "--close-from", "-D", "--chdir", "-p", "--prompt", "-r", "--role",
                       "-t", "--type", "-T", "--command-timeout"}),
    "timeout": _TIMEOUT_OPTIONS,
    "gtimeout": _TIMEOUT_OPTIONS,
    "nice": frozenset({"-n", "--adjustment"}),
    "xargs": frozenset({"-I", "-L", "-n", "-P", "-s", "-E", "-d", "-a"}),
}
_GH_REPO = "GH_REPO"
_GH_REPO_SET = _GH_REPO + "="
#: env options that start the program with GH_REPO cleared.
_ENV_CLEARS = frozenset({"-i", "--ignore-environment", "-", "--unset=" + _GH_REPO})


@dataclass(frozen=True)
class Trigger:
    """One rule a command triggered, and the words that did it (for the audit log)."""

    rule: str
    what: str


@dataclass(frozen=True)
class Lesson:
    """A rule to teach now: its path, its version and its full text."""

    rule: str
    version: str
    text: str


@dataclass(frozen=True)
class Decision:
    """What the gate decided for one command, in one context."""

    action: str  # "none" | "teach" | "pass" | "open"
    context: str
    triggers: tuple = ()
    lessons: tuple = ()
    note: str = ""


# -- reading a command ----------------------------------------------------------

def _quoted_step(line: str, at: int, quote: str) -> tuple:
    """One character inside a quoted string: (next position, the quote still open or None)."""
    if quote == '"' and line[at] == "\\":
        return at + 2, quote
    return at + 1, (None if line[at] == quote else quote)


def _bare_step(line: str, at: int) -> tuple:
    """One step outside quotes: (next position, a quote it opens, a here-document it opens)."""
    char = line[at]
    if char == "\\":
        return at + 2, None, None
    if char in "'\"":
        return at + 1, char, None
    if char == "#" and (at == 0 or line[at - 1] in _COMMENT_AFTER):
        return len(line), None, None
    if line.startswith("<<<", at):
        return at + 3, None, None
    match = _HEREDOC_OP_RE.match(line, at)
    if not match:
        return at + 1, None, None
    delimiter = next(word for word in match.groups()[1:] if word is not None)
    return match.end(), None, (delimiter, bool(match.group(1)))


def _scan_line(line: str, quote) -> tuple:
    """(the quote still open after ``line``, the here-documents it opens outside quotes)."""
    starts: list = []
    at = 0
    while at < len(line):
        if quote:
            at, quote = _quoted_step(line, at, quote)
            continue
        at, quote, start = _bare_step(line, at)
        if start:
            starts.append(start)
    return quote, starts


def strip_heredocs(command: str) -> str:
    """``command`` without here-document bodies, whose lines are text, not commands.

    Public because lib/untrusted_egress.py reads commands for sends with it.

    Operators are found by a scan that follows quotes and comments, so a quoted
    or commented `<<EOF` opens nothing and cannot hide the lines after it.
    """
    kept: list = []
    pending: list = []
    quote = None
    for line in command.split("\n"):
        if pending and quote is None:
            delimiter, tabs = pending[0]
            if (line.lstrip("\t") if tabs else line) == delimiter:
                pending.pop(0)
            continue
        kept.append(line)
        quote, starts = _scan_line(line, quote)
        pending.extend(starts)
    return "\n".join(kept)


def _tokens(text: str) -> list:
    """Shell words and separator runs, quotes respected and removed."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # an unclosed quote: a plain split still finds the programs
        return _FALLBACK_TOKEN_RE.findall(text)


def _is_redirection(token: str) -> bool:
    """`<`, `>>`, `>&`, `&>`, `>|`, `<<<`: an operator whose next word is its operand."""
    chars = set(token)
    return bool(chars & _REDIRECT_CHARS) and not chars & _BREAKS


def _drop_descriptor(words: list) -> None:
    """Drop the descriptor number a redirection was written after (the 2 of `2>`)."""
    if words and words[-1].isdigit():
        words.pop()


def _separate(token: str, items: list) -> None:
    """End the current command at a separator, keeping its subshell parentheses in order."""
    items.extend(char for char in token if char in "()")
    items.append([])


def _simple_commands(command: str) -> list:
    """The command's simple commands, each a list of words, with "(" and ")" between
    them wherever a subshell opens or closes.

    A redirection stays inside its command, and its operand (a file, a descriptor,
    a here-document delimiter) is dropped rather than read as a command of its
    own, so `echo hi > /tmp/codex` runs no codex.
    """
    items: list = [[]]
    operand = False
    for token in _tokens(strip_heredocs(command)):
        punct = bool(token) and set(token) <= _PUNCT_CHARS
        if operand and not punct:
            operand = False
        elif punct and _is_redirection(token):
            _drop_descriptor(items[-1])
            operand = True
        elif punct:
            _separate(token, items)
            operand = False
        else:
            items[-1].append(token)
    return [item for item in items if item]


def _assigned_repo(word: str):
    """True or False when ``word`` assigns GH_REPO (an empty value clears it), else None."""
    return len(word) > len(_GH_REPO_SET) if word.startswith(_GH_REPO_SET) else None


def _repo_change(wrapper, word: str, value: str):
    """How one prefix word changes GH_REPO for the program: True, False, or None for not at all."""
    if _ASSIGNMENT_RE.match(word):
        return _assigned_repo(word)
    if wrapper != "env":
        return None
    unsets = word in ("-u", "--unset") and value == _GH_REPO
    return False if unsets or word in _ENV_CLEARS else None


def _program_index(words: list) -> tuple:
    """(index of the word a simple command runs, or None; GH_REPO as its prefix leaves it).

    Steps past assignments, wrappers, their options, and the value of a wrapper
    option that takes one (`sudo -u user`, `env -u NAME`). The second value is
    True or False when the prefix sets or clears GH_REPO for that one program
    (`GH_REPO=o/r gh ...`, `GH_REPO= gh ...`, `env -u GH_REPO gh ...`, `env -i`),
    and None when it leaves it alone.
    """
    wrapper, repo, at = None, None, 0
    while at < len(words):
        word = words[at]
        change = _repo_change(wrapper, word, _word_at(words, at + 1))
        repo = repo if change is None else change
        if word in _WRAPPERS:
            wrapper = word
        elif word in _WRAPPER_VALUE_OPTIONS.get(wrapper, _NO_OPTIONS):
            at += 1
        elif not (_ASSIGNMENT_RE.match(word) or (wrapper and _wrapper_argument(word))):
            return at, repo
        at += 1
    return None, repo


def _wrapper_argument(word: str) -> bool:
    return word.startswith("-") or bool(_NUMBER_RE.fullmatch(word))


def _name(word: str) -> str:
    name = PurePosixPath(word.replace("\\", "/")).name
    return name[:-4] if name.lower().endswith(".exe") else name


def _first_positional(words: list, start: int, value_options: frozenset):
    """Index of the first word at or after ``start`` that is not an option or an option's value."""
    index = start
    while index < len(words):
        if words[index] in value_options:
            index += 2
        elif words[index].startswith("-"):
            index += 1
        else:
            return index
    return None


def _word_at(words: list, index) -> str:
    return words[index] if index is not None and index < len(words) else ""


def _names_repo(word: str) -> bool:
    return (word in _GH_VALUE_OPTIONS or word.startswith("--repo=")
            or (word.startswith("-R") and len(word) > 2))


def _last_assignment(words: list, current: bool) -> bool:
    changes = [change for change in map(_assigned_repo, words) if change is not None]
    return changes[-1] if changes else current


def _repo_after(words: list, repo_set: bool) -> bool:
    """Whether GH_REPO is exported and set for the commands after ``words``.

    `export GH_REPO=...` sets it, and an empty value clears it; `unset GH_REPO`
    and `export -n GH_REPO` clear it; a bare `GH_REPO=...` changes it only while
    it is already exported, because a bare assignment exports nothing new.
    """
    head, args = words[0], words[1:]
    if head == "unset" and _GH_REPO in args:
        return False
    if head == "export":
        cleared = "-n" in args and _GH_REPO in args
        return False if cleared else _last_assignment(args, repo_set)
    if repo_set and all(map(_ASSIGNMENT_RE.match, words)):
        return _last_assignment(words, repo_set)
    return repo_set


# -- what each program triggers ---------------------------------------------------

def _git_triggers(words: list, start: int) -> list:
    sub = _word_at(words, _first_positional(words, start, _GIT_VALUE_OPTIONS))
    return [Trigger(COMMIT_RULE, "git commit")] if sub == "commit" else []


def _has_placeholder(word: str) -> bool:
    return any(placeholder in word for placeholder in _GH_API_PLACEHOLDERS)


def _resolves_current_repo(group: str, action: str, args: list) -> bool:
    """True when this gh call acts on the current repository unless one is named."""
    if group == "api":
        return any(map(_has_placeholder, args))
    if group == "repo":
        return bool(action) and action not in _GH_REPO_FREE_ACTIONS
    return group in _GH_REPO_GROUPS


def _gh_triggers(words: list, start: int, repo_set: bool) -> list:
    group_at = _first_positional(words, start, _GH_VALUE_OPTIONS)
    group = _word_at(words, group_at)
    if not group:
        return []
    action = _word_at(words, _first_positional(words, group_at + 1, _GH_VALUE_OPTIONS))
    found = []
    if group == "pr" and action in ("create", "edit"):
        found.append(Trigger(COMMIT_RULE, f"gh pr {action}"))
    named = repo_set or any(map(_names_repo, words[start:]))
    if not named and _resolves_current_repo(group, action, words[start:]):
        found.append(Trigger(GH_RULE, f"gh {group} with no -R and no GH_REPO"))
    return found


def _codex_triggers(name: str, words: list, start: int) -> list:
    sub = _word_at(words, _first_positional(words, start, _NO_OPTIONS))
    return [Trigger(GPT_RULE, f"{name} {sub}".strip())]


def _shell_triggers(words: list, start: int, repo_set: bool, depth: int) -> list:
    """``bash -c "<command>"`` is read as that command; ``bash <script>`` runs the script."""
    for index in range(start, len(words)):
        word = words[index]
        if word.startswith("-") and not word.startswith("--") and word.endswith("c"):
            return _triggers(_word_at(words, index + 1), repo_set, depth)
        if not word.startswith("-"):
            return _program_triggers(words, index, repo_set, depth)
    return []


def _program_triggers(words: list, index: int, repo_set: bool, depth: int) -> list:
    name = _name(words[index])
    if name == "git":
        return _git_triggers(words, index + 1)
    if name == "gh":
        return _gh_triggers(words, index + 1, repo_set)
    if name in _CODEX_PROGRAMS:
        return _codex_triggers(name, words, index + 1)
    if name in _SHELLS and depth < _MAX_DEPTH:
        return _shell_triggers(words, index + 1, repo_set, depth + 1)
    return []


def _simple_triggers(words: list, repo_set: bool, depth: int) -> list:
    index, prefix_repo = _program_index(words)
    if index is None:
        return []
    repo = repo_set if prefix_repo is None else prefix_repo
    return _program_triggers(words, index, repo, depth)


def _triggers(command: str, repo_set: bool, depth: int) -> list:
    """Walk the simple commands in order, carrying GH_REPO's state from one to the next.

    A subshell, `( ... )` or `$( ... )`, keeps its changes to itself: the state is
    saved at its "(" and restored at its ")".
    """
    found: list = []
    saved: list = []
    for item in _simple_commands(command):
        if item == "(":
            saved.append(repo_set)
        elif item == ")":
            repo_set = saved.pop() if saved else repo_set
        else:
            found.extend(_simple_triggers(item, repo_set, depth))
            repo_set = _repo_after(item, repo_set)
    return found


def triggers(command: str, environ=None) -> list:
    """Every ``Trigger`` in ``command``, in the order the command runs them."""
    environ = os.environ if environ is None else environ
    repo_set = bool(str(environ.get("GH_REPO", "")).strip())
    return _triggers(command or "", repo_set, 0)


def _matched_rules(found) -> list:
    matched = {trigger.rule for trigger in found}
    return [rule for rule in RULES if rule in matched]


def rules_for(command: str, environ=None) -> tuple:
    """Every rule ``command`` triggers, once each, in teaching order."""
    return tuple(_matched_rules(triggers(command, environ)))


def rule_for(command: str, environ=None):
    """The first rule ``command`` triggers in teaching order, or None."""
    rules = rules_for(command, environ)
    return rules[0] if rules else None


# -- the record -------------------------------------------------------------------

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule_version(text: str) -> str:
    """A rule's version: the sha256 of its text, so an edited rule is taught again."""
    return _digest(text)


def context_key(agent_id) -> str:
    """The recipient context: the subagent's id, or "main" for the session itself."""
    return agent_id if isinstance(agent_id, str) and agent_id else MAIN


def store_path(root, session_id: str) -> Path:
    """The session's record. An id that is not a plain file name is hashed, never joined."""
    stem = session_id if _SAFE_ID_RE.fullmatch(session_id) else "sha-" + _digest(session_id)[:32]
    return Path(root) / STORE_DIR / f"{stem}.json"


def load_store(root, session_id: str) -> dict:
    """``{context: {rule: version}}`` for the session; empty when absent or unreadable."""
    try:
        data = json.loads(store_path(root, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    contexts = data.get("contexts") if isinstance(data, dict) else None
    return contexts if isinstance(contexts, dict) else {}


def should_teach(store: dict, context: str, rule: str, version: str) -> bool:
    """True unless ``context`` was already taught this version of ``rule``."""
    taught = store.get(context)
    return not isinstance(taught, dict) or taught.get(rule) != version


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, sort_keys=True)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _remove_if_stale(entry, cutoff: float, keep: str) -> None:
    with contextlib.suppress(OSError):
        if entry.name.endswith(".json") and entry.path != keep and entry.stat().st_mtime < cutoff:
            os.unlink(entry.path)


def _prune(folder: Path, cutoff: float, keep: Path) -> None:
    """Remove records, and stray temporary files, last written before ``cutoff``."""
    with contextlib.suppress(OSError), os.scandir(folder) as entries:
        for entry in entries:
            _remove_if_stale(entry, cutoff, str(keep))


def mark_taught(root, session_id: str, context: str, versions: dict) -> None:
    """Record that ``context`` was taught ``{rule: version}``. Raises OSError when it cannot."""
    store = load_store(root, session_id)
    taught = store.get(context)
    store[context] = {**(taught if isinstance(taught, dict) else {}), **versions}
    path = store_path(root, session_id)
    _write_atomic(path, {"contexts": store})
    _prune(path.parent, time.time() - MAX_AGE_S, path)


def renew(root, session_id: str) -> bool:
    """Forget every lesson the session was taught, so each is taught again; True if one was held."""
    try:
        store_path(root, session_id).unlink()
    except FileNotFoundError:
        return False
    return True


# -- the decision -----------------------------------------------------------------

def read_rule(root, rule: str):
    """The rule's text, or None when it cannot be read as UTF-8 text."""
    try:
        return (Path(root) / rule).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _lesson(root, rule: str):
    text = read_rule(root, rule)
    return None if text is None else Lesson(rule, rule_version(text), text)


def decide(command: str, session_id, agent_id, root, environ=None) -> Decision:
    """What to do with one Bash command; see the module docstring."""
    context = context_key(agent_id)
    found = tuple(triggers(command or "", environ))
    if not found:
        return Decision("none", context)
    if not isinstance(session_id, str) or not session_id:
        return Decision("open", context, found, note="no session_id, so a lesson cannot be kept")
    store = load_store(root, session_id)
    loaded = {rule: _lesson(root, rule) for rule in _matched_rules(found)}
    missing = [rule for rule, lesson in loaded.items() if lesson is None]
    lessons = tuple(lesson for lesson in loaded.values()
                    if lesson is not None and should_teach(store, context, lesson.rule, lesson.version))
    note = "unreadable: " + ", ".join(missing) if missing else ""
    if lessons:
        return Decision("teach", context, found, lessons, note)
    return Decision("open" if missing else "pass", context, found, note=note)


def refusal_reason(lessons) -> str:
    """The one-line note, then each rule's full text."""
    names = ", ".join(lesson.rule for lesson in lessons)
    head = ("[GRIP teach-once] This is a one-time teaching refusal, not a judgement on the "
            f"command: read {names} below, then run the same command again and it will pass.")
    return "\n\n".join([head, *(lesson.text.strip() for lesson in lessons)])


def audit_record(decision: Decision) -> dict:
    """What the audit log keeps of a decision: rules, triggers and versions, never the command."""
    return {"action": decision.action, "context": decision.context,
            "rules": _matched_rules(decision.triggers),
            "triggers": [trigger.what for trigger in decision.triggers],
            "taught": {lesson.rule: lesson.version[:12] for lesson in decision.lessons},
            "note": decision.note}
