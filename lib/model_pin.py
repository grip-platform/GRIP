#!/usr/bin/env python3
"""One deliberate default model for the Claude Code sessions GRIP launches on this machine.

The default lives in a gitignored, machine-local file, ``config/model-pin.local.json``
(``{"model": "claude-fable-5-1[1m]"}``). ``bin/grip`` injects ``--model <pin>`` at the
Claude launch; the launch flag outranks every settings key (the vendor's model-config
page ranks it 2 of 5 and the ``model`` key 4), so every ``gg++`` session starts on the
pin. Sessions GRIP does not start, a plain ``claude``, the Desktop app and headless
``claude -p`` children alike, take the account default. V>> ruled on 2026-10-11 that
headless calls must not inherit the pin, and the settings file cannot tell a headless
child from a plain launch, so no settings-side route carries the pin at all. GRIP#8011
had written ``env.ANTHROPIC_MODEL`` for that; the guard now removes that entry wherever
it finds it.

``hooks/model-pin-guard.py`` (ConfigChange, matcher ``user_settings``) deletes any
``model`` key the ``/model`` picker saves with Enter, so the shared file never carries a
saved pick and no session can re-pin the others. ``model`` is read once at session
start, so the deletion never changes the session that switched: Enter behaves like ``s``.

Measured 2026-11-16 on Claude Code 2.1.325 (GRIP#9208): no ConfigChange event follows the
picker's own save there, and the same save also drops ``effortLevel`` and reorders every
key. So ``bin/grip`` runs ``reconcile`` at each launch too, and the clean-up writes back
the committed bytes of ``settings.json``, in the file's own line endings, when the file,
less the pick, differs from them only in key order and spacing, or also in
``effortLevel`` when that pass removes a saved pick. Any other difference stays as it is,
and then only the pick is removed.

Three states of the pin file. A model: the launch default. The marker ``"default"``:
the account default is the deliberate choice for launches too, and the guard still
removes any saved pick (Codex on GRIP#8011). No file: the machine is unmanaged, the
guard does nothing and the launcher keeps its ``default`` alias;
``grip-model-default off`` returns there.

The default changes only through ``bin/grip-model-default <choice>``; the picker's
Enter cannot. A choice is an alias from ``lib/grip_model.py``, one of the vendor's own
aliases, or a ``claude-...`` model id; an unknown word is refused, because a typo
written here reaches every launch. Measured 2026-10-11 on Claude Code 2.1.296: a
``/model`` pick with Enter changed exactly one key in the shared file, and three earlier
unexplained pins were the same write. Design settled by the /ultrathink trio, sealed
78a3cb0a3fbc91dd; the headless ruling is the operator's.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# GRIP#8011 wrote this env entry so sessions GRIP does not start would get the pin.
# The operator ruled those sessions take the account default, so the entry is now
# removed wherever the guard finds it (see the module docstring).
ENV_KEY = "ANTHROPIC_MODEL"
PIN_NAME = "config/model-pin.local.json"
LOG_NAME = "logs/model-pin-guard.jsonl"
LOCK_NAME = "state/model-pin.lock"
NO_PIN = "default"
EXIT_NO_PIN = 3
# What may reach `--model` from a file on disk: a Claude model id
# (claude-<family>-<version>, optionally the [1m] variant) or one of the aliases the
# vendor documents for --model and /model. Nothing else: a typo here would be
# requested by every launch.
_MODEL_ID_RE = re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)*(\[1m\])?$")
VENDOR_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "opusplan"})
_ALIAS_RE = re.compile(r"^(opus|sonnet|haiku|fable|opusplan)(\[1m\])?$")


class PinError(ValueError):
    """The pin file exists but cannot be trusted: unreadable, or no valid model."""


def pin_path() -> Path:
    return get_claude_dir() / PIN_NAME


def settings_path() -> Path:
    return get_claude_dir() / "settings.json"


def log_path() -> Path:
    return get_claude_dir() / LOG_NAME


def valid_model(value: object) -> bool:
    """A model id or a vendor alias, never the marker word."""
    if not isinstance(value, str) or len(value) > 64:
        return False
    return bool(_MODEL_ID_RE.match(value) or _ALIAS_RE.match(value))


def read_pin(path: Path | None = None) -> str | None:
    """The pinned model, NO_PIN for the account-default marker, or None with no file.

    Raises PinError when the file exists but holds no value that may be launched.
    """
    path = path or pin_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinError(f"{path}: unreadable ({exc})") from exc
    model = data.get("model") if isinstance(data, dict) else None
    if model == NO_PIN:
        return NO_PIN
    if not valid_model(model):
        raise PinError(f"{path}: 'model' is not a Claude Code model value: {model!r}")
    return model


def write_pin(model: str, path: Path | None = None) -> Path:
    if model != NO_PIN and not valid_model(model):
        raise PinError(f"not a Claude Code model value: {model!r}")
    path = path or pin_path()
    record = {"model": model, "set_at": _now(), "by": "grip-model-default"}
    _write_atomic(path, json.dumps(record, indent=2) + "\n")
    return path


def clear_pin(path: Path | None = None) -> bool:
    """Remove the pin file: the machine becomes unmanaged. See `off`."""
    path = path or pin_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def desired_settings(data: dict) -> tuple[dict, list[str]]:
    """The settings as the guard wants them, and the changes that get there.

    No top-level ``model`` key (a pick the picker saved) and no ``env.ANTHROPIC_MODEL``
    entry (the route GRIP#8011 used before the headless ruling). Every other key is
    left exactly as it is, in its original order.
    """
    out = dict(data)
    changes: list[str] = []
    if "model" in out:
        changes.append(f"removed model={out.pop('model')!r}")
    env = out.get("env")
    if isinstance(env, dict) and ENV_KEY in env:
        env = dict(env)
        changes.append(f"removed env.{ENV_KEY}={env.pop(ENV_KEY)!r}")
        out["env"] = env
    return out, changes


# Besides adding `model`, the /model picker's save drops these keys and reorders every
# key (measured 2026-11-16 on Claude Code 2.1.325, GRIP#9208). In a pass that removes a
# saved pick, a file that differs from the committed one only in key order, spacing and
# these keys IS the committed file. Without a pick, a missing key is a local change.
RESTORABLE_KEYS = ("effortLevel",)
RESTORED = "put back the committed file (only key order, spacing or effortLevel differed)"
GIT_TIMEOUT_S = 5


def tracked_settings_bytes(path: Path) -> bytes | None:
    """The committed bytes of this settings file at HEAD, or None when git cannot say.

    The same source bin/grip-settings-reconcile reads (``git show HEAD:settings.json``),
    addressed from the file's own folder. No git, no repository, an untracked file or a
    timeout all give None, and the caller keeps its plain write.
    """
    import subprocess  # local, like FileLock below: the launcher's read path needs neither

    try:
        proc = subprocess.run(["git", "-C", str(path.parent), "show", f"HEAD:./{path.name}"],
                              capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def shipped_bytes(wanted: dict, path: Path, restorable: tuple = ()) -> bytes | None:
    """The committed bytes, when ``wanted`` equals them apart from key order and ``restorable``.

    The comparison ignores key order only: list order stays, so a hook that moved in its
    chain is a real difference, and so is a changed JSON type. Any real difference gives
    None. A key in ``restorable`` that the committed file has and ``wanted`` lacks does
    not count.
    """
    committed = tracked_settings_bytes(path)
    if committed is None:
        return None
    try:
        shipped = json.loads(committed.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(shipped, dict):
        return None
    candidate = dict(wanted)
    for key in restorable:
        if key in shipped and key not in candidate:
            candidate[key] = shipped[key]
    return committed if _same_json(candidate, shipped) else None


def _same_json(left: dict, right: dict) -> bool:
    """Equal as JSON apart from key order. Python's == calls true equal to 1 and 1 equal
    to 1.0; a dump with sorted keys does not, and it keeps list order (council seal
    e717afd97c4f7e5d)."""
    return (json.dumps(left, sort_keys=True, ensure_ascii=False)
            == json.dumps(right, sort_keys=True, ensure_ascii=False))


def _eol_like(raw: bytes, data: bytes) -> bytes:
    """``data`` with the line endings ``raw`` uses. A CRLF file stays CRLF, so a checkout
    made with core.autocrlf=true is never rewritten for its line endings alone (council
    seal ed3c7bf7c8a9e7be); everything else gets LF, never a text-mode CRLF."""
    lf = data.replace(b"\r\n", b"\n")
    return lf.replace(b"\n", b"\r\n") if b"\r\n" in raw else lf


def enforce_settings(path: Path | None = None) -> dict:
    """Bring settings.json into line, under the shared lock.

    Returns {"changed", "changes", "removed_model"}. Reads inside the lock and writes
    only when something differs, so a wave of guards after one picker write makes at
    most one rewrite; the rest find nothing to do. When the file, less the pick, is the
    committed file apart from key order and spacing, the committed bytes go back
    (GRIP#9208); otherwise only the pick goes and every other key keeps its place. A
    missing RESTORABLE_KEYS entry is forgiven only in a pass that removes a saved pick,
    the picker's own signature: without one, a missing key is a real local change and
    stays. Every write keeps the file's own line endings, and line endings alone are
    never a reason to write. Raises PinError on a settings file that is not a JSON
    object, and writes nothing then.
    """
    from file_lock import FileLock  # local: the launcher's read path imports nothing

    path = path or settings_path()
    lock = path.parent / LOCK_NAME if path == settings_path() else path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(lock, timeout=5):
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise PinError(f"{path}: unreadable ({exc})") from exc
        if not isinstance(data, dict):
            raise PinError(f"{path}: not a JSON object")
        wanted, changes = desired_settings(data)
        restorable = RESTORABLE_KEYS if "model" in data else ()
        shipped = shipped_bytes(wanted, path, restorable)
        if shipped is not None and _eol_like(raw, shipped) != raw:
            changes.append(RESTORED)
            _write_atomic_bytes(path, _eol_like(raw, shipped))
        elif changes:
            text = json.dumps(wanted, indent=2, ensure_ascii=False) + "\n"
            _write_atomic_bytes(path, _eol_like(raw, text.encode("utf-8")))
    return {"changed": bool(changes), "changes": changes,
            "removed_model": data.get("model") if "model" in data else None}


def reconcile(session_id: str | None = None, *, settings: Path | None = None,
              pin_file: Path | None = None, log: Path | None = None,
              source: str | None = None) -> dict:
    """What the guard does, on ConfigChange and at each launch: keep the settings clean,
    log any revert, never raise. ``source`` names the caller in the log row.

    A model and the marker both count as managed: any saved pick is removed. Only a
    missing file means "do nothing".
    """
    result: dict = {"changed": False, "changes": [], "removed_model": None,
                    "pin": None, "error": None}
    try:
        pin = read_pin(pin_file)
        result["pin"] = pin
        if pin is None:
            return result
        result.update(enforce_settings(settings))
    except PinError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 -- the guard must never block a session
        result["error"] = f"{type(exc).__name__}: {exc}"
    if result["changed"] or result["error"]:
        _log_event("policy_revert" if result["changed"] else "error", result, session_id, log,
                   source)
    return result


def notice(result: dict) -> dict:
    """The hook's JSON output: a terminal notification, since ConfigChange discards
    systemMessage (vendor hooks reference). OSC 9 is on the vendor's allowlist."""
    kept = "the account default" if result.get("pin") == NO_PIN else result.get("pin")
    removed = result.get("removed_model")
    if removed is None:
        text = (f"GRIP put back the committed settings.json; the default model stays {kept}. "
                "Change the default with grip-model-default.")
    else:
        text = (f"GRIP kept the default model {kept}; the saved pick "
                f"{removed} applies to that session only. "
                "Change the default with grip-model-default.")
    text = "".join(ch for ch in text if ch.isprintable())
    return {"terminalSequence": f"\x1b]9;{text}\x07"}


def set_default(choice: str, *, settings: Path | None = None, pin_file: Path | None = None,
                log: Path | None = None) -> dict:
    """The one sanctioned way to change the default: pin file first, then settings.

    ``default`` writes the marker, so the account default is enforced rather than
    merely left alone.
    """
    model = _resolve(choice)
    _warn_if_1m_on_pro(model)
    old = _safe_read_pin(pin_file)
    write_pin(model, pin_file)
    applied = enforce_settings(settings)
    result = {"pin": model, "previous_pin": old, **applied}
    _log_event("pin_change", result, None, log)
    return result


def disable(*, settings: Path | None = None, pin_file: Path | None = None,
            log: Path | None = None) -> dict:
    """Remove the pin file: the machine is unmanaged again. Settings are cleaned once more."""
    old = _safe_read_pin(pin_file)
    clear_pin(pin_file)
    applied = enforce_settings(settings)
    result = {"pin": None, "previous_pin": old, **applied}
    _log_event("pin_change", result, None, log)
    return result


def _resolve(choice: str) -> str:
    """An alias from lib/grip_model.py, a vendor alias, or a claude-... model id."""
    from grip_model import FRONTIER_MODELS

    if choice in FRONTIER_MODELS:
        return FRONTIER_MODELS[choice]
    if valid_model(choice):
        return choice
    raise PinError(f"unknown model choice {choice!r}: expected one of "
                   f"{sorted(FRONTIER_MODELS)}, a vendor alias ({', '.join(sorted(VENDOR_ALIASES))}) "
                   "or a claude-... model id")


def _configured_tier() -> str | None:
    """The explicitly configured plan tier (env GRIP_TIER or the tier config), or None.

    Never auto-detects: lib/grip_tier.resolve_configured shells out to nothing."""
    try:
        from grip_tier import resolve_configured
    except ImportError:
        return None
    try:
        return resolve_configured()
    except Exception:  # noqa: BLE001 -- a warning helper must never break a pin write
        return None


def _warn_if_1m_on_pro(model: str) -> None:
    """A [1m] pin on the Pro tier is kept, with one stderr line saying why it buys nothing.

    GRIP's Pro profile runs at 200K by choice (lib/grip_tier.TIER_ENVELOPE["pro"],
    compacting at 180K), so the suffix changes no budget there; the kit pins the plain
    claude-sonnet-5-5 instead. Never a refusal: an operator who typed the suffix gets it."""
    if model.endswith("[1m]") and _configured_tier() == "pro":
        print(f"model_pin: {model} carries [1m] on the Pro tier, where GRIP compacts at 180K "
              "by choice, so the suffix buys nothing; pin kept as given. grip-tier pro pins "
              "the plain claude-sonnet-5-5.", file=sys.stderr)


def _safe_read_pin(pin_file: Path | None) -> str | None:
    try:
        return read_pin(pin_file)
    except PinError:
        return None


def _log_event(event: str, result: dict, session_id: str | None, log: Path | None,
               source: str | None = None) -> None:
    row = {"ts": _now(), "event": event, "pin": result.get("pin"),
           "removed_model": result.get("removed_model"), "changes": result.get("changes"),
           "error": result.get("error"), "session_id": session_id}
    if "previous_pin" in result:
        row["previous_pin"] = result["previous_pin"]
    if source:
        row["source"] = source
    path = log or log_path()
    try:
        from file_lock import locked_append

        path.parent.mkdir(parents=True, exist_ok=True)
        locked_append(path, json.dumps(row))
    except Exception:  # noqa: BLE001 -- telemetry must not stop the guard
        return


def _write_atomic(path: Path, text: str) -> None:
    """Text as UTF-8 bytes, newlines exactly as given. A text-mode write turned every
    newline into CRLF on Windows, so the file differed from HEAD on every line (GRIP#9208)."""
    _write_atomic_bytes(path, text.encode("utf-8"))


def _write_atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------------------ CLI
_USAGE = """usage: model_pin.py <command>
  set <choice>      make <choice> the launch default: an alias from lib/grip_model.py
                    (fable-5-1, opus-5-5, ...), a vendor alias (opus, sonnet, haiku,
                    fable, opusplan) or a claude-... model id; `default` enforces the
                    account default (the guard still removes any saved pick)
  off               remove the pin file: unmanaged, the guard does nothing
  show [--launch]   print the pin; --launch exits 3 with no file and 1 on a bad one
  reconcile [--session-id ID] [--source NAME] [--brief]
                    what the guard does; prints JSON, or with --brief one line per
                    change and nothing when nothing changed (bin/grip, at each launch)
"""


def _describe(pin: str | None) -> str:
    if pin == NO_PIN:
        return "the account default (enforced: the guard removes any saved pick)"
    return pin or "unmanaged (no pin file; the guard does nothing)"


def _cmd_set(choice: str) -> int:
    return _report(set_default(choice))


def _cmd_off() -> int:
    return _report(disable())


def _report(result: dict) -> int:
    pin = result["pin"]
    was = f" (was {_describe(result['previous_pin'])})" if result.get("previous_pin") else ""
    print(f"default model: {_describe(pin)}{was}")
    for change in result["changes"]:
        print(f"settings.json: {change}")
    print("launcher: bin/grip injects --model " + (pin or NO_PIN) + " on the next Claude launch")
    print("elsewhere: a plain `claude`, the Desktop app and headless `claude -p` take the account default")
    return 0


def _cmd_show(launch: bool) -> int:
    try:
        pin = read_pin()
    except PinError as exc:
        print(f"model_pin: {exc}", file=sys.stderr)
        return 1
    if pin is None:
        if not launch:
            print(_describe(None))
        return EXIT_NO_PIN if launch else 0
    print(pin if launch else _describe(pin))
    return 0


def _flag_value(argv: list[str], name: str) -> str | None:
    """The word after ``name`` in argv, or None when the flag or its value is missing."""
    if name not in argv:
        return None
    at = argv.index(name) + 1
    return argv[at] if at < len(argv) else None


def _cmd_reconcile(argv: list[str]) -> int:
    result = reconcile(_flag_value(argv, "--session-id"), source=_flag_value(argv, "--source"))
    if "--brief" not in argv:
        print(json.dumps(result, indent=2))
        return 0
    if result["error"]:
        print(f"[grip] settings.json: not tidied ({result['error']})")
        return 1
    for change in result["changes"] if result["changed"] else []:
        print(f"[grip] settings.json: {change}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE, end="", file=sys.stderr)
        return 0 if argv else 2
    try:
        if argv[0] == "set" and len(argv) == 2:
            return _cmd_set(argv[1])
        if argv[0] == "off":
            return _cmd_off()
        if argv[0] == "show":
            return _cmd_show("--launch" in argv[1:])
        if argv[0] == "reconcile":
            return _cmd_reconcile(argv[1:])
    except PinError as exc:
        print(f"model_pin: {exc}", file=sys.stderr)
        return 2
    print(_USAGE, end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
