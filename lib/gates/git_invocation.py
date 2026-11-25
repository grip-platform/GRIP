"""Look past git's global options to the subcommand a command runs.

Git reads global options between ``git`` and its subcommand:
``git -C <path> push``, ``git -c <name>=<value> reset --hard``,
``git --git-dir=<path> restore <file>``. The gates' patterns expect ``git``
directly before the subcommand, so they test a copy of the command with those
options removed (``without_global_options``). A gate that must know WHICH
repository a command acts on reads the options with ``repository_options``.

A ``cd <folder>`` earlier in the command moves git as ``-C <folder>`` does, so
``repository_options`` reads it as a leading ``-C`` (GRIP#8657). A change of
folder it cannot follow reads as a repository it cannot read.
"""

import os
import re
import shlex

# One shell word: plain characters and whole quoted runs, joined, as in
# -c core.sshCommand="ssh -i key". Each step takes one character or one quoted
# run, so the pattern has no nested quantifier to backtrack through.
_WORD = r"""(?:[^\s"';&|]|"[^"]*"|'[^']*')+"""

# Long options that take their value as the next word as well as after "=".
_LONG_WITH_VALUE = ("git-dir", "work-tree", "namespace", "super-prefix",
                    "config-env", "attr-source")

_OPTION = (
    r"(?:-[Cc]\s+" + _WORD
    + r"|--(?:" + "|".join(_LONG_WITH_VALUE) + r")(?:=|\s+)" + _WORD
    + r"|--[a-z][a-z-]*(?:=" + _WORD + r")?"
    + r"|-[pP])"
)

_GIT_WITH_OPTIONS = re.compile(r"\bgit(?:\s+" + _OPTION + r")+(?=\s)")
_GIT_INVOCATION = re.compile(
    r"\bgit(?P<options>(?:\s+" + _OPTION + r")*)\s+(?P<subcommand>[a-z][a-z-]*)\b")

# Options whose value is the next word when "=" does not join it.
_TAKES_NEXT_WORD = frozenset({"-C", "-c"} | {f"--{name}" for name in _LONG_WITH_VALUE})
# Options that choose the repository a command acts on.
_SELECTS_REPOSITORY = frozenset({"-C", "--git-dir", "--work-tree"})

# One shell token, for reading the commands before a git invocation: an
# operator, or a word as _WORD reads one, except that a parenthesis ends the
# word, because it opens or closes a subshell. Quoted text stays inside its
# word, so an operator inside quotes is not read as one.
_TOKEN = re.compile(
    r"""[ \t\r]*(?:(?P<operator>&&|\|\||[;&|()\n])"""
    r"""|(?P<word>(?:[^\s"';&|()]|"[^"]*"|'[^']*')+))""")

# The commands that change the shell's folder, and the two the gates follow;
# the words of shell control flow, which can make any change of folder
# conditional; the words that may lead a command; the operators after which
# the next command runs in the same shell, in turn; and those after which it
# runs whatever the command before it did.
_FOLLOWED = frozenset({"cd", "pushd"})
_CHANGES_FOLDER = _FOLLOWED | {"popd"}
_CONTROL_WORDS = frozenset({"!", "{", "}", "if", "then", "else", "elif", "fi",
                            "case", "esac", "for", "select", "while", "until",
                            "do", "done", "function"})
_LEADS_A_COMMAND = _CONTROL_WORDS | {"time", "builtin", "command"}
_SAME_SHELL = frozenset({"&&", ";", "\n"})
_STARTS_A_COMMAND = frozenset({";", "\n", "("})
# A change-of-folder command anywhere in text the token reader cannot read.
_NAMES_A_FOLDER_CHANGE = re.compile(r"(?<![\w./-])(?:cd|pushd|popd)(?![\w./-])")


def without_global_options(command: str) -> str:
    """The command with each ``git <global options> <subcommand>`` written as
    ``git <subcommand>``, for patterns that expect the two words together."""
    return _GIT_WITH_OPTIONS.sub("git", command)


def repository_options(command: str, subcommand: str) -> list:
    """For each ``git ... <subcommand>`` in the command, the options that choose
    its repository, as argument words: ``[]`` when it acts on the current
    directory, and ``None`` when a value cannot be read. A long quoted path
    reaches the gates blanked to "", so it names a repository no gate can see.
    A change of folder before the invocation leads its words as a ``-C``."""
    return [_joined(_folder_options(command[:match.start()]),
                    _repository_words(match.group("options")))
            for match in _GIT_INVOCATION.finditer(command)
            if match.group("subcommand") == subcommand]


def _repository_words(options: str):
    """The ``-C``, ``--git-dir`` and ``--work-tree`` words of one invocation."""
    try:
        words = iter(shlex.split(options))
    except ValueError:
        return None
    chosen = []
    for word in words:
        name, joined, value = word.partition("=") if word.startswith("--") else (word, "", "")
        if not joined and name in _TAKES_NEXT_WORD:
            value = next(words, "")
        if name in _SELECTS_REPOSITORY:
            if not value:
                return None
            chosen += [name, os.path.expanduser(os.path.expandvars(value))]
    return chosen


def _joined(folder, words):
    """The folder's ``-C`` words, then the invocation's own; ``None`` when
    either cannot be read."""
    return None if folder is None or words is None else folder + words


def _folder_options(prefix: str):
    """Where the commands before a git invocation leave the shell, as ``-C``
    words: ``[]`` when they change no folder, ``["-C", <folder>]`` after a
    ``cd`` or ``pushd`` joined to git by ``&&``, ``;`` or a new line, and
    ``None`` for a change of folder after which git may run elsewhere."""
    commands, operators = _commands(prefix)
    if commands is None:
        return None if _NAMES_A_FOLDER_CHANGE.search(prefix) else []
    changes = [index for index, words in enumerate(commands) if _changes_folder(words)]
    if not changes:
        return []
    if not _followable(commands, operators, changes):
        return None
    return _follow([commands[index] for index in changes])


def _followable(commands: list, operators: list, changes: list) -> bool:
    """Whether every change of folder has surely run by the time git runs: no
    shell control flow anywhere before git, where a plain ``cd`` can sit inside
    a branch or a loop, and each change placed as ``_has_run_when_git_runs``
    requires, which also rules out a subshell that closes before git."""
    if any(words and words[0] in _CONTROL_WORDS for words in commands):
        return False
    return all(_has_run_when_git_runs(index, operators) for index in changes)


def _has_run_when_git_runs(index: int, operators: list) -> bool:
    """Whether the change of folder at ``index`` has always run by the time git
    runs. Every operator from it to git keeps the same shell, and before it
    comes the start, ``;``, a new line or ``(``, or else ``&&`` with only
    ``&&`` after it, so that git runs only when the change ran."""
    after = operators[index:]
    if not _SAME_SHELL.issuperset(after):
        return False
    before = operators[index - 1] if index else ";"
    return before in _STARTS_A_COMMAND or (before == "&&" and set(after) <= {"&&"})


def _commands(text: str):
    """The commands in ``text`` as lists of unquoted words, and the operators
    between them; ``(None, None)`` when a quote is left open."""
    commands, operators, words = [], [], []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            if text[position:].strip():
                return None, None
            break
        position = match.end()
        if match.group("operator"):
            commands.append(words)
            operators.append(match.group("operator"))
            words = []
            continue
        word = _unquoted(match.group("word"))
        if word is None:
            return None, None
        words.append(word)
    commands.append(words)
    return commands, operators


def _unquoted(word: str):
    """One shell word with its quotes removed, or ``None`` when shlex cannot
    read it as exactly one word."""
    try:
        parts = shlex.split(word)
    except ValueError:
        return None
    return parts[0] if len(parts) == 1 else None


def _changes_folder(words: list) -> bool:
    """Whether a command changes the shell's folder: ``cd``, ``pushd`` or
    ``popd``, first or behind a keyword, ``!`` or ``{``."""
    for word in words:
        if word.lstrip("`") in _CHANGES_FOLDER:
            return True
        if word not in _LEADS_A_COMMAND:
            return False
    return False


def _follow(changes: list):
    """``["-C", <folder>]`` for the folder a run of ``cd`` and ``pushd``
    commands reaches from the current one, or ``None`` when one of them
    cannot be followed."""
    folder = ""
    for words in changes:
        folder = _entered(folder, words)
        if folder is None:
            return None
    return ["-C", folder]


def _entered(folder: str, words: list):
    """The folder one ``cd`` or ``pushd`` reaches from ``folder``. ``None``
    for ``popd``, a change behind a keyword, ``cd -``, a bare ``cd``, an
    option, a blanked or unset path, and a folder that cannot be found: there
    a failed ``cd`` leaves the shell where it was, so after ``;`` git runs
    there."""
    if words[0].lstrip("`") not in _FOLLOWED or len(words) != 2:
        return None
    target = os.path.expanduser(os.path.expandvars(words[1]))
    if not target or target.startswith("-") or "$" in target:
        return None
    path = os.path.join(folder, target)
    return path if os.path.isdir(path) else None
