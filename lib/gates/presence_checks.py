"""Find the presence checks in a shell text, so the env-var guard judges the rest.

A presence check asks whether a variable is set and prints nothing:
`test -n "$X"`, `test -z "$X"`, `[ -n "$X" ]` or `[[ -n "${X:-}" ]]`, standing
alone as a simple command. The env-var guard refuses any text that names a
secret-shaped variable, so it refused the very command its deny message advises
(GRIP#9650), and then any script or chained command holding one (GRIP#9911).

`strip_presence_checks` removes each check and returns the rest of the text, or
returns None, so the caller judges the text unchanged, when the text holds no
check or anything this reader does not model:

- substitutions, backticks, heredocs, here-strings, ANSI-C and locale quoting
  and line continuations (`_UNMODELLED`), because each can turn a check into
  printed text or run code this reader cannot see;
- parentheses and brace groups, which also define functions;
- a command word that is not plain text: an assignment such as
  `functions[test]=...`, `BASH_ALIASES[test]=echo` or `PS4=...`, or an
  expansion such as `${X:-eval}`, which the shell turns into another command;
- a command that can change how a later `test` runs, or make the shell print
  the commands it runs: `alias`, `eval`, `set -x`, `source` and the rest of
  `_SHELL_STATE`.

A check must be exact: only the reserved words in `_RESERVED` may come before
it, and nothing may follow it in its simple command, not even a redirection.
Each text is judged on its own, so this is a filter against accidental
exposure, not a sandbox: a `test` function defined in one text and a check run
from another are out of its sight. That is why the env-var guard uses it on
Bash commands only, which run at once in a fresh shell
(lib/gates/security_gates.bash_text_for_env_guard): a written file runs later,
perhaps under `bash -x`, whose trace prints the expanded check.
"""

from __future__ import annotations

import re

_UNMODELLED = ("$(", "`", "<<", "<(", ">(", "$'", '$"', "\\\n")
_RESERVED = frozenset({"!", "if", "then", "elif", "else", "while", "until", "do"})
_SHELL_STATE = frozenset({
    "alias", "unalias", "enable", "disable", "builtin", "command", "function",
    "source", ".", "eval", "exec", "trap", "set", "setopt", "unsetopt", "shopt",
    "emulate", "typeset", "declare", "local", "export", "readonly", "integer",
    "float", "autoload", "zmodload", "functions", "unfunction", "hash", "unhash",
    "let", "fc", "r", "zle", "bindkey", "noglob", "nocorrect", "coproc",
})
#: A command word the shell runs as written: no quote, expansion, glob,
#: assignment or brace. `[` and `[[` are the bracket checks' own command words.
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./+:@%-]+|\[\[?")
#: The checked variable: one double-quoted parameter, with at most a literal
#: default that holds no quote, `$`, backtick, backslash or newline.
_CHECK_VAR = re.compile(
    r'"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}"\'$`\\\n]*)?\})"')
_CLOSER = {"test": None, "[": "]", "[[": "]]"}

Word = tuple[str, int, int]


def _quote_end(text: str, i: int, quote: str) -> int | None:
    """Index of the quote that closes the one opened just before `i`."""
    if quote == "'":
        end = text.find("'", i)
        return None if end < 0 else end
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            return i
        else:
            i += 1
    return None


def _joins_redirection(text: str, i: int) -> bool:
    """True when the `&` or `|` at `i` belongs to a redirection (`2>&1`,
    `>|file`, `&>file`) rather than separating two commands."""
    return (i > 0 and text[i - 1] in "<>") or (text[i] == "&" and text[i + 1:i + 2] == ">")


def _word_end(text: str, i: int) -> int | None:
    """Index just past the word that starts at `i`, or None for an unclosed quote."""
    while i < len(text):
        ch = text[i]
        if ch in " \t\n;()" or (ch in "&|" and not _joins_redirection(text, i)):
            return i
        if ch in "'\"":
            end = _quote_end(text, i + 1, ch)
            if end is None:
                return None
            i = end + 1
        else:
            i += 2 if ch == "\\" else 1
    return len(text)


def _simple_commands(text: str) -> list[list[Word]] | None:
    """Each simple command's words as (raw text, start, end), or None when the
    text holds a parenthesis or an unclosed quote. A comment runs from a `#`
    that starts a word to the end of its line, and its words are not read."""
    commands: list[list[Word]] = []
    words: list[Word] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "()":
            return None
        if ch in "\n;" or (ch in "&|" and not _joins_redirection(text, i)):
            if words:
                commands.append(words)
                words = []
            i += 1
        elif ch in " \t":
            i += 1
        elif ch == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
        else:
            end = _word_end(text, i)
            if end is None:
                return None
            words.append((text[i:end], i, end))
            i = end
    if words:
        commands.append(words)
    return commands


def _first_command_word(words: list[Word]) -> int:
    """Index of the first word that is not a leading reserved word."""
    k = 0
    while k < len(words) and words[k][0] in _RESERVED:
        k += 1
    return k


def _is_check(raws: list[str]) -> bool:
    """True for exactly `test -n "$X"`, `[ -z "$X" ]` or `[[ -n "${X:-}" ]]`."""
    if len(raws) < 3 or raws[1] not in ("-n", "-z") or not _CHECK_VAR.fullmatch(raws[2]):
        return False
    closer = _CLOSER.get(raws[0], False)
    return closer is not False and raws[3:] == ([closer] if closer else [])


def _changes_shell(raws: list[str]) -> bool:
    """True when the command word is not plain text, or names a command that
    can change how a later `test` runs or make the shell print commands."""
    return bool(raws) and (not _PLAIN_WORD.fullmatch(raws[0]) or raws[0] in _SHELL_STATE)


def _check_spans(text: str) -> list[tuple[int, int]] | None:
    """Start and end of every presence check, or None when the text holds a
    construct or a command that this reader does not trust."""
    if any(marker in text for marker in _UNMODELLED):
        return None
    commands = _simple_commands(text)
    if commands is None:
        return None
    spans = []
    for words in commands:
        k = _first_command_word(words)
        raws = [w[0] for w in words[k:]]
        if _changes_shell(raws):
            return None
        if _is_check(raws):
            spans.append((words[k][1], words[-1][2]))
    return spans


def strip_presence_checks(text: str) -> str | None:
    """The text with every presence check removed, or None when it holds no
    check or anything this reader does not model (see the module docstring)."""
    spans = _check_spans(text)
    if not spans:
        return None
    pieces, last = [], 0
    for start, end in spans:
        pieces.append(text[last:start])
        last = end
    pieces.append(text[last:])
    return "".join(pieces)
