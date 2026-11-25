#!/usr/bin/env python3
"""The launch defaults a plan tier applies: the model pin and the effort level.

A Claude Pro install launches on Sonnet 5.5 at medium effort, because one person on
Pro has a single five-hour-and-weekly allowance and README-FIRST promises exactly
that. Until 2026-10-16 nothing wrote either value: the install profile copied
``CLAUDE_CODE_EFFORT_LEVEL=max`` and ``effortLevel=max`` verbatim
(``config/settings.reference.json``), and with no ``config/model-pin.local.json``
``bin/grip`` launched ``--model default``, the account default.

``apply(tier)`` reads the tier's envelope (``lib/grip_tier.TIER_ENVELOPE``) and acts
on two optional keys. ``launch_model`` is pinned through ``lib/model_pin.set_default``
(the plain vendor id, so the Pro ``[1m]`` warning never fires on the kit's own
default). ``launch_effort`` is written to ``settings.json`` as both
``env.CLAUDE_CODE_EFFORT_LEVEL`` (the env override /effort reports) and
``effortLevel``, under the same lock the pin guard uses, only when a value differs.
A tier whose envelope carries neither key (max, api) changes nothing: the reference
profile's max and the operator's own pin stand.

Callers: ``lib/grip_tier.set_tier(..., apply_launch_defaults=True)``, which the
``grip-tier`` CLI and the wizard's Step 5.65 pass. Idempotent: a second call on the
same files reports no changes. Runs on the system interpreter (``bin/grip-tier``
execs ``python3``): stdlib and lib siblings only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import model_pin  # noqa: E402

EFFORT_ENV_KEY = "CLAUDE_CODE_EFFORT_LEVEL"
EFFORT_SETTINGS_KEY = "effortLevel"


class LaunchDefaultsError(ValueError):
    """The settings file exists but is not a JSON object, so nothing was written."""


def launch_defaults(tier: str) -> dict:
    """The two optional keys of a tier's envelope: {"launch_model", "launch_effort"}."""
    from grip_tier import envelope

    env = envelope(tier)
    return {"launch_model": env.get("launch_model"), "launch_effort": env.get("launch_effort")}


def set_effort(level: str, settings: Path | None = None) -> list[str]:
    """Write ``level`` as the effort env override and the effortLevel key; return the changes.

    Every other key is left as it is, in its original order. Nothing is written when
    both values already match. Raises LaunchDefaultsError on a file that is not a
    JSON object; a missing file is created with just the two keys.
    """
    from file_lock import FileLock

    path = settings or model_pin.settings_path()
    lock = _lock_for(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock, timeout=5):
        data = _read_object(path)
        changes = _desired_effort(data, level)
        if changes:
            _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changes


def apply(tier: str = "pro", *, settings: Path | None = None, pin_file: Path | None = None,
          quiet: bool = False) -> dict:
    """Apply ``tier``'s launch defaults; return what was pinned, set and changed."""
    wanted = launch_defaults(tier)
    result = {"tier": tier, "pin": None, "effort": None, "changes": []}
    model = wanted["launch_model"]
    if model:
        pinned = model_pin.set_default(model, settings=settings, pin_file=pin_file)
        result["pin"] = pinned["pin"]
        result["changes"].extend(pinned["changes"])
    effort = wanted["launch_effort"]
    if effort:
        result["changes"].extend(set_effort(effort, settings))
        result["effort"] = effort
    if not quiet and (model or effort):
        _report(result)
    return result


def _desired_effort(data: dict, level: str) -> list[str]:
    """Mutate ``data`` towards ``level``; return one line per key that changed."""
    changes: list[str] = []
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
        data["env"] = env
    if env.get(EFFORT_ENV_KEY) != level:
        changes.append(f"env.{EFFORT_ENV_KEY}: {env.get(EFFORT_ENV_KEY)!r} -> {level!r}")
        env[EFFORT_ENV_KEY] = level
    if data.get(EFFORT_SETTINGS_KEY) != level:
        changes.append(f"{EFFORT_SETTINGS_KEY}: {data.get(EFFORT_SETTINGS_KEY)!r} -> {level!r}")
        data[EFFORT_SETTINGS_KEY] = level
    return changes


def _read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LaunchDefaultsError(f"{path}: unreadable ({exc})") from exc
    if not isinstance(data, dict):
        raise LaunchDefaultsError(f"{path}: not a JSON object")
    return data


def _lock_for(path: Path) -> Path:
    """The lock model_pin takes for the same file, so the guard and this writer never race."""
    if path == model_pin.settings_path():
        return path.parent / model_pin.LOCK_NAME
    return path.with_suffix(".lock")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _report(result: dict) -> None:
    if result["pin"]:
        print(f"launch model: {result['pin']} (bin/grip injects --model {result['pin']}; "
              "change it with grip-model-default)")
    if result["effort"]:
        print(f"launch effort: {result['effort']} (settings.json env.{EFFORT_ENV_KEY} and "
              f"{EFFORT_SETTINGS_KEY}; change it per session with /effort)")
    for change in result["changes"]:
        print(f"settings.json: {change}")
