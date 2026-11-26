"""The text the env-var guard judges for a Bash command (GRIP#9916).

The other Bash gates judge `strip_heredocs_and_strings(command)`: heredoc bodies
dropped and every quoted string of 20 or more characters emptied, so that the
prose of a commit message does not trip them. The shell still expands `$NAME`
and `${...}` inside a double-quoted string and inside the body of a heredoc whose
delimiter is unquoted, so that view hid exactly what the env-var guard looks
for: `echo "the value is $X here"` reached it as `echo ""`.

`env_guard_view` keeps what the guard needs and still drops prose:

- a line continuation (a backslash and a newline) is deleted first, as the
  shell deletes it, so a `$NAME` split across two lines is judged whole;
- a heredoc body is dropped when its delimiter is quoted (`<<'EOF'`, `<<"EOF"`
  or `<<\\EOF`, where the shell expands nothing), and otherwise reduced to the
  expansions it holds. Its last line follows the shell's rule, measured in zsh
  and bash: the delimiter alone, after leading tabs only for `<<-`;
- a quoted string of 20 or more characters keeps its expansions and loses its
  other words. A single-quoted string keeps them too, because it may be code
  for a nested shell (`sh -c '...'`), which does expand them. A string passed
  to `grep`, `egrep`, `fgrep` or `rg` stays whole, because it is a search
  pattern, not prose (GRIP#9958);
- a string or a heredoc body that holds a command substitution or a backtick is
  kept whole, because that code runs;
- the text is read once, in order, with escapes, comments and ANSI-C strings
  honoured, so quotes pair as the shell pairs them, and a `<<` that is escaped
  or sits in a comment or a plain quoted string is not an operator. A heredoc
  body is never read as shell text, so a quote inside it cannot pair with a
  string after it.

The view models a subset of shell syntax. From the first construct outside it,
the rest of the text stays raw, with no heredoc dropped and no string reduced,
because raw text can only judge more (GRIP#9951). Those constructs are a `<<`
whose delimiter the view cannot read (such as `E"O"F`) or that has no body or
no last line (a `<<` can also be an arithmetic shift, as in `$((1<<2))`); a
second `<<` on a heredoc's line; a quote opened on that line and left open;
and a `#` right after `)`. Measured in zsh and bash, that `#` starts a comment
after a subshell, as in `(true)#x`, and is part of the word after a command
substitution, as in `$(echo a)#b`; the scan cannot tell the two apart.

Still not judged, as before: the body of a quoted-delimiter heredoc that a shell
or an interpreter runs (`bash <<'EOF'`), and words that are code only for an
interpreter inside a long string (`python -c "..."`).
"""

from __future__ import annotations

import bisect
import re

from .patterns import LONG_STRING_THRESHOLD

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
#: The first word of each command: after a line start or a separator, and
#: after `command` when present.
_COMMAND_WORD = re.compile(r"(?:^|[|;&(`])[ \t]*(?:command[ \t]+)?([\w.-]+)", re.MULTILINE)
#: Commands whose quoted arguments are search patterns, not prose (GRIP#9958).
_PATTERN_COMMANDS = frozenset({"grep", "egrep", "fgrep", "rg"})


class _QuoteScan:
    """Where a text stands after reading it in order: in a comment, in a quote,
    and, inside a double-quoted string, whether a substitution or a backtick is
    still open. It records each quoted string's span and the first construct it
    does not model, by their positions in the text it reads."""

    def __init__(self) -> None:
        self.quote = ""      # "", "'", "$'" (ANSI-C) or '"'
        self.comment = False
        self.escaped = False
        self.code = 0        # `$(` still open in the open double-quoted string
        self.ticks = 0       # backticks seen in the open double-quoted string
        self.prev = "\n"
        self.opened = 0      # where the open quote started
        self.spans: list[tuple[int, int]] = []
        self.unmodelled: int | None = None

    def feed(self, text: str, start: int, end: int) -> None:
        """Read text[start:end]."""
        for i in range(start, end):
            ch = text[i]
            if self.escaped:
                # An escaped character is a plain word character, so `\$(`
                # opens no substitution and the `#` in `a\b#` starts no
                # comment. Line continuations are already deleted.
                self.escaped = False
                self.prev = "_"
                continue
            was = self.quote
            if self.comment:
                self.comment = ch != "\n"
            elif self.quote == "'":
                self.quote = "" if ch == "'" else "'"
            elif self.quote:
                self._in_escaping_quote(ch)
            else:
                self._unquoted(ch, i)
            self._mark(was, i)
            self.prev = ch

    def _mark(self, was: str, i: int) -> None:
        """Record where a quote opens, and its span when it closes."""
        if self.quote and not was:
            self.opened = i
        elif was and not self.quote:
            self.spans.append((self.opened, i + 1))

    def _in_escaping_quote(self, ch: str) -> None:
        """A step inside a double-quoted or ANSI-C string."""
        if ch == "\\":
            self.escaped = True
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
            self.escaped = True
        elif ch == "'":
            self.quote = "$'" if self.prev == "$" else "'"
        elif ch == '"':
            self.quote, self.code, self.ticks = '"', 0, 0
        elif ch == "#" and self.prev in " \t\n;|&(":
            self.comment = True
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


def _terminator_lines(text: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """The start of every line that can end a heredoc, by delimiter: for `<<`
    a line that holds the delimiter alone, and for `<<-` one that may also
    begin with tabs. Measured in zsh and bash: an indented or padded line
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


def _heredoc(text: str, at: int, scan: _QuoteScan,
             ends: tuple[dict[str, list[int]], dict[str, list[int]]]) -> tuple[int, int, bool] | None:
    """(body start, body end, quoted) for the heredoc whose `<<` is at `at`,
    after feeding `scan` through the operator's line; None when the view does
    not model it: an unreadable delimiter, no body, a second `<<` on the line,
    a quote opened on the line and left open, or no last line."""
    m = _OPERATOR.match(text, at)
    newline = text.find("\n", at)
    if m is None or newline < 0 or "<<" in text[m.end():newline]:
        return None
    quote = scan.quote
    scan.feed(text, at, newline + 1)
    if scan.quote != quote or scan.prev != "\n" or scan.unmodelled is not None:
        return None
    quoted = m.group("qd") or m.group("bd")
    tail = _body_end(ends[bool(m.group("dash"))], quoted or m.group("pd"), newline + 1)
    return None if tail is None else (newline + 1, tail, bool(quoted))


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
    in one pass; also where each kept body lies in the result, and where the
    raw text starts: at the first construct the view does not model."""
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
        kept = "" if quoted else _reduced(text[body:tail])
        size = _append(out, bodies, size, text[last:body], kept)
        last = fed = tail
    out.append(text[last:])
    return "".join(out), bodies, size + raw - last


def _string_spans(text: str, bodies: dict[int, int], stop: int) -> list[tuple[int, int]]:
    """(start, end) of each quoted string that closes before `stop` and before
    the first construct the scan does not model. The scan reads the text up to
    `stop` but no kept heredoc body (`bodies` maps a body's start in this text,
    the view, to its end). `start` is the opening quote, which for an ANSI-C
    string is the `'` after its `$`."""
    scan, pos = _QuoteScan(), 0
    for start, end in bodies.items():
        scan.feed(text, pos, start)
        pos = end
    scan.feed(text, pos, stop)
    limit = stop if scan.unmodelled is None else scan.unmodelled
    return [span for span in scan.spans if span[1] <= limit]


def _command_words(text: str, spans: list[tuple[int, int]]) -> tuple[list[int], list[bool]]:
    """Where each command word outside a quoted string starts, and whether it
    is a search command, whose quoted arguments are patterns, not prose."""
    opens = [start for start, _ in spans]
    starts: list[int] = []
    searches: list[bool] = []
    for m in _COMMAND_WORD.finditer(text):
        j = bisect.bisect_right(opens, m.start()) - 1
        if j < 0 or m.start() >= spans[j][1]:
            starts.append(m.start(1))
            searches.append(m.group(1) in _PATTERN_COMMANDS)
    return starts, searches


def _strings_view(text: str, bodies: dict[int, int], stop: int) -> str:
    """Each quoted string of 20 or more characters that closes before `stop`
    reduced to its expansions, unless it is an argument of a search command;
    the text from `stop` on stays raw."""
    spans = _string_spans(text, bodies, stop)
    starts, searches = _command_words(text, spans)
    out: list[str] = []
    last = 0
    for start, end in spans:
        inner = text[start + 1:end - 1]
        k = bisect.bisect_right(starts, start) - 1
        if len(inner) >= LONG_STRING_THRESHOLD and not (k >= 0 and searches[k]):
            out.append(text[last:start + 1] + _reduced(inner) + text[end - 1])
            last = end
    out.append(text[last:])
    return "".join(out)


def env_guard_view(command: str) -> str:
    """The command as the env-var guard should judge it: what the shell
    expands, without the prose of long strings and heredoc bodies."""
    return _strings_view(*_heredocs_view(_CONTINUATION.sub(r"\1", command)))
