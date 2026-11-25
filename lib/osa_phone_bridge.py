"""
OsaPhoneBridge — inbound WhatsApp command → HAL OSA dispatcher.

Entry point for the /osa-phone-action skill. Parses /osa and /mac
command messages from authorised JIDs and dispatches to the HAL OSA
registry. The outbound counterpart is lib/osa_phone_bridge not needed —
outbound delivery is handled by send-whatsapp / send-to-phone skills.

Syscall doctrine: one shim, one API. All callers (grip-channel hook,
test harness, future Discord bridge) call on_message(). Implementation
detail (which osa binary, which tier model) is hidden.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wa_identities import operator_jid  # noqa: E402

log = logging.getLogger(__name__)

HAL_OSA_BIN = Path.home() / ".hal" / "bin" / "osa"
OSA_PHONE_LOG = Path.home() / ".claude" / "logs" / "osa-phone.log"

# Authorised JIDs and their permitted tier ceiling. The operator's ID comes from
# the private file (lib/wa_identities.py); with none configured, nobody is
# authorised. Tier 3 (sleep/shutdown/restart) is NEVER permitted via phone.
_OPERATOR = operator_jid()
ALLOWED_JIDS: dict[str, dict] = {_OPERATOR: {"name": "V>>", "max_tier": 2}} if _OPERATOR else {}

# Natural-language /mac shortcuts → canonical OSA script names.
MAC_ALIASES: dict[str, str] = {
    "mute": "mute",
    "unmute": "unmute",
    "lock": "screen_lock",
    "wake": "screen_wake",
    "battery": "battery_get",
    "volume": "volume_get",
    "play": "music_play",
    "pause": "music_pause",
    "next": "music_next_track",
    "previous": "music_previous_track",
    "prev": "music_previous_track",
    "playing": "music_now_playing",
    "findmy": "findmy_status",
    "list": "__list__",
    "help": "__help__",
}

# Scripts that are tier-1 (no capability token required). Keep in step with
# the tiers in HAL's lib/hal/osa/registry.json: a tier-1 script missing here
# is sent down the tier-2 path and has a capability token minted for nothing.
TIER1_SCRIPTS = frozenset({
    "battery_get", "volume_get", "frontmost_app_get",
    "music_now_playing", "screen_wake", "findmy_status",
})

# Named composite macros (multi-step rituals).
MACRO_NAMES = frozenset({"morning", "sprint", "meeting", "wrap", "status", "watch"})


@dataclass
class ScriptCall:
    script_name: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class MacroCall:
    name: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class ParseError:
    reason: str


class TierViolation(Exception):
    def __init__(self, script: str, required_tier: int):
        self.script = script
        self.required_tier = required_tier
        super().__init__(f"Tier-{required_tier} script '{script}' not permitted via phone")


class OsaPhoneBridge:
    """
    Parse inbound WhatsApp /osa|/mac commands and dispatch to HAL OSA.

    Thread-safe: each on_message() call is independent. No shared mutable
    state beyond the audit log (append-only).
    """

    def __init__(
        self,
        allowed_jids: dict[str, dict] | None = None,
        hal_osa_bin: Path | None = None,
        log_path: Path | None = None,
    ):
        self._jids = allowed_jids or ALLOWED_JIDS
        self._hal = hal_osa_bin or HAL_OSA_BIN
        self._log = log_path or OSA_PHONE_LOG
        self._log.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_message(self, jid: str, text: str) -> Optional[str]:
        """
        Called by grip-channel when a WhatsApp message arrives.

        Returns the reply string to send back (or None if not an OSA command).
        Silently returns None for unauthorised JIDs.
        """
        if jid not in self._jids:
            return None  # not authorised — drop silently

        max_tier = self._jids[jid]["max_tier"]
        result = self.parse_command(text)

        if result is None:
            return None  # not an OSA command — pass through to GRIP relay

        if isinstance(result, ParseError):
            return f"Parse error: {result.reason}"

        if isinstance(result, MacroCall):
            try:
                reply = self._run_macro(result.name, max_tier, result.params)
            except TierViolation as e:
                reply = f"Tier-3 action '{e.script}' is not permitted via phone."
            except FileNotFoundError:
                reply = "HAL not installed — /osa-phone-action unavailable."
            self._audit(jid, text, f"macro:{result.name}", reply)
            return reply

        assert isinstance(result, ScriptCall)

        # Special built-in meta-commands
        if result.script_name == "__list__":
            return self._list_scripts()
        if result.script_name == "__help__":
            return self._help_text()

        try:
            output = self._dispatch(result, max_tier)
            reply = f"{result.script_name}: {output}"
        except TierViolation as e:
            reply = f"Tier-3 action '{e.script}' is not permitted via phone. Open a GRIP session to run it."
        except subprocess.CalledProcessError as e:
            reply = f"osa error: {result.script_name} failed — {e.stderr.strip() or e.returncode}"
        except FileNotFoundError:
            reply = "HAL not installed — /osa-phone-action unavailable. Install: git clone https://github.com/CodeTonight-SA/hal.git ~/.hal"

        self._audit(jid, text, result.script_name, reply)
        return reply

    def parse_command(self, text: str) -> Optional[ScriptCall | MacroCall | ParseError]:
        """
        Parse a raw WhatsApp message into a ScriptCall (or None).

        Pure function — no I/O. Suitable for unit testing without HAL.

        Returns:
            ScriptCall  — valid OSA command
            ParseError  — command prefix matched but script not found
            None        — not an OSA/mac command at all
        """
        text = text.strip()
        if not text:
            return None

        lower = text.lower()

        # /osa <script_name> [key=val ...]
        if lower.startswith("/osa "):
            return self._parse_osa_args(text[5:].strip())

        # /mac <alias_or_script> [args...]
        if lower.startswith("/mac "):
            return self._parse_mac_args(text[5:].strip())

        # Bare /osa or /mac (no script name) — return help
        if lower in ("/osa", "/mac"):
            return ScriptCall("__help__")

        return None

    def register(self) -> None:
        """
        Register this bridge as a grip-channel inbound hook.
        One-time setup — idempotent.
        """
        # grip-channel exposes a Python hook registration API via its MCP
        # streamable-http server. Until that API is formally exposed, we
        # write a marker file that the session-start hook reads.
        marker = Path.home() / ".claude" / "state" / "osa-phone-bridge-registered.json"
        marker.write_text(json.dumps({
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "hal_osa_bin": str(self._hal),
            "allowed_jids": list(self._jids.keys()),
        }))
        print(f"Bridge registered: {marker}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_osa_args(self, args_text: str) -> ScriptCall | ParseError:
        parts = args_text.split()
        if not parts:
            return ParseError("No script name provided. Send '/mac help' for usage.")
        script_name = parts[0]
        params = {}
        for token in parts[1:]:
            if "=" in token:
                k, _, v = token.partition("=")
                params[k] = v
        return ScriptCall(script_name=script_name, params=params)

    def _parse_mac_args(self, args_text: str) -> ScriptCall | MacroCall | ParseError:
        # Returns ScriptCall (alias/volume/notify/speak), MacroCall (named
        # composite macros incl. watch), or ParseError (bare-script fall-through
        # via _parse_osa_args). Mirrors parse_command's union — the annotation
        # must advertise every branch so callers don't need undeclared isinstance.
        lower = args_text.lower()

        # /mac volume <n> → volume_set level=n
        m = re.match(r"volume\s+(\d+)", lower)
        if m:
            return ScriptCall("volume_set", {"level": m.group(1)})

        # /mac notify <text> → notification body=<text>
        m = re.match(r"notify\s+(.+)", args_text, re.IGNORECASE)
        if m:
            return ScriptCall("notification", {"title": "GRIP", "body": m.group(1)})

        # /mac speak <text> → speak text=<text>
        m = re.match(r"speak\s+(.+)", args_text, re.IGNORECASE)
        if m:
            return ScriptCall("speak", {"text": m.group(1)})

        # /mac watch <url> → composite macro: caffeinate + wake + open stream fullscreen.
        # Use original-case args_text (URLs are case-sensitive in path/query).
        m = re.match(r"watch\s+(\S+)", args_text, re.IGNORECASE)
        if m:
            return MacroCall(name="watch", params={"url": m.group(1)})

        first_word = lower.split()[0] if lower.split() else ""

        # Named composite macros take priority over individual aliases.
        if first_word in MACRO_NAMES:
            return MacroCall(name=first_word)

        script = MAC_ALIASES.get(first_word)
        if script:
            return ScriptCall(script)

        # Fall through: treat as bare OSA script name
        return self._parse_osa_args(args_text)

    def _dispatch(self, call: ScriptCall, max_tier: int) -> str:
        if not self._hal.exists():
            raise FileNotFoundError(f"HAL OSA binary not found at {self._hal}")

        tier = 1 if call.script_name in TIER1_SCRIPTS else 2

        if tier > max_tier:
            raise TierViolation(call.script_name, tier)

        env = self._hal_env()

        cmd = [str(self._hal), "run", call.script_name]
        for k, v in call.params.items():
            cmd += ["--param", f"{k}={v}"]

        if tier == 2:
            token = subprocess.run(
                [str(self._hal), "mint", call.script_name, "--ttl", "60"],
                capture_output=True, text=True, check=True, env=env,
            ).stdout.strip()
            cmd += ["--token", token]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        return (result.stdout or "done").strip()

    def _hal_env(self) -> dict[str, str]:
        """Current env extended with HAL_OSA_CAPABILITY_SECRET from keychain."""
        env = dict(os.environ)
        if "HAL_OSA_CAPABILITY_SECRET" not in env:
            try:
                secret = subprocess.run(
                    ["security", "find-generic-password",
                     "-s", "grip-hal-osa-capability-secret", "-w"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                env["HAL_OSA_CAPABILITY_SECRET"] = secret
            except subprocess.CalledProcessError:
                pass  # let HAL raise MissingSecret with its own message
        return env

    def _list_scripts(self) -> str:
        if not self._hal.exists():
            return "HAL not installed — cannot list scripts."
        try:
            result = subprocess.run(
                [str(self._hal), "list", "--tier-max", "2"],
                capture_output=True, text=True, check=True,
            )
            names = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            return "Permitted scripts:\n" + ", ".join(names)
        except Exception as e:
            return f"Could not list scripts: {e}"

    def _help_text(self) -> str:
        return (
            "Commands:\n"
            "/mac battery       → battery level\n"
            "/mac mute / unmute → toggle audio\n"
            "/mac volume <n>    → set volume (0–100)\n"
            "/mac play / pause  → music control\n"
            "/mac next / prev   → next / previous track\n"
            "/mac playing       → what's playing\n"
            "/mac lock          → lock screen\n"
            "/mac notify <text> → show notification\n"
            "/mac speak <text>  → speak text aloud\n"
            "/mac list          → all permitted scripts\n"
            "\nMacros:\n"
            "/mac morning  → boot ritual (caffeinate 8h + music + spoken greeting)\n"
            "/mac sprint   → sprint mode (caffeinate 2h + mute + focus notif)\n"
            "/mac meeting  → meeting prep (pause music + screen wake + vol report)\n"
            "/mac wrap     → EOD (pause music + notif + lock)\n"
            "/mac status   → snapshot (battery + music + frontmost app)\n"
            "/mac watch <url> → open your OWN stream fullscreen (caffeinate 3h)"
        )

    # ------------------------------------------------------------------
    # Composite macro engine
    # ------------------------------------------------------------------

    def _dispatch_safe(self, call: ScriptCall, max_tier: int) -> str:
        """Dispatch a single ScriptCall, returning an error string on failure."""
        try:
            return self._dispatch(call, max_tier)
        except (TierViolation, subprocess.CalledProcessError, FileNotFoundError, Exception) as e:
            return f"[{call.script_name}: {e}]"

    def _run_macro(self, name: str, max_tier: int, params: dict[str, str] | None = None) -> str:
        handler = getattr(self, f"_macro_{name}", None)
        if handler is None:
            return f"Unknown macro: {name}. Try: {', '.join(sorted(MACRO_NAMES))}"
        return handler(max_tier, **(params or {}))

    def _macro_morning(self, max_tier: int) -> str:
        # V>> 2026-05-24: voice must precede music — previous order
        # (music_play → speak) drowned the greeting under playback.
        # `_dispatch_safe` → subprocess.run is blocking, so sequencing
        # speak BEFORE music_play guarantees the voice finishes in the
        # clear before background music starts. Notification fires last
        # so any system-ping doesn't clash with the spoken greeting.
        parts = []
        parts.append("caffeinate: " + self._dispatch_safe(ScriptCall("caffeinate_start", {"seconds": "28800"}), max_tier))
        self._dispatch_safe(ScriptCall("screen_wake"), max_tier)

        # Battery-aware greeting text (silent — just computes the string).
        battery_raw = self._dispatch_safe(ScriptCall("battery_get"), max_tier)
        m = re.search(r"(\d+)", battery_raw)
        pct = int(m.group(1)) if m else None
        if pct is not None and pct < 20:
            greeting = f"Warning — battery at {pct} percent. Plug in. Good morning Laurie."
            parts.append(f"battery: {pct}% — LOW, charge now")
        else:
            pct_str = f"{pct} percent" if pct is not None else "unknown"
            greeting = f"Good morning Laurie. Mac is at {pct_str}. GRIP caffeinated."
            parts.append(f"battery: {battery_raw.strip()}")

        # 1) Voice plays alone (blocking — finishes before next step).
        parts.append("speak: " + self._dispatch_safe(ScriptCall("speak", {"text": greeting}), max_tier))
        # 2) Music starts after the greeting completes.
        parts.append("music: " + self._dispatch_safe(ScriptCall("music_play"), max_tier))
        # 3) Notification fires last so any system-ping doesn't compete with voice.
        self._dispatch_safe(ScriptCall("notification", {"title": "GRIP Morning Boot", "body": "Greeted. Caffeinated 8h. Music playing."}), max_tier)

        return "Morning boot:\n" + "\n".join(parts)

    def _macro_sprint(self, max_tier: int) -> str:
        parts = []
        parts.append("caffeinate: " + self._dispatch_safe(ScriptCall("caffeinate_start", {"seconds": "7200"}), max_tier))
        parts.append("mute: " + self._dispatch_safe(ScriptCall("mute"), max_tier))
        self._dispatch_safe(ScriptCall("screen_wake"), max_tier)
        self._dispatch_safe(ScriptCall("notification", {"title": "Sprint Mode", "body": "2h caffeinated, muted, focused."}), max_tier)
        return "Sprint mode:\n" + "\n".join(parts)

    def _macro_meeting(self, max_tier: int) -> str:
        parts = []
        parts.append("music: " + self._dispatch_safe(ScriptCall("music_pause"), max_tier))
        self._dispatch_safe(ScriptCall("screen_wake"), max_tier)
        parts.append("volume: " + self._dispatch_safe(ScriptCall("volume_get"), max_tier))
        self._dispatch_safe(ScriptCall("notification", {"title": "Meeting Prep", "body": "Music paused, screen awake."}), max_tier)
        return "Meeting prep:\n" + "\n".join(parts)

    def _macro_wrap(self, max_tier: int) -> str:
        self._dispatch_safe(ScriptCall("music_pause"), max_tier)
        self._dispatch_safe(ScriptCall("notification", {"title": "EOD", "body": "Wrapping up. Screen locking."}), max_tier)
        self._dispatch_safe(ScriptCall("screen_lock"), max_tier)
        return "Wrapped up. Music paused. Screen locked."

    def _macro_status(self, max_tier: int) -> str:
        battery = self._dispatch_safe(ScriptCall("battery_get"), max_tier)
        playing = self._dispatch_safe(ScriptCall("music_now_playing"), max_tier)
        app = self._dispatch_safe(ScriptCall("frontmost_app_get"), max_tier)
        return f"Battery: {battery.strip()}\nMusic: {playing.strip()}\nApp: {app.strip()}"

    def _macro_watch(self, max_tier: int, url: str | None = None) -> str:
        # /watch-with-me — open the operator's OWN legitimate stream URL fullscreen
        # on this Mac. GRIP orchestrates DISPLAY only; it never sources, scrapes, or
        # circumvents content. caffeinate 3h so the match never sleeps the screen
        # mid-play. The url param is regex-validated in the OSA registry (https only,
        # no quote/space chars) before any AppleScript runs.
        if not url:
            return "Usage: /mac watch <https-url> — opens your own stream fullscreen on this Mac."
        parts = []
        parts.append("caffeinate: " + self._dispatch_safe(
            ScriptCall("caffeinate_start", {"seconds": "10800"}), max_tier))
        self._dispatch_safe(ScriptCall("screen_wake"), max_tier)
        parts.append("open: " + self._dispatch_safe(
            ScriptCall("open_url_fullscreen", {"url": url}), max_tier))
        self._dispatch_safe(ScriptCall("notification",
            {"title": "GRIP Watch", "body": "Stream opening fullscreen."}), max_tier)
        return "Watch mode (your stream, fullscreen):\n" + "\n".join(parts)

    def _audit(self, jid: str, raw_text: str, script: str, reply: str) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "jid": jid,
            "cmd": raw_text,
            "script": script,
            "reply": reply[:200],
        }
        try:
            with self._log.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
