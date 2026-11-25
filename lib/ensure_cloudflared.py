"""ensure_cloudflared — make sure `cloudflared` is on PATH (install it on macOS).

`grip-cos up` needs `cloudflared` to open a public tunnel in front of an
internal GRIP endpoint. A fresh machine (an external user's) does not have it, and the only
symptom is a runtime error at the worst moment. This shim is the single source
of truth for "is cloudflared available, and if not, get it" — called by both the
installer (scripts/install.sh) and the one-command setup (bin/grip-cos-setup).

Auto-install is attempted ONLY where it is safe and unattended: macOS + Homebrew
(`brew install cloudflared`). On Linux/Windows it returns a clear, platform-correct
hint rather than piping a remote script into a shell. `which` / `runner` /
`platform` are injectable so the logic is unit-tested with zero network.
"""
from __future__ import annotations

import argparse
import platform as _platform
import shutil
import subprocess
import sys
from typing import Callable, Optional


def install_hint(platform: str, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """The exact command a user should run to get cloudflared on this platform."""
    if platform == "Darwin":
        if which("brew"):
            return "brew install cloudflared"
        return "Install Homebrew (https://brew.sh), then: brew install cloudflared"
    if platform == "Linux":
        return ("Install cloudflared — see https://pkg.cloudflare.com/ (Debian/Ubuntu "
                "apt repo), or `brew install cloudflared` with Linuxbrew")
    if platform == "Windows":
        return "winget install --id Cloudflare.cloudflared   (or: scoop install cloudflared)"
    return "Install cloudflared: https://developers.cloudflare.com/cloudflared/"


def ensure_cloudflared(
    *,
    install: bool = True,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    platform: Optional[str] = None,
) -> dict:
    """Ensure cloudflared is available. Returns a structured result; never raises.

    Keys: ``present`` (on PATH now), ``installed`` (we installed it this call),
    ``ok`` (usable after this call), ``path``, ``action``, ``hint``.
    """
    platform = platform or _platform.system()
    runner = runner or subprocess.run

    path = which("cloudflared")
    if path:
        return _result(True, False, True, path, "already-present", "")

    hint = install_hint(platform, which)
    if not install:
        return _result(False, False, False, None, "missing", hint)

    # Unattended auto-install only on macOS + Homebrew (safe, no remote pipe-to-shell).
    if platform == "Darwin" and which("brew"):
        proc = runner(["brew", "install", "cloudflared"], capture_output=True, text=True)
        new_path = which("cloudflared")
        if getattr(proc, "returncode", 1) == 0 and new_path:
            return _result(True, True, True, new_path, "installed-via-brew", "")
        return _result(False, False, False, None, "brew-install-failed", hint)

    return _result(False, False, False, None, "no-auto-installer", hint)


def _result(present: bool, installed: bool, ok: bool, path: Optional[str],
            action: str, hint: str) -> dict:
    return {"present": present, "installed": installed, "ok": ok,
            "path": path, "action": action, "hint": hint}


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="ensure_cloudflared",
        description="Ensure cloudflared is installed (auto-installs on macOS+brew).")
    ap.add_argument("--check", action="store_true",
                    help="report only; do not install")
    args = ap.parse_args(argv)

    res = ensure_cloudflared(install=not args.check)
    if res["ok"]:
        verb = "installed" if res["installed"] else "already present"
        print(f"cloudflared: {verb} ({res['path']})")
        return 0
    print(f"cloudflared: not available — {res['action']}", file=sys.stderr)
    print(f"  -> {res['hint']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
