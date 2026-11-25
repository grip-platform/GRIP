#!/usr/bin/env python3
"""remind_fire — the delivery half of `bin/grip-remind` (durable reminders).

The OS scheduler invokes this at the scheduled time with one argument: the
payload JSON written by `bin/grip-remind`. It delivers the reminder to
surfaces that reach the operator WITHOUT a live Claude session, appends an
audit row, then removes its own scheduled job and payload (a one-shot must
not accrete: the closed-or-expired lifecycle rule).

Local surface per platform:
  macOS   — a Notification Centre banner via osascript.
  Windows — a desktop dialog via PowerShell (WScript.Shell popup).
Both are best-effort and both mean "the alert was raised", never "the
operator saw it"; the audit row records which surfaces actually accepted
the message. WhatsApp is attempted on both platforms and its true result
recorded — it is not promised on Windows, where the transport is often
simply not configured.

Cleanup dispatches on the payload's `backend` field (`launchd` /
`schtasks`), defaulting to launchd so reminders armed before the Windows
backend existed still clean up correctly.

Fail-soft per surface: a failed WhatsApp send still raises the local
alert and still logs; nothing here ever raises out — including on a
corrupt or truncated payload (bare subscripts would break that promise, so
every payload field access tolerates absence).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
LOG = CLAUDE_DIR / "logs" / "reminders.jsonl"


def _send_whatsapp(recipient: str, text: str) -> dict:
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.wa_delivery_verify import post_verified as post  # history-verified sender
        return post(recipient=recipient, text=text)
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        return {"ok": False, "error": str(exc)}


def _notify_macos(title: str, text: str) -> bool:
    # AppleScript string escaping: backslash first, then double quotes.
    # The message is the operator's own payload (not untrusted input), but
    # a stray \ or " must never break the notification either.
    def _esc(value: str, limit: int) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')[:limit]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{_esc(text, 180)}" '
             f'with title "{_esc(title, 60)}" sound name "Glass"'],
            check=True, capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _notify_windows(title: str, text: str) -> bool:
    """Raise a desktop dialog via PowerShell. True == the dialog was shown.

    WScript.Shell.Popup rather than a WinForms MessageBox because it takes
    a timeout: the second argument auto-dismisses the dialog after 600
    seconds, so an unattended machine never leaves a modal window pinned
    open and never leaves this process blocked past its task time limit.
    The call returns -1 on that timeout, which is a shown dialog, not a
    failure — so the return code is not consulted, only whether PowerShell
    itself ran.

    PowerShell single-quoted strings escape a quote by doubling it; that is
    the only escape needed inside them, so no backslash handling here.
    """
    def _esc(value: str, limit: int) -> str:
        return value.replace("'", "''")[:limit]
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(New-Object -ComObject WScript.Shell).Popup("
             f"'{_esc(text, 500)}', 600, '{_esc(title, 60)}', 64) | Out-Null"],
            check=True, capture_output=True, timeout=630)
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_windows() -> bool:
    """Windows signal read from sys.platform, never `os.name == "nt"`.

    pathlib picks WindowsPath vs PosixPath off `os.name`, so a test that
    fakes Windows by setting it breaks every unrelated Path in the process
    (see the note in bin/grip-remind::_is_windows). sys.platform is inert.
    """
    return sys.platform in ("win32", "cygwin")


def _notify_local(title: str, text: str) -> bool:
    """Raise the local alert on whichever desktop this is.

    An unknown platform returns False rather than pretending: the audit row
    then honestly records that no local surface accepted the reminder,
    which is the whole point of logging the surfaces separately.
    """
    if sys.platform == "darwin":
        return _notify_macos(title, text)
    if _is_windows():
        return _notify_windows(title, text)
    return False


def _deregister_launchd(label: str) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                   capture_output=True, timeout=10, check=False)


def _deregister_schtasks(label: str) -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", label, "/F"],
                   capture_output=True, timeout=10, check=False)


_DEREGISTER = {"launchd": _deregister_launchd, "schtasks": _deregister_schtasks}


def _cleanup(label: str, job_file: str, payload_path: str,
             backend: str = "launchd") -> None:
    # Files FIRST: `bootout` of our own job SIGTERMs this process, so code
    # after it never runs (verified live 2026-07-15: the smoke fire
    # delivered but left plist+payload behind when bootout came first).
    # Self-termination is the last act. Empty label/job file (malformed
    # payload) skips that half rather than crashing — cleanup is best
    # effort by contract.
    #
    # `backend` defaults to launchd so a payload armed before the Windows
    # backend existed — which has no backend key — still deregisters.
    for path in (job_file, payload_path):
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    deregister = _DEREGISTER.get(backend)
    if label and deregister:
        deregister(label)


def _uid() -> int:
    return os.getuid()


def _audit(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    payload_path = sys.argv[1]
    try:
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # An unreadable payload must not raise out either: record it, leave
        # the files for the operator (grip-remind --list surfaces corrupt
        # payloads with a removal hint), exit clean.
        _audit({"error": f"unreadable payload {payload_path}: {exc}"})
        return 0
    message = payload.get("message", "")
    wa_text = f"GRIP here — reminder you asked for:\n\n{message}"

    wa = _send_whatsapp(payload.get("recipient", ""), wa_text) \
        if payload.get("recipient") else {"ok": False, "error": "no recipient"}
    local = _notify_local("GRIP reminder", message)

    backend = payload.get("backend", "launchd")
    _audit({
        "id": payload.get("id"),
        "fired_for": payload.get("fire_at"),
        "backend": backend,
        "whatsapp": wa,
        "local_notification": local,
    })

    # The job file is a plist under launchd and a task XML under schtasks;
    # take whichever the arming backend recorded.
    job_file = payload.get("plist") or payload.get("task_xml") or ""
    _cleanup(payload.get("label", ""), job_file, payload_path, backend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
