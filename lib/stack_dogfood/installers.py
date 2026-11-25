"""Install strategies for wiring a product into a harness.

Three generic, complete strategies the product adapters parameterise:

* :func:`cli_target`   — a harness with an ``mcp add`` style CLI (codex, gemini,
  opencode, hermes, amp, grok, qwen). Probe by listing; install by adding.
* :func:`json_target`  — a harness configured by a JSON file (cursor,
  claude-desktop). Probe by key presence; install by a backed-up, atomic,
  mode-preserving write.
* :func:`which_target` — a binary that must be on PATH (the happi runtime).
  Probe only; its install is a documented external step, so no auto-install.

A :class:`Target` carries the probe and the optional install for one harness. A
``None`` install means the harness is probe-only, which is honest: the framework
reports MISSING and names the step, rather than pretending to install.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .model import Status
from .run import TIMEOUT_MISSING, Result, run


@dataclass(frozen=True)
class Target:
    """One harness: how to probe it, and (optionally) how to install into it."""

    harness: str
    probe: Callable[[], "tuple[Status, str]"]
    install: Optional[Callable[[], Result]] = None


def _read_listing(text, harness, token, disabled_token) -> "tuple[Status, str]":
    """A row that names ``token`` counts as registered, unless every such row
    also carries ``disabled_token``. hermes saves a server whose connection
    failed as disabled and still lists its name, so the name alone is no proof."""
    rows = [line.lower() for line in text.splitlines() if token.lower() in line.lower()]
    if not rows:
        return Status.MISSING, f"{token} absent from {harness}"
    if disabled_token and all(disabled_token.lower() in row for row in rows):
        return Status.MISSING, f"{token} present but disabled in {harness}"
    return Status.REGISTERED, f"{token} present in {harness}"


def _absent_but_configured(listing: str, token: str, config_path: str, config_keys) -> bool:
    """True when the listing does not name ``token`` but the harness's own JSON
    config holds one of ``config_keys`` (dotted paths such as "mcp.grasp"). The
    keys match exactly, so a path or another server that merely contains the
    word does not count, and a real missing install still reads MISSING. On
    2026-11-25 three runs of `opencode mcp list` printed 26 bytes (the length of
    "No MCP servers configured") while its config held grasp, and five runs a
    minute later listed grasp (GRIP#9514)."""
    if token.lower() in listing.lower():
        return False
    try:
        data = _read_json(Path(config_path).expanduser())
    except (RuntimeError, ValueError):
        return False
    return data is not None and any(_has(data, key) for key in config_keys)


def cli_target(harness, *, list_argv, present_token, add_argv, timeout=20.0,
               add_input="", disabled_token="", config_path="", config_keys=()) -> Target:
    """A harness whose MCP servers are listed and added by a CLI.

    Both the listing and the add run from the home folder. The launcher starts
    in the install root, and a CLI whose default scope is the current project
    (gemini) would otherwise register a server for that one folder and then
    report it as present. ``add_input`` answers a prompt that the add asks
    after it connects (hermes), because empty input cancels it with exit 0.
    ``disabled_token`` marks a listed row whose server is saved but disabled.
    ``config_path`` names the harness's JSON config and ``config_keys`` the
    dotted keys that hold the server there: a listing that leaves out a server
    the config holds reads UNKNOWN, never MISSING, because the framework does
    not report a missing install it cannot see.
    """
    home = str(Path.home())

    def probe() -> "tuple[Status, str]":
        res = run(list_argv, timeout=timeout, cwd=home)
        if res.code == TIMEOUT_MISSING:
            return Status.UNKNOWN, f"{harness} CLI not installed"
        if not res.ok:
            return Status.UNKNOWN, f"{harness} list exited {res.code}"
        listing = res.out + res.err
        if config_path and _absent_but_configured(listing, present_token, config_path, config_keys):
            return Status.UNKNOWN, (f"{present_token} is in {config_path} but absent from the "
                                    f"{harness} listing, so the listing is not trusted (GRIP#9514)")
        return _read_listing(listing, harness, present_token, disabled_token)

    def install() -> Result:
        return run(add_argv, timeout=timeout, input_text=add_input, cwd=home)

    return Target(harness, probe, install)


def which_target(harness, binary, *, note="") -> Target:
    """A binary that must be on PATH. Probe only — no auto-install."""

    def probe() -> "tuple[Status, str]":
        path = shutil.which(binary)
        if path:
            return Status.REGISTERED, path
        return Status.MISSING, note or f"{binary} not on PATH"

    return Target(harness, probe, None)


def json_target(harness, *, config_path, dotted_key, value) -> Target:
    """A harness configured by a JSON file at ``config_path``."""

    path = Path(config_path).expanduser()

    def probe() -> "tuple[Status, str]":
        data = _read_json(path)
        if data is None:
            return Status.UNKNOWN, f"{path} not present or unreadable"
        if _has(data, dotted_key):
            return Status.REGISTERED, f"{dotted_key} set in {path.name}"
        return Status.MISSING, f"{dotted_key} absent from {path.name}"

    def install() -> Result:
        return _json_set_atomic(path, dotted_key, value)

    return Target(harness, probe, install)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _has(data, dotted: str) -> bool:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _ensure_branch(data: dict, parts) -> dict:
    """Walk ``parts`` through nested dicts, creating missing levels."""
    cur = data
    for part in parts:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    return cur


def _backup(path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".bak-stack-dogfood-{stamp}")
    backup.write_text(path.read_text())


def _json_set_atomic(path: Path, dotted: str, value) -> Result:
    """Set ``dotted`` to ``value`` in the JSON at ``path``.

    Backs the file up before writing, writes to a temp file, renames into place
    (atomic), and preserves the original mode. Total: an OS error is an exit-1
    Result, never a raised exception.
    """
    argv = ("json-set", str(path), dotted)
    parts = dotted.split(".")
    if path.exists():
        existing = _read_json(path)
        if not isinstance(existing, dict):
            return Result(argv, 1, "",
                          f"{path.name} exists but is not a readable JSON object; "
                          "refusing to overwrite")
        data = existing
    else:
        data = {}
    _ensure_branch(data, parts[:-1])[parts[-1]] = value
    try:
        mode = path.stat().st_mode & 0o7777 if path.exists() else None
        if path.exists():
            _backup(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp-stack-dogfood")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(path)
        if mode is not None:
            os.chmod(path, mode)
    except OSError as exc:
        return Result(argv, 1, "", str(exc))
    return Result(argv, 0, f"set {dotted} in {path.name}", "")
