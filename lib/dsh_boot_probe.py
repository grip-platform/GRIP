#!/usr/bin/env python3
"""Boot a DSH profile briefly and report whether its plugin tree loads.

This is deliberately a real boot probe rather than an import scanner. DSH can
load plugins dynamically or from side-loaded paths, and only Cordis exercises
the same graph and package versions as a real session.
"""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Mapping, Sequence


class ProbeStatus(IntEnum):
    """Public exit codes for the three possible probe outcomes."""

    PASS = 0
    FAIL = 1
    INCONCLUSIVE = 2


@dataclass(frozen=True)
class Classification:
    """The classification and any package names recoverable from the output."""

    status: ProbeStatus
    missing_package: str | None = None
    failing_plugin: str | None = None


_MISSING_PACKAGE_PATTERNS = (
    re.compile(r"Cannot find package ['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]", re.IGNORECASE),
)
_FAILING_PLUGIN_PATTERN = re.compile(
    r"failed to import loader entry\s+\S+\s+\((@?[^()\s]+)\)", re.IGNORECASE
)
_BOOT_BANNER_PATTERN = re.compile(r"^dsh\s+\S+:\s+https?://", re.MULTILINE)
_BOOT_FAILURE_MARKERS = (
    "plugin tree failed",
    "err_module_not_found",
    "eaddrinuse",
    "address already in use",
    "error: listen",
)


def classify_output(output: str) -> Classification:
    """Classify captured combined output without treating silence as success."""
    lowered_output = output.lower()
    missing_package = next(
        (match.group(1) for pattern in _MISSING_PACKAGE_PATTERNS if (match := pattern.search(output))),
        None,
    )
    failed = missing_package is not None or any(
        marker in lowered_output for marker in _BOOT_FAILURE_MARKERS
    )
    plugin_match = _FAILING_PLUGIN_PATTERN.search(output)
    failing_plugin = plugin_match.group(1) if plugin_match else None
    if failed:
        return Classification(ProbeStatus.FAIL, missing_package, failing_plugin)
    if _BOOT_BANNER_PATTERN.search(output):
        return Classification(ProbeStatus.PASS)
    return Classification(ProbeStatus.INCONCLUSIVE)


def dsh_home(env: Mapping[str, str] | None = None) -> Path:
    """Resolve DSH_HOME in the same order as DSH's HAL route installer."""
    values = os.environ if env is None else env
    configured = values.get("DSH_HOME", "").strip()
    return Path(configured) if configured else Path.home() / ".dsh"


def dependency_version(
    root: Path, profile: str, failing_plugin: str | None, missing_package: str | None
) -> str | None:
    """Read the requested version from the failing plugin's package manifest."""
    if not failing_plugin or not missing_package:
        return None
    manifest = root / "profiles" / profile / "node_modules" / failing_plugin / "package.json"
    try:
        import json

        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    for section_name in ("peerDependencies", "dependencies"):
        section = document.get(section_name)
        if isinstance(section, dict):
            version = section.get(missing_package)
            if isinstance(version, str) and version.strip():
                return version.strip()
    return None


def render_remedy(root: Path, profile: str, missing_package: str, version: str | None) -> str:
    """Render a command that remains usable when no version can be recovered."""
    package_spec = f"{missing_package}@{version}" if version else missing_package
    return f"pnpm --dir {root / 'profiles' / profile} add {package_spec}"


def run_probe(
    profile: str,
    timeout: float = 25.0,
    command: Sequence[str] | None = None,
    settle_window: float = 2.0,
) -> tuple[Classification, str]:
    """Run a profile through its banner settle window, then kill its process group."""
    env = dict(os.environ)
    env.setdefault("HAL_GATEWAY_KEY", "grip-dsh-placeholder")
    try:
        process = subprocess.Popen(
            list(command) if command is not None else ["dsh", "--profile", profile],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        message = f"dsh was not found on PATH; cannot verify profile {profile}"
        return Classification(ProbeStatus.INCONCLUSIVE), message
    process_group = process.pid
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    settle_deadline: float | None = None
    try:
        while True:
            now = time.monotonic()
            active_deadline = min(deadline, settle_deadline or deadline)
            if now >= active_deadline:
                break
            wait = min(0.2, max(0.0, active_deadline - now))
            events = selector.select(wait)
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    chunks.append(chunk)
                else:
                    selector.unregister(key.fileobj)
            output = b"".join(chunks).decode("utf-8", errors="replace")
            classification = classify_output(output)
            if classification.status is ProbeStatus.FAIL:
                return classification, output
            if classification.status is ProbeStatus.PASS and settle_deadline is None:
                settle_deadline = min(deadline, time.monotonic() + settle_window)
            # Once stdout reaches EOF, no later diagnostic can alter the result.
            if not selector.get_map():
                break
            if process.poll() is not None:
                break
        output = b"".join(chunks).decode("utf-8", errors="replace")
        return classify_output(output), output
    finally:
        selector.close()
        # Killing the whole new session is load-bearing: DSH can leave a web
        # child alive after its parent changes state, which would retain the
        # profile's port and make a verification command operationally unsafe.
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        teardown_deadline = time.monotonic() + 2.0
        while True:
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= teardown_deadline:
                print(
                    f"[grip-dsh-verify] warning: process group {process_group} "
                    "is still alive after teardown",
                    file=sys.stderr,
                )
                break
            time.sleep(0.05)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``grip-dsh-verify [<profile>]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("usage: grip-dsh-verify [<profile>]", file=sys.stderr)
        return int(ProbeStatus.INCONCLUSIVE)
    profile = args[0] if args else "web"
    try:
        timeout = float(os.environ.get("GRIP_DSH_VERIFY_TIMEOUT", "25"))
        classification, output = run_probe(profile, timeout)
    except (OSError, ValueError) as exc:
        print(f"[grip-dsh-verify] INCONCLUSIVE: could not run dsh: {exc}", file=sys.stderr)
        return int(ProbeStatus.INCONCLUSIVE)

    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if classification.status is ProbeStatus.PASS:
        print(f"[grip-dsh-verify] PASS: profile {profile!r} reached its web boot banner.")
    elif classification.status is ProbeStatus.FAIL:
        package = classification.missing_package or "unknown package"
        print(f"[grip-dsh-verify] FAIL: profile {profile!r} could not load {package}.")
        if classification.missing_package:
            root = dsh_home()
            version = dependency_version(
                root, profile, classification.failing_plugin, classification.missing_package
            )
            print(render_remedy(root, profile, classification.missing_package, version))
    else:
        print("[grip-dsh-verify] INCONCLUSIVE: no decisive boot signal (see above).")
    return int(classification.status)


if __name__ == "__main__":
    raise SystemExit(main())
