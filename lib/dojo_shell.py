"""Dojo shell — a minimal curses surface for the three dojo lessons.

Borrows ``lib.hal.settings_tui``'s *frame* (╌ header band, ▸ cursor, ╌ footer
keymap) but renders the dojo lesson list, not the provider list. It is NOT a
general toolkit — one dojo, three rows (design falsification c).

The pedagogy lives in ``skills/dojo/SKILL.md``; this shell is the navigable
terminal surface. Lessons are a compact mirror of the SKILL's hook + try-it +
next beats — the observable-run completion gate stays in the SKILL (running the
``try_it`` command), never prose-graded here.
"""

from __future__ import annotations

import curses
import json
import os
import sys
from pathlib import Path
from typing import Optional

from lib.dojo_theme import GLYPHS, THEME, header_line, footer_line

# The three lessons live in config/dojo-lessons.json so the shell and
# skills/dojo/SKILL.md cannot drift silently (tests/test_dojo_lessons.py pins
# the ids/names to the SKILL's). The SKILL.md remains the pedagogical source of
# truth; edit there first, then the data file.


_FALLBACK_LESSONS: list[dict] = [
    {
        "id": "L1",
        "name": "prompt-coach",
        "hook": "Vague in, sharp out — let GRIP rewrite your prompt before you run it.",
        "try_it": "/prompt-coach",
        "next": "Next: make work survive a /clear (save + recall).",
    },
    {
        "id": "L2",
        "name": "save + recall",
        "hook": "Your work survives a /clear.",
        "try_it": "/save  then  /recall \"what we just did\"",
        "next": "Next: iterate to a criterion (converge).",
    },
    {
        "id": "L3",
        "name": "converge",
        "hook": "Iterate to a criterion — stops at score >= 93 or max depth.",
        "try_it": "/converge \"<your text>\" --criterion \"<=3 sentences\" --depth 1 --show-work",
        "next": "That's the core of GRIP. Next: the six how-to commands, starting with /howto-start.",
    },
]


def _load_lessons() -> list[dict]:
    """Load the lesson mirror, falling back to an inline copy if the data file
    is absent (the shell must never crash the REPL over a missing data file)."""
    data_file = (
        Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
        / "config" / "dojo-lessons.json"
    )
    try:
        lessons = json.loads(data_file.read_text(encoding="utf-8")).get("lessons", [])
        if lessons:
            return lessons
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return _FALLBACK_LESSONS


LESSONS: list[dict] = _load_lessons()


def open_dojo() -> Optional[str]:
    """Open the dojo shell. Returns the ``try_it`` command the learner chose to
    run, or ``None`` if they quit without choosing.

    Falls back to a plain-text lesson list on a non-TTY (or any curses failure)
    so ``/dojo`` never silently does nothing — it degrades to something readable
    instead of swallowing the error.
    """
    if not sys.stdout.isatty():
        return _print_plain_lessons()
    try:
        return curses.wrapper(_run)
    except Exception:
        return _print_plain_lessons()


def _print_plain_lessons() -> Optional[str]:
    """Non-TTY fallback: print the three lessons as plain text."""
    print("the dojo — three moves. Run each on your own work.")
    for lesson in LESSONS:
        print(f"\n  {lesson['id']}  {lesson['name']}")
        print(f"    {lesson['hook']}")
        print(f"    your turn: {lesson['try_it']}")
        print(f"    {lesson['next']}")
    return None


def _run(stdscr: "curses._CursesWindow") -> Optional[str]:
    curses.curs_set(0)
    stdscr.keypad(True)

    idx = 0
    selected: Optional[int] = None  # None = list; int = that lesson expanded

    while True:
        h, w = stdscr.getmaxyx()
        _draw(stdscr, idx, selected, h, w)

        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q")):
            return None
        if selected is not None:
            # In "your turn" view: Enter/RETURN runs the command (returns it);
            # any other key returns to the list.
            if key in (ord("\n"), ord("\r"), curses.KEY_ENTER, ord("r"), ord("R")):
                return LESSONS[selected]["try_it"]
            selected = None
            continue
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(LESSONS)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(LESSONS)
        elif key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            selected = idx


def _draw(stdscr: "curses._CursesWindow", idx: int, selected: Optional[int],
          h: int, w: int) -> None:
    stdscr.erase()
    _draw_header(stdscr, w)
    if selected is None:
        _draw_lesson_list(stdscr, idx, h, w)
    else:
        _draw_lesson_detail(stdscr, selected, h, w)
    _draw_footer(stdscr, h, w, detail=selected is not None)
    stdscr.refresh()


def _draw_header(stdscr: "curses._CursesWindow", w: int) -> None:
    title = " the dojo "
    _safe_addstr(stdscr, 0, 0, header_line(title, w)[: w - 1], curses.A_DIM)
    _safe_addstr(stdscr, 1, 2, "Three moves. Run each on your own work.", curses.A_DIM)


def _draw_lesson_list(stdscr: "curses._CursesWindow", idx: int, h: int, w: int) -> None:
    section = f"{GLYPHS['bar'] * 2} Lessons " + GLYPHS["bar"] * max(1, w - 13)
    _safe_addstr(stdscr, 3, 2, section[: w - 3], curses.A_DIM)
    for i, lesson in enumerate(LESSONS):
        y = 5 + i * 3
        if y + 2 >= h - 2:
            break
        is_sel = i == idx
        attr = curses.A_BOLD if is_sel else curses.A_DIM
        cursor = GLYPHS["cursor"] if is_sel else " "
        _safe_addstr(stdscr, y, 0, f"  {cursor} {lesson['id']}  {lesson['name']}", attr)
        _safe_addstr(stdscr, y + 1, 5, _clip(lesson["hook"], w - 7), curses.A_DIM)


def _draw_lesson_detail(stdscr: "curses._CursesWindow", idx: int, h: int, w: int) -> None:
    lesson = LESSONS[idx]
    _safe_addstr(stdscr, 3, 2, f"{lesson['id']}  {lesson['name']}", curses.A_BOLD)
    y = 5
    for line in _wrap(lesson["hook"], w - 6):
        if y >= h - 3:
            break
        _safe_addstr(stdscr, y, 4, line, curses.A_DIM)
        y += 1
    y += 1
    _safe_addstr(stdscr, y, 4, "Run it — press Enter:", curses.A_BOLD)
    y += 1
    for line in _wrap(lesson["try_it"], w - 8):
        if y >= h - 3:
            break
        _safe_addstr(stdscr, y, 6, line, curses.A_BOLD)
        y += 1
    y += 1
    for line in _wrap(lesson["next"], w - 6):
        if y >= h - 3:
            break
        _safe_addstr(stdscr, y, 4, line, curses.A_DIM)
        y += 1


def _draw_footer(stdscr: "curses._CursesWindow", h: int, w: int, *,
                 detail: bool = False) -> None:
    keys = ("Enter run · any key back · q quit" if detail
            else "↑↓ navigate · Enter view · q quit")
    _safe_addstr(stdscr, h - 1, 0, footer_line(keys, w)[: w - 1], curses.A_DIM)


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text[: max(0, n - 1)] + "…" if len(text) > n else text


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    # break_long_words=False so `--show-work` is never split mid-token; it wraps
    # to the next line whole. break_on_hyphens=False keeps hyphenated flags
    # (`--criterion`) intact too.
    return textwrap.wrap(text, width=max(1, width), break_long_words=False,
                         break_on_hyphens=False) or [""]


def _safe_addstr(stdscr: "curses._CursesWindow", y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


__all__ = ["LESSONS", "open_dojo", "THEME"]
