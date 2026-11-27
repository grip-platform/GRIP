"""The text the env-var guard judges for a Bash command (GRIP#9916).

The other Bash gates judge `strip_heredocs_and_strings(command)`: heredoc bodies
dropped and every quoted string of 20 or more characters emptied, so that the
prose of a commit message does not trip them. The shell still expands `$NAME`
and `${...}` inside a double-quoted string and inside the body of a heredoc whose
delimiter is unquoted, so that view hid exactly what the env-var guard looks
for: `echo "the value is $X here"` reached it as `echo ""`.

`env_guard_view` keeps what the guard needs and still drops prose:

- a line continuation (a backslash and a newline) is deleted where the shell
  deletes it, so a `$NAME` split across two lines is judged whole. The shell
  keeps it inside the body of a quoted heredoc and at the end of a comment,
  which ends at its newline, so the view reads heredocs and comments on the
  original text and joins only the rest (GRIP#9983, AGORA on GRIP#10121);
- a heredoc body is dropped when its delimiter is quoted (`<<'EOF'`, `<<"EOF"`
  or `<<\\EOF`, where the shell expands nothing), and otherwise reduced to the
  expansions it holds. Its last line follows the shell's rule, measured in zsh
  and bash: the delimiter alone, after leading tabs only for `<<-`. In an
  unquoted heredoc the shell joins continued lines before it compares, so
  `EO\\` then `F` closes it on `EOF`, and `x \\` then `EOF` does not;
- a quoted string of 20 or more characters keeps its expansions and loses its
  other words. A single-quoted string keeps them too, because it may be code
  for a nested shell (`sh -c '...'`), which does expand them. A string passed
  to `grep`, `egrep`, `fgrep` or `rg` is a search pattern, not prose, so it
  stays, with any `|`, `;`, `&` or blank in it read as `_` (GRIP#9958,
  GRIP#9983). That includes a string inside a substitution in the search's
  arguments, whose output may be the pattern (Codex on GRIP#10121);
- a string or a heredoc body that holds a command substitution or a backtick is
  kept whole, because that code runs;
- each word is read as the shell runs it, after quote removal: a quoted string
  loses its quotes, an ANSI-C or locale string (`$'...'`, `$"..."`) loses its
  `$` too and an ANSI-C one is decoded, and a backslash outside quotes is
  dropped. So `\\grep`, `'grep'`, `g"r"ep`, `$"grep"` and `$'\\x67rep'` are
  all `grep`, and a directory before a command name, as in `/usr/bin/grep`,
  changes nothing (GRIP#10120). An escaped separator, blank, bracket or brace
  is word text. It reads as `_` when every command in the text only prints its
  words (`echo`, `printf`, `print`) and no substitution or redirection hides one,
  so `echo env\\ \\|\\ grep\\ KEY` prints a literal and is not a listing (Codex
  on GRIP#10159). Any other command may read the words as shell text again (a
  shell, `eval`, `ssh`, `xargs sh -c`, or a name the view cannot read), so
  there the backslash is dropped, as such a command reads it, and
  `eval env\\ \\|\\ grep\\ KEY` is a listing (GRIP#10199 review). The command a string
  belongs to is found on a copy where every separator, blank or quote that is
  text is masked, so an escaped `|` starts no command, and a command word the
  view cannot read, such as `"$X"`, may be a search, so its quoted arguments
  are kept;
- the text is read once, in order, with escapes, comments and ANSI-C strings
  honoured, so quotes pair as the shell pairs them, and a `<<` that is escaped
  or sits in a comment or a plain quoted string is not an operator. A comment
  ends at its newline even after a backslash (zsh and bash). A heredoc body is
  never read as shell text, so a quote inside it cannot pair with a string
  after it.

The view models a subset of shell syntax. From the first construct outside it,
the rest of the text stays raw, with no heredoc dropped and no string reduced,
because raw text can only judge more (GRIP#9951). Those constructs are a `<<`
whose delimiter the view cannot read (such as `E"O"F`, or one a continuation
splits) or that has no body or no last line (a `<<` can also be an arithmetic
shift, as in `$((1<<2))`); a second `<<` on a heredoc's line; a quote opened on
that line and left open; a `<` in shell text followed by a continuation, which
bash joins with the next line into a `<<` (zsh refuses it); and a `#` right
after `)`. Measured in zsh and bash, that `#` starts a comment after a
subshell, as in `(true)#x`, and is part of the word after a command
substitution, as in `$(echo a)#b`; the scan cannot tell the two apart. Raw
text is judged as written and once more without its quotes and backslashes,
since the view cannot tell how the shell reads it.

Still not judged, as before: the body of a quoted-delimiter heredoc that a shell
or an interpreter runs (`bash <<'EOF'`), words that are code only for an
interpreter inside a long string (`python -c "..."`, `bash -c '...'`,
GRIP#10198), and a command named through a variable, brace expansion or a glob
(`$G`, `{grep,}`, `gre?`).
"""

from __future__ import annotations

import bisect
import re

from .patterns import LONG_STRING_THRESHOLD, WRAPPER_PREFIX

#: A line continuation: a backslash, not itself escaped, before a newline.
_CONTINUATION = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")
#: `$NAME` or `${...}`, the forms the env-var guard's patterns match.
_EXPANSION = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*")
#: A command substitution or a backtick: code that runs where it stands.
_RUNS_CODE = re.compile(r"\$\(|`")
#: Any `<<` that is not a here-string (`<<<`).
_DOUBLE_LT = re.compile(r"(?<!<)<<(?!<)")
#: A heredoc operator at a `<<`, with its delimiter quoted ('EOF', "EOF", \EOF)
#: or plain. The delimiter must end where a shell word ends.
_OPERATOR = re.compile(
    r"<<(?P<dash>-?)[ \t]*"
    r"(?:(?P<q>['\"])(?P<qd>[\w.-]+)(?P=q)|\\(?P<bd>[\w.-]+)|(?P<pd>[\w.-]+))"
    r"(?=[\s;&|<>()]|$)")
#: A line that is only a word, after any leading tabs.
_TERMINATOR = re.compile(r"^(\t*)([\w.-]+)$", re.MULTILINE)
#: One physical line, and the newline that ends it or nothing at the end.
_PHYSICAL_LINE = re.compile(r"([^\n]*)(\n|$)")
#: The first word of each command: after a line start, a separator, a
#: subshell's `(` or a brace group's `{`, and after a wrapper such as `sudo`
#: with its options (GRIP#9991, GRIP#9993). A `$(` or a backtick opens a
#: substitution inside the current command, and a `${` names a variable, so
#: none of them starts a command (Codex on GRIP#9972). A `{` opens a brace
#: group only when a blank follows it; `{a,b}` is brace expansion. It is
#: matched on the detection copy, where quotes are already removed (GRIP#10120).
_COMMAND_WORD = re.compile(
    r"(?:^|[|;&]|(?<![$(])\(|(?<!\$)\{(?=[ \t]))[ \t]*" + WRAPPER_PREFIX + r"([^\s;&|()<>]+)",
    re.MULTILINE)
#: A command name the view can read, with or without a directory, as in
#: `/usr/bin/grep`. Any other command word (an expansion, a masked character,
#: an assignment) may name a search, so its quoted arguments are kept.
_PLAIN = re.compile(r"[\w./-]+")
#: What a separator, blank or quote that is text becomes on the detection
#: copy: no word character, so a masked word is never a plain name.
_MASK = "\x00"
#: Commands whose quoted arguments are search patterns, not prose (GRIP#9958).
#: `patterns._SEARCH` holds the same names, so change both together.
_PATTERN_COMMANDS = frozenset({"grep", "egrep", "fgrep", "rg"})
#: The separators and blanks a search pattern may hold, read as `_` in the view.
_SEPARATORS = re.compile(r"[|;&\s]")
#: The characters after which a `#` starts a comment.
_COMMENT_AFTER = " \t\n;|&("
#: A backslash escape outside quotes; the shell drops the backslash. One
#: before a newline survives in the view only at the end of a comment, where
#: it is text.
_ESCAPE = re.compile(r"\\([^\n])")
#: Characters that build command structure: escaped, each is word text, and
#: the judged text reads it as `_` when every command only prints (Codex on
#: GRIP#10159).
_STRUCTURE = re.compile(r"[|;&()<>{}\s]")
#: Commands that print their words and never read them as shell text. Any
#: other command may read its words as shell text again, where an escaped
#: separator is syntax: a shell, `eval`, `ssh`, `xargs sh -c`, `watch`, or a
#: name the view cannot read (GRIP#10199 review).
_PRINT_COMMANDS = frozenset({"echo", "printf", "print"})
#: A command substitution or a redirection. A substitution runs a command that
#: no command word shows, and a redirection can stand before the command word,
#: as in `< x eval ...`, where the word scan does not see it (AGORA on
#: GRIP#10204).
_HIDDEN = re.compile(r"\$\(|`|[<>]")
#: An escape that the shell removes inside a double-quoted string.
_DQ_ESCAPE = re.compile(r"\\([$`\"\\])")
#: An escape inside an ANSI-C string, `$'...'`, as bash and zsh read it.
_ANSI_C = re.compile(
    r"\\(?:x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|([0-7]{1,3})|c(.)|(.))",
    re.DOTALL)
_ANSI_C_NAMED = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
                 "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
#: Characters that separate, group or quote words: on the detection copy,
#: where they are text, each reads as `_MASK`.
_SYNTAX = re.compile(r"[|;&()<>{}\s'\"\\`$]")
#: Characters that quote removal deletes, for the quote-free copy of raw text.
_QUOTING = re.compile(r"[\\'\"]")


class _QuoteScan:
    """Where a text stands after reading it in order: in a comment, in a quote,
    and, inside a double-quoted string, whether a substitution or a backtick is
    still open. It records each quoted string's span, each comment's span and
    the first construct it does not model, by their positions in the text it
    reads."""

    def __init__(self) -> None:
        self.quote = ""      # "", "'", "$'" (ANSI-C) or '"'
        self.comment = False
        self.escaped = False
        self.before_escape = "\n"  # the character read before that backslash
        self.code = 0        # `$(` still open in the open double-quoted string
        self.ticks = 0       # backticks seen in the open double-quoted string
        self.prev = "\n"
        self.opened = 0      # where the open quote started
        self.spans: list[tuple[int, int]] = []
        self.kinds: dict[int, str] = {}  # each string's quote, by its start
        self.comment_start = 0
        self.comments: list[tuple[int, int]] = []  # (its `#`, its newline)
        self.unmodelled: int | None = None

    def feed(self, text: str, start: int, end: int) -> None:
        """Read text[start:end]."""
        for i in range(start, end):
            ch = text[i]
            if self.escaped:
                # An escaped character is a plain word character, so `\$(`
                # opens no substitution and the `#` in `a\b#` starts no
                # comment.
                self.escaped = False
                if ch == "\n":
                    self._continued(i)
                else:
                    self.prev = "_"
                continue
            was = self.quote
            if self.comment:
                self._in_comment(ch, i)
            elif self.quote == "'":
                self.quote = "" if ch == "'" else "'"
            elif self.quote:
                self._in_escaping_quote(ch)
            else:
                self._unquoted(ch, i)
            self._mark(was, i)
            self.prev = ch

    def _continued(self, i: int) -> None:
        """A line continuation at `i`, which the shell deletes, so the
        character before its backslash stays the last one read. After a `<`
        in shell text, bash joins the next line into it, so a `<<` may form
        there: not modelled."""
        if self.before_escape == "<" and not self.quote and self.unmodelled is None:
            self.unmodelled = i - 2
        self.prev = self.before_escape

    def _mark(self, was: str, i: int) -> None:
        """Record where a quote opens and which quote it is, and its span when
        it closes. A double quote after an unescaped `$` opens a locale string,
        which bash reads as a double-quoted one."""
        if self.quote and not was:
            self.opened = i
            self.kinds[i] = '$"' if self.quote == '"' and self.prev == "$" else self.quote
        elif was and not self.quote:
            self.spans.append((self.opened, i + 1))

    def _in_comment(self, ch: str, i: int) -> None:
        """A step inside a comment, which ends at its newline even after a
        backslash (zsh and bash)."""
        if ch == "\n":
            self.comment = False
            self.comments.append((self.comment_start, i))

    def _in_escaping_quote(self, ch: str) -> None:
        """A step inside a double-quoted or ANSI-C string."""
        if ch == "\\":
            self.escaped, self.before_escape = True, self.prev
        elif ch == self.quote[-1]:
            self.quote = ""
        elif self.quote == '"' and ch == "(" and self.prev == "$":
            self.code += 1
        elif self.quote == '"' and ch == ")" and self.code:
            self.code -= 1
        elif self.quote == '"' and ch == "`":
            self.ticks += 1

    def _unquoted(self, ch: str, i: int) -> None:
        """A step outside any quote."""
        if ch == "\\":
            self.escaped, self.before_escape = True, self.prev
        elif ch == "'":
            self.quote = "$'" if self.prev == "$" else "'"
        elif ch == '"':
            self.quote, self.code, self.ticks = '"', 0, 0
        elif ch == "#" and self.prev in _COMMENT_AFTER:
            self.comment, self.comment_start = True, i
        elif ch == "#" and self.prev == ")" and self.unmodelled is None:
            self.unmodelled = i

    def operator_is_text(self) -> bool:
        """True when a `<<` here is no heredoc operator: escaped (Bash and zsh
        then read `\\<` as a plain `<`), in a comment or a single-quoted
        string, or in a double-quoted one with no substitution or backtick
        open."""
        if self.escaped or self.comment or self.quote in ("'", "$'"):
            return True
        return self.quote == '"' and not (self.code or self.ticks % 2)


def _reduced(inner: str) -> str:
    """The expansions a long string or a heredoc body holds, or all of it when
    it runs code."""
    return inner if _RUNS_CODE.search(inner) else " ".join(_EXPANSION.findall(inner))


def _join(text: str) -> str:
    """The text with its line continuations deleted, as the shell deletes them."""
    return _CONTINUATION.sub(r"\1", text)


def _inside(ranges: list[tuple[int, int]], i: int) -> bool:
    """True when `i` lies in one of the sorted, disjoint (start, end) ranges."""
    k = bisect.bisect_right(ranges, (i, float("inf"))) - 1
    return k >= 0 and i < ranges[k][1]


def _join_shell(text: str, start: int, end: int, comments: list[tuple[int, int]]) -> str:
    """text[start:end] with its line continuations deleted, except one at the
    end of a comment: the shell ends a comment at its newline, so joining it
    would hide the next line inside the comment (AGORA on GRIP#10121)."""
    out, last = [], start
    for m in _CONTINUATION.finditer(text, start, end):
        if _inside(comments, m.end() - 2):
            continue
        out.append(text[last:m.end(1)])
        last = m.end()
    out.append(text[last:end])
    return "".join(out)


def _terminator_lines(text: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """The start of every line that can end a quoted heredoc, by delimiter:
    for `<<` a line that holds the delimiter alone, and for `<<-` one that may
    also begin with tabs. Measured in zsh and bash: an indented or padded line
    ends neither."""
    plain: dict[str, list[int]] = {}
    dashed: dict[str, list[int]] = {}
    for m in _TERMINATOR.finditer(text):
        dashed.setdefault(m.group(2), []).append(m.start())
        if not m.group(1):
            plain.setdefault(m.group(2), []).append(m.start())
    return plain, dashed


def _body_end(ends: dict[str, list[int]], delim: str, start: int) -> int | None:
    """The start of the first line at or after `start` that ends the heredoc."""
    lines = ends.get(delim, [])
    k = bisect.bisect_left(lines, start)
    return lines[k] if k < len(lines) else None


def _ends_continued(line: str) -> bool:
    """True when a physical line ends in a line continuation: an odd run of
    backslashes before its newline."""
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


def _unquoted_last_line(text: str, delim: str, start: int, dash: bool) -> int | None:
    """The start of the line that ends an unquoted heredoc. The shell joins a
    line that ends in a continuation to the next one, then compares the result
    with the delimiter, after its leading tabs for `<<-` (zsh and bash)."""
    head, pieces = start, []
    for m in _PHYSICAL_LINE.finditer(text, start):
        line = m.group(1)
        if m.group(2) and _ends_continued(line):
            pieces.append(line[:-1])
            continue
        whole = "".join(pieces) + line
        if (whole.lstrip("\t") if dash else whole) == delim:
            return head
        head, pieces = m.end(), []
    return None


def _last_line(text: str, ends: tuple[dict[str, list[int]], dict[str, list[int]]],
               m: re.Match[str], start: int) -> int | None:
    """The start of the line that ends the heredoc whose operator `m` matched.
    The shell keeps continuations in a quoted body, so there the first
    physical line that is the delimiter ends it (GRIP#9983)."""
    dash = bool(m.group("dash"))
    quoted = m.group("qd") or m.group("bd")
    if quoted:
        return _body_end(ends[dash], quoted, start)
    return _unquoted_last_line(text, m.group("pd"), start, dash)


def _line_end(text: str, at: int, scan: _QuoteScan) -> int | None:
    """The newline that ends the line holding the operator at `at`, after
    feeding `scan` through it: the first one that no continuation escapes.
    None when the text ends first, or the line leaves a quote open or holds a
    construct the view does not model."""
    quote, pos = scan.quote, at
    newline = text.find("\n", pos)
    while newline >= 0:
        scan.feed(text, pos, newline + 1)
        if scan.prev == "\n":
            return newline if scan.quote == quote and scan.unmodelled is None else None
        pos = newline + 1
        newline = text.find("\n", pos)
    return None


def _heredoc(text: str, at: int, scan: _QuoteScan,
             ends: tuple[dict[str, list[int]], dict[str, list[int]]]) -> tuple[int, int, bool] | None:
    """(body start, body end, quoted) for the heredoc whose `<<` is at `at`,
    after feeding `scan` through the operator's line, which a continuation may
    carry on; None when the view does not model it: an unreadable delimiter,
    no body, a second `<<` on the line, a quote opened on the line and left
    open, or no last line."""
    m = _OPERATOR.match(text, at)
    newline = None if m is None else _line_end(text, at, scan)
    if newline is None or "<<" in text[m.end():newline]:
        return None
    tail = _last_line(text, ends, m, newline + 1)
    return None if tail is None else (newline + 1, tail, bool(m.group("qd") or m.group("bd")))


def _append(out: list[str], bodies: dict[int, int], size: int, head: str, kept: str) -> int:
    """Add shell text and a kept heredoc body to the view, record where the
    body lies, and return the view's new length."""
    out.append(head)
    size += len(head)
    if kept:
        out.append(kept + "\n")
        bodies[size] = size + len(kept) + 1
        size += len(kept) + 1
    return size


def _heredocs_view(text: str) -> tuple[str, dict[int, int], int]:
    """Heredoc bodies dropped (quoted delimiter) or reduced to their expansions
    in one pass over the original text, with continuations deleted in shell
    text outside comments; also where each kept body lies in the result, and
    where the raw text starts: at the first construct the view does not
    model. From there on every continuation is deleted, since raw text can
    only judge more."""
    ends, scan, out, bodies = _terminator_lines(text), _QuoteScan(), [], {}
    last = fed = size = 0
    raw = len(text)
    for m in _DOUBLE_LT.finditer(text):
        if m.start() < last:
            continue
        scan.feed(text, fed, m.start())
        fed = m.start()
        if scan.unmodelled is not None:
            raw = scan.unmodelled
            break
        if scan.operator_is_text():
            continue
        found = _heredoc(text, m.start(), scan, ends)
        if found is None:
            raw = m.start()
            break
        body, tail, quoted = found
        kept = "" if quoted else _reduced(_join(text[body:tail]))
        size = _append(out, bodies, size, _join_shell(text, last, body, scan.comments), kept)
        last = fed = tail
    # Comments only change where continuations are joined, and a split `<<`
    # needs a continuation, so a text without one needs no more scanning.
    if "\\\n" in text and fed < raw:
        scan.feed(text, fed, raw)
        raw = raw if scan.unmodelled is None else min(raw, scan.unmodelled)
    head = _join_shell(text, last, raw, scan.comments)
    out.append(head + _join(text[raw:]))
    return "".join(out), bodies, size + len(head)


def _string_spans(text: str, bodies: dict[int, int],
                  stop: int) -> tuple[list[tuple[int, int]], dict[int, str], int]:
    """Each quoted string that closes before the limit, as (start, end); the
    quote that opened each, by its start; and the limit itself: `stop`, the
    first construct the scan does not model, or a quote still open at `stop`,
    whichever comes first. The scan reads the text up to `stop` but no kept
    heredoc body (`bodies` maps a body's start in this text, the view, to its
    end). A string's `start` is its opening quote, which for an ANSI-C string
    is the `'` after its `$`. The view keeps a comment's continuation, so this
    scan finds the same comments as the one in `_heredocs_view`."""
    scan, pos = _QuoteScan(), 0
    for start, end in bodies.items():
        scan.feed(text, pos, start)
        pos = end
    scan.feed(text, pos, stop)
    limit = stop if scan.unmodelled is None else scan.unmodelled
    if scan.quote:
        limit = min(limit, scan.opened)
    return [span for span in scan.spans if span[1] <= limit], scan.kinds, limit


def _command_words(text: str) -> tuple[list[int], list[bool], bool]:
    """Where each command word starts on the detection copy, and whether it may
    be a search command, whose quoted arguments are patterns, not prose: a
    plain name is one when its last part is, and any other word may be. Also
    whether every command word names a command that only prints its words."""
    starts: list[int] = []
    searches: list[bool] = []
    prints = True
    for m in _COMMAND_WORD.finditer(text):
        word = m.group(1)
        name = word.rsplit("/", 1)[-1]
        starts.append(m.start(1))
        searches.append(not _PLAIN.fullmatch(word) or name in _PATTERN_COMMANDS)
        prints = prints and name in _PRINT_COMMANDS
    return starts, searches, prints


def _kept(inner: str, search: bool) -> str | None:
    """What a quoted string becomes in the view, or None when it stays as it
    is. A search command's pattern stays, with `|`, `;`, `&` and blanks read
    as `_`, so the listing pattern stops only at a separator the shell sees,
    and the pattern's words never form listing syntax of their own, with or
    without a pipe (Codex on GRIP#9972 and GRIP#9984, GRIP#9983); one that
    runs code stays exactly. A long string of anything else is reduced to its
    expansions."""
    if search:
        return None if _RUNS_CODE.search(inner) else _SEPARATORS.sub("_", inner)
    return _reduced(inner) if len(inner) >= LONG_STRING_THRESHOLD else None


def _ansi_escape(m: re.Match[str]) -> str:
    """One ANSI-C escape decoded; one neither shell knows stays as written."""
    code = m.group(1) or m.group(2) or m.group(3)
    if code:
        return chr(int(code, 16)) if int(code, 16) <= 0x10FFFF else m.group(0)
    if m.group(4):
        return chr(int(m.group(4), 8) & 0xFF)
    if m.group(5):
        return chr(ord(m.group(5)) & 0x1F)
    return _ANSI_C_NAMED.get(m.group(6), m.group(0))


def _unquoted(text: str, start: int, end: int, kind: str) -> str:
    """A quoted string's text after quote removal: a single-quoted string as
    written, a double-quoted or locale (`$"..."`) one without the escapes the
    shell removes there, and an ANSI-C one decoded."""
    inner = text[start + 1:end - 1]
    if kind == "'":
        return inner
    if kind == "$'":
        return _ANSI_C.sub(_ansi_escape, inner)
    return _DQ_ESCAPE.sub(r"\1", inner)


def _segments(text: str, bodies: dict[int, int], spans: list[tuple[int, int]],
              kinds: dict[int, str], limit: int) -> list[tuple[str, int, int]]:
    """The text before `limit` in order, as (kind, start, end): "text" for
    shell text, "body" for a kept heredoc body, or a quoted string's quote.
    The `$` of an ANSI-C or a locale string is left out of the shell text
    before it."""
    marks = sorted([(s, e, "body") for s, e in bodies.items() if s < limit]
                   + [(s, e, kinds[s]) for s, e in spans])
    out, pos = [], 0
    for start, end, kind in marks:
        cut = start - 1 if kind.startswith("$") else start
        if pos < cut:
            out.append(("text", pos, cut))
        out.append((kind, start, end))
        pos = end
    if pos < limit:
        out.append(("text", pos, limit))
    return out


def _masked_escape(m: re.Match[str]) -> str:
    """An escaped character on the detection copy: `_MASK` when it would be
    syntax, since the escape makes it text."""
    return _MASK if _SYNTAX.match(m.group(1)) else m.group(1)


def _detect_form(text: str, segment: tuple[str, int, int]) -> str:
    """A segment as command detection reads it: quotes and escapes removed,
    and every separator, blank or quote that is text read as `_MASK`, so that
    only shell syntax starts a command (GRIP#10120)."""
    kind, start, end = segment
    if kind == "text":
        return _ESCAPE.sub(_masked_escape, text[start:end])
    if kind == "body":
        return text[start:end]
    return _SYNTAX.sub(_MASK, _unquoted(text, start, end, kind))


def _judged_escape(m: re.Match[str]) -> str:
    """An escaped character in a text whose every command only prints: `_` for
    one that would build command structure, since the escape makes it word
    text, and the character itself otherwise, so `\\grep` reads as `grep` and
    `\\$X` as `$X`."""
    return "_" if _STRUCTURE.match(m.group(1)) else m.group(1)


def _final_form(text: str, segment: tuple[str, int, int], search: bool, literal: bool) -> str:
    """A segment as the guard judges it: shell text with each escape read by
    `_judged_escape` when the text is `literal`, and otherwise with every
    escape's backslash dropped, as a command that reads its words as shell
    text again reads them; a kept body as it is; and a quoted string's text
    after quote removal, as `_kept` makes it."""
    kind, start, end = segment
    if kind == "text":
        return _ESCAPE.sub(_judged_escape if literal else r"\1", text[start:end])
    if kind == "body":
        return text[start:end]
    inner = _unquoted(text, start, end, kind)
    kept = _kept(inner, search)
    return inner if kept is None else kept


def _raw_tail(raw: str) -> str:
    """The text from the first construct the view does not model, as written,
    then, when quoting changes it, again with its quotes and backslashes
    deleted: the view cannot tell how the shell reads it, so it judges both.
    The `;` between them keeps a match from running across the join."""
    bare = _QUOTING.sub("", raw)
    return raw if bare == raw else raw + "\n;\n" + bare


def _search_at(starts: list[int], searches: list[bool], at: int) -> bool:
    """Whether the command in force at `at` on the detection copy is a search."""
    k = bisect.bisect_right(starts, at) - 1
    return k >= 0 and searches[k]


def _strings_view(text: str, bodies: dict[int, int], stop: int) -> str:
    """The text before the raw point with each word read as the shell runs it,
    and each quoted string as `_kept` makes it for the command it belongs to,
    found on the detection copy; the raw text after. An escaped separator in
    shell text stays text only when every command only prints its words, no
    substitution or redirection hides one, and no raw text follows, where a
    command the view cannot see may run what was printed."""
    spans, kinds, limit = _string_spans(text, bodies, stop)
    segments = _segments(text, bodies, spans, kinds, limit)
    detect: list[str] = []
    offsets: list[int] = []
    size = 0
    for segment in segments:
        form = _detect_form(text, segment)
        offsets.append(size)
        detect.append(form)
        size += len(form)
    detection = "".join(detect)
    starts, searches, prints = _command_words(detection)
    literal = prints and not _HIDDEN.search(detection) and not text[limit:].strip()
    out = [_final_form(text, segment, _search_at(starts, searches, at), literal)
           for segment, at in zip(segments, offsets)]
    return "".join(out) + _raw_tail(text[limit:])


def env_guard_view(command: str) -> str:
    """The command as the env-var guard should judge it: what the shell
    expands, with each word read as the shell runs it, and without the prose
    of long strings and heredoc bodies."""
    return _strings_view(*_heredocs_view(command))
