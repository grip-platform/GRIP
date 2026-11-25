"""GRIP embeddings-warm wizard — opt-in always-warm semantic memory.

Mirrors the resolution contract of ``lib/grip_channels_wizard.py``: the user's
choice is persisted to ``state/grip-embeddings-config.json`` and resolved with a
fixed precedence so ``bin/grip`` / onboarding never reach into the JSON directly.

Resolution precedence (``resolve_or_signal``):
  1. env ``GRIP_EMBEDDINGS_WARM`` (on/1/true/enabled -> enabled; off/0/false/disabled -> disabled)
  2. ``state/grip-embeddings-config.json`` ``choice`` field
  3. Sentinel ``WIZARD`` — caller runs the interactive flow

On ENABLE the wizard drives ``lib.embeddings_warm.ensure_ready`` (idempotent,
never blocks) and prints a plain-English summary. Rule 17: plain language only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

GRIP_HOME = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CONFIG_PATH = GRIP_HOME / "state" / "grip-embeddings-config.json"
ENV_VAR = "GRIP_EMBEDDINGS_WARM"
CONFIG_VERSION = 1

CHOICE_ENABLED = "enabled"
CHOICE_DISABLED = "disabled"
CHOICE_ASK = "ask"
WIZARD_SIGNAL = "WIZARD"
VALID_CHOICES = (CHOICE_ENABLED, CHOICE_DISABLED, CHOICE_ASK)

_TRUEY = {"on", "1", "true", "yes", "enabled"}
_FALSEY = {"off", "0", "false", "no", "disabled"}


# ---------------------------------------------------------------------------
# Resolution (env -> json -> WIZARD)
# ---------------------------------------------------------------------------

def _env_choice() -> str | None:
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    if raw in _TRUEY:
        return CHOICE_ENABLED
    if raw in _FALSEY:
        return CHOICE_DISABLED
    return None


def _read_choice(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    choice = data.get("choice")
    return choice if choice in VALID_CHOICES else None


def resolve_or_signal(path: Path | None = None) -> str:
    """Return ``enabled``/``disabled``, or ``WIZARD`` when the caller must ask."""
    env = _env_choice()
    if env is not None:
        return env
    stored = _read_choice(path or CONFIG_PATH)
    if stored == CHOICE_ENABLED or stored == CHOICE_DISABLED:
        return stored
    return WIZARD_SIGNAL  # unset or "ask" -> run the wizard


def write_choice(choice: str, path: Path | None = None, set_by: str = "wizard") -> None:
    """Persist the choice atomically to ``state/grip-embeddings-config.json``."""
    if choice not in VALID_CHOICES:
        raise ValueError(f"choice must be one of {VALID_CHOICES}, got {choice!r}")
    target = path or CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "choice": choice,
        "set_at": datetime.now(timezone.utc).isoformat(),
        "set_by": set_by,
        "version": CONFIG_VERSION,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


# ---------------------------------------------------------------------------
# Setup + summary
# ---------------------------------------------------------------------------

def _run_setup_and_report() -> bool:
    """Drive ensure_ready and print a plain-English summary. Returns its ok flag."""
    from lib.embeddings_warm import ensure_ready

    status = ensure_ready(interactive=True)
    if status["ok"]:
        print("Always-warm semantic memory is ready. Your recall and search will be "
              "faster and sharper.")
    elif status.get("skipped_reason"):
        print(status["skipped_reason"])
    for line in status.get("messages", []):
        print(f"  - {line}")
    return bool(status["ok"])


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def run_interactive(path: Path | None = None) -> int:
    """Ask the operator, persist the choice, and set up on ENABLE."""
    print("\nAlways-warm semantic memory (recommended)")
    print("GRIP can run a small local model (ollama + nomic-embed-text) that turns your")
    print("notes and history into searchable meaning, kept always-on for instant recall.")
    print("It is optional and never sends anything off your machine.\n")
    try:
        answer = input("Enable always-warm semantic memory? [Y/n] ").strip().lower()
    except EOFError:
        answer = ""
    choice = CHOICE_DISABLED if answer in _FALSEY else CHOICE_ENABLED
    write_choice(choice, path=path, set_by="wizard")
    if choice == CHOICE_ENABLED:
        _run_setup_and_report()
    else:
        print("Skipped. Enable it any time with `grip embeddings-warm`.")
    return 0


def run_probe_only(path: Path | None = None) -> int:
    print(resolve_or_signal(path))
    return 0


def run_accept(value: str, path: Path | None = None) -> int:
    if value not in VALID_CHOICES:
        print(f"invalid choice {value!r}; expected one of {VALID_CHOICES}", file=sys.stderr)
        return 2
    write_choice(value, path=path, set_by="accept")
    if value == CHOICE_ENABLED:
        _run_setup_and_report()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grip embeddings-warm", add_help=True)
    parser.add_argument("--resolve", action="store_true", help="print resolved choice or WIZARD")
    parser.add_argument("--probe-only", action="store_true", help="alias for --resolve")
    parser.add_argument("--accept", choices=VALID_CHOICES, help="set the choice non-interactively")
    parser.add_argument("--no-animations", action="store_true", help="(accepted for parity; no-op)")
    parser.add_argument("--no-chime", action="store_true", help="(accepted for parity; no-op)")
    args = parser.parse_args(argv)
    if args.resolve or args.probe_only:
        return run_probe_only()
    if args.accept:
        return run_accept(args.accept)
    return run_interactive()


if __name__ == "__main__":
    raise SystemExit(main())
