"""Self-inflicted-tool-error detector: check your OWN command before blaming a tool.

Origin (2026-07-14, hypothesis H798 — the RTK incident): an ``rg -rn`` typo
silently rewrote every match in the command's own output (``-r`` is
``--replace``, not "recursive" — recursion is ripgrep's default), so
``claude-sonnet-4-6`` came back as ``claude-n-4-6``. The mangled reads were
blamed on an innocent external tool and a two-session false investigation
followed. The cheap check that kills this class: scan the COMMAND for known
footgun flags, and scan the OUTPUT for the mangling signature those footguns
leave behind.

This module is the pure, zero-side-effect detector behind
``hooks/tool-error-self-check.py``. Two entry points:

``scan_command(cmd)``
    Match the command against a data-driven FOOTGUN table (pattern + why +
    fix). Seeded with the incidents GRIP has actually paid for: ``rg -r``
    misread as recursive, bare ``sed -i`` on macOS, a pipeline ending on
    ``grep -c`` (exit 1 on zero matches fails monitored scripts), and zsh
    ``for x in $VAR`` (zsh does not word-split unquoted variables).

    Each entry matches a flag in the ARGUMENT LIST of an invocation of the
    tool it belongs to — never the same characters appearing in a commit
    message, an echoed string, or a documentation line the command is merely
    carrying (issue #4421).

``scan_output(text)``
    Conservative mangling-signature heuristic: the same single-letter
    segment appearing inside two or more DISTINCT hyphenated identifiers
    (the exact residue ``--replace`` substitution leaves). Precision is
    chosen over recall — common legitimate single-letter segments
    (``v``/``x`` version-tag shapes) are excluded, and one odd token alone
    never flags. Paraphrased or novel mangling shapes will NOT match; that
    is the documented false-negative boundary of an advisory-only detector.

    The two signals are NOT peers. A flag in a command is evidence about
    something the shell executed; a shape in output is evidence about
    arbitrary data, which may well be a document describing this very
    footgun. So the caller treats the output scan as CORROBORATION only —
    it enriches an advisory the command already earned, and never raises
    one by itself.

Both return plain dicts; no I/O, no state, no environment reads.
"""

from __future__ import annotations

import re
from collections import defaultdict

__all__ = [
    "scan_command",
    "scan_output",
    "strip_heredoc_bodies",
    "strip_quoted_content",
    "substitution_capable",
    "FOOTGUNS",
]

# ---------------------------------------------------------------------------
# Separating a command's STRUCTURE from the text it merely carries (#4421)
# ---------------------------------------------------------------------------
#
# A footgun is a FLAG, and a flag lives in the argument list of an invocation.
# The same characters appearing in a commit message, an echoed string, or a
# documentation line are data the command transports, not instructions it obeys.
# Matching raw text conflates the two: 91 of 186 live ``rg-replace-flag``
# advisories had walked across a pipe or a newline onto some other program's
# ``-r``, and 7 fired on prose that literally read "no -r".
#
# So every pattern below is matched against text that has had its data removed:
# heredoc BODIES dropped, quoted CONTENT blanked. The delimiters, the token
# count and the emptiness of each argument survive, because "ignore anything
# inside quotes" would disarm the scanner outright -- a genuine invocation
# almost always carries quoted arguments (``rg -rn 'pattern' path/``).

_LINE_CONTINUATION = re.compile(r"\\\r?\n[ \t]*")

# ``<<DELIM`` / ``<<-DELIM`` / ``<<'DELIM'`` / ``<<"DELIM"``. Deliberately does
# NOT require a newline straight after the delimiter: ``<<'MSG' && git push`` is
# a real and common shape, and the equivalent helper in lib/gates/bash_gates.py
# misses it (noted in issue #4421's definition of done).
_HEREDOC_START = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_-]*)\1")

# A single- or double-quoted argument. Single quotes are literal in every POSIX
# shell, so ``'[^']*'`` is exact; double quotes honour backslash escapes.
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")

# A shell comment: ``#`` starting a word, running to end of line. Anchoring on
# the preceding whitespace is what leaves ``${x#y}``, ``%h#%s`` and a URL
# fragment alone -- in all three the ``#`` is mid-word, so no comment begins.
_COMMENT = re.compile(r"(?:\A|(?<=\s))#")

# Stands in for removed argument content. Non-word and non-space, so it neither
# creates a ``\b`` boundary nor merges two tokens into one.
_OPAQUE = "\x00"


def _delimiters_opened_on(line: str) -> list[str]:
    """Heredoc delimiters this line really opens.

    A ``<<`` inside a quoted string opens nothing — ``echo 'see <<TAG'`` is one
    argument, not a redirection — and treating it as one would swallow every
    following line, hiding a real footgun further down. The quoted delimiter of
    a genuine heredoc (``<<'MSG'``) must still be read, so the test is
    positional: the ``<<`` itself must lie outside the quotes. Blanking is
    length-preserving, which is exactly what makes that offset check possible.
    """
    mask = strip_quoted_content(line)
    return [m.group(2) for m in _HEREDOC_START.finditer(line)
            if mask[m.start()] == "<"]


def strip_heredoc_bodies(command: str) -> str:
    """Drop every heredoc BODY, keeping the command lines around it.

    A heredoc body is data being handed to a program's stdin -- a commit
    message, a Python script, a documentation block. Nothing in it is a flag
    this shell will act on.
    """
    kept: list[str] = []
    pending: list[str] = []
    for line in (command if isinstance(command, str) else "").splitlines():
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(_delimiters_opened_on(line))
    return "\n".join(kept)


def _blank_one(match: re.Match[str]) -> str:
    """One quoted argument reduced to its delimiters."""
    quoted = match.group(0)
    body = "".join("\n" if ch == "\n" else _OPAQUE for ch in quoted[1:-1])
    return quoted[0] + body + quoted[-1]


def strip_quoted_content(command: str) -> str:
    """Blank the CONTENT of every quoted argument, keeping the quotes.

    Two properties the callers depend on. Emptiness survives, because the whole
    discriminator for ``sed -i`` on macOS is whether the next argument is the
    empty string. And length and line breaks survive, so the result doubles as
    a positional MASK: an offset that is inside a quote here is inside the same
    quote in the original.
    """
    return _QUOTED.sub(_blank_one, command if isinstance(command, str) else "")


def _uncomment(text: str) -> str:
    """Drop shell comments, which the shell never executes either.

    The mask from ``strip_quoted_content`` is what keeps a ``#`` inside a quoted
    argument from being mistaken for the start of a comment.
    """
    masked = strip_quoted_content(text)
    return "\n".join(
        line[:found.start()] if (found := _COMMENT.search(mask)) else line
        for line, mask in zip(text.splitlines(), masked.splitlines())
    )


def _shell_source(cmd: str) -> str:
    """Only what the shell will actually run: heredoc bodies dropped, comments
    dropped, backslash continuations spliced into the one logical line they are.

    Quoted arguments are left intact here — see ``_structure`` for the variant
    that also removes their content.
    """
    text = cmd if isinstance(cmd, str) else ""
    return _LINE_CONTINUATION.sub(" ", _uncomment(strip_heredoc_bodies(text)))


def _structure(cmd: str) -> str:
    """A command reduced to its invocation skeleton: ``_shell_source`` with the
    content of every quoted argument blanked as well.

    This is what a FLAG is matched against, because a flag can only live in the
    skeleton. Text that survives inside the quotes is data the command carries.
    """
    return strip_quoted_content(_shell_source(cmd))


# A token is the program being INVOKED only at the start of a simple command:
# the start of the text, just after a shell separator or subshell, or just after
# a block keyword -- optionally behind inline env assignments and transparent
# wrappers. Same reasoning, and the same shape, as ``_CMD_POSITION`` in
# lib/gates/patterns.py, kept local so an advisory hook never depends on a
# safety gate's import graph. (Two callers now meet the functor's N=2 count,
# so a shared shim is eligible; it waits for the promotion gate in
# rules/knowledge-maturation-functor.md, and any shared copy must still not
# pull a safety gate's import graph into this advisory hook.)
#
# ``rtk`` leads the wrapper list because GRIP's own token-compression prefix
# would otherwise hide every real invocation behind it.
_CMD_START = r"(?:\A|[\n;&|(){}`]|&&|\|\||\bdo\b|\bthen\b|\belse\b)"
_ENV_ASSIGN = r"(?:[A-Za-z_]\w*=\S*[ \t]+)*"
_WRAPPER = (
    r"(?:(?:rtk|sudo|env|time|command|nohup|xargs|nice|stdbuf|git)"
    r"[ \t]+(?:-\S+[ \t]+)*)*"
)
_CMD_POSITION = _CMD_START + r"[ \t]*" + _ENV_ASSIGN + _WRAPPER

# Argument tokens of ONE simple command. Excluding the separator characters is
# what stops the walk at a pipe: with a bare ``\S+`` the search happily stepped
# over ``| sed -r`` and reported rg's flag (91 of 186 live advisories).
_ARGV = r"(?:[^\s|;&()<>\n]+[ \t]+)*?"


def _invocation(tool: str, footgun: str) -> re.Pattern[str]:
    """``tool`` invoked in command position, carrying ``footgun`` in its argv."""
    return re.compile(_CMD_POSITION + tool + r"[ \t]+" + _ARGV + footgun)


# Data-driven footgun denylist: (name, compiled pattern, why, fix).
FOOTGUNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "rg-replace-flag",
        _invocation("rg", r"-(?:[a-z]*r[a-z]*\b|-replace\b)"),
        "rg -r is --replace, NOT recursive (recursion is the default): every "
        "match in the output is silently rewritten to the replacement text — "
        "'-rn' turns each match into the literal 'n'",
        "use rg -n for line numbers; only pass -r/--replace when you genuinely "
        "want substitution",
    ),
    (
        "sed-inplace-macos",
        _invocation("sed", r"-i(?=[ \t]+(?!''))"),
        "macOS sed -i requires a backup extension argument; bare -i consumes "
        "the NEXT argument as the backup suffix and edits the wrong operand",
        "on macOS use sed -i '' <expr> (empty suffix) or a portable python "
        "edit",
    ),
    (
        "grep-count-unguarded-tail",
        # ``git grep -c`` exits 1 on zero matches exactly as ``grep -c`` does,
        # and rides in on the wrapper list. The tail anchor now also refuses to
        # cross a newline: only the LAST line's exit status reaches the caller.
        _invocation("grep", r"-c\b(?![^|;&\n]*\|\|)[^|;&\n]*\s*\Z"),
        "grep -c exits 1 when the count is 0, so a monitored/background "
        "script ending on it reports a SUCCESSFUL run as failed",
        "guard the tail: append '|| true' (or end on 'exit 0') when zero "
        "matches is an acceptable outcome",
    ),
    (
        "zsh-unquoted-var-iteration",
        # A shell keyword rather than a program, so it takes the command-position
        # anchor without an argv walk: ``for`` opening a loop, never the word
        # "for" inside a sentence.
        re.compile(_CMD_POSITION + r"for[ \t]+\w+[ \t]+in[ \t]+\$[A-Za-z_]\w*\b"),
        "zsh does not word-split an unquoted $VAR: the loop sees the whole "
        "string as ONE item",
        "use a zsh array (arr=(a b c); for f in \"${arr[@]}\") or a "
        "while-read loop",
    ),
)

def _footgun(name: str) -> re.Pattern[str]:
    """The compiled pattern of one FOOTGUNS entry, by name (single source of truth)."""
    return next(pattern for entry_name, pattern, _, _ in FOOTGUNS if entry_name == name)


# Invocations that can actually REWRITE text between input and output.
#
# The signature ``scan_output`` hunts for is the RESIDUE of such a rewrite, so
# when no substitution-capable invocation is present the residue is impossible
# BY CONSTRUCTION and every match is noise. Issue #4409 recorded three live
# false fires in one session, all on ordinary text: the PET radioligand name
# ``6-O-desmethyldiprenorphine`` (from ``gh issue view``), and the regex
# character classes ``A-Za-z0-9_`` / ``A-Za-z_`` / ``a-z`` (from ``cat -n`` of a
# source file, and from an ``rg`` read).
#
# Naming the tool is NOT sufficient, and that distinction is the whole point:
# ``rg -n -B3 -A6 pattern file`` contains "rg" yet cannot substitute — it was one
# of the three false fires. Every entry below therefore requires the substituting
# FLAG or EXPRESSION, never merely the executable name.
#
# Precision over recall, deliberately: a rewrite performed by a tool absent from
# this table (or in an EARLIER command, whose mangled output a later command
# merely prints) is not detected. That is an accepted boundary — the advisory's
# own text is "check your OWN command flags", which is unactionable when the
# command carries no such flag.
#
# These entries share the #4421 defect too, so they take the same command-position
# anchor. They do NOT take the quote-blanking, and that asymmetry is deliberate:
# a footgun is a FLAG and so lives in a command's structure, but a substitution
# is an EXPRESSION and lives inside a quoted argument. Blanking ``'s/a/b/'`` away
# would make every ``sed`` look read-only. Anchoring the tool in command position
# is what keeps prose about sed out.
_SUBSTITUTION_CAPABLE: tuple[re.Pattern[str], ...] = (
    _footgun("rg-replace-flag"),                              # rg -r / --replace
    re.compile(_CMD_POSITION + r"sed\b[^|;&\n]*[sy][/|,#!:@]"),  # sed 's/a/b/'
    re.compile(_CMD_POSITION + r"perl\b[^|;&\n]*\bs[/|,#!:@]"),  # perl -pe 's/a/b/'
    re.compile(_CMD_POSITION + r"awk\b[^|;&\n]*\bg?sub\s*\("),   # awk '{gsub(/a/,"b")}'
    re.compile(_CMD_POSITION + r"tr[ \t]+\S"),                # tr a-z A-Z
)


def substitution_capable(cmd: str) -> bool:
    """True when ``cmd`` could actually have rewritten text in its own output.

    Gates the ``scan_output`` mangling heuristic: no rewriting invocation means
    no mangling residue is possible, so the heuristic has nothing to find and
    any match it reports is a false positive (issue #4409).
    """
    text = _shell_source(cmd)
    return any(pattern.search(text) for pattern in _SUBSTITUTION_CAPABLE)


# Single-letter segments that are common in LEGITIMATE identifiers
# (version-tag shapes like release-v-2, matrix axes like build-x-64).
_BENIGN_SINGLE_SEGMENTS = frozenset({"v", "x"})

_HYPHEN_TOKEN = re.compile(r"\b\w+(?:-\w+)+\b")


def _hit_for(entry: tuple[str, re.Pattern[str], str, str], text: str) -> dict | None:
    """One footgun-table entry checked against one command; None when clean."""
    name, pattern, why, fix = entry
    match = pattern.search(text)
    if not match:
        return None
    return {"name": name, "why": why, "fix": fix, "span": match.group(0)}


def scan_command(cmd: str) -> dict:
    """Scan one shell command for known self-inflicted footgun flags.

    Returns ``{"flagged": bool, "hits": [{"name", "why", "fix", "span"}],
    "message": str}``. Advisory by contract — the caller nudges, never
    blocks.
    """
    text = _structure(cmd)
    hits = [hit for entry in FOOTGUNS if (hit := _hit_for(entry, text))]
    message = "; ".join(f"{h['name']}: {h['why']} — fix: {h['fix']}" for h in hits)
    return {"flagged": bool(hits), "hits": hits, "message": message}


def _single_letter_segments(token: str) -> set[str]:
    """Suspicious single-letter alphabetic segments inside one hyphenated token."""
    return {
        seg.lower()
        for seg in token.split("-")
        if len(seg) == 1 and seg.isalpha()
        and seg.lower() not in _BENIGN_SINGLE_SEGMENTS
    }


def _index_tokens_by_letter(body: str) -> dict[str, set[str]]:
    """Map each suspicious single-letter segment to the distinct tokens carrying it."""
    index: dict[str, set[str]] = defaultdict(set)
    for token in _HYPHEN_TOKEN.findall(body):
        _index_one_token(token, index)
    return index


def _index_one_token(token: str, index: dict[str, set[str]]) -> None:
    """Record one token under every suspicious single-letter segment it carries."""
    for letter in _single_letter_segments(token):
        index[letter].add(token)


def _first_repeated_letter(index: dict[str, set[str]]) -> tuple[str, list[str]] | None:
    """The first letter (sorted) carried by >=2 distinct tokens, with its tokens."""
    for letter in sorted(index):
        if len(index[letter]) >= 2:
            return letter, sorted(index[letter])
    return None


def scan_output(text: str) -> dict:
    """Scan command output for the --replace mangling signature.

    Flags only when the SAME single-letter segment appears inside two or
    more distinct hyphenated identifiers (e.g. ``claude-n-4-6`` and
    ``gemini-n-flash`` both carrying ``n`` where full words belong). Returns
    ``{"flagged": bool, "letter": str | None, "tokens": [str, ...],
    "message": str}``.
    """
    body = text if isinstance(text, str) else ""
    repeated = _first_repeated_letter(_index_tokens_by_letter(body))
    if repeated is None:
        return {"flagged": False, "letter": None, "tokens": [], "message": ""}
    letter, tokens = repeated
    return {
        "flagged": True,
        "letter": letter,
        "tokens": tokens,
        "message": (
            f"output shows a mangling signature: the single letter "
            f"'{letter}' sits where identifiers belong in {', '.join(tokens)} "
            "— check your OWN command flags (rg -r = --replace) before "
            "blaming an external tool"
        ),
    }


# ---------------------------------------------------------------------------
# Blind negative: a filtered search over ignored content, reported as absence.
#
# Origin: 2026-08-18. `fd -e jsonl . ~/.grip/projects` returned nothing and was
# published as "0 transcripts — every prior session's record is gone". The store
# in fact held 1,186 transcripts and 13,823 files: `projects/` is gitignored and
# `fd` skips ignored content BY DEFAULT, so the search never entered the
# directory it named. The same session had already been warned by the
# `rg-replace-flag` entry above, and the operator memory "a blind measurement
# returns a confident zero" was loaded in context at the time. Prose did not
# prevent it; this entry is the mechanical floor.
#
# WHY THIS IS A DISTINCT SHAPE from the FOOTGUNS table: those entries fire on a
# command that is wrong on its face. This command is CORRECT — `fd PATTERN PATH`
# is exactly right for a tracked path. It is only wrong in conjunction with an
# ignored target and an empty result, and only when that emptiness is then read
# as a fact about the world. So the trigger is a CONJUNCTION, and the ignored-ness
# is I/O the caller supplies (`git check-ignore`), keeping this module pure.
#
# PRECISION over recall, in the module's established style. `grep` and `find` are
# deliberately ABSENT: neither consults .gitignore, so neither can produce this
# failure. A search with no path operand is absent too — that is the ordinary
# cwd-wide search, where a zero result is usually honest and flagging it would
# bury the signal. Every clause below removes false fires rather than adding
# reach.
#
# FALSIFIER: if, over ~20 fires, the advisory never once precedes a negative
# claim the author would have published (i.e. it only ever nags on exploratory
# searches), narrow it further to searches whose output is EXACTLY empty, or
# demote it to audit-only. Track logs/tool-error-self-check.jsonl.

# Tools that consult .gitignore and therefore skip ignored content by default.
_IGNORE_AWARE_TOOLS = ("fd", "rg")

# Flags that reach ignored content. `-u`/`-uu`/`-uuu` are the ripgrep short
# forms; `--no-ignore-vcs` keeps the hidden-file skip but does reach gitignored
# files, which is the half that matters here.
_IGNORE_BYPASS = re.compile(
    r"(?:^|[ \t])(?:--no-ignore(?:-vcs|-dot|-parent|-global)?\b|--unrestricted\b|-u{1,3}\b)"
)

# A token that looks like a path operand rather than a flag or a pattern:
# anything containing a slash, or a leading ~ / . segment.
_PATH_OPERAND = re.compile(r"(?:^|[ \t])((?:~|\.{1,2})?/[^\s|;&()<>]*|~[^\s|;&()<>]*)")

# Output that asserts nothing was found. A bare count of zero counts: `| wc -l`
# on an empty search prints "0", which is how the originating failure surfaced.
_EMPTY_RESULT = re.compile(r"\A\s*(?:0|)\s*\Z")


def blind_negative_candidates(cmd: str, output: str) -> list[str]:
    """Path operands of an ignore-aware search that returned nothing.

    Returns the candidate paths when ``cmd`` invokes an ignore-aware search tool
    with no ignore-bypass flag AND names at least one path operand AND ``output``
    asserts absence. Empty list otherwise — including for every ordinary search,
    which is the point.

    The caller decides whether any returned path is actually ignored (that needs
    ``git check-ignore``, i.e. I/O); this function stays pure so it is testable
    without a repository.
    """
    command = cmd if isinstance(cmd, str) else ""
    body = output if isinstance(output, str) else ""
    if not _EMPTY_RESULT.match(body):
        return []
    if _IGNORE_BYPASS.search(command):
        return []
    if not any(
        re.search(_CMD_POSITION + tool + r"\b", command)
        for tool in _IGNORE_AWARE_TOOLS
    ):
        return []
    return [m.group(1) for m in _PATH_OPERAND.finditer(command) if m.group(1)]


def blind_negative_message(paths: list[str]) -> str:
    """The advisory text for a confirmed blind negative over ``paths``."""
    named = ", ".join(paths[:3])
    return (
        f"this search returned nothing, but {named} is GITIGNORED and fd/rg skip "
        "ignored content by default — so the zero is a property of your flags, "
        "not of the store. Re-run with --no-ignore (or rg -uu) before reporting "
        "an absence, and never publish a negative claim from a filtered search"
    )
