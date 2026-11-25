"""Relay-host syscall shim — selects WHICH node serves a channel, and fails over.

GRIP runs N always-on **relay nodes**, each an instance of grip-channel serving
one or more channels:

  - ``mac-operator`` (macOS) serves WhatsApp(+web) — must stay on the Mac
    because WhatsApp blocks datacenter IPs (residential-IP requirement).
  - ``vps-grip-remote-v2`` (linux) serves Slack+Discord — laptop-independent.

A second WhatsApp node (e.g. a teammate's Windows machine, a separate Baileys pairing
to a different number) is just ANOTHER row in the registry — added by appending
one object to ``config/relay-nodes.json``, never by editing code. The shim
handles N data-described nodes uniformly; that genericity IS its completeness.

This shim sits ABOVE the per-channel SEND transports (``lib.whatsapp_transport``,
``lib.slack_transport``): it does node-SELECTION and FAILOVER; the per-channel
transport does the actual byte-on-the-wire send. It is the single source of
truth that kills the relay split-brain (three env-var names —
``GRIP_RELAY_MCP_URL`` / ``GRIP_RELAY_CHANNEL_MCP_URL`` / ``GRIP_CHANNEL_MCP_PORT``
— governing one logical "which node serves this channel" concern).

Public API (provider-independent — ``channel`` is the stable noun; nodes / URLs
/ workspaces are hidden behind it)::

    from lib.relay_host import RelayHost

    host = RelayHost()
    host.nodes()                       # all nodes, registry order = failover order
    host.nodes_for("whatsapp")         # registry-ordered subset serving a channel
    host.health("mac-operator") # probe one node -> NodeHealth (never raises)
    host.health_all()                  # probe every node -> [NodeHealth] (card source)
    host.active_node("slack")          # first healthy node for a channel | None
    host.route("whatsapp", recipient, payload)  # select + send + failover -> dict

Syscall-doctrine compliance (rules/syscall-doctrine.md):
  1. Stable provider-independent API — ``route``/``health_all``/``active_node``
     do not change when a node is added/removed (data edit), when WhatsApp moves
     from MCP to REST (adapter-internal), or when the VPS domain changes
     (registry edit).
  2. Trivially mockable — three constructor DIP seams (``registry_path``,
     ``transports``, ``health_probe``) plus per-call overrides on ``route``; the
     shim IS the mocking surface, so tests inject fakes with no monkey-patching.
  3. Audit-ready — ``route`` returns ``{ok, channel, node, attempts:[...]}``
     recording who actually served + the full failover trail. Secrets never
     enter this module (Slack's token stays inside slack_transport's scope; WA
     has none), so the result dict is leak-safe to log by construction.
  4. Hot-swappable backend — the node registry + health-gated registry-order
     walk IS the HAL-style ladder (cloud -> Piper -> macos_say analog). Adding a
     node = a new ladder rung with zero code change.
  5. Plain-language errors — every failure path emits operator-readable English,
     never an exception or a code (Rule 17).

Failure semantics: NEVER raises on transport / health / registry errors — every
such failure becomes a fail-soft value (a ``NodeHealth(alive=False, ...)`` row or
a ``{ok: False, error: <plain language>}`` dict).
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

__all__ = [
    "RelayNode",
    "NodeHealth",
    "RelayHost",
    "TransportMap",
    "HealthProbe",
    "DEFAULT_TRANSPORTS",
]

# Registry path — repo-relative (parent of this lib/ directory), the canonical
# in-tree idiom (cf. lib/file_integrity_monitor.py, lib/pre_review_pipeline.py).
# NOT get_claude_dir(): on this machine cwd is the repo (.grip / .claude), and
# get_claude_dir() would resolve ~/.claude — the wrong tree. Anchoring to the
# file's own location is correct regardless of cwd or which clone. Overridable
# per-instance via RelayHost(registry_path=...).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = _REPO_ROOT / "config" / "relay-nodes.json"

# Health-probe HTTP timeout. Short — a relay daemon answers /health in <50ms or
# not at all (a sleeping laptop / down daemon should fail fast, not hang a card).
_HEALTH_TIMEOUT_S = 3.0


# ---------------------------------------------------------------------------
# Data records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayNode:
    """An immutable registry entry describing one always-on relay node.

    ``channels`` is a tuple (hashable, order-preserving) — its order within the
    registry is the failover priority for that channel. ``notes`` is a plain
    operator note (e.g. the residential-IP requirement) surfaced in errors and
    cards; it is never parsed for logic.
    """

    name: str
    platform: str
    channels: tuple[str, ...]
    transport_url: str
    health_url: str
    notes: str = ""

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "RelayNode":
        """Build a RelayNode from a registry dict, validating required keys.

        Raises ``ValueError`` (never a bare ``KeyError``) naming the offending
        node and the missing key, in plain language, so a malformed registry
        produces a readable load error rather than a cryptic traceback.
        """
        name = d.get("name")
        required = ("name", "platform", "channels", "transport_url", "health_url")
        missing = [k for k in required if not d.get(k)]
        if missing:
            who = f"node {name!r}" if name else "a relay node"
            raise ValueError(
                f"{who} in the registry is missing required "
                f"key(s): {', '.join(missing)}"
            )
        channels = d["channels"]
        if not isinstance(channels, (list, tuple)):
            raise ValueError(
                f"node {name!r}: 'channels' must be a list of channel ids, "
                f"got {type(channels).__name__}"
            )
        return cls(
            name=str(d["name"]),
            platform=str(d["platform"]),
            channels=tuple(str(c) for c in channels),
            transport_url=str(d["transport_url"]),
            health_url=str(d["health_url"]),
            notes=str(d.get("notes", "")),
        )


@dataclass(frozen=True)
class NodeHealth:
    """The result of probing one node's health endpoint.

    ``detail`` is ALWAYS plain language ('reachable in 41ms' / 'no response
    within 3s — daemon down or laptop asleep' / 'connection refused on
    127.0.0.1:3101 — grip-channel not running'). ``latency_ms`` is ``None``
    whenever ``alive`` is ``False``.
    """

    name: str
    alive: bool
    latency_ms: float | None
    detail: str

    def to_dict(self) -> dict:
        """Serialise for the relay-health delight card (mirrors DeliveryResult)."""
        return asdict(self)


# DIP seams — named so tests pass a dict of fakes / a scripted probe without
# monkey-patching lib internals.
HealthProbe = Callable[[RelayNode], NodeHealth]
TransportMap = Mapping[str, Callable[[RelayNode, str, dict], dict]]


# ---------------------------------------------------------------------------
# Default health probe (stdlib urllib, fail-soft — NEVER raises)
# ---------------------------------------------------------------------------


def _unhealthy(node: RelayNode, status: int) -> NodeHealth:
    """A reachable-but-not-2xx daemon — plain-language not-healthy row."""
    return NodeHealth(
        node.name, False, None,
        f"daemon answered with HTTP {status} on {node.health_url} — "
        "reachable but not healthy",
    )


def _urlerror_detail(node: RelayNode, reason: object) -> str:
    """Map a URLError/timeout reason to a plain-language cause string."""
    text = str(reason).lower()
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return f"no response within {_HEALTH_TIMEOUT_S:g}s — daemon down or laptop asleep"
    if "refused" in text:
        return f"connection refused on {node.health_url} — grip-channel not running"
    return f"unreachable ({reason})"


def _http_health_probe(node: RelayNode) -> NodeHealth:
    """Probe ``node.health_url`` over HTTP. Fail-soft: any error -> alive=False.

    A 2xx response is alive; the latency is wall-clock round-trip in ms. Every
    failure mode (timeout, connection refused, DNS, HTTP error) maps to an
    ``alive=False`` row carrying a plain-language ``detail`` — this function
    never raises, so ``health``/``health_all`` are always safe to call.
    """
    import time

    start = time.monotonic()
    req = urllib.request.Request(node.health_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT_S) as resp:
            status = resp.status
            resp.read(1)  # touch the body so a half-open connection surfaces
    except urllib.error.HTTPError as e:
        return _unhealthy(node, e.code)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return NodeHealth(node.name, False, None, _urlerror_detail(node, getattr(e, "reason", e)))
    except Exception as e:  # noqa: BLE001 — fail-soft on anything unexpected
        return NodeHealth(node.name, False, None, f"health probe failed: {type(e).__name__}: {e}")

    latency_ms = round((time.monotonic() - start) * 1000, 1)
    if 200 <= status < 300:
        return NodeHealth(node.name, True, latency_ms, f"reachable in {latency_ms:g}ms")
    return _unhealthy(node, status)


# ---------------------------------------------------------------------------
# Per-channel transport adapters — map (node, recipient, payload) onto the
# existing per-channel SEND shims. relay_host sits ABOVE these and does NOT
# re-implement the MCP handshake or the Slack POST (DRY); it calls into them.
# Each adapter honours grip_notify.Backend.send's NEVER-RAISES contract: any
# exception (a malformed payload, slack_transport's unknown-workspace ValueError
# or missing-token RuntimeError) is converted to {ok: False, error: ...} so
# route() records a failed attempt and advances the ladder.
# ---------------------------------------------------------------------------


def _whatsapp_via_node(node: RelayNode, recipient: str, payload: dict) -> dict:
    """Send a WhatsApp message via the node the ladder selected.

    Routes to THIS node's grip-channel MCP daemon by deriving the MCP URL from
    ``node.transport_url`` and passing it as ``whatsapp_transport.post``'s
    ``base_url`` — so a non-local node (a second WA pairing) is actually hit,
    not silently the local daemon. Required key: ``payload['text']``.
    """
    try:
        text = payload["text"]
    except KeyError:
        return {
            "ok": False,
            "error": (
                "whatsapp payload missing required key 'text'; "
                f"got keys: {sorted(payload)}"
            ),
        }
    base_url = node.transport_url.rstrip("/") + "/mcp"
    try:
        # Deliberately the RAW transport: relay_host targets per-NODE daemons via
        # base_url, and each node's history log lives on that node — a local
        # wa_delivery_verify poll would false-negative every remote-node send.
        from lib.whatsapp_transport import post  # shim — orchestrated, not reimplemented

        result = post(recipient=recipient, text=text, base_url=base_url)
    except Exception as e:  # noqa: BLE001 — Backend.send never-raises contract
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return result


def _slack_via_node(node: RelayNode, recipient: str, payload: dict) -> dict:
    """Send a Slack message for the channel the ladder selected.

    Slack's Web API endpoint is identical regardless of which relay node relays
    (the node difference is which daemon/bot-token, not the URL), so ``node`` is
    audit metadata here; the send maps the payload onto ``slack_transport.post``
    (workspace-keyed, ``channel_id=recipient``). Required keys:
    ``payload['workspace']`` + ``payload['text']``; optional ``payload['blocks']``.
    """
    missing = [k for k in ("workspace", "text") if k not in payload]
    if missing:
        return {
            "ok": False,
            "error": (
                f"slack payload missing required key(s): {', '.join(missing)}; "
                f"got keys: {sorted(payload)}"
            ),
        }
    try:
        from lib.slack_transport import post  # shim — orchestrated, not reimplemented

        result = post(
            workspace=payload["workspace"],
            channel_id=recipient,
            text=payload["text"],
            blocks=payload.get("blocks"),
            thread_ts=payload.get("thread_ts"),
        )
    except Exception as e:  # noqa: BLE001 — converts unknown-workspace ValueError
        # / missing-token RuntimeError to a failed attempt, never propagates.
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return result


# Channel -> the real underlying transport adapter. Injectable per-instance and
# per-call so tests pass {'whatsapp': fake} with no monkey-patching.
DEFAULT_TRANSPORTS: TransportMap = {
    "whatsapp": _whatsapp_via_node,
    "web": _whatsapp_via_node,
    "slack": _slack_via_node,
    "discord": _slack_via_node,
}


# ---------------------------------------------------------------------------
# RelayHost
# ---------------------------------------------------------------------------


class RelayHost:
    """Selects which relay node serves a channel and fails over, fail-soft.

    The failover ladder is the registry array order: ``nodes_for(channel)``
    returns the registry-ordered subset serving ``channel``, and both
    ``active_node`` and ``route`` walk that order. Today each channel has
    exactly one serving node, so the ladder is a 1-rung degenerate; appending a
    second WhatsApp node to the registry gives WhatsApp a 2-rung ladder with
    ZERO code change.
    """

    def __init__(
        self,
        *,
        registry_path: Path | None = None,
        transports: TransportMap | None = None,
        health_probe: HealthProbe | None = None,
    ) -> None:
        self._registry_path = registry_path if registry_path is not None else _REGISTRY_PATH
        self._transports: TransportMap = transports if transports is not None else DEFAULT_TRANSPORTS
        self._health_probe: HealthProbe = health_probe if health_probe is not None else _http_health_probe
        # Fail-soft load: a missing/corrupt registry yields an empty node list
        # plus a stored plain-language load_error — never an import/__init__ raise.
        self._nodes: list[RelayNode]
        self.load_error: str | None
        self._nodes, self.load_error = self._load_registry(self._registry_path)

    # -- registry loading (fail-soft) --------------------------------------

    @staticmethod
    def _load_registry(path: Path) -> tuple[list[RelayNode], str | None]:
        """Load + parse the node registry. Returns (nodes, load_error).

        Never raises: a missing file, unreadable file, bad JSON, or a malformed
        node each yields ``([], <plain-language error>)`` so the host degrades
        to a 'no nodes loaded' state rather than crashing a caller or a card.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], f"could not read {path.name}: file not found; no nodes loaded"
        except OSError as e:
            return [], f"could not read {path.name}: {e}; no nodes loaded"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return [], f"could not parse {path.name}: invalid JSON ({e}); no nodes loaded"
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return [], (
                f"could not parse {path.name}: expected an object with a "
                "'nodes' array; no nodes loaded"
            )
        nodes: list[RelayNode] = []
        for entry in data["nodes"]:
            if not isinstance(entry, dict):
                continue  # skip non-object rows; keep the rest (fail-soft)
            try:
                nodes.append(RelayNode.from_dict(entry))
            except ValueError as e:
                return [], f"could not load {path.name}: {e}; no nodes loaded"
        return nodes, None

    # -- pure queries (no I/O) ---------------------------------------------

    def nodes(self) -> list[RelayNode]:
        """All registered nodes in registry order (the failover ladder order)."""
        return list(self._nodes)

    def nodes_for(self, channel: str) -> list[RelayNode]:
        """Registry-ordered subset of nodes whose ``channels`` contains ``channel``."""
        return [n for n in self._nodes if channel in n.channels]

    def _channels(self) -> list[str]:
        """All distinct channel ids served by the registry, in first-seen order.

        Internal helper for plain-language error text. Order-preserving dedup
        via ``dict.fromkeys`` over the flattened channel stream
        (O(total-channel-mentions), hash-based membership — no nested scan).
        """
        flat = (c for n in self._nodes for c in n.channels)
        return list(dict.fromkeys(flat))

    # -- health (fail-soft probes) -----------------------------------------

    def health(self, name: str) -> NodeHealth:
        """Probe one node by name. Fail-soft — never raises.

        An unknown name returns an ``alive=False`` row naming the known nodes
        (audit- and card-friendly), not a raise. Any probe error is already
        absorbed by the probe contract into an ``alive=False`` row.
        """
        node = next((n for n in self._nodes if n.name == name), None)
        if node is None:
            known = ", ".join(n.name for n in self._nodes) or "(none registered)"
            return NodeHealth(
                name, False, None,
                f"no node named {name!r} in the registry; known nodes: {known}",
            )
        try:
            return self._health_probe(node)
        except Exception as e:  # noqa: BLE001 — defensive: a fake probe may raise
            return NodeHealth(
                name, False, None,
                f"health probe raised: {type(e).__name__}: {e}",
            )

    def health_all(self) -> list[NodeHealth]:
        """Probe every registered node, registry order. The card's data source.

        Never raises. An all-down registry returns N ``alive=False`` rows; an
        empty/unloaded registry returns ``[]``.
        """
        return [self.health(n.name) for n in self._nodes]

    def active_node(self, channel: str) -> RelayNode | None:
        """First node serving ``channel`` whose health is alive, by registry order.

        ``None`` when no node serves the channel or none are healthy (the caller
        / ``route`` turns ``None`` into a plain-language error).
        """
        for node in self.nodes_for(channel):
            if self.health(node.name).alive:
                return node
        return None

    # -- registration -------------------------------------------------------

    def register_node(self, node: RelayNode) -> None:
        """Append (or replace by name) a node in the IN-MEMORY registry.

        Idempotent on ``name``: an existing same-name node is replaced in place
        (order preserved); otherwise the node is appended at the end. This is
        in-memory only — persisting to ``config/relay-nodes.json`` is a separate
        explicit concern (adding a real node is a file edit, not a runtime call).
        """
        for i, existing in enumerate(self._nodes):
            if existing.name == node.name:
                self._nodes[i] = node
                return
        self._nodes.append(node)

    # -- routing (select + send + failover, fail-soft) ---------------------

    def route(
        self,
        channel: str,
        recipient: str,
        payload: dict,
        *,
        transports: TransportMap | None = None,
        health_probe: HealthProbe | None = None,
    ) -> dict:
        """Send ``payload`` on ``channel`` to ``recipient``, failing over by node.

        Walks ``nodes_for(channel)`` in registry order; for each candidate it
        ATTEMPTS the send via ``transports[channel]`` and returns on the first
        ``ok=True``. A failed attempt advances to the next node (the failover
        ladder — this also covers a stale-health race where a node was alive but
        the send still failed). The transport attempt IS the liveness signal, so
        ``route`` does NOT pre-probe health per candidate (KISS — one round-trip
        per node, not two); ``health``/``health_all`` remain the card's path.

        Per-call ``transports`` / ``health_probe`` override the instance defaults
        for test injection. ``health_probe`` is accepted for API symmetry; it is
        unused on the attempt-is-liveness path and reserved for a future
        health-gated routing mode.

        NEVER raises. Returns an audit dict::

            {ok, channel, recipient, node, attempts: [{node, ok, error}, ...]}

        where ``node`` is the name that actually served (or ``None``), and
        ``attempts`` is the full failover trail. On no-serving-node /
        all-failed / unloaded-registry it returns ``ok=False`` with a
        plain-language ``error``.
        """
        del health_probe  # accepted for symmetry; attempt-is-liveness here.
        tmap = transports if transports is not None else self._transports

        candidates = self.nodes_for(channel)
        if not candidates:
            return self._route_failure(
                channel, recipient,
                self._no_node_error(channel),
                attempts=[],
            )

        adapter = tmap.get(channel)
        if adapter is None:
            known = ", ".join(sorted(tmap)) or "(none)"
            return self._route_failure(
                channel, recipient,
                f"no send transport wired for channel {channel!r}; "
                f"wired channels: {known}",
                attempts=[],
            )

        attempts: list[dict] = []
        for node in candidates:
            try:
                result = adapter(node, recipient, payload)
            except Exception as e:  # noqa: BLE001 — adapters shouldn't raise, but be safe
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            ok = bool(result.get("ok"))
            attempts.append({"node": node.name, "ok": ok, "error": result.get("error")})
            if ok:
                return {
                    "ok": True,
                    "channel": channel,
                    "recipient": recipient,
                    "node": node.name,
                    "attempts": attempts,
                }

        # Every serving node was attempted and all failed.
        trail = "; ".join(
            f"{a['node']} ({a['error'] or 'send failed'})" for a in attempts
        )
        n = len(candidates)
        return self._route_failure(
            channel, recipient,
            f"all {n} node(s) serving {channel!r} failed to send: {trail}",
            attempts=attempts,
        )

    # -- private helpers ----------------------------------------------------

    def _no_node_error(self, channel: str) -> str:
        """Plain-language error for a channel no registered node serves."""
        if self.load_error:
            return self.load_error
        known = ", ".join(self._channels()) or "(none — registry is empty)"
        return (
            f"no relay node serves channel {channel!r}; "
            f"known channels: {known}"
        )

    @staticmethod
    def _route_failure(
        channel: str, recipient: str, error: str, *, attempts: list[dict]
    ) -> dict:
        """Build the standard fail-soft route() result dict."""
        return {
            "ok": False,
            "channel": channel,
            "recipient": recipient,
            "node": None,
            "error": error,
            "attempts": attempts,
        }
