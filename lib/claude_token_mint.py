"""Mint a long-lived Claude token and store it without ever showing it.

``claude setup-token`` prints the new token on the terminal, once. From there
it is one glance, one scrollback buffer or one copy away from somewhere it
should not be: ``grip-account token`` relied on the operator pasting it into
``grip-relay-bill store``, which puts it on the screen and on the clipboard.

This module runs the same command behind a pseudo-terminal instead. It reads
everything the command writes, shows the operator only the lines that are safe
to see (the approval link, the prompts), picks the token off the screen and
hands it to the store. The token is never written to a real terminal, an
argument list, a log or the clipboard.

WHAT WAS MEASURED, AND WHAT WAS NOT. ``claude setup-token`` 2.1.283 was run on
a headless host on 2026-10-02, behind this module, up to the point of a wrong
code being refused. No token was minted. What that showed is the test fixture:

- words are placed with cursor moves rather than spaces, lines end in
  ``\\r\\r\\n``, and the link sits inside an OSC 8 hyperlink;
- one frame arrives in several reads, so anything can straddle two of them;
- a REDRAW moves the cursor up, erases lines and rewrites only the cells that
  changed. A character that was already on the screen is stepped over, not sent
  again, so the bytes of a redraw are not the text it leaves behind. Read as a
  stream, "code" drawn over an old "o" arrives as "c", a cursor step, "de".

That last fact is why this module keeps a small model of the screen and reads
the token off it, instead of searching the stream.

The LAST screen, the one that carries the token, could not be looked at before
a real mint: seeing it means minting a token. So nothing here depends on its
wording. The token is recognised by its shape, and a capture that is wrong in
any way (cut short, polluted by old text, belonging to another account) fails
the check below and is stored nowhere.

The first real mint through this module was made on 2026-10-02 (2.1.283). It
stored the token, the check said "same", and these are the rows it let through,
in order: "✓ Long-lived authentication token created successfully!", "Your
OAuth token (valid for 1 year):", "Store this token securely. You won't be able
to see it again.", "Use this token by setting: export
CLAUDE_CODE_OAUTH_TOKEN=<token>". The token's own row, between the second and
the third, was withheld. That is the WORDING of the last screen; its bytes were
not kept, so the test fixture's cursor moves are still modelled on the first
screen's.

THE CHECK BEFORE STORING. A new token is kept only if a tiny live call made
with it reports the same usage windows as the same call made with the slot's
own login. That is what ties a stored label to an account: the slot's login
says who it is (``claude auth status`` gives the email), and the matching
fingerprints say the token belongs to that account. It is corroboration, not
proof: two accounts could share a weekly reset time by coincidence.

The same call settles WHICH READING OF THE SCREEN is the token. A pause in the
middle of a frame can leave the screen showing the token with a row's old text
still on its end, or only the beginning of it. One reading is then the start of
the other, and the screen cannot say which is right: "the longer one" is wrong
for the first case and "the later one" for the second. So every different
reading is kept, and they are tried in turn, the latest screen's first. The
service refuses a reading that is not a token (status 401, measured 2026-10-02
with a made-up one), and the next is tried. Only that refusal moves on. A call
that fails some other way (a usage limit, an outage, no answer) says nothing
about the token and is reported as a check that could not be made.

WHAT CAN STILL SHOW. A row is withheld when it carries the token prefix or a
long opaque string. A piece of a token shorter than twelve characters, alone on
a row of its own, would pass. The pseudo-terminal is a thousand columns wide
precisely so the command has no reason to break the token across rows.
"""
from __future__ import annotations

import codecs
import contextlib
import hashlib
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

# The store's own library, found beside this file wherever this file is
# loaded from: ``lib/`` here, and the directory the fleet tool unpacks its
# modules into on another machine.
import claude_token_store

# A pseudo-terminal exists on POSIX only. This file must still IMPORT on
# Windows, because everything that reads the screen, judges a token and writes
# the ledger works there; only running the command behind a pseudo-terminal
# does not, and that says so when asked (tests/test_no_unguarded_posix_only_imports.py).
try:
    import fcntl
    import pty
    import termios
except ImportError:
    fcntl = pty = termios = None

#: False where there is no pseudo-terminal to run the minting command behind.
PTY_AVAILABLE = pty is not None

#: The shape of a long-lived OAuth token, as ``claude setup-token`` prints it.
TOKEN_SHAPE = re.compile(r"sk-ant-oat[A-Za-z0-9_-]{20,}")

#: Wide enough that the command never has to wrap a token onto a second row.
WIDE_COLUMNS = 1000

#: Variables that outrank or replace a login. A child that must use one
#: specific credential is started without them.
AUTH_OVERRIDES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

#: How many different token-shaped readings of the screen are remembered. Each
#: one that is refused costs a live call, so the list is short.
MAX_READINGS = 6

#: The status with which the service refuses a credential it does not accept.
REFUSED_STATUS = 401

PROBE_MODEL = "claude-haiku-4-5-20251001"
LEDGER = "minted.json"
COPY_OFF = "(copying is switched off here: select the link with the mouse instead)"
STILL_WAITING = ("(still waiting. If you have approved in the browser and nothing "
                 "happens, press Enter. Ctrl-C stops, and nothing is stored.)")

_URL = re.compile(r"https?://\S+")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{12,}")

# One piece of what a terminal is sent: an escape sequence, a control
# character, or a run of text. An escape sequence that has not all arrived yet
# matches nothing, which is how a read that ends in the middle of one is held
# back until the rest comes.
_PIECE = re.compile(
    r"\x1b\[(?P<params>[0-?]*)[ -/]*(?P<final>[@-~])"    # control sequence
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"                  # operating-system command
    r"|\x1b[ -/]+[0-~]"                                     # character-set choice
    r"|\x1b(?P<short>[0-Z\\^_`a-~])"                        # two-character escape
    r"|(?P<control>[\x00-\x1a\x1c-\x1f\x7f])"
    r"|(?P<text>[^\x00-\x1f\x7f]+)"
)
_STEP = {"A": (-1, 0), "B": (1, 0), "C": (0, 1), "D": (0, -1)}
#: Cursor and editing sequences this model does not carry out. Seeing one means
#: the screen below may be wrong, and a failed run says so.
_NOT_MODELLED = frozenset("HfEFdSTLMPX@")
_LONGEST_ESCAPE = 8192


def _mixed(run: str) -> bool:
    return any(ch.isdigit() for ch in run) and any(ch.isalpha() for ch in run)


def safe_to_show(line: str) -> bool:
    """False for any line that could carry the token or a piece of it.

    Links are allowed through whole: the approval link is long and opaque, and
    it is the one thing the operator needs. Everything else on the line is
    held to the rule.
    """
    if "sk-ant" in line.lower():
        return False
    return not any(map(_mixed, _OPAQUE.findall(_URL.sub(" ", line))))


def _key(row: str) -> str:
    """A row with everything but its letters and digits ignored, so that a
    spinner turning in front of the same words is the same row."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in row).split())


def _erased(col: int, cursor: int, mode: int) -> bool:
    """Whether "erase in line" removes this cell: 0 from the cursor on, 1 up
    to the cursor, 2 the whole line."""
    return mode == 2 or (mode == 0 and col >= cursor) or (mode == 1 and col <= cursor)


class _Screen:
    """Just enough of a terminal to know what text is on it.

    Rows grow downward without limit and nothing scrolls: the command only ever
    moves relative to where it is, so a plane is all it needs.
    """

    def __init__(self) -> None:
        self.rows: dict[int, dict[int, str]] = {}
        self.row = self.col = 0
        self._saved = (0, 0)
        self.not_modelled: set[str] = set()

    def write(self, text: str) -> str:
        """Carry out as much of ``text`` as is complete; return the rest."""
        pos = 0
        while pos < len(text):
            piece = _PIECE.match(text, pos)
            if piece is None:
                break
            self._carry_out(piece)
            pos = piece.end()
        return text[pos:]

    def lines(self) -> list[str]:
        """The text of every row that holds any, top to bottom."""
        return [self._row_text(self.rows[row]) for row in sorted(self.rows) if self.rows[row]]

    @staticmethod
    def _row_text(cells: dict[int, str]) -> str:
        return "".join(cells.get(col, " ") for col in range(max(cells) + 1))

    def _carry_out(self, piece: re.Match) -> None:
        if piece["text"]:
            self._type(piece["text"])
        elif piece["control"]:
            self._control(piece["control"])
        elif piece["final"]:
            self._sequence(piece["final"], piece["params"])
        elif piece["short"] == "7":
            self._saved = (self.row, self.col)
        elif piece["short"] == "8":
            self.row, self.col = self._saved

    def _type(self, text: str) -> None:
        cells = self.rows.setdefault(self.row, {})
        cells.update(zip(range(self.col, self.col + len(text)), text))
        self.col += len(text)

    def _control(self, char: str) -> None:
        if char == "\r":
            self.col = 0
        elif char == "\n":
            self.row += 1
        elif char == "\x08":
            self.col = max(0, self.col - 1)
        elif char == "\t":
            self.col = (self.col // 8 + 1) * 8

    def _sequence(self, final: str, params: str) -> None:
        first = params.split(";")[0]
        count = int(first) if first.isdigit() else 0
        if final in _STEP:
            down, right = _STEP[final]
            self.row = max(0, self.row + down * max(count, 1))
            self.col = max(0, self.col + right * max(count, 1))
        elif final == "G":
            self.col = max(count, 1) - 1
        elif final == "K":
            self._erase_line(count)
        elif final == "J":
            self._erase_screen(count)
        elif final in _NOT_MODELLED:
            self.not_modelled.add(final)

    def _erase_line(self, mode: int) -> None:
        cells = self.rows.get(self.row, {})
        self.rows[self.row] = {col: char for col, char in cells.items()
                               if not _erased(col, self.col, mode)}

    def _erase_screen(self, mode: int) -> None:
        if mode in (2, 3):
            self.rows = {}
            return
        self._erase_line(mode)
        keep_above = mode == 0
        self.rows = {row: cells for row, cells in self.rows.items()
                     if (row <= self.row) == keep_above or row == self.row}


class TokenReadings:
    """The token-shaped strings that settled screens have shown.

    A screen alone cannot always say which reading is the token. When two
    screens show overlapping readings, one the beginning of the other, the
    longer may be the token with a row's old text still clinging to it, and
    the shorter may be the token with part of it not yet drawn. Both happen,
    depending on where in a frame the command pauses, so neither rule ("the
    longer wins", "the later wins") is right every time. Every different
    reading is therefore kept, and the live check tells them apart.
    """

    def __init__(self) -> None:
        self._latest: list[str] = []    # on the most recent screen that showed any
        self._all: list[str] = []       # every different one, the most recently seen last

    def note(self, seen: list[str]) -> None:
        """Take one settled screen's readings. A screen that shows none
        changes nothing: the command may clear its screen after the token."""
        if not seen:
            return
        self._latest = seen
        earlier = [reading for reading in self._all if reading not in seen]
        self._all = (earlier + list(dict.fromkeys(seen)))[-MAX_READINGS:]

    def best(self) -> str | None:
        """The latest screen's own answer, or None when it gives no single one.

        On one screen the same token can appear more than once, or a shorter
        cut of it beside the whole: the longest wins when every other reading
        is its beginning. Two that disagree are two answers, and the screen
        does not choose between them.
        """
        if not self._latest:
            return None
        longest = max(self._latest, key=len)
        return longest if all(map(longest.startswith, self._latest)) else None

    def candidates(self) -> list[str]:
        """Every reading that could be the token, the likeliest first: the
        latest screen's own answer, then the others, the most recently seen
        first. Empty when no settled screen ever showed anything token-shaped."""
        best = self.best()
        others = [reading for reading in reversed(self._all) if reading != best]
        return ([best] if best else []) + others

    def lengths(self) -> list[int]:
        """How long each different reading was. Never the readings themselves."""
        return sorted({len(reading) for reading in self._all})


class MintScreen:
    """Watches what the minting command draws. Says what may be shown, and
    keeps what must not be.

    Nothing is judged while a frame is still arriving. ``feed`` only updates
    the picture of the screen; ``settle`` is called once the command pauses,
    and that is when rows are shown and the token is looked for.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = ""
        self._screen = _Screen()
        self.readings = TokenReadings()
        self._on_screen: set[str] = set()
        self._withheld: set[str] = set()
        self.bytes = 0

    @property
    def withheld(self) -> int:
        """How many different rows were kept back from the operator."""
        return len(self._withheld)

    def feed(self, chunk: bytes, final: bool = False) -> None:
        """Take one read's worth of output."""
        self.bytes += len(chunk)
        rest = self._screen.write(self._pending + self._decoder.decode(chunk, final))
        if len(rest) > _LONGEST_ESCAPE:     # an escape that never ends: drop its first byte
            rest = self._screen.write(rest[1:])
        self._pending = rest

    def settle(self) -> list[str]:
        """The command has paused: look at the screen. Returns the rows that
        are safe to show and were not there at the last look, and notes any
        token on it.

        "Not there at the last look" rather than "never shown before": after a
        refused code the prompt goes away and comes back, and the operator has
        to see that it is back.
        """
        rows = [" ".join(row.split()) for row in self._screen.lines()]
        self.readings.note(TOKEN_SHAPE.findall("\n".join(rows)))
        visible: dict[str, str] = {}
        for row in filter(self._may_show, rows):
            visible.setdefault(_key(row), row)
        fresh = [row for key, row in visible.items() if key not in self._on_screen]
        self._on_screen = set(visible)
        return fresh

    def finish(self) -> list[str]:
        """The stream has ended: take what is left and look one last time."""
        self.feed(b"", final=True)
        return self.settle()

    def token_in_sight(self) -> bool:
        """True as soon as the screen holds anything token-shaped, settled or not."""
        return bool(TOKEN_SHAPE.search("\n".join(self._screen.lines())))

    def token(self) -> str | None:
        """The token on the most recent screen that showed one, or None.

        Only the latest such screen counts. A frame caught half-drawn can show
        the start of the token running into the old text of its row, which is
        token-shaped and wrong; the finished frame that follows replaces it. A
        screen that is later cleared shows no token and so replaces nothing.

        This is the screen's best reading and no more than that: see
        ``TokenReadings`` for why the others are kept beside it.
        """
        return self.readings.best()

    def summary(self) -> str:
        """What was seen, for a failed run. Never any of the text itself."""
        lengths = self.readings.lengths()
        unknown = "".join(sorted(self._screen.not_modelled)) or "none"
        return (f"{self.bytes} bytes, {len(self._screen.rows)} rows, "
                f"{self.withheld} withheld, cursor sequences not modelled: {unknown}, "
                f"token-shaped strings of length: {lengths or 'none'}")

    def _may_show(self, row: str) -> bool:
        if not any(map(str.isalnum, row)):
            return False
        if not safe_to_show(row):
            self._withheld.add(hashlib.sha256(row.encode()).hexdigest())
            return False
        return True


# --- the command, behind a pseudo-terminal -----------------------------------

def _open_wide_pty() -> tuple[int, int]:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, WIDE_COLUMNS, 0, 0))
    return master, slave


def _read(fd: int) -> bytes:
    try:
        return os.read(fd, 4096)
    except OSError:      # Linux reports a finished child as an I/O error
        return b""


def _burst(master: int) -> bytes:
    """Everything the command has written for now, which is usually one whole
    frame: a frame arrives in several reads with no pause between them."""
    data = _read(master)
    while data and len(data) < 262144 and select.select([master], [], [], 0.03)[0]:
        more = _read(master)
        if not more:
            break
        data += more
    return data


def _relay_keyboard(fd: int, master: int, say: Callable[[str], None]) -> int | None:
    """Pass what was typed on to the command, a line at a time. Returns None
    once input ends.

    A lone ``c`` is the command's "copy to the clipboard" key, and it is not
    passed on: nothing here goes through the clipboard. Each line is judged on
    its own, because one read can deliver several (a paste, a fast typist).
    """
    data = _read(fd)
    if not data:
        return None
    for typed in data.decode("utf-8", "replace").splitlines() or [""]:
        text = typed.strip()
        if text.lower() == "c":
            say(COPY_OFF)
        else:
            os.write(master, text.encode() + b"\r")
    return fd


def _stop(proc: subprocess.Popen) -> None:
    """End the command. One that is already finishing is given a moment to
    finish, so its own exit code is the one reported."""
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=0.5)
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)


@dataclass
class _Watch:
    """The state of one run of the pump: when it must end and what it has seen."""
    deadline: float
    linger: float
    hint_after: float
    last_output: float
    hinted: bool = False

    def token_settled(self, screen: MintScreen, now: float) -> bool:
        return bool(screen.readings.candidates()) and now - self.last_output >= self.linger

    def hint_due(self, screen: MintScreen, now: float) -> bool:
        quiet = now - self.last_output >= self.hint_after
        return quiet and not self.hinted and not screen.readings.candidates()


def _pump(proc, master: int, screen: MintScreen, say, keyboard_fd, watch: _Watch) -> None:
    """Move bytes until the command ends, the token has settled, or time is up."""
    while time.monotonic() < watch.deadline:
        typing = keyboard_fd is not None and not screen.token_in_sight()
        ready, _, _ = select.select([master, keyboard_fd] if typing else [master], [], [], 0.2)
        now = time.monotonic()
        if master in ready:
            frame = _burst(master)
            if not frame:
                return
            watch.last_output = now
            screen.feed(frame)
            list(map(say, screen.settle()))
        elif proc.poll() is not None or watch.token_settled(screen, now):
            return
        # Asked again, because this very read may have brought the token: from
        # then on the only key the command still offers is "copy".
        if typing and keyboard_fd in ready and not screen.token_in_sight():
            keyboard_fd = _relay_keyboard(keyboard_fd, master, say)
        if watch.hint_due(screen, now):
            watch.hinted = True
            say(STILL_WAITING)


def run_behind_pty(argv: list[str], env: dict, screen: MintScreen,
                   say: Callable[[str], None], keyboard_fd: int | None = None, *,
                   timeout: float = 900.0, linger: float = 2.0,
                   hint_after: float = 45.0) -> int | None:
    """Run ``argv`` on a wide pseudo-terminal, feeding its output to ``screen``.

    Nothing the command writes reaches a real terminal except through
    ``screen``. Once a token has been seen and the command has been quiet for
    ``linger`` seconds it is stopped: it may be waiting for a key, and the only
    key it offers at that point is "copy".

    Raises ``OSError`` on a platform with no pseudo-terminal (Windows).
    """
    if not PTY_AVAILABLE:
        raise OSError("this platform has no pseudo-terminal, so a token cannot be minted here")
    master, slave = _open_wide_pty()
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                            start_new_session=True, close_fds=True)
    os.close(slave)
    started = time.monotonic()
    watch = _Watch(deadline=started + timeout, linger=linger,
                   hint_after=hint_after, last_output=started)
    try:
        _pump(proc, master, screen, say, keyboard_fd, watch)
    finally:
        _stop(proc)
        os.close(master)
    list(map(say, screen.finish()))
    return proc.returncode


# --- who the slot is, and whether the token is the same account --------------

def clean_env(base, **overlay: str) -> dict:
    """``base`` without the variables that outrank a login, plus ``overlay``."""
    env = {key: value for key, value in base.items() if key not in AUTH_OVERRIDES}
    env.update(overlay)
    return env


def who_is(config_path: Path, claude: str) -> dict:
    """What ``claude auth status`` says about one config directory's login.

    Asked without any API key or token in the environment, because with one
    present the command reports that instead and gives no email.
    """
    env = clean_env(os.environ, CLAUDE_CONFIG_DIR=str(config_path), ANTHROPIC_BASE_URL="")
    try:
        done = subprocess.run([claude, "auth", "status"], env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)
        status = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return status if isinstance(status, dict) else {}


def live_call(claude: str, env: dict, cwd: str) -> str:
    """One tiny headless call. Returns its stream, which holds no credential."""
    argv = [claude, "-p", "Reply with the single word ok", "--model", PROBE_MODEL,
            "--output-format", "stream-json", "--verbose", "--max-turns", "1",
            "--settings", '{"disableAllHooks":true}']
    # No standard input: the call has no business with the operator's keyboard,
    # and without a terminal there it would wait three seconds for input.
    done = subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=180)
    return done.stdout


def _records(stream: str, kind: str) -> list[dict]:
    """The JSON lines of one type in a headless call's stream."""
    marker = f'"type":"{kind}"'
    found = []
    for line in filter(lambda text: marker in text.replace(" ", ""), stream.splitlines()):
        with contextlib.suppress(ValueError):
            found.append(json.loads(line))
    return [record for record in found if isinstance(record, dict)]


def fingerprint(stream: str) -> dict | None:
    """The account-specific part of a call's usage record: when the weekly
    window resets and why extra usage is off. None when no record came back."""
    for record in _records(stream, "rate_limit_event"):
        info = record.get("rate_limit_info") or {}
        weekly = (info.get("unifiedWindows") or {}).get("seven_day") or {}
        if weekly.get("resetsAt"):
            return {"weekly_reset": weekly["resetsAt"],
                    "extra_usage_off": info.get("overageDisabledReason")}
    return None


def _last_result(stream: str) -> dict:
    results = _records(stream, "result")
    return results[-1] if results else {}


def call_failure(stream: str) -> str | None:
    """Why the call failed, or None when it answered."""
    last = _last_result(stream)
    if not last:
        return "the call returned no result"
    if not last.get("is_error"):
        return None
    said = str(last.get("result") or f"HTTP {last.get('api_error_status')}")
    return said[:160].rstrip(". ")


def token_refused(stream: str) -> bool:
    """True when the service refused the credential itself, which is what a
    damaged or made-up token gets.

    Measured 2026-10-02 with a made-up token: the call ends in an error result
    with status 401 ("OAuth access token is invalid") and no usage record. A
    call that fails for any other reason (a usage limit, an outage, no answer
    at all) says nothing about the token, and is not called a refusal.
    """
    last = _last_result(stream)
    return bool(last.get("is_error")) and last.get("api_error_status") == REFUSED_STATUS


def _why_not_compared(token_stream: str, login_stream: str) -> str:
    """Why the two calls could not be compared, naming the one that failed."""
    for whose, stream in (("the new token", token_stream), ("the slot's login", login_stream)):
        failure = call_failure(stream)
        if failure:
            return (f"the call made with {whose} failed, so the account could not be "
                    f"compared: {failure}")
    return "no usage record came back, so the account could not be compared"


def judge_token(token_stream: str, login_stream: str) -> tuple[str, str]:
    """Compare a call made with the new token to one made with the slot's login.

    Returns one of ``same``, ``different``, ``rejected``, ``unknown`` and the
    reason in plain words. Only ``same`` says the token may be stored.

    ``rejected`` means the service refused the token itself. A call that failed
    some other way is ``unknown``, unless both calls still brought back a usage
    record: that record comes from the service, for the account the credential
    belongs to, so it says whose the token is even when the call got no further
    (an account at its usage limit, for one).
    """
    if token_refused(token_stream):
        return "rejected", f"the service refused the new token: {call_failure(token_stream)}"
    mine, theirs = fingerprint(token_stream), fingerprint(login_stream)
    if mine is None or theirs is None:
        return "unknown", _why_not_compared(token_stream, login_stream)
    if mine != theirs:
        return "different", ("the token reports different usage windows from the slot's "
                             "login, so the browser approved another account")
    return "same", "the token and the slot's login report the same usage windows"


def check_token(token: str, slot_dir: Path, claude: str, scratch: str,
                call: Callable[[str, dict, str], str] = live_call) -> tuple[str, str]:
    """Make the two live calls and judge them. The token travels in the
    environment of one child process and nowhere else."""
    empty = Path(scratch) / "no-login"
    empty.mkdir(exist_ok=True)
    as_token = clean_env(os.environ, CLAUDE_CODE_OAUTH_TOKEN=token,
                         CLAUDE_CONFIG_DIR=str(empty), ANTHROPIC_BASE_URL="")
    as_login = clean_env(os.environ, CLAUDE_CONFIG_DIR=str(slot_dir), ANTHROPIC_BASE_URL="")
    try:
        streams = call(claude, as_token, scratch), call(claude, as_login, scratch)
    except (OSError, subprocess.SubprocessError) as exc:
        # The token already exists by now. A call that could not be made is a
        # check that could not be made, never a reason to lose the run.
        return "unknown", f"the check could not be made ({type(exc).__name__})"
    return judge_token(*streams)


# --- storing, and remembering when ---------------------------------------------

def digest(token: str) -> str:
    """Twelve characters that identify a token without revealing it."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def store_token(label: str, token: str, bill: Path) -> tuple[bool, str]:
    """Hand the token to ``grip-relay-bill store`` on standard input, so there
    is one writer of the store and one check of what goes into it.

    Returns whether it was stored, and words to go with that: why it was
    refused, or, when it was stored, what the store did besides (kept the
    token the label had before, moved the relay to the new one), one line
    each and empty when there was nothing."""
    try:
        done = subprocess.run([sys.executable, str(bill), "store", label],
                              input=token + "\n", capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    if done.returncode != 0:
        return False, claude_token_store.scrubbed(done.stderr or done.stdout, 300, end=False)
    return True, "\n".join(claude_token_store.besides(done.stdout))


def _read_ledger(path: Path) -> dict:
    """The ledger as it stands. One that is missing or unreadable is empty: it
    only holds dates, and a damaged one must not stop a token being recorded."""
    try:
        ledger = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return ledger if isinstance(ledger, dict) else {}


def record_mint(store_dir: Path, label: str, entry: dict) -> Path:
    """Remember when a label was minted and for whom, beside the tokens.

    A token does not say when it expires, so the date has to be written down
    at the one moment it is known.
    """
    path = Path(store_dir) / LEDGER
    ledger = _read_ledger(path)
    ledger[label] = entry
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)
    return path


def mint_entry(token: str, account: str, cli_version: str, verdict: str,
               now: datetime | None = None) -> dict:
    """The ledger entry for one mint. Holds no part of the token."""
    minted = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    return {"account": account, "check": verdict, "cli": cli_version,
            "digest": digest(token), "minted_at": minted.isoformat(),
            "expires_about": (minted + timedelta(days=365)).date().isoformat()}


# --- the whole job ---------------------------------------------------------------

@dataclass
class MintJob:
    """Everything one mint needs to know. ``say`` is the only way anything
    reaches the operator's terminal."""
    slot_dir: Path
    label: str
    account: str
    claude: str
    cli: str
    bill: Path
    store_dir: Path
    say: Callable[[str], None]
    keyboard_fd: int | None = None
    allow_unverified: bool = False


def _may_keep(verdict: str, allow_unverified: bool) -> bool:
    return verdict == "same" or (verdict == "unknown" and allow_unverified)


def pick_reading(candidates: list[str],
                 check: Callable[[str], tuple[str, str]]) -> tuple[str, str, str, int]:
    """Check the readings in turn, the likeliest first, and stop at the first
    one the service does not refuse. Returns that reading, its verdict, the
    reason, and how many were tried.

    Only a refusal moves on to the next reading: it means this reading is not
    a working token. Any other verdict is about the account or about the check
    itself, and another reading would not change it. When every reading is
    refused, the likeliest one's refusal is the one reported.

    There has to be at least one reading. ``mint`` ends before it gets here
    when the screen gave none; asked with none anyway, this raises
    ``ValueError`` rather than failing on an index.
    """
    if not candidates:
        raise ValueError("there are no readings to check")
    tried = []
    for reading in candidates:
        tried.append((reading, *check(reading)))
        if tried[-1][1] != "rejected":
            return (*tried[-1], len(tried))
    return (*tried[0], len(tried))


def _how_many_tried(tried: int) -> str:
    """A note for the operator when the first reading was not the last tried."""
    return "" if tried == 1 else f" ({tried} readings of the screen were tried)"


def report(label: str, entry: dict, why: str, besides: str = "") -> str:
    """What the operator is told after a token is stored. No part of the token.
    ``besides`` is what the store did apart from storing, one line each."""
    return "\n".join([
        f"Stored as '{label}' for {entry['account']}. The token was never shown.",
        f"  check:   {why}",
        f"  minted:  {entry['minted_at'][:10]}, expires about {entry['expires_about']}",
        f"  digest:  {entry['digest']} (identifies the stored file without revealing it)",
        *(f"  also:    {line}" for line in besides.splitlines()),
    ])


def mint(job: MintJob, *, run=run_behind_pty, check=check_token, store=store_token) -> int:
    """Mint a token, check whose it is, store it. Returns the exit code:
    0 stored, 1 minted but not kept, 3 no token was captured.

    The order matters: nothing is written under the label until the new token
    has answered a live call as the slot's own account, so a wrong or damaged
    capture never replaces a token that works.

    The same live call is what settles which reading of the screen is the
    token, when the screen gave more than one.
    """
    screen = MintScreen()
    env = clean_env(os.environ, CLAUDE_CONFIG_DIR=str(job.slot_dir), ANTHROPIC_BASE_URL="")
    run([job.claude, "setup-token"], env, screen, job.say, job.keyboard_fd)
    candidates = screen.readings.candidates()
    if not candidates:
        job.say(f"No token was captured, so nothing was stored ({screen.summary()}).")
        return 3
    with tempfile.TemporaryDirectory(prefix="grip-mint-") as scratch:
        token, verdict, why, tried = pick_reading(
            candidates, lambda reading: check(reading, job.slot_dir, job.claude, scratch))
    why += _how_many_tried(tried)
    if not _may_keep(verdict, job.allow_unverified):
        job.say(f"Not stored: {why}.")
        return 1
    stored, detail = store(job.label, token, job.bill)
    if not stored:
        job.say(f"Not stored: the token store refused it ({detail}).")
        return 1
    entry = mint_entry(token, job.account, job.cli, verdict)
    record_mint(job.store_dir, job.label, entry)
    job.say(report(job.label, entry, why, detail))
    return 0
