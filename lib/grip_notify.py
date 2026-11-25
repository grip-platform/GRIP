"""GRIP cross-device push notifications.

Composable multi-backend notifier for delivering notifications to
locations that matter to the operator. Mirrors the FindMyReader
template-method pattern from lib/findmy_reader.py:

  - Base `Backend` class with three hooks (`_probe`, `_permission_needed`,
    `_do_send`) and a template method `send()` that wraps the common
    availability check.
  - Concrete backends: MacOSLocalBackend (osascript display notification),
    IMessageBackend (Messages.app AppleScript to a configured handle),
    SlackBotBackend (direct curl to Slack Web API using a keychain token).
  - A `GripNotifier` facade that fans out a single Notification to every
    enabled backend and returns per-backend DeliveryResult objects.

Cross-device delivery to iPhone works via:
  1. IMessageBackend addressed to the operator's own Apple ID email or
     phone number — iMessage routes through APNs and the iPhone signed
     into the same Apple ID raises a native Messages notification.
  2. SlackBotBackend posting to the operator's Slack DM in a workspace
     where they have Slack mobile installed; Slack's own push
     infrastructure then delivers to the phone.

Config lives at `docs/restricted/notify-targets.json` (git-crypted) so
personal addresses, phone numbers, and Slack channel IDs never appear
in plaintext commits. See `skills/grip-notify/SKILL.md` for the full
documentation.

Privacy: nothing leaves the Mac except the notification content itself
and, for Slack/iMessage, routes through Apple's or Slack's own servers.
No third-party push services, no analytics, no logging beyond macOS
unified log and local JSON state.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable, List, Optional

from lib.handoff_chime import CHIME_WAV, ChimeSpec, play_wav, write_wav

__all__ = [
    "Notification",
    "DeliveryResult",
    "NotifyUnavailable",
    "PermissionNeeded",
    "Backend",
    "MacOSLocalBackend",
    "IMessageBackend",
    "SlackBotBackend",
    "SlackTransportBackend",
    "WhatsAppTransportBackend",
    "EmailTransportBackend",
    "GripNotifier",
    "load_config",
    "default_notifier",
    "build_ladder",
    "notify",
    "notify_sprint_completion",
    "PRIORITY_LOW",
    "PRIORITY_NORMAL",
    "PRIORITY_HIGH",
    "PRIORITY_URGENT",
    "MODE_FIRST_SUCCESS",
    "MODE_FAN_OUT",
    "DEFAULT_LADDER",
    "NOTIFY_MAX_MESSAGE_LEN",
]

PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_URGENT = "urgent"

# Delivery modes for the multi-shim ladder (GripNotifier.send(mode=...)).
#   MODE_FIRST_SUCCESS — walk the ladder in order, STOP at the first channel
#                        that delivers. The resilient default for the ladder
#                        API: a down channel (WhatsApp offline) is skipped and
#                        the next rung fires until one succeeds.
#   MODE_FAN_OUT       — attempt EVERY channel regardless of earlier success.
#                        This is the historical GripNotifier.send() behaviour
#                        and remains the default when no mode is passed, so
#                        existing callers are byte-for-byte unchanged.
MODE_FIRST_SUCCESS = "first-success"
MODE_FAN_OUT = "fan-out"

# Canonical resilient ladder order, most-likely-reachable first, with the
# always-available macOS local banner as the last resort. Channel names map to
# backend builders in build_ladder(); unknown / unconfigured names are skipped.
DEFAULT_LADDER = ("slack", "discord", "whatsapp", "email", "macos")

# Message-length clamp for module-level notify() convenience function — mirrors
# macOS Notification Centre and Slack mobile push truncation behaviour. Two
# hundred characters is a safe payload across iMessage, Slack push, and APNs
# body fields. Class-based GripNotifier.send() is NOT clamped (advanced callers
# control their own framing).
NOTIFY_MAX_MESSAGE_LEN = 200


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
DEFAULT_CONFIG_PATH = CLAUDE_DIR / "docs" / "restricted" / "notify-targets.json"


# ---------------------------------------------------------------------------
# Shared formatting helper (DRY — every backend assembles the same body shape)
# ---------------------------------------------------------------------------


def _assemble_lines(
    notif: "Notification",
    *,
    head: List[str],
    blank_after_subtitle: bool = False,
) -> str:
    """Assemble a notification into newline-joined text.

    Every text backend (iMessage, WhatsApp, email, Slack-markdown) builds
    the same shape: one or more head lines (title, optionally decorated),
    an optional subtitle, a blank separator, the body, then an optional
    trailing blank + url. This helper is the single source of that shape
    so the formatters don't each re-implement it (DRY).

    Args:
        notif: The notification to render.
        head: Pre-formatted leading lines (e.g. ``[title]`` or
            ``[f"*{title}*"]``). The subtitle is appended after these.
        blank_after_subtitle: When True (email), emit a blank line right
            after the subtitle instead of treating subtitle as part of the
            header block. Keeps the email's subject/preamble layout.

    Returns:
        The newline-joined message text.
    """
    lines = list(head)
    if notif.subtitle:
        lines.append(notif.subtitle)
        if blank_after_subtitle:
            lines.append("")
    if not blank_after_subtitle:
        lines.append("")
    lines.append(notif.body)
    if notif.url:
        lines.append("")
        lines.append(notif.url)
    return "\n".join(lines)


def _format_slack_markdown(notif: "Notification") -> str:
    """Render a notification as Slack mrkdwn (bold title, italic subtitle).

    Module-level so BOTH Slack backends — the legacy ``SlackBotBackend``
    and the shim-backed ``SlackTransportBackend`` — share one source of the
    Slack message shape, without one reaching into the other's privates
    (AGORA #3313 concern: hidden cross-class coupling).
    """
    head = [f"*{notif.title}*"]
    if notif.subtitle:
        head.append(f"_{notif.subtitle}_")
    # Suppress the helper's plain subtitle line — Slack already rendered the
    # italicised version in `head`.
    return _assemble_lines(replace(notif, subtitle=None), head=head)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NotifyUnavailable(RuntimeError):
    """Raised when a backend cannot deliver (configuration, transport, etc)."""


class PermissionNeeded(NotifyUnavailable):
    """A specific backend is blocked by a missing grant or credential.

    Carries the backend name and the human-readable remediation path so
    operators get a clear next step rather than a cryptic error.
    """

    def __init__(self, backend: str, remedy: str, detail: str = ""):
        self.backend = backend
        self.remedy = remedy
        super().__init__(
            f"{backend}: {remedy}" + (f" ({detail})" if detail else "")
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Notification:
    """A message to deliver via one or more backends.

    `title` is the top line, `subtitle` is an optional second line (macOS
    only uses it for display), `body` is the main content, `url` is an
    optional tappable link on mobile backends, and `priority` is an
    advisory hint for backends that support it (Slack uses it to choose
    between broadcast and direct message tone).
    """

    title: str
    body: str
    subtitle: Optional[str] = None
    url: Optional[str] = None
    priority: str = "normal"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeliveryResult:
    """Per-backend outcome for one send call."""

    backend: str
    ok: bool
    error: Optional[str] = None
    detail: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Base Backend
# ---------------------------------------------------------------------------


class Backend:
    """Template-method base class for all notification backends.

    Subclasses override three hooks:
      _probe()              -> bool  : is this backend operational right now?
      _permission_needed()  -> PermissionNeeded : what the operator must fix
      _do_send(notif)       -> str  : backend-specific delivery; returns
                                      a detail string for the DeliveryResult
                                      or raises NotifyUnavailable on failure.

    The public `send()` wraps the common availability check and error
    handling so every backend reports results in a uniform shape.
    """

    name: str = "abstract"

    def is_available(self) -> bool:
        try:
            return self._probe()
        except Exception:  # noqa: BLE001 — probe failures are non-fatal
            return False

    def send(self, notif: Notification) -> DeliveryResult:
        if not self.is_available():
            perm = self._permission_needed()
            return DeliveryResult(
                backend=self.name, ok=False, error=str(perm)
            )
        try:
            detail = self._do_send(notif)
            return DeliveryResult(backend=self.name, ok=True, detail=detail)
        except NotifyUnavailable as e:
            return DeliveryResult(backend=self.name, ok=False, error=str(e))
        except Exception as e:  # noqa: BLE001 — convert to result
            return DeliveryResult(
                backend=self.name, ok=False, error=f"{type(e).__name__}: {e}"
            )

    def _probe(self) -> bool:
        raise NotImplementedError

    def _permission_needed(self) -> PermissionNeeded:
        raise NotImplementedError

    def _do_send(self, notif: Notification) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Backend 1: macOS local notification via osascript
# ---------------------------------------------------------------------------


class MacOSLocalBackend(Backend):
    """Display a local macOS notification via `osascript display notification`.

    Always available on macOS. Not cross-device — this raises a banner
    on the Mac only. Useful as a fallback and as the canonical "this
    machine saw it" receipt.
    """

    name = "macos_local"

    def _probe(self) -> bool:
        return sys.platform == "darwin" and shutil.which("osascript") is not None

    def _permission_needed(self) -> PermissionNeeded:
        return PermissionNeeded(
            backend=self.name,
            remedy="macOS local notifications require osascript",
            detail="not on darwin or osascript missing",
        )

    def _do_send(self, notif: Notification) -> str:
        # Preferred path: HAL osa registry handles both title+body and
        # title+body+subtitle. Falls back to direct osascript only when
        # HAL is completely unavailable (binary missing, auth unset).
        from lib.hal_osa import notify as hal_notify
        if hal_notify(notif.title, notif.body, subtitle=notif.subtitle):
            return "delivered to Notification Center (via hal osa)"

        parts = [f'display notification {self._q(notif.body)}']
        parts.append(f'with title {self._q(notif.title)}')
        if notif.subtitle:
            parts.append(f'subtitle {self._q(notif.subtitle)}')
        script = " ".join(parts)
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise NotifyUnavailable(f"osascript failed: {r.stderr.strip()}")
        return "delivered to Notification Center"

    @staticmethod
    def _q(s: str) -> str:
        """AppleScript string literal with proper escaping."""
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Backend 2: iMessage self-send via Messages.app
# ---------------------------------------------------------------------------


class IMessageBackend(Backend):
    """Send iMessage from the Mac to a configured handle.

    When the handle is the operator's own Apple ID email or phone
    number, iMessage routes through APNs to every device signed into
    that Apple ID, including iPhone. This is the closest native-Apple
    path to cross-device push notifications without writing an iOS app.

    The handle is configured per-operator in the git-crypted
    notify-targets.json file; this backend never embeds personal data
    in source.
    """

    name = "imessage"

    def __init__(self, handle: Optional[str] = None) -> None:
        self._handle = handle

    @property
    def handle(self) -> Optional[str]:
        return self._handle

    def _probe(self) -> bool:
        if sys.platform != "darwin":
            return False
        if not self._handle:
            return False
        if shutil.which("osascript") is None:
            return False
        return True

    def _permission_needed(self) -> PermissionNeeded:
        if sys.platform != "darwin":
            detail = "iMessage backend requires macOS"
        elif not self._handle:
            detail = "no iMessage handle configured (set in notify-targets.json)"
        else:
            detail = "osascript missing"
        return PermissionNeeded(
            backend=self.name,
            remedy="configure iMessage handle and Messages.app accessibility",
            detail=detail,
        )

    def _do_send(self, notif: Notification) -> str:
        text = self._format_text(notif)
        script = (
            'tell application "Messages"\n'
            '  set iMessageService to 1st service whose service type = iMessage\n'
            f'  set targetBuddy to buddy {MacOSLocalBackend._q(self._handle)} of iMessageService\n'
            f'  send {MacOSLocalBackend._q(text)} to targetBuddy\n'
            'end tell\n'
        )
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            raise NotifyUnavailable(
                f"Messages.app AppleScript failed: {r.stderr.strip()}"
            )
        return f"iMessage dispatched to {self._handle}"

    @staticmethod
    def _format_text(notif: Notification) -> str:
        return _assemble_lines(notif, head=[notif.title])


# ---------------------------------------------------------------------------
# Backend 3: Slack Web API direct (bot token from macOS keychain)
# ---------------------------------------------------------------------------


class SlackBotBackend(Backend):
    """Post a notification as a Slack DM via the Slack Web API.

    Uses a bot token resolved from the macOS keychain rather than an
    MCP server, because the Claude MCP Slack integration is typically
    authenticated to a different workspace than the operator's GRIP
    workspace. This backend takes the workspace's bot token from a
    keychain service name (`grip-slack-bot` by default) and posts to
    a channel/DM ID configured per-operator.

    When the operator has Slack mobile installed and signed in, Slack's
    own push infrastructure delivers the message to their phone as a
    Slack notification. Not as on-brand as iMessage but zero Apple-ID
    dependencies.
    """

    name = "slack_bot"

    def __init__(
        self,
        channel_id: Optional[str] = None,
        keychain_service: str = "grip-slack-bot",
        user_id: Optional[str] = None,
    ) -> None:
        self._channel_id = channel_id
        self._keychain_service = keychain_service
        # When user_id is set, _resolve_channel() calls conversations.open at
        # first send and caches the returned DM channel id for the lifetime
        # of the instance — so DM rotations (#1377) don't recur silently.
        self._user_id = user_id
        self._resolved_channel_id: Optional[str] = None

    def _probe(self) -> bool:
        if not self._channel_id and not self._user_id:
            return False
        if shutil.which("security") is None:
            return False
        return self._keychain_token() is not None

    def _permission_needed(self) -> PermissionNeeded:
        if not self._channel_id and not self._user_id:
            detail = "no slack channel_id or user_id configured (set in notify-targets.json)"
        elif shutil.which("security") is None:
            detail = "macOS `security` binary missing (non-macOS host?)"
        else:
            detail = (
                f"no bot token in keychain service '{self._keychain_service}' — "
                "add via `security add-generic-password -s "
                f"{self._keychain_service} -a <user> -w xoxb-...`"
            )
        return PermissionNeeded(
            backend=self.name,
            remedy="configure Slack bot token + channel",
            detail=detail,
        )

    def _keychain_token(self) -> Optional[str]:
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s",
                 self._keychain_service, "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        token = r.stdout.strip()
        return token if token.startswith("xoxb-") or token.startswith("xoxp-") else None

    def _resolve_channel(self, token: str) -> str:
        """Return the DM channel ID, resolving from user_id if needed.

        Cached after first call. When user_id is set, calls Slack
        conversations.open to get the live DM channel — this is durable
        against DM rotations (Slack DM IDs are NOT stable; channel_id
        gets stale on archive/reopen). When only channel_id is set,
        returns it directly (back-compat).
        """
        if self._resolved_channel_id:
            return self._resolved_channel_id
        if self._user_id:
            payload = json.dumps({"users": self._user_id}).encode("utf-8")
            req = urllib.request.Request(
                "https://slack.com/api/conversations.open",
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                raise NotifyUnavailable(f"conversations.open failed: {e}") from e
            if not body.get("ok"):
                raise NotifyUnavailable(
                    f"conversations.open returned: {body.get('error', 'unknown')}"
                )
            self._resolved_channel_id = body["channel"]["id"]
        else:
            self._resolved_channel_id = self._channel_id  # type: ignore[assignment]
        return self._resolved_channel_id  # type: ignore[return-value]

    def _do_send(self, notif: Notification) -> str:
        token = self._keychain_token()
        if token is None:
            raise NotifyUnavailable("keychain token disappeared between probe and send")
        channel = self._resolve_channel(token)
        text = self._format_markdown(notif)
        payload = json.dumps({
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise NotifyUnavailable(f"slack api call failed: {e}") from e
        if not body.get("ok"):
            raise NotifyUnavailable(f"slack api returned: {body.get('error', 'unknown')}")
        ts = body.get("ts", "")
        return f"slack message ts={ts} channel={channel}"

    @staticmethod
    def _format_markdown(notif: Notification) -> str:
        # Thin delegate to the module-level formatter (shared with
        # SlackTransportBackend). Kept as a static method for back-compat
        # with callers/tests that reference SlackBotBackend._format_markdown.
        return _format_slack_markdown(notif)


# ---------------------------------------------------------------------------
# Backend 4: Slack via lib.slack_transport shim (workspace-aware)
# ---------------------------------------------------------------------------


class SlackTransportBackend(Backend):
    """Post a notification via the ``lib.slack_transport.post`` shim.

    This is the syscall-doctrine sibling of ``SlackBotBackend``. Where
    ``SlackBotBackend`` carries its own keychain + HTTP code (legacy),
    this backend ORCHESTRATES the canonical ``slack_transport`` shim,
    which resolves the workspace bot token (keychain → env) inside its
    own process scope. Use this when you want the workspace-dispatch
    behaviour (``codetonight`` or another configured workspace) and Block
    Kit support that the shim provides; ``grip_notify`` never re-implements
    Slack here.
    """

    name = "slack"

    def __init__(
        self,
        channel_id: Optional[str] = None,
        workspace: str = "codetonight",
    ) -> None:
        self._channel_id = channel_id
        self._workspace = workspace

    def _probe(self) -> bool:
        # Cheap structural probe — a missing channel is the only thing we can
        # check without paying for a token resolution. Token/network failures
        # surface as a failed DeliveryResult from _do_send, never block the
        # ladder (the post() shim returns {ok: False} rather than raising).
        return bool(self._channel_id)

    def _permission_needed(self) -> PermissionNeeded:
        return PermissionNeeded(
            backend=self.name,
            remedy="configure a Slack channel_id for the slack backend",
            detail="no slack channel_id configured (set in notify-targets.json)",
        )

    def _do_send(self, notif: Notification) -> str:
        from lib.slack_transport import post  # shim — orchestrated, not reimplemented
        text = _format_slack_markdown(notif)  # module-level — no cross-class coupling
        try:
            result = post(
                workspace=self._workspace,  # type: ignore[arg-type]
                channel_id=self._channel_id,  # type: ignore[arg-type]
                text=text,
            )
        except (ValueError, RuntimeError) as e:
            # Unknown workspace / no token configured — plain-language, no abort.
            raise NotifyUnavailable(f"slack transport: {e}") from e
        if not result.get("ok"):
            raise NotifyUnavailable(
                f"slack transport returned: {result.get('error', 'unknown')}"
            )
        return f"slack message ts={result.get('ts')} channel={result.get('channel')}"


# ---------------------------------------------------------------------------
# Backend 5: WhatsApp via lib.whatsapp_transport shim (grip-channel MCP)
# ---------------------------------------------------------------------------


class WhatsAppTransportBackend(Backend):
    """Send a notification over WhatsApp via the ``lib.whatsapp_transport`` shim.

    The shim performs the grip-channel MCP handshake to the local daemon
    (default :3101). When WhatsApp is DOWN — daemon offline, Baileys
    disconnected, IP-blocked — the shim returns ``{ok: False, error: ...}``
    rather than raising, so this backend reports a failed DeliveryResult
    and the ladder continues to the next rung. That is the motivating
    resilience case: WhatsApp being unreachable must never block Slack,
    email, or the macOS banner.
    """

    name = "whatsapp"

    def __init__(self, recipient: Optional[str] = None) -> None:
        self._recipient = recipient

    def _probe(self) -> bool:
        # A JID is the only cheap precondition. Daemon reachability is NOT
        # probed here — that would cost an MCP round-trip on every status
        # check; an offline daemon surfaces as a failed _do_send instead.
        return bool(self._recipient) and "@" in (self._recipient or "")

    def _permission_needed(self) -> PermissionNeeded:
        return PermissionNeeded(
            backend=self.name,
            remedy="configure a WhatsApp recipient JID for the whatsapp backend",
            detail="no recipient JID configured (set in notify-targets.json)",
        )

    def _do_send(self, notif: Notification) -> str:
        from lib.wa_delivery_verify import post_verified as post  # verified shim — orchestrated, not reimplemented
        text = self._format_text(notif)
        try:
            result = post(recipient=self._recipient, text=text)  # type: ignore[arg-type]
        except ValueError as e:
            raise NotifyUnavailable(f"whatsapp transport: {e}") from e
        if not result.get("ok"):
            raise NotifyUnavailable(
                f"whatsapp transport returned: {result.get('error', 'unknown')}"
            )
        return f"whatsapp message id={result.get('message_id')} to {result.get('recipient')}"

    @staticmethod
    def _format_text(notif: Notification) -> str:
        # GRIP WA self-identification prefix is PARAMOUNT (operator rule):
        # every GRIP-generated WhatsApp message opens with a GRIP identity.
        return _assemble_lines(notif, head=[f"GRIP here — {notif.title}"])


# ---------------------------------------------------------------------------
# Backend 6: Email via lib.agentmail shim
# ---------------------------------------------------------------------------


class EmailTransportBackend(Backend):
    """Send a notification as an email via the ``lib.agentmail`` shim.

    Orchestrates ``agentmail.send_message`` from the configured outbound
    inbox to the operator's address. The shim hides API-key retrieval in
    its own scope and raises ``AgentMailError`` on any failure; this
    backend converts that to a failed DeliveryResult so the ladder is
    never aborted by an email outage.

    Defaults: ``inbox_id`` falls back to the proven-deliverable
    ``grip-trial-out@agentmail.to`` outbound inbox when not configured.
    """

    name = "email"
    _DEFAULT_INBOX = "grip-trial-out@agentmail.to"

    def __init__(
        self,
        to: Optional[str] = None,
        inbox_id: Optional[str] = None,
    ) -> None:
        self._to = to
        self._inbox_id = inbox_id or self._DEFAULT_INBOX

    def _probe(self) -> bool:
        # Recipient is the only cheap precondition. API-key presence is NOT
        # probed (it would require a keychain read on every status call);
        # a missing key surfaces as a failed _do_send.
        return bool(self._to)

    def _permission_needed(self) -> PermissionNeeded:
        return PermissionNeeded(
            backend=self.name,
            remedy="configure a recipient address for the email backend",
            detail="no email 'to' address configured (set in notify-targets.json)",
        )

    def _do_send(self, notif: Notification) -> str:
        from lib.agentmail import send_message, AgentMailError  # shim — orchestrated
        subject, text = self._format_email(notif)
        try:
            result = send_message(
                self._inbox_id,
                to=[self._to],  # type: ignore[list-item]
                subject=subject,
                text=text,
            )
        except AgentMailError as e:
            raise NotifyUnavailable(f"email transport: {e}") from e
        msg_id = result.get("message_id") or result.get("id") or "sent"
        return f"email id={msg_id} to {self._to}"

    @staticmethod
    def _format_email(notif: Notification) -> tuple:
        # title -> subject; body block has no title line. subtitle (if any)
        # becomes a preamble line followed by a blank separator.
        subject = notif.title
        body = _assemble_lines(notif, head=[], blank_after_subtitle=True)
        return subject, body


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


_chime_synthesised: bool = False  # in-process cache — reuse on subsequent urgent sends


def _play_urgent_chime_if_needed(notif: Notification) -> None:
    """Play the bell once when priority is 'urgent'. Best-effort: never raises."""
    global _chime_synthesised
    if notif.priority != PRIORITY_URGENT:
        return
    try:
        if not _chime_synthesised:
            write_wav(CHIME_WAV, ChimeSpec())
            _chime_synthesised = True
        play_wav(CHIME_WAV)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"grip-notify: chime failed (non-fatal): {exc}\n")


class GripNotifier:
    """Route a notification through an ordered ladder of backends.

    Two delivery modes (see MODE_* constants):

      - ``MODE_FAN_OUT`` (default) — attempt every backend; each returns a
        DeliveryResult independently and one backend failing does not
        prevent others from firing. This is the historical behaviour, so
        ``send(notif)`` with no ``mode`` is byte-for-byte unchanged.
      - ``MODE_FIRST_SUCCESS`` — walk the ladder in order and STOP at the
        first backend that delivers. A down channel raises/returns a
        failure, which is recorded and the ladder simply continues to the
        next rung. This is the resilient default for the ladder API: a
        notification reaches the operator via the first reachable channel.

    In BOTH modes a single backend raising is caught (``Backend.send``
    never raises — it converts errors to a failed DeliveryResult), so one
    unreachable transport can never abort delivery through the others.
    """

    def __init__(self, backends: Iterable[Backend]) -> None:
        self._backends: List[Backend] = list(backends)

    def send(
        self, notif: Notification, mode: str = MODE_FAN_OUT
    ) -> List[DeliveryResult]:
        if mode == MODE_FIRST_SUCCESS:
            results = self._send_first_success(notif)
        else:
            results = [b.send(notif) for b in self._backends]
        # Ring the urgent chime only AFTER a delivery actually succeeded.
        # Ringing before the ladder runs (or when nothing was delivered)
        # would alert the operator to a notification that never arrived —
        # in first-success, the chime would fire even if the first channel
        # then failed and nothing was sent (AGORA #3313 concern).
        if any(r.ok for r in results):
            _play_urgent_chime_if_needed(notif)
        return results

    def _send_first_success(self, notif: Notification) -> List[DeliveryResult]:
        """Walk the ladder in order; stop at the first OK delivery.

        Returns every result attempted up to and including the first
        success (so callers see which channels were tried and which one
        won). If no backend succeeds, returns the full attempt list.
        """
        results: List[DeliveryResult] = []
        for backend in self._backends:
            result = backend.send(notif)  # never raises — see Backend.send
            results.append(result)
            if result.ok:
                break
        return results

    def status(self) -> dict:
        return {b.name: b.is_available() for b in self._backends}


# ---------------------------------------------------------------------------
# Config + default notifier assembly
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> dict:
    """Load notify-targets config from git-crypted JSON (best effort).

    Returns an empty dict if the file is missing or unreadable. Never
    raises on missing config — the default notifier degrades gracefully
    to macOS-local only.
    """
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def default_notifier(
    config: Optional[dict] = None,
    force_slack: bool = False,
) -> GripNotifier:
    """Build a GripNotifier from the default config file.

    Structure of notify-targets.json:

        {
          "backends": {
            "macos_local": {"enabled": true},
            "imessage":    {"enabled": true, "handle": "laurie@example.com"},
            "slack_bot":   {"enabled": true, "channel_id": "D...",
                            "keychain_service": "grip-slack-bot"}
          }
        }

    Each backend entry is optional. Missing entries are treated as
    disabled. If the file is missing entirely, only macos_local is
    wired up.
    """
    cfg = config if config is not None else load_config()
    backend_cfg = cfg.get("backends", {}) if isinstance(cfg, dict) else {}
    backends: List[Backend] = []

    local_cfg = backend_cfg.get("macos_local", {"enabled": True})
    if local_cfg.get("enabled", True):
        backends.append(MacOSLocalBackend())

    imsg_cfg = backend_cfg.get("imessage", {})
    if imsg_cfg.get("enabled") and imsg_cfg.get("handle"):
        backends.append(IMessageBackend(handle=imsg_cfg["handle"]))

    slack_cfg = backend_cfg.get("slack_bot", {})
    slack_enabled = slack_cfg.get("enabled") or force_slack
    slack_channel_id = slack_cfg.get("channel_id")
    slack_user_id = slack_cfg.get("user_id")
    if slack_enabled and (slack_channel_id or slack_user_id):
        backends.append(SlackBotBackend(
            channel_id=slack_channel_id,
            keychain_service=slack_cfg.get("keychain_service", "grip-slack-bot"),
            user_id=slack_user_id,
        ))

    return GripNotifier(backends)


# ---------------------------------------------------------------------------
# Multi-shim ladder assembly
# ---------------------------------------------------------------------------


def _build_slack_transport(backend_cfg: dict) -> Optional[Backend]:
    cfg = backend_cfg.get("slack", {})
    channel_id = cfg.get("channel_id")
    if not channel_id:
        return None
    return SlackTransportBackend(
        channel_id=channel_id,
        workspace=cfg.get("workspace", "codetonight"),
    )


def _build_whatsapp(backend_cfg: dict) -> Optional[Backend]:
    cfg = backend_cfg.get("whatsapp", {})
    recipient = cfg.get("recipient")
    if not recipient:
        return None
    return WhatsAppTransportBackend(recipient=recipient)


def _build_email(backend_cfg: dict) -> Optional[Backend]:
    cfg = backend_cfg.get("email", {})
    to = cfg.get("to")
    if not to:
        return None
    return EmailTransportBackend(to=to, inbox_id=cfg.get("inbox_id"))


def _build_macos(_backend_cfg: dict) -> Optional[Backend]:
    # Always-available last resort. No config required.
    return MacOSLocalBackend()


def _build_discord(_backend_cfg: dict) -> Optional[Backend]:
    # KNOWN channel, no backend YET. Discord has no synchronous transport
    # shim (the channel adapters in lib/channel/adapters/discord.py are async
    # MCP-bridge internals, not a stable sync post() shim like
    # slack_transport / whatsapp_transport). Until such a shim exists,
    # 'discord' is a recognised ladder slot that resolves to no backend, so
    # it is explicitly listed here (not silently absent — AGORA #3313) and
    # reserved for the future shim. Returning None makes build_ladder skip it
    # exactly like an unconfigured channel.
    return None


# Channel-name -> backend builder. A builder returns None when the channel is
# known but yields no backend (unconfigured, or no shim yet). 'discord' is
# listed explicitly so it is a recognised-but-pending slot rather than an
# unknown name; truly unknown names (not in this map) are also skipped.
_LADDER_BUILDERS = {
    "slack": _build_slack_transport,
    "discord": _build_discord,
    "whatsapp": _build_whatsapp,
    "email": _build_email,
    "macos": _build_macos,
}


def build_ladder(
    channels: Optional[Iterable[str]] = None,
    config: Optional[dict] = None,
) -> GripNotifier:
    """Build a GripNotifier from an ordered list of channel names.

    Each channel name maps to a transport-shim-backed Backend (Slack via
    ``slack_transport``, WhatsApp via ``whatsapp_transport``, email via
    ``agentmail``, macOS via osascript). The RELATIVE order of the channels
    that resolve to a backend is preserved exactly — so a
    ``MODE_FIRST_SUCCESS`` send tries them in the given sequence.

    The returned ladder may have FEWER backends than ``len(channels)``:
    channels that are unknown (no shim — e.g. ``discord``, which is accepted
    as a name but has no sync transport shim yet) or unconfigured (no
    recipient/channel in ``config``) are SKIPPED, so the ladder degrades
    gracefully rather than erroring. The surviving backends keep their
    relative order. ``macos`` requires no config and is the always-available
    last resort.

    Args:
        channels: Ordered channel names. Defaults to ``DEFAULT_LADDER``
            (``slack, discord, whatsapp, email, macos``).
        config: Optional config dict (defaults to on-disk notify-targets).
            Per-channel identifiers live under ``config["backends"][name]``
            — e.g. ``{"backends": {"whatsapp": {"recipient": "27...@s.whatsapp.net"}}}``.

    Returns:
        A GripNotifier whose backend list is the resolved, in-order ladder
        (only the channels that mapped to a configured, known backend).
    """
    names = list(channels) if channels is not None else list(DEFAULT_LADDER)
    cfg = config if config is not None else load_config()
    backend_cfg = cfg.get("backends", {}) if isinstance(cfg, dict) else {}

    backends: List[Backend] = []
    for name in names:
        builder = _LADDER_BUILDERS.get(name)
        if builder is None:
            continue  # truly unknown channel name — skip
        backend = builder(backend_cfg)
        if backend is not None:
            backends.append(backend)
        # else: known channel but no backend (unconfigured, or no shim yet
        # like 'discord') — skipped, surviving channels keep their order.

    return GripNotifier(backends)


# ---------------------------------------------------------------------------
# Module-level convenience shim
# ---------------------------------------------------------------------------


def notify(
    message: str,
    *,
    title: str = "GRIP",
    status: str = "proactive",
    subtitle: Optional[str] = None,
    url: Optional[str] = None,
    priority: str = PRIORITY_NORMAL,
    config: Optional[dict] = None,
    force_slack: bool = False,
    channels: Optional[Iterable[str]] = None,
    mode: Optional[str] = None,
) -> bool:
    """One-shot push notification — convenience over `default_notifier().send()`.

    Pythonic convenience pattern (cf. `re.compile()` + `re.match()`,
    `logging.getLogger()` + `logging.info()`): the class `GripNotifier` is the
    advanced/reusable API; this function is the one-line dispatch path used by
    spawned agents, scripts, and inline hooks where setting up a long-lived
    notifier object is friction.

    Two dispatch paths, selected by the ``channels`` argument:

      - ``channels=None`` (default, BACKWARD-COMPATIBLE): uses
        ``default_notifier()`` exactly as before — the existing
        macOS/iMessage/SlackBot backends, fan-out delivery. Existing
        callers see byte-for-byte identical behaviour. The ``mode``
        argument is ignored on this path (fan-out is implicit).
      - ``channels=[...]``: builds a multi-shim ladder via
        ``build_ladder(channels)`` and delivers through it using ``mode``
        (default ``MODE_FIRST_SUCCESS`` — stop at the first reachable
        channel). This is the resilience path: e.g.
        ``notify("x", channels=["slack","whatsapp","email","macos"])``
        reaches the operator even when WhatsApp is down.

    Args:
        message: The notification body. Clamped to NOTIFY_MAX_MESSAGE_LEN
            (200 chars) — anything longer is truncated with a trailing "...".
            Class-based callers (`GripNotifier(...).send(...)`) are NOT
            clamped; this clamp is specific to the convenience surface.
        title: Top line of the notification. Defaults to "GRIP".
        status: Advisory tag for callers that classify notifications by
            source class (e.g. "proactive", "reactive", "alert"). Currently
            informational only — recorded in the dispatched message body
            when no explicit subtitle is supplied. Defaults to "proactive".
        subtitle: Optional second line. If unset and `status` is provided,
            `status` is used as the subtitle.
        url: Optional tappable link surfaced by mobile backends.
        priority: One of PRIORITY_LOW / PRIORITY_NORMAL / PRIORITY_HIGH /
            PRIORITY_URGENT. URGENT also fires the handoff chime.
        config: Optional config dict override (mostly for tests). Defaults
            to on-disk `notify-targets.json`.
        force_slack: Force the Slack backend on even if config disables it.
            Only affects the ``channels=None`` (default_notifier) path.
        channels: Optional ordered channel names for the multi-shim ladder
            (e.g. ``["slack", "whatsapp", "email", "macos"]``). When set,
            the ladder path is used instead of default_notifier.
        mode: Delivery mode — ``MODE_FIRST_SUCCESS`` (stop at the first
            reachable channel) or ``MODE_FAN_OUT`` (try all). Applies to
            BOTH paths. Default is ``None`` ("auto"), which resolves to
            ``MODE_FAN_OUT`` for the ``channels=None`` path (byte-for-byte
            backward-compatible) and ``MODE_FIRST_SUCCESS`` for the ladder
            path (resilient). An explicit value is honoured on either path.

    Returns:
        True if AT LEAST ONE backend successfully delivered the notification,
        False otherwise. Never raises — backend errors are caught and
        translated to False so callers can use this in `try`/`finally`
        cleanup paths and dispatched-agent terminal turns without guarding.

    Examples:
        >>> notify("Sprint W3 complete")  # one-shot, defaults (back-compat)
        True
        >>> notify("PR #2891 merged", priority=PRIORITY_HIGH, url="https://...")
        True
        >>> # Resilient ladder — survives WhatsApp being down:
        >>> notify("Build failed", channels=["slack", "whatsapp", "macos"])
        True
    """
    try:
        # Clamp message — keep last 3 chars for "..." marker, never drop the
        # truncation signal silently.
        clamped = message
        if len(message) > NOTIFY_MAX_MESSAGE_LEN:
            clamped = message[: NOTIFY_MAX_MESSAGE_LEN - 3] + "..."

        # Default subtitle to status when caller did not supply one.
        effective_subtitle = subtitle if subtitle is not None else status

        notification = Notification(
            title=title,
            body=clamped,
            subtitle=effective_subtitle,
            url=url,
            priority=priority,
        )
        if channels is not None:
            # Multi-shim ladder path — resilient. mode=None auto-resolves to
            # first-success (the resilient default); explicit mode honoured.
            effective_mode = mode if mode is not None else MODE_FIRST_SUCCESS
            notifier = build_ladder(channels=channels, config=config)
            results = notifier.send(notification, mode=effective_mode)
        else:
            # Backward-compatible path. mode=None auto-resolves to fan-out
            # (the historical behaviour), so existing callers are unchanged;
            # an explicit mode is still honoured here so the parameter is
            # never silently ignored (AGORA #3313 concern).
            effective_mode = mode if mode is not None else MODE_FAN_OUT
            notifier = default_notifier(config=config, force_slack=force_slack)
            results = notifier.send(notification, mode=effective_mode)
        return any(r.ok for r in results)
    except Exception as exc:  # noqa: BLE001 — fail-safe for one-shot callers
        sys.stderr.write(f"grip-notify: notify() failed (non-fatal): {exc}\n")
        return False


# ---------------------------------------------------------------------------
# Sprint completion notification
# ---------------------------------------------------------------------------


def notify_sprint_completion(
    pr_url: Optional[str] = None,
    wave_count: int = 0,
    goal: Optional[str] = None,
    config: Optional[dict] = None,
) -> List[DeliveryResult]:
    """Fire a notification when an /rsi-optimal or /sprint run completes.

    Called after /auto-ship merges the final PR (per-wave or batched). Uses
    the default notifier so all configured backends (macOS, iMessage, Slack)
    receive the completion signal without any additional wiring.

    Args:
        pr_url:     URL of the merged PR, if available.
        wave_count: Number of Fibonacci waves completed.
        goal:       Short description of the sprint goal, if known.
        config:     Optional config dict for testing; defaults to on-disk config.

    Returns:
        List of DeliveryResult from each backend (empty list if no backends available).
    """
    subtitle_parts = []
    if wave_count:
        subtitle_parts.append(f"{wave_count} wave{'s' if wave_count != 1 else ''}")
    if goal:
        subtitle_parts.append(goal[:60])
    subtitle = " · ".join(subtitle_parts) or None

    body = "Sprint complete."
    if pr_url:
        body = f"Sprint complete. PR merged: {pr_url}"

    notif = Notification(
        title="GRIP Sprint Done",
        body=body,
        subtitle=subtitle,
        url=pr_url,
        priority="high",
    )
    notifier = default_notifier(config=config)
    return notifier.send(notif)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_status() -> int:
    notifier = default_notifier()
    print(json.dumps(notifier.status(), indent=2))
    return 0


# CLI send flags that take a string value. Parsed via a dispatch table to keep
# _cli_send's cyclomatic complexity low (extract-dispatch, cc-enforcement.md).
_CLI_VALUE_FLAGS = {"--title", "--body", "--subtitle", "--url", "--channels", "--mode"}


def _parse_send_args(argv: List[str]) -> Optional[dict]:
    """Parse `send` flags into an options dict. Returns None on a parse error.

    Value flags consume the next token; ``--slack`` is a bare boolean. An
    unknown flag or a value flag missing its argument is a parse error
    (caller maps None -> exit 2).
    """
    opts: dict = {
        "title": "GRIP", "body": "", "subtitle": None, "url": None,
        "force_slack": False, "channels": None, "mode": MODE_FIRST_SUCCESS,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--slack":
            opts["force_slack"] = True
            i += 1
            continue
        if a in _CLI_VALUE_FLAGS:
            if i + 1 >= len(argv):
                print(f"missing value for {a}", file=sys.stderr)
                return None
            value = argv[i + 1]
            if a == "--channels":
                opts["channels"] = [c.strip() for c in value.split(",") if c.strip()]
            else:
                opts[a[2:]] = value  # --title -> title, --mode -> mode, etc.
            i += 2
            continue
        print(f"unknown arg: {a}", file=sys.stderr)
        return None
    return opts


def _cli_send(argv: List[str]) -> int:
    opts = _parse_send_args(argv)
    if opts is None:
        return 2
    if not opts["body"]:
        print("body is required (--body)", file=sys.stderr)
        return 2
    notif = Notification(
        title=opts["title"], body=opts["body"],
        subtitle=opts["subtitle"], url=opts["url"],
    )
    if opts["channels"] is not None:
        notifier = build_ladder(channels=opts["channels"])
        results = notifier.send(notif, mode=opts["mode"])
    else:
        notifier = default_notifier(force_slack=opts["force_slack"])
        results = notifier.send(notif)
    print(json.dumps([r.to_dict() for r in results], indent=2))
    return 0 if any(r.ok for r in results) else 3


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("status", "probe"):
        return _cli_status()
    if args[0] == "send":
        return _cli_send(args[1:])
    print("usage: python3 -m lib.grip_notify [status | send --title X --body Y]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
