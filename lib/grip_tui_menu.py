"""GRIP terminal menu: the one keyboard picker behind gg++'s harness and model steps.

lib/grip_harness_picker.py and lib/grip_harness_model.py each carried a
copy-adapted menu (N=2), and both copies had the same defects. Driven under a
real pseudo-terminal on 2026-11-24 they showed: rows wider than the terminal
wrapped, so the fixed cursor-up count repainted over the wrong lines and the
menu walked down the screen; a 77-row model list taller than the terminal
pushed a new copy into the scrollback on every key; a lone Esc waited for two
more keys; keys typed during a repaint were echoed and then discarded, because
the terminal went back to line mode between keys; and NO_COLOR was ignored.

This module is the shared shim, and each rule below is pinned by
tests/test_grip_tui_menu.py:

  * every row fits on ONE terminal line (cut with an ellipsis), so a repaint
    moves up by exactly the number of lines it drew;
  * a frame is never taller than the terminal: long lists scroll inside a
    window, with "N more" lines above and below;
  * the terminal stays in no-echo, key-at-a-time mode for the whole menu, so a
    key typed during a repaint is neither echoed nor lost;
  * a lone Esc returns after a 50 ms escape timeout;
  * meaning is carried by text (a marker, a number, a badge word), never by
    colour alone, and NO_COLOR (any non-empty value) removes every style code;
  * no terminal, TERM=dumb or a terminal too small gives a plain numbered
    prompt instead.

Results are tuples: ("choose", index), ("cancel", None) for Esc or q,
("abort", None) for Ctrl-C, and ("unavailable", None) when the terminal cannot
be put into key-at-a-time mode (the caller then uses plain_pick). Stdlib only.
"""
from __future__ import annotations

import os
import select
import sys
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, TextIO, Tuple

# The three tunables. ESC_TIMEOUT_S is how long a lone Esc byte waits for the
# rest of an escape sequence: an arrow key arrives as ESC [ A in one write, so a
# short wait tells a pressed Esc from the start of a sequence. Longer makes Esc
# feel slow (test_pty_lone_esc_returns_at_once); shorter risks a sequence split
# by a slow remote link reading as Esc followed by "[A" -- raise it there.
# MIN_COLS x MIN_ROWS is the smallest terminal the live menu draws in; below
# it interactive_ok() is False and both pickers fall back to plain_pick.
ESC_TIMEOUT_S = 0.05
MIN_COLS = 40
MIN_ROWS = 8
CURSOR_MARK = "▶ "
CHOOSE, CANCEL, ABORT, UNAVAILABLE = "choose", "cancel", "abort", "unavailable"

_SGR = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "cyan": "\033[36m",
    "mag": "\033[35m", "grn": "\033[32m", "yel": "\033[33m",
}


@dataclass(frozen=True)
class MenuItem:
    """One choosable row. `badge` is a short status word shown in its own
    column (so it is never cut off); `note` is the full sentence shown in the
    status line while the row is highlighted; `warn` styles the badge as a
    warning, but the badge TEXT always carries the meaning on its own."""

    key: str
    label: str
    detail: str = ""
    badge: str = ""
    note: str = ""
    group: str = ""
    warn: bool = False


# --- capability -------------------------------------------------------------

def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def color_enabled(stream: Optional[TextIO] = None, env: Optional[dict] = None) -> bool:
    """Colour only on a capable terminal, and never when NO_COLOR is set to a
    non-empty value (the no-color.org convention)."""
    env = os.environ if env is None else env
    if env.get("NO_COLOR", ""):
        return False
    stream = sys.stderr if stream is None else stream
    return _isatty(stream) and env.get("TERM", "") not in ("", "dumb")


def terminal_size() -> Tuple[int, int]:
    """(columns, rows) of the terminal the menu draws on; 80x24 if unknown."""
    for stream in (sys.stderr, sys.stdin, sys.stdout):
        try:
            size = os.get_terminal_size(stream.fileno())
        except (AttributeError, ValueError, OSError):
            continue
        if size.columns > 0 and size.lines > 0:
            return size.columns, size.lines
    return 80, 24


def interactive_ok(env: Optional[dict] = None) -> bool:
    """True when the live menu can run: stdin and stderr are terminals, TERM is
    not dumb, termios exists and the terminal is at least MIN_COLS x MIN_ROWS."""
    env = os.environ if env is None else env
    if env.get("TERM", "") in ("", "dumb"):
        return False
    if not (_isatty(sys.stdin) and _isatty(sys.stderr)):
        return False
    try:
        import termios  # noqa: F401
    except ImportError:
        return False
    cols, rows = terminal_size()
    return cols >= MIN_COLS and rows >= MIN_ROWS


# --- text measurement -------------------------------------------------------

def _cells(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cell_width(text: str) -> int:
    """Terminal cells `text` occupies (wide CJK = 2, combining marks = 0)."""
    return sum(_cells(ch) for ch in text)


def fit(text: str, cells: int) -> str:
    """`text` cut to at most `cells` terminal cells, ending in an ellipsis when cut."""
    if cells <= 0:
        return ""
    if cell_width(text) <= cells:
        return text
    out, used = [], 0
    for ch in text:
        if used + _cells(ch) > cells - 1:
            break
        out.append(ch)
        used += _cells(ch)
    return "".join(out) + "…"


def pad(text: str, cells: int) -> str:
    """`text` fitted, then padded with spaces to exactly `cells` cells."""
    text = fit(text, cells)
    return text + " " * max(0, cells - cell_width(text))


def _style(text: str, style: str, color: bool) -> str:
    if not (text and color and style):
        return text
    return "".join(_SGR[s] for s in style.split()) + text + _SGR["reset"]


def compose(segments: Sequence[Tuple[str, str]], width: int, color: bool) -> str:
    """Join (text, style) segments into one line of at most width-1 cells.

    One cell is always left free: a line that reaches the last column makes
    some terminals wrap, and a wrapped row is exactly what made the old menus
    walk down the screen."""
    budget = max(1, width - 1)
    parts: List[str] = []
    for text, style in segments:
        if budget <= 0:
            break
        piece = fit(text, budget)
        budget -= cell_width(piece)
        parts.append(_style(piece, style, color))
        if piece != text:
            break
    return "".join(parts)


# --- keyboard state (pure) ---------------------------------------------------

_STEP = {"up": -1, "ctrl-p": -1, "down": 1, "ctrl-n": 1}
_LETTER_STEP = {"k": -1, "j": 1}
_ENDS = {"home": 0, "end": -1}
_LETTER_ENDS = {"g": 0, "G": -1}
_PAGE = {"pgup": -1, "pgdn": 1}


def _haystack(item: MenuItem) -> str:
    return f"{item.label} {item.detail} {item.key} {item.group} {item.badge}".lower()


class Menu:
    """Cursor, search query, typed-number buffer and scroll position. Pure: it
    reads key names and never touches a terminal, so every rule is testable."""

    def __init__(self, items: Sequence[MenuItem], cursor: int = 0) -> None:
        self.items = list(items)
        self.cursor = cursor if 0 <= cursor < len(self.items) else 0
        self.query = ""
        self.searching = False
        self.digits = ""
        self.top = 0
        self.page = 10

    def matches(self) -> List[int]:
        """Indices of the rows that match every word of the search query."""
        words = self.query.lower().split()
        if not words:
            return list(range(len(self.items)))
        return [i for i, it in enumerate(self.items)
                if all(w in _haystack(it) for w in words)]

    def handle(self, key: str) -> Optional[Tuple[str, Optional[int]]]:
        """Apply one key; return a result tuple when the menu is finished."""
        if key == "ctrl-c":
            return (ABORT, None)
        if self.searching:
            return self._search_key(key)
        return self._normal_key(key)

    def _normal_key(self, key: str):
        if key == "enter":
            return self._choose()
        if key in ("esc", "q"):
            return (CANCEL, None)
        if len(key) == 1 and key.isdigit():
            return self._digit(key)
        self.digits = ""
        if key == "/":
            self.searching = True
        else:
            self._navigate(key, letters=True)
        return None

    def _search_key(self, key: str):
        if key == "enter":
            return self._choose()
        if key == "esc":
            self.query, self.searching = "", False
        elif key == "backspace":
            self.searching = bool(self.query)
            self.query = self.query[:-1]
        elif key == "ctrl-u":
            self.query = ""
        elif not self._navigate(key, letters=False) and len(key) == 1 and key.isprintable():
            self.query += key
        self._keep_cursor_on_a_match()
        return None

    def _navigate(self, key: str, letters: bool) -> bool:
        step = _STEP.get(key, _LETTER_STEP.get(key) if letters else None)
        if step is not None:
            self._step(step, wrap=True)
            return True
        if key in _PAGE:
            self._step(_PAGE[key] * max(1, self.page), wrap=False)
            return True
        end = _ENDS.get(key, _LETTER_ENDS.get(key) if letters else None)
        if end is None:
            return False
        shown = self.matches()
        if shown:
            self.cursor = shown[end]
        return True

    def _step(self, delta: int, wrap: bool) -> None:
        shown = self.matches()
        if not shown:
            return
        pos = shown.index(self.cursor) if self.cursor in shown else 0
        pos = (pos + delta) % len(shown) if wrap else max(0, min(len(shown) - 1, pos + delta))
        self.cursor = shown[pos]

    def _keep_cursor_on_a_match(self) -> None:
        shown = self.matches()
        if shown and self.cursor not in shown:
            self.cursor = shown[0]

    def _choose(self):
        return (CHOOSE, self.cursor) if self.cursor in self.matches() else None

    def _digit(self, ch: str):
        """Jump by row number. A number that no longer number starts with
        chooses at once, so 2-9 still choose in one key on a list of 19, while
        '1' waits for a second digit (or Enter) because 10-19 start with it."""
        count = len(self.items)
        for typed in (self.digits + ch, ch):
            if 1 <= int(typed) <= count:
                self.cursor = int(typed) - 1
                if int(typed) * 10 > count:
                    self.digits = ""
                    return (CHOOSE, self.cursor)
                self.digits = typed
                return None
        self.digits = ""
        return None


# --- rendering (pure) --------------------------------------------------------

_SEARCH_HINT = ("type to filter", "↑↓ move", "Enter choose", "Esc clear search")


def render(menu: Menu, title: str, *, width: int, height: int, color: bool,
           subtitle: str = "", hint: Sequence[str] = ()) -> List[str]:
    """The frame as a list of lines, each at most width-1 cells, never more
    than height-1 lines (given a terminal of at least MIN_ROWS)."""
    head = [_title_line(title, subtitle, width, color)]
    if menu.searching:
        head.append(_search_line(menu, width, color))
    foot = [_status_line(menu, width, color), _hint_line(menu, hint, width, color)]
    room = max(1, height - 1 - len(head) - len(foot))
    return head + _body(menu, width, room, color) + foot


def _display_rows(menu: Menu) -> List[Tuple[str, object]]:
    rows: List[Tuple[str, object]] = []
    group = None
    for idx in menu.matches():
        name = menu.items[idx].group
        if name and name != group:
            rows.append(("group", name))
        group = name
        rows.append(("item", idx))
    return rows


def _scroll(menu: Menu, rows: list, size: int) -> None:
    pos = rows.index(("item", menu.cursor)) if ("item", menu.cursor) in rows else 0
    first = pos - 1 if pos > 0 and rows[pos - 1][0] == "group" else pos
    if first < menu.top:
        menu.top = first
    elif pos >= menu.top + size:
        menu.top = pos - size + 1
    menu.top = max(0, min(menu.top, len(rows) - size))


def _body(menu: Menu, width: int, room: int, color: bool) -> List[str]:
    rows = _display_rows(menu)
    cols = _columns(menu, width)
    if len(rows) <= room:
        menu.top, menu.page = 0, max(1, room)
        return [_row_line(menu, row, cols, width, color) for row in rows]
    size = max(1, room - 2)
    menu.page = size
    _scroll(menu, rows, size)
    shown = rows[menu.top:menu.top + size]
    return ([_more_line("↑", rows[:menu.top], width, color)]
            + [_row_line(menu, row, cols, width, color) for row in shown]
            + [_more_line("↓", rows[menu.top + size:], width, color)])


def _more_line(arrow: str, hidden: list, width: int, color: bool) -> str:
    count = sum(1 for kind, _ in hidden if kind == "item")
    return compose([(f"     {arrow} {count} more", "dim")], width, color) if count else ""


def _columns(menu: Menu, width: int) -> dict:
    items = menu.items
    num = len(str(len(items)))
    badge = max((cell_width(it.badge) for it in items), default=0)
    longest = max((cell_width(it.label) for it in items), default=0)
    fixed = 8 + num + (badge + 2 if badge else 0)
    return {"num": num, "badge": badge,
            "label": max(8, min(longest, (width - fixed) // 2))}


def _row_line(menu: Menu, row: tuple, cols: dict, width: int, color: bool) -> str:
    kind, value = row
    if kind == "group":
        return compose([("  ── " + str(value), "dim")], width, color)
    item = menu.items[value]
    on = value == menu.cursor
    label_style = "bold" if on else ("dim" if item.warn else "")
    segs = [("  ", ""), (CURSOR_MARK if on else "  ", "mag bold" if on else ""),
            (str(value + 1).rjust(cols["num"]), "dim"), ("  ", ""),
            (pad(item.label, cols["label"]), label_style), ("  ", "")]
    if cols["badge"]:
        segs += [(pad(item.badge, cols["badge"]), "yel" if item.warn else "grn"), ("  ", "")]
    segs.append((item.detail, "dim"))
    return compose(segs, width, color)


def _title_line(title: str, subtitle: str, width: int, color: bool) -> str:
    segs = [("  ", ""), ("GRIP", "bold cyan"), ("  ", ""), (title, "bold")]
    if subtitle:
        segs += [("  ", ""), (subtitle, "dim")]
    return compose(segs, width, color)


def _search_line(menu: Menu, width: int, color: bool) -> str:
    count = len(menu.matches())
    found = f"{count} match" + ("" if count == 1 else "es")
    return compose([("  search: ", "dim"), (menu.query + "_", "bold"), ("   ", ""),
                    (found, "dim")], width, color)


def _status_line(menu: Menu, width: int, color: bool) -> str:
    if not menu.matches():
        return compose([("  no match for ", "dim"), (f"'{menu.query}'", "bold"),
                        (" -- Backspace edits, Esc clears", "dim")], width, color)
    item = menu.items[menu.cursor]
    return compose([("  ", ""), (item.note or item.detail, "yel" if item.warn else "dim")],
                   width, color)


def _hint_line(menu: Menu, hint: Sequence[str], width: int, color: bool) -> str:
    """Key hints in priority order; the ones that do not fit are dropped whole."""
    kept: List[str] = []
    used = 2
    for part in (_SEARCH_HINT if menu.searching else hint):
        cost = cell_width(part) + (3 if kept else 0)
        if used + cost > width - 1:
            break
        kept.append(part)
        used += cost
    return compose([("  " + " · ".join(kept), "dim")], width, color)


# --- keys --------------------------------------------------------------------

_SEQ = {
    b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left", b"[H": "home",
    b"[F": "end", b"[1~": "home", b"[4~": "end", b"[7~": "home", b"[8~": "end",
    b"[5~": "pgup", b"[6~": "pgdn", b"[3~": "delete",
    b"OA": "up", b"OB": "down", b"OH": "home", b"OF": "end",
}
_CTRL = {
    b"\r": "enter", b"\n": "enter", b"\x03": "ctrl-c", b"\x7f": "backspace",
    b"\x08": "backspace", b"\x0e": "ctrl-n", b"\x10": "ctrl-p", b"\x15": "ctrl-u",
}


def _utf8_length(lead: int) -> int:
    if lead >= 0xF0:
        return 4
    if lead >= 0xE0:
        return 3
    return 2 if lead >= 0xC0 else 1


def _parse_text(buf: bytes, final: bool) -> Tuple[Optional[str], int]:
    need = _utf8_length(buf[0])
    if len(buf) < need and not final:
        return None, 0
    used = min(need, len(buf))
    ch = buf[:used].decode("utf-8", "replace")
    return (ch if ch.isprintable() and "�" not in ch else ""), used


def _parse_escape(buf: bytes, final: bool) -> Tuple[Optional[str], int]:
    incomplete = ("esc", 1) if final else (None, 0)
    if len(buf) == 1:
        return incomplete
    intro = buf[1:2]
    if intro == b"O":
        return (_SEQ.get(bytes(buf[1:3]), ""), 3) if len(buf) >= 3 else incomplete
    if intro != b"[":
        return "esc", 1
    end = 2
    while end < len(buf) and 0x20 <= buf[end] <= 0x3F:
        end += 1
    if end >= len(buf):
        return incomplete
    return _SEQ.get(bytes(buf[1:end + 1]), ""), end + 1


def parse_key(buf: bytes, final: bool) -> Tuple[Optional[str], int]:
    """(key name, bytes used) for the first key in `buf`.

    (None, 0) means the bytes so far may be the start of a longer key and more
    may still arrive; with `final` True (the escape timeout has passed) a lone
    ESC is the Esc key. An unknown sequence comes back as "" so the caller
    skips it instead of treating its bytes as typed text."""
    if not buf:
        return None, 0
    if buf[:1] == b"\x1b":
        return _parse_escape(buf, final)
    if buf[:1] in _CTRL:
        return _CTRL[buf[:1]], 1
    return _parse_text(buf, final)


# --- terminal (the only impure part) ----------------------------------------

class _Terminal:
    """No-echo, key-at-a-time mode, held from open() to close().

    Holding the mode for the whole menu is the fix for lost keys: the old code
    switched to raw mode for each key with tty.setraw, whose default TCSAFLUSH
    discards whatever was typed while the previous frame was being drawn."""

    def __init__(self, fd: int, out: TextIO) -> None:
        self.fd, self.out = fd, out
        self.drawn, self.buf, self.saved = 0, b"", None

    def open(self) -> None:
        import termios
        self.saved = termios.tcgetattr(self.fd)
        mode = termios.tcgetattr(self.fd)
        mode[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        mode[6][termios.VMIN], mode[6][termios.VTIME] = 1, 0
        # TCSAFLUSH once, on entry only: a stray key pressed before the menu
        # appeared must not choose something the operator has not seen.
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, mode)
        try:
            self._write("\033[?25l")
        except BaseException:
            # A failed open must leave the terminal as it found it: the caller
            # falls back to a plain prompt, which needs echo and line mode back.
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            raise

    def close(self) -> None:
        import termios
        self.erase()
        self._write("\033[?25h")
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def _rewind(self) -> str:
        up = f"\033[{self.drawn}A" if self.drawn else ""
        return "\r" + up + "\033[J"

    def draw(self, lines: List[str]) -> None:
        self._write(self._rewind() + "".join(line + "\n" for line in lines))
        self.drawn = len(lines)

    def erase(self) -> None:
        self._write(self._rewind())
        self.drawn = 0

    def _fill(self, timeout: Optional[float]) -> bool:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return False
        data = os.read(self.fd, 1024)
        if not data:
            raise EOFError
        self.buf += data
        return True

    def read_key(self) -> str:
        while True:
            key, used = parse_key(self.buf, final=False)
            if key is None:
                if self._fill(ESC_TIMEOUT_S if self.buf else None):
                    continue
                key, used = parse_key(self.buf, final=True)
            self.buf = self.buf[used:]
            if key:
                return key


def _open_terminal(out: TextIO) -> Optional[_Terminal]:
    try:
        import termios
    except ImportError:
        return None
    term = _Terminal(sys.stdin.fileno(), out)
    try:
        term.open()
    except (termios.error, OSError, ValueError):
        return None
    return term


def run_menu(items: Sequence[MenuItem], *, title: str, cursor: int = 0,
             subtitle: str = "", hint: Sequence[str] = ()) -> Tuple[str, Optional[int]]:
    """Run the live menu on stdin, drawing on stderr, and erase it afterwards."""
    out = sys.stderr
    term = _open_terminal(out)
    if term is None:
        return (UNAVAILABLE, None)
    menu, color = Menu(items, cursor), color_enabled(out)
    try:
        while True:
            cols, rows = terminal_size()
            term.draw(render(menu, title, width=cols, height=rows, color=color,
                             subtitle=subtitle, hint=hint))
            result = menu.handle(term.read_key())
            if result:
                return result
    except EOFError:
        return (CANCEL, None)
    finally:
        term.close()


def announce(text: str) -> None:
    """The one summary line a menu leaves behind after it erases itself."""
    cols, _ = terminal_size()
    line = compose([("  ", ""), ("GRIP", "bold cyan"), ("  ", ""), (text, "")],
                   cols, color_enabled(sys.stderr))
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


# --- plain fallback ------------------------------------------------------------

def _plain_row(index: int, item: MenuItem, default: Optional[int]) -> str:
    tags = [t for t in (item.badge, "default" if index == default else "") if t]
    tag = f" [{', '.join(tags)}]" if tags else ""
    detail = f" -- {item.detail}" if item.detail else ""
    return f"  {index + 1}. {item.label}{tag}{detail}"


def plain_pick(items: Sequence[MenuItem], *, title: str, default: Optional[int] = None,
               read=None) -> Tuple[str, Optional[int]]:
    """Numbered list on stderr and one typed line, for a pipe or a dumb terminal.

    A number in range chooses that row. An empty, unreadable or out-of-range
    line keeps the default: ("choose", default), or ("cancel", None) when there
    is no default row. Ctrl-C aborts. Nothing here assumes a working terminal."""
    read = input if read is None else read
    out = sys.stderr
    out.write(f"\n{title}\n")
    group = None
    for index, item in enumerate(items):
        if item.group and item.group != group:
            out.write(f"  -- {item.group}\n")
        group = item.group
        out.write(_plain_row(index, item, default) + "\n")
    keep = f"default {default + 1}" if default is not None else "Enter keeps the current one"
    out.write(f"Choose 1-{len(items)} [{keep}]: ")
    out.flush()
    try:
        raw = read().strip()
    except KeyboardInterrupt:
        return (ABORT, None)
    except EOFError:
        raw = ""
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return (CHOOSE, int(raw) - 1)
    return (CHOOSE, default) if default is not None else (CANCEL, None)
