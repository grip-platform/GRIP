"""GRIP-side thin adapter onto HAL's osa() primitive.

This module is the ONLY bridge between GRIP's Python and HAL's osascript
control layer. It shells out to ``hal osa run <name>`` via subprocess so
the two repos stay decoupled — GRIP does not import HAL Python modules.

Scope:

*   β-4a (PR #2323): narrow facade for ``display notification``.
*   β-4b (this change): adds ``close_chromium_tabs_by_url``,
    ``findmy_process_probe``, ``crash_dialog_detect``, and
    ``crash_dialog_dismiss`` so the remaining 5 GRIP call sites can
    delegate to the registry-hardened HAL primitive. Every function
    degrades gracefully — the caller retains its existing
    direct-osascript path for the HAL-unavailable case.

Design choices:

*   **Option A (subprocess)** over Option B (sys.path injection). HAL and
    GRIP stay independently versioned; neither imports the other's code.
    The subprocess latency (~50 ms) is negligible versus osascript
    execution (~80 ms).
*   **Graceful degradation**. If ``hal`` binary is missing, if
    ``HAL_OSA_CAPABILITY_SECRET`` is unset, if the registry rejects the
    params, or if the subprocess fails for any reason — return
    :data:`False` / :data:`None` depending on the function. Caller MUST
    have a direct-osascript fallback path.
*   **No logging, no telemetry here**. The HAL audit log (see
    ``lib/hal/osa/audit.py``) already records every dispatch. Adding a
    second log in GRIP would duplicate that observability.
*   **Tier-2 auth via subprocess env**. ``hal osa run`` reads
    ``HAL_OSA_CAPABILITY_SECRET`` from the child process env (inherited
    from this process). Tier-2 dispatches require either an operator
    consent flag (``HAL_OSA_TOOL_CHAIN_AUTOMINT=1``) or the
    ``GRIP_OSA_UNSAFE=1`` escape hatch on the HAL side. When neither is
    set, the subprocess exits non-zero and we return False/None so the
    caller falls back.
*   **Single ``_run`` primitive**. All four adapter functions share one
    subprocess dispatcher so the error handling, env copy, timeout
    semantics, and argv construction live in one place — not duplicated
    per function.

Public API::

    from lib.hal_osa import (
        notify, close_chromium_tabs_by_url, findmy_process_probe,
        crash_dialog_detect, crash_dialog_dismiss,
    )

    ok = notify("GRIP", "Build done")
    if not ok:
        # fall back to existing direct-osascript path
        ...
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "notify",
    "close_chromium_tabs_by_url",
    "findmy_process_probe",
    "crash_dialog_detect",
    "crash_dialog_dismiss",
    "hal_osa_available",
]

# Canonical HAL location. Honours HAL_HOME if the operator has customised it;
# otherwise falls back to the conventional ~/.hal layout.
_HAL_HOME = Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))
_OSA_BIN = _HAL_HOME / "bin" / "osa"


def hal_osa_available() -> bool:
    """Return True iff the hal osa CLI is reachable from this process.

    Checks the :data:`HAL_HOME`-resolved path first, falling back to
    ``shutil.which('osa')`` so operators with a custom install still work.
    Does NOT probe for :data:`HAL_OSA_CAPABILITY_SECRET` — the caller
    learns about auth failures via :func:`notify` returning ``False``.
    """
    if _OSA_BIN.is_file() and os.access(_OSA_BIN, os.X_OK):
        return True
    return shutil.which("osa") is not None


def _resolve_bin() -> str:
    """Return the absolute path to the hal osa CLI, preferring HAL_HOME."""
    if _OSA_BIN.is_file():
        return str(_OSA_BIN)
    which = shutil.which("osa")
    if which:
        return which
    return str(_OSA_BIN)  # Best-effort; caller handles the subprocess error


def _run(
    name: str,
    params: dict[str, str],
    timeout_s: float,
) -> tuple[bool, str]:
    """Invoke ``hal osa run <name>`` with ``--param k=v`` pairs.

    Returns ``(ok, stdout)``. ``ok`` is True iff the subprocess exited zero;
    ``stdout`` is the captured stdout trimmed of trailing whitespace.
    Every failure mode (binary missing, timeout, non-zero exit, OSError)
    collapses to ``(False, "")`` — callers never see an exception.
    """
    if not hal_osa_available():
        return False, ""
    argv: list[str] = [_resolve_bin(), "run", name]
    for key, value in params.items():
        argv.extend(["--param", f"{key}={value}"])
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=os.environ.copy(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""
    if result.returncode != 0:
        return False, ""
    return True, (result.stdout or "").strip()


def notify(
    title: str,
    body: str,
    *,
    subtitle: str | None = None,
    sound: str | None = None,
    timeout_s: float = 10.0,
) -> bool:
    """Dispatch a ``display notification`` via HAL's osa registry.

    Returns ``True`` if the notification was delivered through HAL. Returns
    ``False`` (never raises) in every degraded case so the caller can fall
    back to its existing direct-osascript path without a try/except:

    *   HAL binary not on this host
    *   HAL_OSA_CAPABILITY_SECRET unset (tier-2 auth rejection)
    *   ``sound`` requested — not yet supported by the registry
    *   Registry validation rejects a param (e.g. non-printable chars, too long)
    *   Subprocess failure for any other reason
    """
    if sound is not None:
        return False
    if subtitle is not None:
        ok, _ = _run(
            "notification_with_subtitle",
            {"title": title, "body": body, "subtitle": subtitle},
            timeout_s,
        )
        return ok
    ok, _ = _run("notification", {"title": title, "body": body}, timeout_s)
    return ok


def close_chromium_tabs_by_url(
    browser: str,
    url_pattern: str,
    *,
    timeout_s: float = 15.0,
) -> int | None:
    """Close tabs whose URL contains ``url_pattern`` in a Chromium browser.

    Returns the number closed. Returns :data:`None` on any degraded path
    (HAL unavailable, registry rejects the browser/url_pattern, subprocess
    failure). A :data:`None` return means the caller should fall back to
    its native implementation; a :data:`0` return means HAL answered
    successfully and no tabs matched.

    Args:
        browser: Exact application name. The HAL registry whitelists:
            Google Chrome, Brave Browser, Arc, Microsoft Edge, Chromium,
            Vivaldi, Opera. Anything else returns None.
        url_pattern: Substring to match inside each tab's URL. Must be
            URL-safe — quotes, backslashes, and newlines are rejected at
            the HAL layer for injection safety.
        timeout_s: Subprocess upper bound (osascript may itself be slow
            when many tabs are open).
    """
    ok, stdout = _run(
        "tab_close_chromium_by_url",
        {"browser": browser, "url_pattern": url_pattern},
        timeout_s,
    )
    if not ok:
        return None
    try:
        return int(stdout)
    except ValueError:
        return None


def findmy_process_probe(*, timeout_s: float = 5.0) -> bool:
    """Return True iff the Find My app process is running AND reachable
    via Accessibility. This matches ``UIAutomationReader._probe`` semantics
    in GRIP's findmy_reader: a True verdict means osascript could send
    AppleEvents to Find My in this session. False covers every degraded
    path (HAL down, permission missing, process absent).

    Note: a False return does not distinguish between "Find My not running"
    and "Accessibility permission denied"; callers needing that split
    should keep using the direct-osascript probe which returns stderr.
    """
    ok, _ = _run("findmy_process_probe", {}, timeout_s)
    return ok


def crash_dialog_detect(*, timeout_s: float = 5.0) -> str | None:
    """Detect an active macOS crash-reporter dialog.

    Returns ``"Problem Reporter"``, ``"UserNotificationCenter"``, or
    :data:`None`. None covers both the "no dialog present" case (HAL
    answered "none") and the "HAL unavailable" case — the caller should
    treat both as "nothing to dismiss right now".
    """
    ok, stdout = _run("crash_dialog_detect", {}, timeout_s)
    if not ok:
        return None
    if stdout == "none" or not stdout:
        return None
    return stdout


def crash_dialog_dismiss(
    process_name: str,
    *,
    timeout_s: float = 5.0,
) -> bool:
    """Dismiss a crash-reporter dialog via HAL.

    Returns True on successful dispatch, False otherwise. The HAL registry
    whitelists ``process_name`` to exactly two values
    (``Problem Reporter`` | ``UserNotificationCenter``); anything else
    returns False at the validation layer.
    """
    ok, _ = _run(
        "crash_dialog_dismiss",
        {"process_name": process_name},
        timeout_s,
    )
    return ok
