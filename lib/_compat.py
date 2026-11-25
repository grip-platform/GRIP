"""
Cross-platform compatibility shims for GRIP.

Import from here instead of directly importing platform-specific modules.

Usage:
    from _compat import fcntl, grip_socket_path, grip_instance_path, run_text

A byte-identical copy lives in oss/idr-chain/idr_chain/lib/_compat.py, and
tests/test_oss_extraction_parity.py fails CI until both match: copy this file
there after every change.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# fcntl shim (POSIX file locking)
#
# Windows has no fcntl module, so on Windows this shim supplies flock() itself,
# on LockFileEx (GRIP#8752). It used to do nothing, on the theory that atomic
# tempfile+rename keeps every file whole. Rename keeps a rewritten file whole,
# but an append has no rename: two writers on one machine, such as two sessions
# at once, lost records from the signed decision chain.
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    import errno
    import functools

    #: The one byte every Windows flock() locks. It lies far past the end of any
    #: real file, so the lock binds other flock() callers only, which is what
    #: advisory means on POSIX, and no read or write of the data ever meets it.
    #: It stays below 2**63, inside every file system's signed 64-bit offsets.
    _LOCK_BYTE_OFFSET = (1 << 63) - 2
    _LOCKFILE_FAIL_IMMEDIATELY = 0x1
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2
    _ERROR_LOCK_VIOLATION = 33
    _ERROR_NOT_LOCKED = 158

    class _Overlapped(ctypes.Structure):
        """Win32 OVERLAPPED. For LockFileEx it only carries the lock's offset."""

        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", ctypes.c_uint32),
            ("OffsetHigh", ctypes.c_uint32),
            ("hEvent", ctypes.c_void_p),
        ]

    @functools.lru_cache(maxsize=None)
    def _kernel32() -> object:
        """kernel32 with LockFileEx and UnlockFileEx typed, bound on first use.

        A private binding, so these argtypes never clash with another module's
        ``ctypes.windll.kernel32``.
        """
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dword, overlapped = ctypes.c_uint32, ctypes.POINTER(_Overlapped)
        dll.LockFileEx.argtypes = [ctypes.c_void_p, dword, dword, dword, dword, overlapped]
        dll.UnlockFileEx.argtypes = [ctypes.c_void_p, dword, dword, dword, overlapped]
        dll.LockFileEx.restype = dll.UnlockFileEx.restype = ctypes.c_int
        return dll

    def _at_lock_byte() -> _Overlapped:
        """A fresh OVERLAPPED that points at the lock byte."""
        overlapped = _Overlapped()
        overlapped.Offset = _LOCK_BYTE_OFFSET & 0xFFFFFFFF
        overlapped.OffsetHigh = _LOCK_BYTE_OFFSET >> 32
        return overlapped

    def _os_handle(fd: object) -> int:
        """The Win32 handle behind a descriptor, or behind an object with fileno()."""
        # Imported here, not above: tests/test_compat.py runs this branch on
        # Linux too, where msvcrt does not exist.
        import msvcrt

        return msvcrt.get_osfhandle(fd if isinstance(fd, int) else fd.fileno())

    def _lock_byte(handle: int, flags: int) -> None:
        """LockFileEx on the lock byte, failing the way POSIX flock() fails."""
        if _kernel32().LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_at_lock_byte())):
            return
        code = ctypes.get_last_error()
        if code == _ERROR_LOCK_VIOLATION:
            raise BlockingIOError(errno.EAGAIN, "the file is locked through another descriptor")
        raise ctypes.WinError(code)

    def _unlock_byte(handle: int) -> None:
        """UnlockFileEx on the lock byte. Holding no lock there is not an error."""
        if _kernel32().UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_at_lock_byte())):
            return
        code = ctypes.get_last_error()
        if code != _ERROR_NOT_LOCKED:
            raise ctypes.WinError(code)

    class _FcntlShim:
        """``fcntl.flock`` for Windows, on LockFileEx.

        It keeps the POSIX flock() contract that every caller here was written
        against:

        * A lock binds every other descriptor, in this process or another. With
          ``LOCK_NB``, a lock held elsewhere raises ``BlockingIOError`` (errno
          ``EAGAIN``) instead of waiting.
        * A new lock replaces the one the descriptor holds. As flock(2) says of
          a conversion, the old lock goes first and the new one follows, so
          locking twice never waits on its own lock.
        * ``LOCK_UN`` with no lock held is not an error.
        * Closing the descriptor, or the end of the process, drops the lock.

        The constants carry the POSIX values. There is no ``lockf``: its record
        locks belong to the process, not to the descriptor, and LockFileEx
        cannot give that. A caller gets AttributeError, not a lock that behaves
        differently.
        """

        LOCK_SH = 1
        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8

        def flock(self, fd: object, operation: int) -> None:
            kind = operation & ~self.LOCK_NB
            if kind not in (self.LOCK_SH, self.LOCK_EX, self.LOCK_UN):
                raise OSError(errno.EINVAL, "flock() needs LOCK_SH, LOCK_EX or LOCK_UN")
            handle = _os_handle(fd)
            _unlock_byte(handle)
            if kind == self.LOCK_UN:
                return
            flags = _LOCKFILE_EXCLUSIVE_LOCK if kind == self.LOCK_EX else 0
            if operation & self.LOCK_NB:
                flags |= _LOCKFILE_FAIL_IMMEDIATELY
            _lock_byte(handle, flags)

    fcntl = _FcntlShim()
else:
    import fcntl  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Cross-platform temp path helpers for GRIP runtime files
#
# Always use these instead of hardcoding /tmp/ — on Windows the OS temp dir
# is C:\Users\<user>\AppData\Local\Temp (or TMPDIR if set in the shell).
# tempfile.gettempdir() honours TMPDIR/TEMP/TMP env vars cross-platform.
# ---------------------------------------------------------------------------

def grip_tmp_dir() -> str:
    """Return the OS temporary directory, honouring TMPDIR/TEMP/TMP."""
    return tempfile.gettempdir()


def grip_socket_path(session_id: str) -> str:
    """Return the Unix socket path for a GRIP session control server.

    Uses /tmp on macOS to avoid AF_UNIX 104-byte sun_path limit.
    tempfile.gettempdir() returns /var/folders/.../ (48 chars) on macOS,
    which combined with a UUID session ID hits 103 chars — the exact limit.
    """
    if sys.platform == "darwin":
        return f"/tmp/grip-session-{session_id}.sock"
    return os.path.join(grip_tmp_dir(), f"grip-session-{session_id}.sock")


def grip_instance_path(pid: int | str) -> str:
    """Return the instance UUID file path for a given Claude Code PID."""
    return os.path.join(grip_tmp_dir(), f"grip-instance-{pid}.id")


def grip_sessions_dir() -> str:
    """Return the GRIP session breadcrumb directory."""
    return os.path.join(grip_tmp_dir(), "grip-sessions")


def grip_tmp_file(filename: str) -> str:
    """Return path to any GRIP-managed temp file by name."""
    return os.path.join(grip_tmp_dir(), filename)


def grip_injection_path(session_id: str) -> str:
    """Return the steering injection file path for a session."""
    return grip_tmp_file(f"grip-injected-{session_id}.txt")


def grip_server_pid_path(session_id: str) -> str:
    """Return the control server PID file path for a session."""
    return grip_tmp_file(f"grip-session-server-{session_id}.pid")


def grip_session_map_path(session_id: str) -> str:
    """Return the session ID remapping file path."""
    return grip_tmp_file(f"grip-session-map-{session_id}")


# ---------------------------------------------------------------------------
# Process liveness on Windows (GRIP#8817)
#
# os.kill(pid, 0) is no probe there: CPython sends signal 0 as a console
# CTRL_C_EVENT. Measured 2026-10-29, it answered for the caller's own children
# but raised OSError for every live process outside its tree that was tried, and
# it still answered for a child that had exited while its handle was open. A pid
# of 0 would reach every process on the caller's console.
# ---------------------------------------------------------------------------

def windows_pid_alive(pid: int) -> bool:
    """True while a Windows process with this pid exists.

    0 is the System Idle Process and 1 is never a Windows pid, so neither can name
    a session. Without psutil there is no safe probe, and the answer is True: a
    record kept too long still expires by its age, but a live session's record,
    once deleted, does not come back.
    """
    if pid <= 1:
        return False
    try:
        import psutil
    except ImportError:
        return True
    return psutil.pid_exists(pid)


def process_created(pid: int) -> float | None:
    """When the process with this pid was created, in epoch seconds, or None.

    None means the time cannot be read: psutil is missing, no process has the pid,
    or access is refused. A pid alone can name a later, unrelated process once the
    recorded one has gone, so a caller that must know it still has the process it
    recorded compares this time with the time of its record (GRIP#8883).

    A pid that is not a positive int is None too (GRIP#9619): psutil reads None as
    the calling process and 0 as the kernel's, and raises for "7" or -1.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


# ---------------------------------------------------------------------------
# Console encoding shim (the Windows ANSI code page)
#
# Windows Python defaults stdout/stderr to the ANSI code page -- cp1252 on a
# Western locale -- not UTF-8. GRIP's renderers emit box-drawing and status
# glyphs, and 447 of the 1512 files under bin/ lib/ hooks/ scripts/ contain at
# least one character cp1252 cannot encode. Printing any of them raises
# UnicodeEncodeError and kills the command outright:
#
#     UnicodeEncodeError: 'charmap' codec can't encode character '✔'
#
# That is not cosmetic. It took out `grip-doctor --check` on A>>'s machine on
# 2026-08-11 -- the health check an operator runs to verify everything else.
#
# The fix belongs on the STREAM, never on the glyphs. lib/grip_activation_render
# commits itself in its own docstring to "only box-drawing + status glyphs" so
# that chunk-streaming can never split an ANSI escape mid-byte; swapping those
# for ASCII would trade a Windows crash for a regression everywhere else.
# ---------------------------------------------------------------------------

def grip_python(claude_dir: object = None) -> str:
    """Path to an interpreter that will actually run GRIP's code.

    Two things make the obvious one-liner wrong on Windows.

    The venv layout differs: POSIX puts the interpreter at ``venv/bin/python3``,
    Windows at ``venv/Scripts/python.exe`` with no ``python*`` level at all. A
    caller that probes only the POSIX path finds nothing on Windows and falls
    through to its fallback.

    And that fallback is the trap. Windows ships an App Execution Alias at
    ``AppData/Local/Microsoft/WindowsApps/python3.exe`` which exists, is
    executable, and satisfies ``shutil.which`` -- then prints "Python was not
    found; run without arguments to install from the Microsoft Store" and exits
    49. So falling back to the bare string "python3" does not fail loudly, it
    fails as a *Python* error attributed to whatever GRIP was trying to do.
    That is how /save came to report nine of its eleven subsystems as failed
    with an installation message, on a machine with a working Python.

    Hence the final rung is ``sys.executable``: the interpreter currently
    running is, by construction, one that runs. It is never a stub and never
    absent, on any platform.
    """
    from pathlib import Path

    root = Path(claude_dir) if claude_dir is not None else Path(
        os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude")
    )
    if sys.platform == "win32":
        candidates = [
            root / "venv" / "Scripts" / "python.exe",
            root / "venv" / "Scripts" / "python3.exe",
        ]
    else:
        candidates = [root / "venv" / "bin" / "python3", root / "venv" / "bin" / "python"]

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def ensure_utf8_stdio() -> bool:
    """Force this process's stdout and stderr to UTF-8. Returns True if changed.

    Call once, early, from an entry point that prints non-ASCII. Idempotent, so
    nesting is safe, and total -- it never raises, because a diagnostic that
    dies while setting up its own output is worse than the encoding it was
    fixing.

    Returns False when nothing needed doing: already UTF-8, or the stream is not
    reconfigurable (pytest capture and some CI wrappers replace sys.stdout with
    an object that has no .reconfigure). False means "no change", never "failed"
    -- a non-reconfigurable stream is usually a StringIO that has no encoding
    problem in the first place.

    Covered by tests/test_windows_console_encoding.py, which reproduces the real
    crash under a forced cp1252 and therefore runs on every platform, not just
    Windows -- the bug is a property of the code page, not the OS.
    """
    changed = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:  # pythonw.exe and some daemonised contexts
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError, AttributeError):
            # Detached, already closed, or a wrapper that lied about having
            # .reconfigure. Leave it alone rather than take the process down.
            continue
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Subprocess READ encoding (the mirror image of ensure_utf8_stdio)
#
# ensure_utf8_stdio above fixes what GRIP WRITES. This fixes what GRIP READS
# back when one GRIP process captures another.
#
# subprocess.run(..., text=True) with no explicit encoding= decodes the child's
# bytes with locale.getpreferredencoding(False) -- cp1252 on Windows. The child
# now emits correct UTF-8 (that is what the write-side fix guarantees), so the
# three bytes E2 9C 94 of a U+2714 check mark come back as the three characters
# 'a-circumflex, oe, quote'. Nothing raises. returncode stays 0. The parent just
# quietly believes the wrong string.
#
# That silence is what makes this worse than the write-side crash it mirrors. A
# UnicodeEncodeError names its own cause in the traceback; mojibake names
# nothing, and the first thing it looks like is a bug in the child. GRIP's own
# Windows CI hit both faces of it:
#
#   * run 31485272561 (2026-08-11) failed on `assert '#' in '\xe2-^...'`
#     -- E2 96 88 (U+2588 FULL BLOCK) read back through cp1252.
#   * the windows-smoke runner then reported 11 failures whose returncode was 0
#     in every single case. The shim under test was working perfectly; the test
#     harness was misreading it. Fixed in #4658, which is where the returncode-0
#     tell was first written down.
#
# Pin the decoder and the whole class disappears.
# ---------------------------------------------------------------------------

def run_text(
    *popenargs: object,
    encoding: str = "utf-8",
    errors: str = "replace",
    **kwargs: object,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` in text mode with the decoder pinned, not inferred.

    Use this instead of ``subprocess.run(..., text=True)`` wherever the captured
    output can contain non-ASCII -- GRIP renderers, LLM prose, memory text,
    anything with box-drawing or status glyphs. For a child whose output is
    genuinely pure ASCII (``git rev-parse``, a PID, a JSON blob of identifiers)
    plain ``subprocess.run`` is already correct and this adds nothing.

    ``errors="replace"`` is deliberate, and it is the one choice here worth
    arguing with. It is right for output that gets DISPLAYED: an undecodable
    byte becomes one U+FFFD instead of a ``UnicodeDecodeError`` that kills the
    reader, which is the same trade ``ensure_utf8_stdio`` makes on the write
    side -- a diagnostic that dies while reading its own input is worse than one
    that shows a replacement character. It is arguably WRONG for output that
    gets PARSED, where a silent U+FFFD can smuggle corruption into a value
    nobody checks. Those callers pass ``errors="strict"`` and get the loud
    failure they want.

    Note that this cannot be a regression anywhere: macOS and Linux already
    default to UTF-8 *strict*, so relaxing to ``replace`` only ever removes a
    crash. Windows is where the ``encoding`` argument does the real work.

    Text mode is not negotiable -- the function exists to guarantee it -- so
    passing ``text=`` or ``universal_newlines=`` is a caller bug and raises
    rather than being quietly dropped. A silently-ignored ``text=False`` would
    hand back ``str`` to code expecting ``bytes``, which is exactly the kind of
    quiet wrongness this whole module is here to end.
    """
    for banned in ("text", "universal_newlines"):
        if banned in kwargs:
            raise TypeError(
                f"run_text() always runs in text mode; drop the {banned}= "
                "argument. For bytes, call subprocess.run directly."
            )
    return subprocess.run(  # noqa: S603 - caller supplies argv, as with run()
        *popenargs, text=True, encoding=encoding, errors=errors, **kwargs
    )
