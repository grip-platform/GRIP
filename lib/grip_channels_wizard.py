"""GRIP channels wizard — first-launch TUI for the inbound-relay trade-off.

The wizard mediates the user's choice for ``GRIP_CHANNELS_FLAG`` resolution.
Three persisted choices: ``enabled`` (inject the dangerous flag), ``disabled``
(skip injection), ``ask`` (run the wizard every launch).

Resolution precedence (callable from ``bin/grip`` via ``--resolve``):

1. ``GRIP_CHANNELS_FLAG`` env var (force/on/1/true → ON; off/0/false → OFF)
2. ``state/grip-channels-config.json`` ``choice`` field
3. Sentinel ``WIZARD`` — caller runs the interactive wizard

Per ``rules/syscall-doctrine.md``, this is the syscall boundary for the
channels-flag decision. ``bin/grip`` calls ``resolve_or_signal()`` and never
reaches into the JSON itself.

Per ``rules/efficiency-rules.md`` Rule 17, every user-facing string is plain
English. No "DELIGHT", no "convergence depth", no insider terminology.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.request
# ---------------------------------------------------------------------------
# termios/tty are POSIX-only and msvcrt is Windows-only. Importing either at
# module scope killed this file on the other platform -- the same defect that
# made grip_session_wizard.py unimportable on A>>'s Windows machine
# (2026-08-17). Import what exists; _select_key_reader() picks accordingly.
#
# NOTE: this is the second instance of this reader. GRIP's YSH threshold
# abstracts at three -- at the next one, lift it into lib/_compat.py, which is
# already the home of the cross-platform fcntl shim.
# ---------------------------------------------------------------------------

try:
    import termios
    import tty
except ModuleNotFoundError:  # Windows
    termios = None
    tty = None

try:
    import msvcrt
except ModuleNotFoundError:  # POSIX
    msvcrt = None

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


# ---------------------------------------------------------------------------
# Constants — single source of truth (DRY)
# ---------------------------------------------------------------------------

GRIP_HOME = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CONFIG_PATH = GRIP_HOME / "state" / "grip-channels-config.json"
HEALTH_URL = "http://127.0.0.1:3101/health"
HEALTH_TIMEOUT_S = 1.0
CONFIG_VERSION = 1

CHOICE_ENABLED = "enabled"
CHOICE_DISABLED = "disabled"
CHOICE_ASK = "ask"
WIZARD_SIGNAL = "WIZARD"  # caller runs the interactive flow

VALID_CHOICES = (CHOICE_ENABLED, CHOICE_DISABLED, CHOICE_ASK)

# Env var → effective choice (lowercase). Anything else = no opinion.
ENV_TRUTHY = {"force", "on", "1", "true"}
ENV_FALSY = {"off", "0", "false"}

# ANSI helpers — degrade gracefully via env detection
_NO_COLOR = bool(os.environ.get("NO_COLOR"))
_TERM = os.environ.get("TERM", "")
_ANSI_OK = (not _NO_COLOR) and _TERM not in ("", "dumb")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthSnapshot:
    """Normalised view of grip-channel /health for the wizard's status table."""

    online: bool
    whatsapp: dict
    slack: dict
    discord: dict
    web: dict
    uptime_ms: int
    bridge_last_check: dict  # bridge name → epoch_ms

    @classmethod
    def offline(cls) -> "HealthSnapshot":
        return cls(False, {}, {}, {}, {}, 0, {})

    @classmethod
    def from_payload(cls, p: Mapping[str, Any]) -> "HealthSnapshot":
        watchdog_bridges = (p.get("watchdog") or {}).get("bridges") or {}
        last = {b: int((watchdog_bridges.get(b) or {}).get("lastCheck", 0))
                for b in ("whatsapp", "slack", "web", "discord")}
        return cls(
            online=True,
            whatsapp=p.get("whatsapp") or {},
            slack=p.get("slack") or {},
            discord=p.get("discord") or {},
            web=p.get("web") or {},
            uptime_ms=int(p.get("uptime") or 0),
            bridge_last_check=last,
        )


# ---------------------------------------------------------------------------
# Resolution (called from bin/grip)
# ---------------------------------------------------------------------------


def _env_choice() -> str | None:
    """Return ``enabled``/``disabled`` from ``GRIP_CHANNELS_FLAG``, else None."""
    raw = os.environ.get("GRIP_CHANNELS_FLAG", "").strip().lower()
    if raw in ENV_TRUTHY:
        return CHOICE_ENABLED
    if raw in ENV_FALSY:
        return CHOICE_DISABLED
    return None


def _config_path() -> Path:
    """Resolve config path lazily (so mock.patch on CONFIG_PATH works)."""
    return CONFIG_PATH


def _persisted_choice(path: Path | None = None) -> str | None:
    """Return persisted choice (``enabled``/``disabled``/``ask``) or None."""
    target = path if path is not None else _config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    choice = data.get("choice")
    return choice if choice in VALID_CHOICES else None


def resolve_or_signal(path: Path | None = None) -> str:
    """Return ``enabled``/``disabled``/``WIZARD`` per the resolution order.

    ``ask`` persisted choice maps to ``WIZARD`` (caller runs wizard each time).
    """
    env = _env_choice()
    if env is not None:
        return env
    persisted = _persisted_choice(path)
    if persisted == CHOICE_ASK:
        return WIZARD_SIGNAL
    if persisted in (CHOICE_ENABLED, CHOICE_DISABLED):
        return persisted
    return WIZARD_SIGNAL


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_config(choice: str, set_by: str = "wizard",
                 path: Path | None = None) -> None:
    """Write the choice to ``state/grip-channels-config.json`` atomically."""
    if choice not in VALID_CHOICES:
        raise ValueError(f"invalid choice: {choice!r}")
    target = path if path is not None else _config_path()
    payload = {
        "choice": choice,
        "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "set_by": set_by,
        "version": CONFIG_VERSION,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


def fetch_health(url: str = HEALTH_URL,
                 timeout: float = HEALTH_TIMEOUT_S) -> HealthSnapshot:
    """Fetch and parse the grip-channel daemon's /health endpoint.

    Never raises — returns ``HealthSnapshot.offline()`` on any error.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return HealthSnapshot.offline()
    return HealthSnapshot.from_payload(payload)


# ---------------------------------------------------------------------------
# ANSI styling — small DRY helpers
# ---------------------------------------------------------------------------


_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "inv": "\033[7m",
    "fg_dim": "\033[38;5;244m",
    "fg_orange": "\033[38;5;208m",
    "fg_green": "\033[38;5;42m",
    "fg_red": "\033[38;5;203m",
}


def _style(text: str, *names: str) -> str:
    """Apply ANSI styles. Degrades to plain text if ANSI is unavailable."""
    if not _ANSI_OK:
        return text
    codes = "".join(_ANSI.get(n, "") for n in names)
    return f"{codes}{text}{_ANSI['reset']}"


def _term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


# ---------------------------------------------------------------------------
# Renderers — kept under 30 lines each (CC + KISS)
# ---------------------------------------------------------------------------


_HEADER_LINES = (
    "GRIP SWITCHBOARD",
    "channels.wizard / inbound relay configuration",
)


def render_header(width: int) -> list[str]:
    """Stark, asymmetric header. No emoji, no AI-slop."""
    title = _HEADER_LINES[0]
    sub = _HEADER_LINES[1]
    rule = "─" * min(width, 64)
    return [
        _style(title, "bold"),
        _style(sub, "fg_dim"),
        _style(rule, "fg_dim"),
        "",
    ]


def _format_age(epoch_ms: int) -> str:
    if not epoch_ms:
        return "—"
    delta_ms = max(0, int(time.time() * 1000) - epoch_ms)
    if delta_ms < 1000:
        return f"{delta_ms}ms ago"
    if delta_ms < 60_000:
        return f"{delta_ms // 1000}s ago"
    return f"{delta_ms // 60_000}m ago"


def _format_uptime(uptime_ms: int) -> str:
    s = uptime_ms // 1000
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


def _row_whatsapp(h: HealthSnapshot) -> tuple[str, str, str]:
    if not h.online:
        return ("WhatsApp", "offline", "—")
    wa = h.whatsapp
    state = "connected" if wa.get("connected") else "disconnected"
    jid = wa.get("ownJid") or "—"
    age = _format_age(h.bridge_last_check.get("whatsapp", 0))
    return ("WhatsApp", state, f"{jid} · last check {age}")


def _row_slack(h: HealthSnapshot) -> tuple[str, str, str]:
    if not h.online:
        return ("Slack", "offline", "—")
    state = "connected" if h.slack.get("connected") else "disconnected"
    age = _format_age(h.bridge_last_check.get("slack", 0))
    return ("Slack", state, f"workspace bridge · last check {age}")


def _row_discord(h: HealthSnapshot) -> tuple[str, str, str]:
    if not h.online:
        return ("Discord", "offline", "—")
    state = "connected" if h.discord.get("connected") else "disconnected"
    age = _format_age(h.bridge_last_check.get("discord", 0))
    return ("Discord", state, f"guild bridge · last check {age}")


def _row_web(h: HealthSnapshot) -> tuple[str, str, str]:
    if not h.online:
        return ("Web", "offline", "—")
    web = h.web
    state = "connected" if web.get("connected") else "disconnected"
    port = web.get("port", "?")
    sessions = web.get("sessions", 0)
    return ("Web", state, f"port {port} · {sessions} active session(s)")


def _row_daemon(h: HealthSnapshot) -> tuple[str, str, str]:
    if not h.online:
        return ("Daemon", "OFFLINE — daemon not running", "")
    return ("Daemon", "running", f"uptime {_format_uptime(h.uptime_ms)}")


_ROW_BUILDERS: tuple[Callable[[HealthSnapshot], tuple[str, str, str]], ...] = (
    _row_whatsapp, _row_slack, _row_discord, _row_web, _row_daemon,
)


def _format_status_cell(state: str) -> str:
    if state == "connected" or state == "running":
        return _style(state.ljust(14), "fg_green")
    if state.startswith("OFFLINE"):
        return _style(state, "fg_red")
    return _style(state.ljust(14), "fg_dim")


def render_status_table(h: HealthSnapshot, width: int) -> list[str]:
    """Five-row table: WhatsApp/Slack/Discord/Web/Daemon."""
    lines = [_style("BRIDGE STATUS", "bold")]
    label_w = 10
    state_w = 14
    detail_w = max(20, width - label_w - state_w - 4)
    for builder in _ROW_BUILDERS:
        label, state, detail = builder(h)
        cell_state = _format_status_cell(state)
        cell_detail = (detail[:detail_w]) if len(detail) > detail_w else detail
        lines.append(f"  {label.ljust(label_w)} {cell_state} {cell_detail}")
    lines.append("")
    return lines


def render_current_config(persisted: str | None, env: str | None,
                          effective: str) -> list[str]:
    """Block: persisted choice / env override / effective on next launch."""
    label_persisted = persisted or "(none — first launch)"
    env_line = (f"GRIP_CHANNELS_FLAG={os.environ.get('GRIP_CHANNELS_FLAG')}"
                if env else "(unset)")
    return [
        _style("CURRENT CONFIGURATION", "bold"),
        f"  Persisted choice  : {label_persisted}",
        f"  Env override      : {env_line}",
        f"  Effective on next : {_style(effective.upper(), 'fg_orange', 'bold')}",
        "",
    ]


_TRADE_OFF_LINES = (
    ("ENABLED ", "phone (WhatsApp/Slack/Discord/Web) can drive this Claude"),
    ("        ", "Code session. AskUserQuestion / ExitPlanMode /"),
    ("        ", "EnterPlanMode are globally suppressed by Claude Code"),
    ("        ", ">= 2.1.75 (upstream issue #39342, still open). All HITL"),
    ("        ", "prompts in skills will fail silently."),
    ("DISABLED", "HITL gates work normally. Outbound channel posts still"),
    ("        ", "work (channel_reply, channel_post_rich,"),
    ("        ", "channel_send_file). Only the *inbound* relay"),
    ("        ", "(phone -> session) is disabled."),
    ("ASK     ", "wizard runs every session start to choose fresh."),
)


def render_trade_off() -> list[str]:
    out = [_style("TRADE-OFF", "bold")]
    for label, body in _TRADE_OFF_LINES:
        out.append(f"  {_style(label, 'fg_orange')}  {body}")
    out.append("")
    return out


_ACTION_PROMPT = (
    "[E]nable  [D]isable  [A]sk-each-launch  [V]iew docs  [Q]uit (no change)"
)

_KEY_TO_ACTION = {
    "e": "enable", "d": "disable", "a": "ask",
    "v": "view", "q": "quit",
}


# ---------------------------------------------------------------------------
# Easter eggs — restrained, low-probability
# ---------------------------------------------------------------------------


_FOOTER_PRIMARY = "Run `grip channels` anytime to revisit."

_EASTER_EGGS = (
    "Switchboard's open. Talk to me anytime.",   # Mei Ling, codec
    "Same coffee, different cup.",                # /coffee
    "140.85 — colonel, can you hear me?",         # /140-85
)


def _maybe_easter_egg(rng: random.Random | None = None) -> str | None:
    rng = rng or random.Random()
    if rng.random() < 0.03:
        return rng.choice(_EASTER_EGGS)
    return None


def render_footer() -> list[str]:
    lines = [_style(_FOOTER_PRIMARY, "fg_dim")]
    egg = _maybe_easter_egg()
    if egg is not None:
        lines.append(_style(f"  // {egg}", "fg_dim"))
    return lines


# ---------------------------------------------------------------------------
# Single-key input
# ---------------------------------------------------------------------------


def _read_one_key_posix() -> str:
    """One keypress, no Return, via termios/tty (macOS, Linux)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_one_key_windows() -> str:
    """One keypress, no Return, via msvcrt (Windows console)."""
    return msvcrt.getwch()


def _read_one_key_line() -> str:
    """Fallback when neither module is present: first char of a whole line."""
    return sys.stdin.readline()[:1]


def _select_key_reader():
    """Pick the reader whose platform module actually imported."""
    if msvcrt is not None:
        return _read_one_key_windows
    if termios is not None and tty is not None:
        return _read_one_key_posix
    return _read_one_key_line


def _read_one_key() -> str:
    """Single-key stdin read. Returns lowercase letter or ''."""
    if not sys.stdin.isatty():
        return ""
    ch = _select_key_reader()()
    if not ch or ord(ch) in (3, 4, 27):  # Ctrl+C / Ctrl+D / Esc
        raise KeyboardInterrupt()
    return ch.lower()


def _prompt_for_action() -> str:
    """Loop until a recognised key is pressed. Returns action string."""
    while True:
        sys.stdout.write(_ACTION_PROMPT + " ")
        sys.stdout.flush()
        try:
            key = _read_one_key()
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return "quit"
        sys.stdout.write("\n")
        action = _KEY_TO_ACTION.get(key)
        if action is not None:
            return action
        sys.stdout.write(_style("  unknown key — try again\n", "fg_dim"))


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------


def _typewrite(line: str, delay_s: float, animate: bool) -> None:
    if not animate or not sys.stdout.isatty():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        return
    for ch in line:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay_s)
    sys.stdout.write("\n")


def _maybe_chime(skip: bool) -> None:
    if skip or os.environ.get("NO_AUDIO"):
        return
    try:
        from lib.handoff_chime import play_chime  # local import — testable
    except Exception:
        try:
            from handoff_chime import play_chime  # type: ignore
        except Exception:
            return
    try:
        play_chime()
    except Exception:
        pass  # never let chime block the wizard


# ---------------------------------------------------------------------------
# Main composition (small steps, dispatchable)
# ---------------------------------------------------------------------------


def _action_to_choice(action: str) -> str | None:
    return {"enable": CHOICE_ENABLED,
            "disable": CHOICE_DISABLED,
            "ask": CHOICE_ASK}.get(action)


def _docs_block() -> list[str]:
    return [
        "",
        _style("DOCS", "bold"),
        "  rules/operational-context.md  — canonical policy",
        "  bin/grip                      — resolution implementation",
        "  lib/grip_channels_wizard.py   — this wizard",
        "  Upstream: anthropics/claude-code#39342",
        "",
    ]


def render_full_screen(h: HealthSnapshot, persisted: str | None,
                       env: str | None, effective: str) -> list[str]:
    """Compose every section. Pure — no I/O, no input — testable."""
    width = _term_width()
    lines: list[str] = []
    lines.extend(render_header(width))
    lines.extend(render_status_table(h, width))
    lines.extend(render_current_config(persisted, env, effective))
    lines.extend(render_trade_off())
    return lines


def _render_to_stdout(lines: list[str]) -> None:
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Wizard entry points
# ---------------------------------------------------------------------------


def _resolve_for_display() -> tuple[str | None, str | None, str]:
    """Return (persisted, env, effective_label) for the configuration block."""
    persisted = _persisted_choice()
    env = _env_choice()
    if env is not None:
        effective = env
    elif persisted == CHOICE_ASK or persisted is None:
        effective = "wizard"
    else:
        effective = persisted
    return persisted, env, effective


def _persist_with_feedback(choice: str, animate: bool, skip_chime: bool) -> None:
    _typewrite("Persisting choice...", 0.015, animate)
    write_config(choice, set_by="wizard")
    _typewrite("Saved.", 0.015, animate)
    _maybe_chime(skip_chime)


def run_interactive(animate: bool, skip_chime: bool) -> int:
    """Render once, prompt, persist. Returns process exit code."""
    while True:
        h = fetch_health()
        persisted, env, effective = _resolve_for_display()
        _render_to_stdout(render_full_screen(h, persisted, env, effective))
        _render_to_stdout([_ACTION_PROMPT])
        action = _prompt_for_action()
        if action == "quit":
            sys.stdout.write(_style("no change\n", "fg_dim"))
            return 0
        if action == "view":
            _render_to_stdout(_docs_block())
            continue  # re-render and re-prompt
        choice = _action_to_choice(action)
        if choice is None:
            return 0
        _persist_with_feedback(choice, animate, skip_chime)
        _render_to_stdout(render_footer())
        return 0


def run_probe_only() -> int:
    h = fetch_health()
    persisted, env, effective = _resolve_for_display()
    _render_to_stdout(render_full_screen(h, persisted, env, effective))
    return 0


def run_accept(value: str) -> int:
    if value not in VALID_CHOICES:
        sys.stderr.write(f"--accept must be one of {VALID_CHOICES}\n")
        return 2
    write_config(value, set_by="manual")
    sys.stdout.write(f"choice persisted: {value}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip channels",
        description="Configure GRIP inbound channel relay (WhatsApp/Slack/Discord/Web).",
    )
    parser.add_argument("--no-animations", action="store_true",
                        help="skip typewriter effect")
    parser.add_argument("--no-chime", action="store_true",
                        help="skip success chime")
    parser.add_argument("--probe-only", action="store_true",
                        help="render status without prompting (no stdin read)")
    parser.add_argument("--accept", choices=VALID_CHOICES,
                        help="non-interactive set (test/install)")
    parser.add_argument("--resolve", action="store_true",
                        help="print resolved choice or WIZARD signal and exit")
    args = parser.parse_args(argv)

    if args.resolve:
        sys.stdout.write(resolve_or_signal() + "\n")
        return 0
    if args.accept is not None:
        return run_accept(args.accept)
    if args.probe_only:
        return run_probe_only()
    return run_interactive(animate=not args.no_animations,
                           skip_chime=args.no_chime)


if __name__ == "__main__":
    raise SystemExit(main())
