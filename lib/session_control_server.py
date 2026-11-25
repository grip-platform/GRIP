#!/usr/bin/env python3
"""
Session Control Server - Pair-mode monitoring infrastructure for GRIP.

Provides real-time session event streaming and steering for pair programming
across Claude Code sessions.

Architecture:
  - Unix socket server on /tmp/grip-session-{session_id}.sock
    Receives broadcast events from session-broadcast.py (PostToolUse hook)
  - WebSocket server on TCP port (optional, for remote monitors)
    Accepts monitor connections for event streaming and steering

Launched by: hooks/session-start.sh (auto-starts when multiple sessions exist)

Usage:
  python3 session-control-server.py <session_id> [--tcp-port PORT]

Protocol (server -> monitor):
  {"type": "event", "event_type": "Bash", "timestamp": "...", "data": {...}}
  {"type": "heartbeat", "timestamp": "...", "session_id": "..."}
  {"type": "steering_acquired", "monitor_id": "..."}
  {"type": "steering_denied", "holder": "..."}
  {"type": "steering_expiring", "remaining_seconds": N, "holder": "..."}
  {"type": "steering_expired", "holder": "...", "elapsed_seconds": N}
  {"type": "steering_grace", "grace_seconds": 30, "holder": "..."}
  {"type": "steering_released", "reason": "grace_expired"}
  {"type": "heartbeat_ack", "ttl_remaining": 300}
  {"type": "queued", "queue_size": N}
  {"type": "steer_ack", "command_type": "...", "path": "..."}
  {"type": "challenge", "nonce": "...", "identity": {...}}
  {"type": "auth_mutual_ok", "identity": {...}}
  {"type": "auth_mutual_failed", "reason": "..."}
  {"type": "mesh_hello", "session_id": "...", "capabilities": [...]}
  {"type": "mesh_response", "query_id": "...", "responder": "...", "results": [...], "latency_ms": N}
  {"type": "mesh_error", "query_id": "...", "reason": "..."}

Protocol (monitor -> server):
  {"type": "subscribe", "monitor_id": "...", "events": ["Bash", "Write"]}
  {"type": "acquire_steering", "monitor_id": "..."}
  {"type": "release_steering"}
  {"type": "heartbeat"}
  {"type": "challenge_response", "hmac": "...", "identity": {...}}
  {"type": "steer_command", "command_type": "inject_context", "text": "..."}
  {"type": "steer_configure", "permissions": {"allowed_tools": [...], "deny_patterns": [...]}}
  {"type": "mesh_query", "id": "...", "query_text": "...", "desired_types": [], "max_results": 5}

Steering: Single holder with 300s TTL + warning at 240s + 30s grace period.
  - Warning event broadcast when elapsed >= 240s (STEERING_TTL_WARNING)
  - Monitor heartbeat resets TTL indefinitely
  - Grace period queues commands instead of dropping on expiry
  - Queued commands replay when lock is renewed via heartbeat
  - Scoped permissions: allowed_tools, deny_patterns, rate limiting (Issue #336)

Mutual Auth (Issue #335):
  After auth_accepted, server initiates challenge-response:
  1. Server sends {"type": "challenge", "nonce": "...", "identity": {...}}
  2. Client responds {"type": "challenge_response", "hmac": "...", "identity": {...}}
  3. Server verifies HMAC-SHA256(nonce, shared_secret) and responds auth_mutual_ok/failed
  --require-mutual CLI flag rejects clients that don't complete the handshake.

Mesh Transport:
  Peer-to-peer KONO queries between sessions on the same machine.
  Same protocol types as instance mesh (grip-server), different transport (WebSocket session).
  - mesh_hello sent on subscription to advertise capabilities
  - mesh_query triggers in-process KONO search via EmbeddingEngine
  - FAIL_CLOSED: errors return mesh_error, never leak exceptions to caller

Version: 1.5.0
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Optional WebSocket support for remote monitors
try:
    import websockets

    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

CLAUDE_DIR = Path.home() / ".claude"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

HEARTBEAT_INTERVAL = 30
STEERING_TTL = 300
STEERING_TTL_WARNING = 240  # send warning when elapsed seconds reaches this threshold
STEERING_GRACE = 30  # grace period after TTL expires (queue instead of drop)
EVENT_BUFFER_SIZE = 50
AUTH_TIMEOUT = 5
MUTUAL_AUTH_TIMEOUT = 5
import tempfile as _tempfile
import os as _os
# Cross-platform injection path template; callers use .format(session_id=...)
INJECTION_PATH = _os.path.join(_tempfile.gettempdir(), "grip-injected-{session_id}.txt")
MAX_INJECTION_SIZE = 10000  # 10KB safety limit

# Default patterns always blocked regardless of permissions
DEFAULT_DENY_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"git\s+push\s+--force",
    r"git\s+reset\s+--hard",
    r":(){ :\|:& };:",  # fork bomb
]


@dataclass
class SteeringPermissions:
    """Fine-grained permission scope for a steering connection.

    Controls what a connected monitor can inject via steer_command.
    Stored per-connection in the monitor state dict.
    """

    allowed_tools: list[str] = field(default_factory=list)  # empty = all allowed
    allowed_commands: list[str] = field(default_factory=list)  # regex patterns, empty = all
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))
    max_injections_per_minute: int = 10
    require_ack: bool = False

    # Internal rate-limit state (not serialized to clients)
    _injection_timestamps: list[float] = field(default_factory=list, repr=False)

    def check_tool(self, tool_name: str) -> str | None:
        """Return rejection reason if tool is not allowed, else None."""
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return f"Tool '{tool_name}' not in allowed_tools: {self.allowed_tools}"
        return None

    def check_deny(self, text: str) -> str | None:
        """Return rejection reason if text matches a deny pattern, else None."""
        for pattern in self.deny_patterns:
            if re.search(pattern, text):
                return f"Text matches deny pattern: {pattern}"
        return None

    def check_rate_limit(self) -> str | None:
        """Return rejection reason if rate limit exceeded, else None."""
        now = time.monotonic()
        # Prune timestamps older than 60s
        self._injection_timestamps = [
            t for t in self._injection_timestamps if now - t < 60
        ]
        if len(self._injection_timestamps) >= self.max_injections_per_minute:
            return f"Rate limit exceeded: {self.max_injections_per_minute}/min"
        return None

    def record_injection(self) -> None:
        """Record a successful injection for rate limiting."""
        self._injection_timestamps.append(time.monotonic())

    def check_all(self, tool_name: str, text: str) -> str | None:
        """Run all permission checks. Return first rejection reason or None."""
        return self.check_tool(tool_name) or self.check_deny(text) or self.check_rate_limit()

    @classmethod
    def from_dict(cls, data: dict) -> "SteeringPermissions":
        """Build from a dict (e.g. from a WebSocket message)."""
        return cls(
            allowed_tools=data.get("allowed_tools", []),
            allowed_commands=data.get("allowed_commands", []),
            deny_patterns=data.get("deny_patterns", list(DEFAULT_DENY_PATTERNS)),
            max_injections_per_minute=data.get("max_injections_per_minute", 10),
            require_ack=data.get("require_ack", False),
        )

    def to_dict(self) -> dict:
        """Serialize to dict (excludes internal rate-limit state)."""
        return {
            "allowed_tools": self.allowed_tools,
            "allowed_commands": self.allowed_commands,
            "deny_patterns": self.deny_patterns,
            "max_injections_per_minute": self.max_injections_per_minute,
            "require_ack": self.require_ack,
        }


@dataclass
class AttributedSteer:
    """Attribution metadata for a steering injection."""

    source_session_id: str
    source_user: str
    timestamp: str  # ISO 8601
    steer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    command_type: str = ""
    text: str = ""

    @classmethod
    def from_steer_command(cls, msg: dict, monitor_id: str) -> "AttributedSteer":
        """Build attribution from an incoming steer_command message."""
        return cls(
            source_session_id=msg.get("source_session_id", monitor_id),
            source_user=msg.get("source_user", _resolve_user()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            command_type=msg.get("command_type", ""),
            text=msg.get("text", ""),
        )

    def format_header(self) -> str:
        """Produce the visible attribution header for the injection."""
        short_ts = self.timestamp[:19].replace("T", " ")
        return f"[STEER from {self.source_user} @ {short_ts}]"


@dataclass
class PairIdentity:
    """Identity exchanged during pair-mode mutual authentication handshake."""

    session_id: str
    machine_name: str
    user: str
    authenticated_at: str  # ISO 8601
    mutual: bool = False

    @classmethod
    def local(cls, session_id: str, mutual: bool = False) -> "PairIdentity":
        """Build identity for the local side."""
        return cls(
            session_id=session_id,
            machine_name=platform.node(),
            user=_resolve_user(),
            authenticated_at=datetime.now(timezone.utc).isoformat(),
            mutual=mutual,
        )


def _resolve_user() -> str:
    """Best-effort user identity from git config or env."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


def _append_audit_trail(audit_path: str, entry: dict) -> None:
    """Append a single JSON line to the steering audit trail."""
    try:
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        with open(audit_path, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        pass


def log(level: str, message: str):
    """Append to GRIP hook log."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [SESSION-SERVER][{level}] {message}\n")
    except Exception:
        pass


def calculate_tcp_port(session_id: str) -> int:
    """Deterministic port from session ID (matches session-lister.py)."""
    h = hashlib.sha256(session_id.encode()).hexdigest()
    return 19000 + (int(h[:8], 16) % 1000)


class SessionControlServer:
    """Dual-transport server: Unix socket (broadcasts) + WebSocket (monitors)."""

    def __init__(self, session_id: str, socket_path: str, tcp_port: int = None,
                 audit_trail_path: str | None = None,
                 require_mutual: bool = False):
        self.session_id = session_id
        self.socket_path = socket_path
        self.tcp_port = tcp_port
        self.audit_trail_path = audit_trail_path
        self.require_mutual = require_mutual
        self.monitors: dict = {}  # monitor_id -> {"ws": websocket, "events": []}
        self.steering_holder: str | None = None
        self.steering_acquired_at: datetime | None = None
        self.event_buffer: list = []
        self.claude_session_id: str | None = None  # learned from broadcast events
        self.instance_uuid: str | None = None  # per-instance UUID for steering isolation
        self._running = False
        self._unix_server = None
        self._ws_server = None
        # Steering heartbeat + grace period state
        self._steering_warning_sent = False
        self._steering_grace_active = False
        self._steering_grace_deadline: float = 0
        self._steering_grace_queue: list = []

    def _update_rsi_lock(self, new_session_id: str):
        """Point an existing RSI lock at the new session ID; never create one.

        GRIP#8989: this used to CREATE state/rsi/rsi-active.lock with just
        {"session_id": ...} and no "pid" every time the control server learned
        a Claude session id, so every session start looked like a running RSI
        loop, until GRIP#9017 taught the lock's readers to count only a lock
        that names a live process. Only a running loop creates the lock; this
        method changes its session_id and keeps every other field, including
        pid. When there is no lock, or the lock is not a JSON object (it does
        not parse, or it is valid JSON such as null or a list), it writes
        nothing (AGORA on GRIP#9025).
        """
        lock_path = _os.path.join(str(CLAUDE_DIR), "state", "rsi", "rsi-active.lock")
        if not _os.path.exists(lock_path):
            return
        try:
            with open(lock_path) as f:
                try:
                    data = json.loads(f.read().strip())
                except (json.JSONDecodeError, ValueError):
                    data = None
            if not isinstance(data, dict):
                log("WARN", "RSI lock is not a JSON object; leaving it untouched")
                return
            data["session_id"] = new_session_id
            # Write a temporary file and rename it over the lock, so a reader
            # never sees the lock empty or half written (AGORA on GRIP#9025).
            # When the write or rename fails, the finally block removes the
            # temporary file. If that removal fails too, the file stays, and the
            # removal gets its own warning. The name carries the process and the
            # thread, so two writes that run at the same time never share a
            # temporary file (AGORA on GRIP#9031).
            tmp_path = f"{lock_path}.tmp-{_os.getpid()}-{threading.get_ident()}"
            try:
                with open(tmp_path, "w") as f:
                    f.write(json.dumps(data))
                _os.replace(tmp_path, lock_path)
            finally:
                self._remove_leftover(tmp_path)
            log("INFO", f"Updated RSI lock with session_id {new_session_id[:8]}")
        except OSError as e:
            log("WARN", f"Could not update RSI lock: {e}")

    @staticmethod
    def _remove_leftover(path: str) -> None:
        """Remove the temporary file that a failed write or rename left behind.

        It never raises, so a cleanup that fails cannot replace the error that
        left the file: that error still reaches the caller's warning, and this
        failure gets a warning of its own (AGORA on GRIP#9031). A file that is
        not there was renamed into place, or was never created.
        """
        try:
            _os.remove(path)
        except FileNotFoundError:
            return
        except OSError as e:
            log("WARN", f"Could not remove the temporary file {path}: {e}")

    async def start(self):
        """Start both Unix socket and optional WebSocket servers."""
        self._running = True

        # Clean stale socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        # Unix socket for broadcast events from session-broadcast.py
        self._unix_server = await asyncio.start_unix_server(
            self._handle_broadcast, path=self.socket_path
        )
        os.chmod(self.socket_path, 0o600)
        log("INFO", f"Unix socket listening: {self.socket_path}")

        # Warn if websockets not installed but TCP port requested
        if not HAS_WEBSOCKETS and self.tcp_port:
            log("WARN", f"websockets not installed — TCP port {self.tcp_port} will NOT bind. Install: pip install websockets")

        # WebSocket for remote monitors (requires websockets library)
        if HAS_WEBSOCKETS and self.tcp_port:
            try:
                # Bind to all interfaces so Tailscale peers can connect for pair-mode
                bind_addr = os.environ.get("GRIP_WS_BIND", "0.0.0.0")
                self._ws_server = await websockets.serve(
                    self._handle_monitor, host=bind_addr, port=self.tcp_port
                )
                log("INFO", f"WebSocket server on port {self.tcp_port}")
            except OSError as e:
                log("WARN", f"WebSocket port {self.tcp_port} unavailable: {e}")

        # Heartbeat loop
        asyncio.create_task(self._heartbeat_loop())

        log("INFO", f"Server ready for session {self.session_id}")

        # Run until signal
        stop = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        await self.cleanup()

    # ── Broadcast handler (Unix socket, raw TCP) ─────────────────────

    async def _handle_broadcast(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Receive JSON events from session-broadcast.py."""
        try:
            data = await asyncio.wait_for(reader.read(65536), timeout=5.0)
            if data:
                for line in data.decode("utf-8").strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        # Learn/update Claude Code session ID from broadcast events
                        cid = event.pop("claude_session_id", None)
                        if cid and cid != self.claude_session_id:
                            old = self.claude_session_id
                            self.claude_session_id = cid
                            if old:
                                log("INFO", f"Session ID changed: {old[:8]} -> {cid[:8]} (compaction?)")
                            else:
                                log("INFO", f"Learned Claude session ID: {cid[:8]}")
                            self._update_rsi_lock(cid)
                        # Learn per-instance UUID for steering isolation
                        iid = event.pop("instance_uuid", None)
                        if iid and not self.instance_uuid:
                            self.instance_uuid = iid
                            log("INFO", f"Learned instance UUID: {iid[:8]}")
                        await self._distribute_event(event)
                    except json.JSONDecodeError:
                        pass
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log("ERROR", f"Broadcast error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # ── Monitor message handlers (one per msg_type) ──────────────────

    async def _msg_subscribe(self, websocket, msg: dict, monitor_id) -> str:
        """Handle 'subscribe' — register monitor and replay buffered events."""
        new_id = msg.get("monitor_id", f"m-{id(websocket)}")
        event_filter = msg.get("events", [])
        perms_data = msg.get("steering_permissions", {})
        perms = SteeringPermissions.from_dict(perms_data) if perms_data else SteeringPermissions()
        self.monitors[new_id] = {"ws": websocket, "events": event_filter, "steering_permissions": perms}
        log("INFO", f"Monitor subscribed: {new_id}")
        await self._send(websocket, {
            "type": "subscribed",
            "monitor_id": new_id,
            "session_id": self.session_id,
            "events": event_filter,
            "buffer_size": len(self.event_buffer),
        })
        await self._send_mesh_hello(websocket)
        for ev in self.event_buffer:
            if self._matches(ev, event_filter):
                await self._send(websocket, ev)
        return new_id

    async def _msg_acquire_steering(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'acquire_steering' — grant or deny the steering lock."""
        mid = msg.get("monitor_id", monitor_id)
        if self.steering_holder is None:
            self.steering_holder = mid
            self.steering_acquired_at = datetime.now(timezone.utc)
            await self._send(websocket, {"type": "steering_acquired", "monitor_id": mid})
            log("INFO", f"Steering acquired by: {mid}")
        else:
            await self._send(websocket, {"type": "steering_denied", "holder": self.steering_holder})

    async def _msg_release_steering(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'release_steering' — release the steering lock if held by this monitor."""
        if self.steering_holder == monitor_id:
            self._release_steering_lock()
            await self._send(websocket, {"type": "steering_released"})
            log("INFO", f"Steering released by: {monitor_id}")

    async def _msg_steer_configure(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'steer_configure' — update steering permissions for this monitor."""
        if monitor_id and monitor_id in self.monitors:
            perms_data = msg.get("permissions", {})
            self.monitors[monitor_id]["steering_permissions"] = SteeringPermissions.from_dict(perms_data)
            await self._send(websocket, {
                "type": "steer_configured",
                "monitor_id": monitor_id,
                "permissions": self.monitors[monitor_id]["steering_permissions"].to_dict(),
            })
            log("INFO", f"Steering permissions configured for {monitor_id}")
        else:
            await self._send(websocket, {"type": "error", "reason": "Subscribe first before configuring steering"})

    async def _msg_heartbeat(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'heartbeat' / 'heartbeat_ack'."""
        await self._handle_heartbeat(websocket, monitor_id)

    async def _msg_mesh_query(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'mesh_query'."""
        await self._handle_mesh_query(websocket, msg)

    async def _msg_steer_command(self, websocket, msg: dict, monitor_id) -> None:
        """Handle 'steer_command' (non-grace path)."""
        await self._handle_steer(websocket, monitor_id, msg)

    _MONITOR_HANDLERS: dict = {}  # populated after class body; see _build_monitor_handlers()

    # ── Monitor handler (WebSocket) ──────────────────────────────────

    async def _auth_gate(self, websocket) -> tuple[bool, list[dict]]:
        """Run the GRIP_PAIR_AUTH + mutual-auth handshake before the main loop.

        Returns (proceed, replay_msgs). proceed is False when the connection
        must be closed without entering the main message loop; replay_msgs
        carries any non-challenge message consumed during mutual auth that
        the main loop still needs to see.
        """
        auth_token = os.environ.get("GRIP_PAIR_AUTH")
        if not auth_token:
            return True, []
        try:
            raw_auth = await asyncio.wait_for(websocket.recv(), timeout=AUTH_TIMEOUT)
            auth_msg = json.loads(raw_auth)
            if auth_msg.get("type") != "auth" or auth_msg.get("token") != auth_token:
                await self._send(websocket, {"type": "auth_rejected", "reason": "invalid token"})
                await websocket.close()
                log("WARN", "Monitor auth rejected: invalid token")
                return False, []
            await self._send(websocket, {"type": "auth_accepted"})
            log("INFO", "Monitor auth accepted")
            # Mutual authentication phase (Issue #335)
            auth_ok, consumed = await self._do_mutual_auth(websocket, auth_token)
            if not auth_ok:
                return False, []
            return True, consumed
        except asyncio.TimeoutError:
            await self._send(websocket, {"type": "auth_rejected", "reason": "auth timeout"})
            await websocket.close()
            log("WARN", "Monitor auth rejected: timeout")
            return False, []
        except (json.JSONDecodeError, Exception) as e:
            await self._send(websocket, {"type": "auth_rejected", "reason": "malformed auth"})
            await websocket.close()
            log("WARN", f"Monitor auth rejected: {type(e).__name__}")
            return False, []

    async def _dispatch_monitor_message(self, websocket, raw, monitor_id):
        """Handle one raw message from the monitor loop.

        Returns the (possibly updated) monitor_id, so the caller's loop
        variable stays correct across subscribe/handler dispatch.
        """
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, RecursionError):
            # Four kinds of frame fail here: text that does not parse
            # (JSONDecodeError), a binary frame that is not valid UTF-8
            # (UnicodeDecodeError), a number longer than Python converts
            # (4,300 digits by default), which raises a plain ValueError, and
            # nesting deep enough to overflow the stack (RecursionError, not a
            # ValueError), which a frame under websockets' 1 MiB limit can
            # reach. Each is ignored instead of ending the connection (AGORA on
            # GRIP#9031).
            return monitor_id
        if not isinstance(msg, dict):
            # Valid JSON that is not an object is ignored, as text that does
            # not parse is, instead of ending the connection (AGORA on GRIP#9025).
            return monitor_id

        msg_type = msg.get("type", "")

        # Late auth: ack and continue (pre-subscribe handshake)
        if msg_type == "auth":
            await self._send(websocket, {"type": "auth_accepted"})
            return monitor_id

        # Grace-period queue: intercept steer_command before dispatch
        if (msg_type == "steer_command"
                and self._steering_grace_active
                and self.steering_holder == monitor_id):
            self._steering_grace_queue.append(msg)
            await self._send(websocket, {
                "type": "queued",
                "queue_size": len(self._steering_grace_queue),
                "message": f"Command queued (grace period). {len(self._steering_grace_queue)} in queue.",
            })
            return monitor_id

        handler = self._MONITOR_HANDLERS.get(msg_type)
        if handler is None:
            return monitor_id
        result = await handler(self, websocket, msg, monitor_id)
        return result if result is not None else monitor_id

    async def _handle_monitor(self, websocket):
        """Handle WebSocket connection from a monitor client."""
        monitor_id = None
        try:
            proceed, replay_msgs = await self._auth_gate(websocket)
            if not proceed:
                return

            async for raw in self._iter_with_replay(websocket, replay_msgs):
                monitor_id = await self._dispatch_monitor_message(websocket, raw, monitor_id)

        except Exception as e:
            log(
                "INFO",
                f"Monitor disconnected: {monitor_id or 'unknown'} ({type(e).__name__})",
            )
        finally:
            if monitor_id:
                self.monitors.pop(monitor_id, None)
                if self.steering_holder == monitor_id:
                    self._release_steering_lock()
                    log("INFO", f"Steering released (disconnect: {monitor_id})")

    # ── Mesh transport (peer-to-peer KONO queries) ───────────────────

    async def _send_mesh_hello(self, websocket) -> None:
        """Send mesh_hello after a monitor subscribes, advertising session capabilities."""
        await self._send(websocket, {
            "type": "mesh_hello",
            "session_id": self.session_id,
            "capabilities": ["kono_search"],
            "protocol_version": "1.0",
        })
        log("INFO", f"mesh_hello sent for session {self.session_id[:8]}")

    @staticmethod
    def _offer_genes_for_query(query_text: str) -> list:
        """Offer convergence genes relevant to this query.

        FAIL_CLOSED: share nothing on any import or runtime error.
        """
        try:
            from mesh_gene_exchange import find_genes_for_query
            return find_genes_for_query(query_text)
        except Exception:
            return []

    async def _search_kono_for_query(self, websocket, query_id: str, query_text: str,
                                      desired_types: list, max_results: int):
        """Run the timed KONO search for a mesh_query.

        FAIL_CLOSED: on any exception, sends mesh_error and returns None.
        On success returns (results, latency_ms).
        """
        t_start = time.monotonic()
        try:
            results = await asyncio.get_running_loop().run_in_executor(
                None,
                self._kono_search,
                query_text,
                desired_types,
                max_results,
            )
        except Exception as exc:
            log("ERROR", f"mesh_query failed [{query_id[:8]}]: {exc}")
            await self._send(websocket, {
                "type": "mesh_error",
                "query_id": query_id,
                "reason": "kono_search_failed",
            })
            return None

        latency_ms = round((time.monotonic() - t_start) * 1000)
        return results, latency_ms

    async def _handle_mesh_query(self, websocket, msg: dict) -> None:
        """Handle a mesh_query message — search KONO and return results.

        Protocol:
          in:  {"type": "mesh_query", "id": "...", "query_text": "...",
                "desired_types": [], "max_results": 5}
          out: {"type": "mesh_response", "query_id": "...", "responder": "...",
                "results": [...], "latency_ms": N}
          err: {"type": "mesh_error", "query_id": "...", "reason": "..."}

        FAIL_CLOSED: any exception produces mesh_error, never propagates.
        """
        query_id = msg.get("id", "")
        query_text = msg.get("query_text", "").strip()
        desired_types = msg.get("desired_types") or []
        max_results = int(msg.get("max_results") or 5)

        if not query_text:
            await self._send(websocket, {
                "type": "mesh_error",
                "query_id": query_id,
                "reason": "query_text is required",
            })
            return

        outcome = await self._search_kono_for_query(
            websocket, query_id, query_text, desired_types, max_results
        )
        if outcome is None:
            return
        results, latency_ms = outcome

        offered_genes = self._offer_genes_for_query(query_text)
        await self._send(websocket, {
            "type": "mesh_response",
            "query_id": query_id,
            "responder": self.session_id,
            "results": results,
            "offered_genes": offered_genes,
            "latency_ms": latency_ms,
        })
        log("INFO", f"mesh_query [{query_id[:8]}] answered: {len(results)} results in {latency_ms}ms")

    @staticmethod
    def _search_by_types(engine, query_text: str, desired_types: list, max_results: int) -> list:
        """Search once per desired type, or once with no type filter.

        When desired_types is non-empty, results across types are merged,
        deduplicated by id (dict-based, O(n), never a nested loop over
        hits), re-ranked by similarity, and capped at max_results.
        """
        if not desired_types:
            return engine.search_similar(query_text=query_text, limit=max_results)

        all_hits: list = []
        for dtype in desired_types:
            all_hits.extend(engine.search_similar(
                query_text=query_text,
                embed_type=dtype,
                limit=max_results,
            ))

        # Dedup by id, keeping the first occurrence (dict preserves
        # insertion order) — a hash lookup, not a nested scan of hits.
        deduped: dict = {}
        for hit in all_hits:
            deduped.setdefault(hit["id"], hit)

        raw = list(deduped.values())
        raw.sort(key=lambda r: r.get("similarity", 0), reverse=True)
        return raw[:max_results]

    @staticmethod
    def _serialise_hit(r: dict) -> dict:
        """Strip binary/large fields before serialisation over the wire."""
        return {
            "id": r.get("id"),
            "type": r.get("type"),
            "content": r.get("content"),
            "similarity": round(float(r.get("similarity") or 0), 4),
            "success_score": round(float(r.get("success_score") or 0), 4),
            "session_id": r.get("session_id"),
            "project_path": r.get("project_path"),
            "created_at": r.get("created_at"),
        }

    @staticmethod
    def _kono_search(query_text: str, desired_types: list, max_results: int) -> list:
        """Run an in-process KONO similarity search.

        Kept as a plain (non-async) method so it can be dispatched to an
        executor without dragging the event loop into the embedding model call.

        Returns a list of result dicts with fields: id, type, content,
        similarity, success_score, session_id.  Strips heavy/binary fields
        (vector bytes) before returning over the wire.
        """
        if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        # Dispatch via get_engine() so the GRIP_EMBED_BACKEND registry contract
        # (ADR-0007 / W3-W6, incl. dim validation) is honoured — NOT a bare
        # backend constructor. The active backend now exposes the public
        # search_similar (#3383); model loads lazily on first embed.
        from embeddings import get_engine

        engine = get_engine()
        try:
            engine.init_schema()
            raw = SessionControlServer._search_by_types(engine, query_text, desired_types, max_results)
        finally:
            engine.close()

        return [SessionControlServer._serialise_hit(r) for r in raw]

    def _write_injection_paths(self, attributed_text: str) -> list:
        """Write attributed_text to all applicable injection paths.

        Tries instance UUID (preferred), Claude Code session ID, and server session ID
        as fallbacks. Skips duplicate resolved paths. Returns list of paths written.
        """
        paths_written = []
        for sid in filter(None, [self.instance_uuid, self.claude_session_id, self.session_id]):
            p = INJECTION_PATH.format(session_id=sid)
            if p not in paths_written:
                try:
                    with open(p, "w") as f:
                        f.write(attributed_text)
                    os.chmod(p, 0o600)
                    paths_written.append(p)
                except OSError:
                    pass
        return paths_written

    async def _reject_steer_on_permissions(self, websocket, monitor_id: str,
                                            perms: "SteeringPermissions",
                                            cmd_type: str, text: str) -> bool:
        """Run the scoped steering permission checks (Issue #336).

        Sends the rejection message and logs it when a check fails.
        Returns True if the command was rejected (caller must return).
        """
        tool_rejection = perms.check_tool(cmd_type)
        if tool_rejection:
            await self._send(websocket, {
                "type": "steer_denied",
                "reason": tool_rejection,
                "command_type": cmd_type,
            })
            log("INFO", f"Steer denied (tool): {monitor_id} -> {cmd_type}: {tool_rejection}")
            return True

        deny_rejection = perms.check_deny(text)
        if deny_rejection:
            await self._send(websocket, {
                "type": "steer_denied",
                "reason": deny_rejection,
                "command_type": cmd_type,
            })
            log("INFO", f"Steer denied (pattern): {monitor_id}: {deny_rejection}")
            return True

        rate_rejection = perms.check_rate_limit()
        if rate_rejection:
            await self._send(websocket, {
                "type": "steer_rate_limited",
                "reason": rate_rejection,
                "command_type": cmd_type,
            })
            log("INFO", f"Steer rate-limited: {monitor_id}: {rate_rejection}")
            return True

        return False

    async def _apply_inject_context(self, websocket, monitor_id: str,
                                     attribution: "AttributedSteer", text: str) -> None:
        """Write the attributed injection to all applicable paths, then ack/err."""
        attributed_text = f"{attribution.format_header()} {text}"
        paths_written = self._write_injection_paths(attributed_text)
        if paths_written:
            await self._send(websocket, {
                "type": "steer_ack",
                "command_type": attribution.command_type,
                "path": paths_written[0],
                "steer_id": attribution.steer_id,
                "attribution": asdict(attribution),
            })
            log("INFO", f"Context injected by {attribution.source_user} via {monitor_id} ({len(text)} chars) -> {len(paths_written)} paths [steer_id={attribution.steer_id[:8]}]")
        else:
            await self._send(websocket, {"type": "error", "reason": "No injection paths available"})

    def _audit_steer(self, attribution: "AttributedSteer", cmd_type: str, text: str) -> None:
        """Append a steering audit record, if an audit trail path is configured."""
        if not self.audit_trail_path:
            return
        _append_audit_trail(self.audit_trail_path, {
            "steer_id": attribution.steer_id,
            "source_session_id": attribution.source_session_id,
            "source_user": attribution.source_user,
            "timestamp": attribution.timestamp,
            "target_session_id": self.session_id,
            "command_type": cmd_type,
            "text_length": len(text),
        })

    async def _handle_steer(self, websocket, monitor_id: str, msg: dict):
        """Process a steering command from a monitor."""
        if self.steering_holder != monitor_id:
            await self._send(
                websocket, {"type": "error", "reason": "Steering not held"}
            )
            return

        # Auto-renew TTL on activity
        self.steering_acquired_at = datetime.now(timezone.utc)

        # Build attribution metadata
        attribution = AttributedSteer.from_steer_command(msg, monitor_id)

        cmd_type = attribution.command_type
        text = attribution.text

        # ── Scoped steering permission checks (Issue #336) ──────────
        perms = self.monitors.get(monitor_id, {}).get(
            "steering_permissions", SteeringPermissions()
        )
        if await self._reject_steer_on_permissions(websocket, monitor_id, perms, cmd_type, text):
            return

        # All checks passed — record for rate limiting
        perms.record_injection()

        if cmd_type == "inject_context":
            # Sanitize: length limit
            if len(text) > MAX_INJECTION_SIZE:
                await self._send(
                    websocket,
                    {"type": "error", "reason": f"Injection too large: {len(text)} > {MAX_INJECTION_SIZE}"},
                )
                return
            await self._apply_inject_context(websocket, monitor_id, attribution, text)
        else:
            await self._send(websocket, {"type": "error", "reason": f"Unknown command: {cmd_type}"})

        # Audit trail (append-only JSONL)
        self._audit_steer(attribution, cmd_type, text)

    # ── Mutual authentication (Issue #335) ─────────────────────────

    async def _await_challenge_response(
        self, websocket
    ) -> tuple[dict | None, tuple[bool, list[dict]] | None]:
        """Receive and type-check the client's challenge_response message.

        Returns (msg, None) when the handshake should continue to HMAC
        verification, or (None, outcome) when it is already decided
        (success or failure) and the caller must return `outcome` as-is.
        """
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=MUTUAL_AUTH_TIMEOUT)
            msg = json.loads(raw)
        except asyncio.TimeoutError:
            if self.require_mutual:
                await self._send(websocket, {"type": "auth_mutual_failed", "reason": "challenge timeout"})
                await websocket.close()
                log("WARN", "Mutual auth failed: challenge response timeout")
                return None, (False, [])
            # Legacy client did not respond -- allow if not required
            log("INFO", "Mutual auth skipped (legacy client, not required)")
            return None, (True, [])
        except (json.JSONDecodeError, Exception):
            await self._send(websocket, {"type": "auth_mutual_failed", "reason": "malformed response"})
            await websocket.close()
            log("WARN", "Mutual auth failed: malformed challenge response")
            return None, (False, [])

        if msg.get("type") != "challenge_response":
            if self.require_mutual:
                await self._send(websocket, {"type": "auth_mutual_failed", "reason": "unexpected message type"})
                await websocket.close()
                log("WARN", f"Mutual auth failed: expected challenge_response, got {msg.get('type')}")
                return None, (False, [])
            # Non-challenge message consumed during handshake — return it
            # so the caller can replay it in the main message loop
            log("INFO", f"Mutual auth skipped (legacy client). Consumed {msg.get('type')} message — will replay.")
            return None, (True, [msg])

        return msg, None

    async def _verify_challenge_response(
        self, websocket, msg: dict, nonce: str, shared_secret: str,
        server_identity: "PairIdentity",
    ) -> tuple[bool, list[dict]]:
        """Verify the HMAC and complete the mutual-auth handshake."""
        expected = hmac.new(shared_secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(msg.get("hmac", ""), expected):
            await self._send(websocket, {"type": "auth_mutual_failed", "reason": "invalid hmac"})
            await websocket.close()
            log("WARN", "Mutual auth failed: HMAC mismatch")
            return False, []

        # Mutual auth succeeded -- exchange identities
        client_identity = msg.get("identity")
        server_identity.mutual = True
        await self._send(websocket, {
            "type": "auth_mutual_ok",
            "identity": asdict(server_identity),
        })
        client_name = client_identity.get("machine_name", "unknown") if client_identity else "unknown"
        log("INFO", f"Mutual auth succeeded with {client_name}")
        return True, []

    async def _do_mutual_auth(self, websocket, shared_secret: str) -> tuple[bool, list[dict]]:
        """Run mutual authentication challenge-response after initial auth.

        Returns (success, consumed_messages) where consumed_messages contains
        any non-challenge messages that were read during the handshake and need
        to be replayed by the caller.
        """
        nonce = secrets.token_hex(32)
        server_identity = PairIdentity.local(self.session_id, mutual=False)
        await self._send(websocket, {
            "type": "challenge",
            "nonce": nonce,
            "identity": asdict(server_identity),
        })

        msg, outcome = await self._await_challenge_response(websocket)
        if outcome is not None:
            return outcome

        return await self._verify_challenge_response(
            websocket, msg, nonce, shared_secret, server_identity
        )

    # ── Steering heartbeat + grace ──────────────────────────────────

    async def _handle_heartbeat(self, websocket, monitor_id: str):
        """Handle heartbeat from monitor — resets steering TTL."""
        if self.steering_holder != monitor_id:
            await self._send(websocket, {
                "type": "heartbeat_ack",
                "ttl_remaining": 0,
                "message": "No steering lock held.",
            })
            return

        # Reset TTL
        self.steering_acquired_at = datetime.now(timezone.utc)
        self._steering_warning_sent = False

        # If in grace period, replay queued commands and exit grace
        if self._steering_grace_active:
            self._steering_grace_active = False
            replayed = len(self._steering_grace_queue)
            for queued in self._steering_grace_queue:
                await self._handle_steer(websocket, monitor_id, queued)
            self._steering_grace_queue.clear()
            log("INFO", f"Heartbeat during grace: replayed {replayed} queued commands for {monitor_id}")

        await self._send(websocket, {
            "type": "heartbeat_ack",
            "ttl_remaining": STEERING_TTL,
        })
        log("DEBUG", f"Heartbeat received from {monitor_id}, TTL reset")

    def _release_steering_lock(self):
        """Release steering lock and reset all associated state."""
        self.steering_holder = None
        self.steering_acquired_at = None
        self._steering_warning_sent = False
        self._steering_grace_active = False
        self._steering_grace_deadline = 0
        self._steering_grace_queue.clear()

    async def _broadcast_monitors(self, msg: dict):
        """Send a message to all connected monitors."""
        for mid, mon in list(self.monitors.items()):
            if not await self._send(mon["ws"], msg):
                self.monitors.pop(mid, None)

    # ── Message replay helper ────────────────────────────────────────

    async def _iter_with_replay(self, websocket, replay_msgs: list[dict]):
        """Yield replay messages first, then yield from the websocket.

        This allows messages consumed during mutual auth to be processed
        by the main message loop as if they arrived normally.
        """
        for msg in replay_msgs:
            yield msg
        async for raw in websocket:
            yield raw

    # ── Event distribution ───────────────────────────────────────────

    async def _distribute_event(self, event: dict):
        """Forward event to all subscribed monitors and buffer it."""
        self.event_buffer.append(event)
        if len(self.event_buffer) > EVENT_BUFFER_SIZE:
            self.event_buffer.pop(0)

        for mid, mon in list(self.monitors.items()):
            if self._matches(event, mon.get("events", [])):
                if not await self._send(mon["ws"], event):
                    self.monitors.pop(mid, None)

    @staticmethod
    def _matches(event: dict, event_filter: list) -> bool:
        """Check if event matches filter (empty filter = all events)."""
        if not event_filter:
            return True
        return event.get("event_type", "") in event_filter

    @staticmethod
    async def _send(websocket, data: dict) -> bool:
        """Send JSON to websocket. Returns False if connection is dead."""
        try:
            await websocket.send(json.dumps(data))
            return True
        except Exception:
            return False

    # ── Heartbeat ────────────────────────────────────────────────────

    async def _enter_steering_grace(self, elapsed: float) -> None:
        """TTL expired — notify monitors then enter the grace period."""
        await self._broadcast_monitors({
            "type": "steering_expired",
            "holder": self.steering_holder,
            "elapsed_seconds": int(elapsed),
            "message": f"Steering TTL ({STEERING_TTL}s) expired. Entering grace period.",
        })
        log("INFO", f"Steering expired for {self.steering_holder} after {int(elapsed)}s")
        self._steering_grace_active = True
        self._steering_grace_deadline = asyncio.get_event_loop().time() + STEERING_GRACE
        await self._broadcast_monitors({
            "type": "steering_grace",
            "grace_seconds": STEERING_GRACE,
            "holder": self.steering_holder,
            "message": f"{STEERING_GRACE}s grace period — commands queued.",
        })
        log("INFO", f"Steering grace period started for {self.steering_holder}")

    async def _tick_steering_active_phase(self) -> None:
        """Phase 1: active steering — warn at threshold, expire into grace at TTL."""
        elapsed = (datetime.now(timezone.utc) - self.steering_acquired_at).total_seconds()
        remaining = STEERING_TTL - elapsed

        if elapsed >= STEERING_TTL_WARNING and not self._steering_warning_sent:
            self._steering_warning_sent = True
            await self._broadcast_monitors({
                "type": "steering_expiring",
                "remaining_seconds": int(remaining),
                "holder": self.steering_holder,
                "message": f"Steering lock expires in {int(remaining)}s. Send heartbeat to extend.",
            })
            log("INFO", f"Steering warning sent: {int(remaining)}s remaining for {self.steering_holder}")

        if remaining <= 0:
            await self._enter_steering_grace(elapsed)

    async def _tick_steering_grace_phase(self) -> None:
        """Phase 2: grace period — release the lock once the deadline passes."""
        if asyncio.get_event_loop().time() > self._steering_grace_deadline:
            holder = self.steering_holder
            self._release_steering_lock()
            await self._broadcast_monitors({
                "type": "steering_released",
                "reason": "grace_expired",
                "message": "Grace period expired. Steering released.",
            })
            log("INFO", f"Steering grace expired, released: {holder}")

    async def _tick_steering_ttl(self) -> None:
        """Enforce steering TTL: emit warning, expire into grace, release on grace deadline.

        Single-responsibility coroutine called once per heartbeat tick.
        Mutates self.steering_* state; all broadcasts go through _broadcast_monitors.
        No-ops when no steering holder is active.
        """
        if not (self.steering_holder and self.steering_acquired_at):
            return

        if not self._steering_grace_active:
            await self._tick_steering_active_phase()
        else:
            await self._tick_steering_grace_phase()

    async def _heartbeat_loop(self):
        """Periodic heartbeat to monitors + steering TTL tick."""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._tick_steering_ttl()

            # Send heartbeat to all monitors. _broadcast_monitors drops a
            # monitor whose send fails, as the inline loop it replaced did.
            heartbeat = {
                "type": "heartbeat",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "session_id": self.session_id,
                "monitors": len(self.monitors),
                "steering": self.steering_holder,
            }
            await self._broadcast_monitors(heartbeat)

    # ── Cleanup ──────────────────────────────────────────────────────

    async def cleanup(self):
        """Release all resources on shutdown."""
        self._running = False

        if self._unix_server:
            self._unix_server.close()
            await self._unix_server.wait_closed()

        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        for path in (
            self.socket_path,
            _os.path.join(_tempfile.gettempdir(), f"grip-session-server-{self.session_id}.pid"),
        ):
            try:
                os.unlink(path)
            except OSError:
                pass

        log("INFO", "Server shutdown complete")


# Populate dispatch table after class body so unbound method references resolve.
SessionControlServer._MONITOR_HANDLERS = {
    "subscribe":         SessionControlServer._msg_subscribe,
    "acquire_steering":  SessionControlServer._msg_acquire_steering,
    "release_steering":  SessionControlServer._msg_release_steering,
    "steer_configure":   SessionControlServer._msg_steer_configure,
    "heartbeat":         SessionControlServer._msg_heartbeat,
    "heartbeat_ack":     SessionControlServer._msg_heartbeat,
    "mesh_query":        SessionControlServer._msg_mesh_query,
    "steer_command":     SessionControlServer._msg_steer_command,
}


def verify_environment():
    """Verify required environment variables for remote pair-mode.

    When launching via SSH, ~/.zshrc is not automatically sourced,
    so env vars like GRIP_PAIR_AUTH may be missing. This provides
    a clear error message instead of silent auth bypass.
    """
    required = ["GRIP_PAIR_AUTH"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(
            f"ERROR: Missing environment variables: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Fix: source ~/.zshrc before launching, or set variables directly:",
            file=sys.stderr,
        )
        for v in missing:
            print(f"  export {v}=<value>", file=sys.stderr)
        sys.exit(1)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the session control server."""
    parser = argparse.ArgumentParser(description="GRIP Session Control Server")
    parser.add_argument("session_id", help="Session ID to serve")
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=None,
        help="TCP port for WebSocket monitors (auto-calculated if omitted)",
    )
    parser.add_argument(
        "--no-auth-check",
        action="store_true",
        help="Skip GRIP_PAIR_AUTH verification (local-only mode)",
    )
    parser.add_argument(
        "--require-mutual",
        action="store_true",
        help="Reject connections that do not complete mutual authentication",
    )
    parser.add_argument(
        "--audit-trail",
        action="store_true",
        help="Log all steering events to cache/steering-audit.jsonl",
    )
    return parser


def main():
    args = _build_arg_parser().parse_args()

    # Verify env vars unless explicitly skipped (e.g. local-only use)
    if not args.no_auth_check:
        verify_environment()

    session_id = args.session_id
    from _compat import grip_socket_path
    socket_path = grip_socket_path(session_id)
    tcp_port = args.tcp_port or calculate_tcp_port(session_id)

    audit_path = None
    if args.audit_trail:
        audit_path = str(CLAUDE_DIR / "cache" / "steering-audit.jsonl")

    log(
        "INFO",
        f"Starting: session={session_id}, socket={socket_path}, port={tcp_port}, audit={audit_path}",
    )

    server = SessionControlServer(session_id, socket_path, tcp_port,
                                  audit_trail_path=audit_path,
                                  require_mutual=args.require_mutual)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        log("INFO", "Server interrupted")


if __name__ == "__main__":
    main()
