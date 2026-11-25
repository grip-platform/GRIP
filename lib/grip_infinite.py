"""grip_infinite — how does this Claude Code launch reach HAL, if at all?

Wave W0 of ADR-0002 (hal/infinite), rebuilt for ADR-0016 (the InfiniteMachine
v2, Step 3). It answers one question at launch time, with no side effects
beyond a bounded receipt: which route does this session's Anthropic traffic
take?

For a Claude Code session on an Anthropic model, three rungs, in order:

1. **Wired by proxy** (``wired-by-proxy``). HAL's wire -- a loopback
   intercepting proxy on 127.0.0.1:4011 -- answers ``GET /health`` as
   ``hal-wire`` within 300 ms, AND its CA certificate is on disk. The launch
   exports ``HTTPS_PROXY`` and ``NO_PROXY`` (both spellings of each) and
   ``NODE_EXTRA_CA_CERTS``, and UNSETS ``ANTHROPIC_BASE_URL``. The base URL
   stays Anthropic's, which is what keeps Remote Control working: Claude Code
   refuses Remote Control for any base URL but api.anthropic.com (ADR-0016
   decision 1). All or nothing: if any value cannot be produced, the next rung.
   It also points ``CLAUDE_ENV_FILE`` at a script that sends Bash commands
   direct (GRIP#7460), unless the caller has one of their own.
2. **Wired by base URL** (``wired``). No usable wire, but HAL's gateway on
   :4010 answers as ``hal-gateway``: exactly the W0 behaviour, kept so main
   never loses the witness while the wire is being built. Its receipt says
   ``transitional``: ADR-0016 Step 7 removes this rung for Claude Code, and it
   refuses Remote Control.
3. **Unwitnessed** (``unwitnessed``). Neither answers, or the brake is
   engaged. The launch goes direct, says so on one visible line, and leaves a
   receipt (decision 4: a host whose wire is down launches direct, badged).

A model-family launch (``gg++ claude --kimi``: ``GRIP_HARNESS_MODEL``) and
every other harness keep their base-URL wiring untouched: a non-Anthropic
model needs the gateway's translation, which only the base URL reaches.

Two failure directions, deliberately opposite:

* **HAL is down → fail OPEN.** Launch direct. A dead wire or gateway must
  never cost the operator a session. Each probe runs in a daemon thread with
  a HARD wall-clock bound — urllib/http.client timeouts do not cover DNS
  resolution, so a hanging lookup on a custom URL would otherwise hold the
  launch (AGORA #5022 round 1).
* **The operator brake is engaged → fail CLOSED.** Do not wire a new
  session, and treat an unreadable marker as engaged. A brake you cannot
  read is a brake you must assume is on.

Credential custody (H-INF-3, measured live 2026-08-24 across three arms):
with ``ANTHROPIC_API_KEY`` in the environment a headless ``claude -p`` sends
``x-api-key`` and bills the key, not the subscription — through the gateway
and around it alike, so the gateway is not the cause; the ambient key is. A
WIRED launch therefore scrubs ``ANTHROPIC_API_KEY`` and
``ANTHROPIC_AUTH_TOKEN`` (via the launch protocol's ``UNSET`` lines): the
declared purpose of the wire is subscription custody. The proxy rung makes no
exception: a wired-by-proxy session NEVER inherits an ambient API key or token,
whatever the operator's auth-failover marker (``bin/grip-auth-failover``) says.
It runs on the signed-in subscription, and Remote Control refuses API-key
sign-ins, so carrying the key would defeat the route. When the marker has
selected the API-key bucket, the launch says on one visible line that it was
not applied. The marker itself stays: the base-URL and direct rungs keep its
semantics exactly as before, and the conflict closes for good in ADR-0016
Step 5, when HAL owns failover (GRIP#7274). Unwired launches keep the
operator's env exactly as found.

Identity, not just liveness: any process can squat a port, a mute listener
accepts and never replies, and every web server speaks HTTP — so each probe
requires its server's own ``GET /health`` identity (``ok:true`` plus
``service:"hal-gateway"`` or ``service:"hal-wire"``), a local route that
touches no upstream. A gateway answering on the wire's port is not the wire.

``GRIP_INFINITE_URL`` overrides the gateway base (default
http://127.0.0.1:4010; ``GRIP_INFINITE_BASE_URL`` is honoured as an alias),
``GRIP_WIRE_URL`` the wire (default http://127.0.0.1:4011, loopback only), and
``HAL_HOME`` where the wire's CA lives (default ~/.hal). Every launch decision
appends one JSONL row to ``state/hal-infinite/wire.jsonl`` (bounded write — a
wedged filesystem cannot hold the launch), the D5 soak evidence.
"""

from __future__ import annotations

import datetime as _dt
import http.client
import json
import os
import re
import ssl
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

#: Default gateway the base-URL rung wires to. Real, verified port — never a
#: framework default (``rules/port-registry.md``).
DEFAULT_URL = "http://127.0.0.1:4010"

#: Socket-level budget for the gateway probe. The daemon-thread join adds
#: ``_PROBE_GRACE_S`` on top as the HARD wall-clock bound (covers DNS).
PROBE_TIMEOUT_S = 0.5
_PROBE_GRACE_S = 0.5

#: The gateway's local liveness identity (HAL #532): no auth, no upstream.
HEALTH_PATH = "/health"
HEALTH_SERVICE = "hal-gateway"

#: HAL's wire (ADR-0016 Step 2): the loopback intercepting proxy, its port
#: reserved in rules/port-registry.md. Loopback only — decrypted traffic and the
#: host's CA never leave the host (ADR-0016 decision 4).
DEFAULT_WIRE_URL = "http://127.0.0.1:4011"
WIRE_SERVICE = "hal-wire"
#: The wire answers within this budget or the launch does not use it. It is
#: also the probe's hard wall-clock bound: no grace on top, so a slow wire
#: costs a launch at most this long.
WIRE_PROBE_TIMEOUT_S = 0.3
#: Hosts a wired session reaches without the proxy: loopback, the tailnet's
#: MagicDNS names and its CGNAT range.
WIRE_NO_PROXY = "127.0.0.1,localhost,.ts.net,100.64.0.10/10"

_ENV_FLAG = "GRIP_INFINITE"
_ENV_URL = "GRIP_INFINITE_URL"
_ENV_URL_ALIAS = "GRIP_INFINITE_BASE_URL"
_ENV_BASE = "ANTHROPIC_BASE_URL"
_ENV_WIRE_URL = "GRIP_WIRE_URL"
_ENV_FAMILY = "GRIP_HARNESS_MODEL"
_ENV_FAILOVER = "GRIP_AUTH_FAILOVER_MARKER"
_CA_VAR = "NODE_EXTRA_CA_CERTS"
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy")
_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")
#: The order Claude Code reads proxy variables in. Vendor network-config page,
#: read 2026-10-05: "the first one that's set in the order https_proxy,
#: HTTPS_PROXY, http_proxy, HTTP_PROXY".
_PROXY_READ_ORDER = ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY")

#: Ambient credentials a WIRED launch scrubs (see module docstring). Order is
#: the precedence order Claude Code itself applies.
_SCRUB_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: The wire's protocol lines, exactly as bin/grip's grip_handoff validates them
#: (its _GRIP_WIRE_*_RE twins; the tests run both over one corpus). A value the
#: validator would reject is never emitted: the rung falls through instead.
_PROXY_LINE_RE = re.compile(r"(HTTPS_PROXY|https_proxy)=http://127\.0\.0\.1:([1-9][0-9]{0,4})")
_NO_PROXY_LINE_RE = re.compile(r"(NO_PROXY|no_proxy)=[A-Za-z0-9.*:/_-]+(,[A-Za-z0-9.*:/_-]+)*")
_CA_LINE_RE = re.compile(r'NODE_EXTRA_CA_CERTS=/[^"\\\x00-\x1f\x7f]+\.pem')
#: A shell runs this file, so its path admits no space, quote or metacharacter.
_ENV_FILE_LINE_RE = re.compile(r"CLAUDE_ENV_FILE=/[A-Za-z0-9._/@+-]+\.sh")
# HAL#657 T1 S6 measured 25 s clean on all four clients; 5d is not yet shipped.
# Retain the 600 s API ceiling, above that measured floor (not a 600 s proof).
WIRE_HOLD_TIMEOUT_MS = 600_000
_TIMEOUT_LINE_RE = re.compile(r"(API_TIMEOUT_MS|CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS|CLAUDE_STREAM_IDLE_TIMEOUT_MS|CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS)=[1-9][0-9]{0,8}")
#: Claude Code's gateway hint headers (x-claude-code-prompt-id and
#: x-claude-code-request-class) reach a custom base URL only with this set
#: (vendor llm-gateway-protocol page; measured on 2.1.301: unset sends the
#: session id alone). The wire reads both, so the wire launch sets it, and
#: the helper emits exactly "=1": the shape admits nothing else (GRIP#8456).
_HINT_VAR = "CLAUDE_CODE_GATEWAY_HINT_HEADERS"
_HINT_LINE_RE = re.compile(r"CLAUDE_CODE_GATEWAY_HINT_HEADERS=1")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

Fetch = Callable[[str, float], Tuple[int, bytes]]


def _value(environ: Mapping[str, str], name: str) -> str:
    """One environment value, stripped; "" when absent."""
    return str(environ.get(name, "")).strip()


def is_wire_line(line: str) -> bool:
    """Does one protocol line pass the wire's strict shapes? (bash validator's twin)"""
    proxy = _PROXY_LINE_RE.fullmatch(line)
    if proxy:
        return int(proxy.group(2)) <= 65535
    return bool(_NO_PROXY_LINE_RE.fullmatch(line) or _CA_LINE_RE.fullmatch(line)
                or _ENV_FILE_LINE_RE.fullmatch(line) or _TIMEOUT_LINE_RE.fullmatch(line)
                or _HINT_LINE_RE.fullmatch(line))


_USERINFO_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)?[^/@\s]+@")
_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    """A URL or label fit for a log line: no ``user:password@``, no Anthropic key.

    Every value this module prints passes through here — a caller's preset
    base URL, a proxy, a URL override — because any of them can carry a
    credential in its userinfo, and a corporate proxy usually does. This is the
    scrub the ``already-wired`` branch needed for the preset it echoes,
    applied to every branch's output (ADR-0016 Step 3).
    """
    shown = _USERINFO_RE.sub(lambda m: (m.group(1) or "") + "***@", str(text))
    return _KEY_RE.sub("sk-ant-***", shown)


# ── operator brake ───────────────────────────────────────────────────────

def brake_marker_path(claude_dir: Optional[Path] = None) -> Path:
    """Where the operator brake lives: ``state/session/infinite-stop.json``.

    Sibling of ``bin/grip-stop``'s outbound brake, deliberately a *separate*
    file — the WhatsApp brake and the InfiniteMachine brake must never trip
    each other. The SAME path is produced by ``bin/grip-infinite-stop`` and
    consumed by HAL's gateway (``lib/hal/gateway_brake.py``, reported live on
    ``GET /health``): one marker, one kill switch, both halves.
    """
    root = claude_dir if claude_dir is not None else get_claude_dir()
    return root / "state" / "session" / "infinite-stop.json"


@dataclass(frozen=True)
class BrakeState:
    """Whether the operator has pulled the brake, and what we know about it."""

    engaged: bool
    reason: str = ""
    since: str = ""
    unreadable: bool = False


def brake_state(marker: Optional[Path] = None) -> BrakeState:
    """Read the brake. PRESENCE of the marker IS the engaged state.

    The deployed HAL consumer (gateway_brake.brake_engaged) uses the same
    presence semantics, and the two halves of one kill switch must agree — a
    marker whose *content* could release it here while the gateway stayed
    frozen would be two divergent brakes wearing one name. A marker that
    exists but cannot be read or parsed still engages (fail-closed); its
    reason/since are surfaced when it parses.
    """
    path = marker if marker is not None else brake_marker_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return BrakeState(engaged=False)
    except (OSError, UnicodeDecodeError, ValueError):
        # Unreadable bytes are exactly as engaged as an unreadable file.
        return BrakeState(engaged=True, reason="marker unreadable", unreadable=True)
    try:
        info = json.loads(raw)
        if not isinstance(info, dict):
            raise ValueError("marker is not an object")
    except ValueError:
        return BrakeState(engaged=True, reason="marker unparseable", unreadable=True)
    return BrakeState(
        engaged=True,
        reason=str(info.get("reason") or "manual"),
        since=str(info.get("engaged_at") or ""),
    )


def _brake_text(brake: BrakeState) -> str:
    """The brake's reason, with when it was pulled when the marker says."""
    text = brake.reason or "manual"
    return f"{text} (since {brake.since})" if brake.since else text


# ── HAL liveness + identity: the gateway and the wire ───────────────────

def _endpoint(url: str) -> tuple:
    """(host, port) for a URL, with the spellings of localhost unified.

    ``localhost``, ``127.0.0.1`` and ``::1`` name the same gateway, and a
    trailing ``/v1`` or ``/`` does not change which server answers. Comparing
    raw strings judged those "elsewhere", so an engaged brake silently failed
    to detach a preset written any way but one (AGORA #5020 round 3).
    """
    parts = urlsplit(url)
    host = (parts.hostname or "127.0.0.1").lower()
    if host in ("localhost", "::1", "ip6-localhost"):
        host = "127.0.0.1"
    return host, parts.port or (443 if parts.scheme == "https" else 80)


def same_endpoint(a: str, b: str) -> bool:
    """Do two URLs reach the same server? Path and host spelling ignored."""
    try:
        return _endpoint(a) == _endpoint(b)
    except ValueError:
        return False


def preset_routing(env: Optional[Mapping[str, str]] = None) -> Tuple[str, bool]:
    """The caller's own base URL, and whether it reaches OUR gateway.

    Three branches need exactly this pair and each had grown its own copy:
    the off-branch (is a launch we are not wiring nonetheless routed?), the
    preset branch (is the caller's routing ours or someone else's?), and the
    custody check (is there a wire here that nobody scrubbed?). Three
    independent spellings of one predicate is three places for them to drift
    apart — and drifting apart is precisely how the off-branch came to report
    "direct" while the preset branch, testing the same condition, did not.
    """
    environ = os.environ if env is None else env
    preset = _value(environ, _ENV_BASE)
    if not preset:
        return "", False
    # Compare against the DEFAULT gateway as well as the configured one. The
    # override is itself stale-config-prone — the same four-day-old-shell
    # family this whole change is about — and keying the check to it lets an
    # old GRIP_INFINITE_URL pointing elsewhere report "direct" for a launch
    # that really is crossing the default gateway unscrubbed.
    return preset, any(same_endpoint(preset, base)
                       for base in (gateway_base(environ), DEFAULT_URL))


def _default_fetch(url: str, timeout: float) -> Tuple[int, bytes]:
    """GET ``url``'s /health → (status, body). Raises on transport errors."""
    host, port = _endpoint(url)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", HEALTH_PATH)
        resp = conn.getresponse()
        return int(resp.status), resp.read()
    finally:
        conn.close()


def _identity_probe(url: str, service: str, timeout: float, bound: float,
                    fetch: Fetch) -> bool:
    """True only on a 200 from /health whose body is ``{ok: true, service}``.

    The fetch runs in a daemon thread joined with a HARD wall-clock ``bound``:
    socket timeouts do not cover ``getaddrinfo``, so an unresolvable custom
    host could otherwise hang the probe and with it the launch (AGORA #5022
    round 1). A probe still running at the bound is abandoned and reads down.
    """
    outcome: dict = {}

    def _probe() -> None:
        try:
            outcome["resp"] = fetch(url, timeout)
        except Exception:
            outcome["resp"] = None

    worker = threading.Thread(target=_probe, daemon=True)
    worker.start()
    worker.join(bound)
    resp = outcome.get("resp")
    if worker.is_alive() or resp is None:
        return False
    try:
        status, body = resp
        doc = json.loads(body.decode("utf-8"))
        return status == 200 and doc.get("ok") is True and doc.get("service") == service
    except Exception:
        return False


def gateway_alive(
    url: str = DEFAULT_URL,
    timeout: float = PROBE_TIMEOUT_S,
    fetch: Optional[Fetch] = None,
) -> bool:
    """Strict identity: 200 on /health AND the body names the HAL gateway.

    Port answering is not evidence (any process can squat a port); a mute
    listener that accepts and never replies is not alive (AGORA #5020); the
    stdlib 404 page is not identity. Only ``{"ok": true, "service":
    "hal-gateway"}`` wires a launch — anything else, including a timeout or
    a hang, reads as down.

    A malformed URL raises rather than returning False: that is a
    configuration error, not a gateway that is down, and the two deserve
    different answers. Both entry points fail open around it.
    """
    _endpoint(url)  # outside the guard: a bad URL is a loud error
    return _identity_probe(url, HEALTH_SERVICE, timeout, timeout + _PROBE_GRACE_S,
                           _default_fetch if fetch is None else fetch)


def wire_alive(
    url: str = DEFAULT_WIRE_URL,
    timeout: float = WIRE_PROBE_TIMEOUT_S,
    fetch: Optional[Fetch] = None,
) -> bool:
    """Strict identity for HAL's wire: 200 on /health naming ``hal-wire``.

    The gateway's rules, with one difference: the budget IS the bound, so the
    wire answers within 300 ms or the launch goes on without it.
    """
    _endpoint(url)
    return _identity_probe(url, WIRE_SERVICE, timeout, timeout,
                           _default_fetch if fetch is None else fetch)


def gateway_base(env: Optional[Mapping[str, str]] = None) -> str:
    """The gateway URL: GRIP_INFINITE_URL → its legacy alias → the default."""
    environ = os.environ if env is None else env
    return (_value(environ, _ENV_URL) or _value(environ, _ENV_URL_ALIAS)
            or DEFAULT_URL)


def infinite_requested(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only on the exact opt-in ``GRIP_INFINITE=1``.

    Exact match on BOTH surfaces — bin/grip gates on the same exact string —
    so no value one side documents is unreachable through the other (AGORA
    #5022 round 2). Padded or truthy-looking values stay off.
    """
    environ = os.environ if env is None else env
    return environ.get(_ENV_FLAG, "") == "1"


# ── the wire: HAL's loopback intercepting proxy (ADR-0016) ──────────────

def wire_base(env: Optional[Mapping[str, str]] = None) -> str:
    """The wire URL: GRIP_WIRE_URL → the default 127.0.0.1:4011."""
    environ = os.environ if env is None else env
    return _value(environ, _ENV_WIRE_URL) or DEFAULT_WIRE_URL


def hal_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """HAL's home: ``$HAL_HOME``, else ``~/.hal`` (HAL's own convention)."""
    environ = os.environ if env is None else env
    home = _value(environ, "HAL_HOME")
    return Path(home).expanduser() if home else Path.home() / ".hal"


def wire_ca_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The wire's public CA certificate, where HAL's Step 2 writes it."""
    return hal_home(env) / "state" / "wire-ca" / "hal-wire-ca.pem"


def ca_certificate_ok(path: Path) -> bool:
    """Does OpenSSL load at least one CA certificate from this file?

    Stdlib ``ssl`` only, so it runs under whichever interpreter bin/grip finds.
    Stricter than "parses as PEM", deliberately: a leaf certificate parses but
    cannot anchor the wire's leaves, so a file holding no CA certificate is no
    wire. Expiry is not checked here; the TLS handshake owns that.
    """
    try:
        if not path.is_file():
            return False
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=str(path))
        return ctx.cert_store_stats().get("x509_ca", 0) > 0
    except (OSError, ssl.SSLError, ValueError):
        return False


def _wire_proxy_url(url: str) -> str:
    """The HTTPS_PROXY value for a wire URL, or "" unless it is loopback http."""
    try:
        host, port = _endpoint(url)
        scheme = urlsplit(url).scheme
    except ValueError:
        return ""
    if scheme != "http" or host != "127.0.0.1" or not 0 < port < 65536:
        return ""
    return f"http://127.0.0.1:{port}"


@dataclass(frozen=True)
class WireCheck:
    """Whether this launch can ride the wire, and every value it would need."""

    ready: bool
    detail: str
    proxy: str = ""
    ca: str = ""


def check_wire(env: Optional[Mapping[str, str]] = None, *,
               alive: Optional[Callable[[str], bool]] = None) -> WireCheck:
    """All or nothing: ready only when every value the proxy rung emits exists.

    The cheap checks run first, so a host with no wire CA on disk pays no
    network probe at all, and its launch costs what it did before the wire.
    """
    environ = os.environ if env is None else env
    url = wire_base(environ)
    proxy = _wire_proxy_url(url)
    if not proxy:
        return WireCheck(False, f"{redact(url)} is not a loopback http:// address")
    ca = wire_ca_path(environ)
    if not is_wire_line(f"{_CA_VAR}={ca}"):
        return WireCheck(False, f"the CA path {ca} is not one the launcher accepts")
    if not ca_certificate_ok(ca):
        return WireCheck(False, f"no CA certificate at {ca}")
    try:
        up = (alive or wire_alive)(proxy)
    except Exception:
        up = False
    if not up:
        budget = int(WIRE_PROBE_TIMEOUT_S * 1000)
        return WireCheck(False, f"nothing answers as {WIRE_SERVICE} on {proxy} "
                                f"within {budget} ms")
    return WireCheck(True, f"{WIRE_SERVICE} on {proxy}", proxy=proxy, ca=str(ca))


def wire_env(check: WireCheck) -> dict:
    """The routing, timeout and hint-header values a wired-by-proxy launch exports."""
    values = {name: check.proxy for name in _PROXY_VARS}
    values.update({name: WIRE_NO_PROXY for name in _NO_PROXY_VARS})
    values[_CA_VAR] = check.ca
    values.update({name: str(WIRE_HOLD_TIMEOUT_MS) for name in (
        "API_TIMEOUT_MS", "CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS")})
    # The wire copies x-claude-code-prompt-id onto its signed row (HAL#769) and
    # its failover classifier reads x-claude-code-request-class. Behind a custom
    # base URL Claude Code sends neither unless this is set; whether a proxy in
    # front of Anthropic's own base URL counts as "direct" is unmeasured, and
    # the opt-in costs nothing here. Only the wire launch carries it: the
    # gateway rung reads no hint, and a direct session keeps Claude Code's
    # default (GRIP#8456, after Codex's review of the managed-env version).
    values[_HINT_VAR] = "1"
    return values


def _is_our_wire(value: str, environ: Mapping[str, str]) -> bool:
    return any(same_endpoint(value, base)
               for base in (wire_base(environ), DEFAULT_WIRE_URL))


def proxy_routing(env: Optional[Mapping[str, str]] = None) -> Tuple[str, str, bool]:
    """The proxy Claude Code would use: (variable, value, is it OUR wire)."""
    environ = os.environ if env is None else env
    for name in _PROXY_READ_ORDER:
        value = _value(environ, name)
        if value:
            return name, value, _is_our_wire(value, environ)
    return "", "", False


def inherited_wire_vars(env: Optional[Mapping[str, str]] = None) -> tuple:
    """The variables in this env that carry OUR wire's values.

    A launch from inside a wired session inherits them. When this launch does
    not ride the wire — the brake is on, or the wire stopped answering — they
    must go: otherwise a braked child still rides the wire, and a child of a
    dead wire cannot reach Anthropic at all. Exact matches only: a NO_PROXY or
    a CA bundle of the caller's own is theirs. The hint-header opt-in rides
    with the wire's values (GRIP#8456), so it goes with them, and only with
    them: its value carries no mark of ours, and a caller may set it for a
    gateway of their own, so on its own it is never touched (GRIP#8490).
    """
    environ = os.environ if env is None else env
    found = [n for n in _PROXY_VARS
             if _value(environ, n) and _is_our_wire(_value(environ, n), environ)]
    found += [n for n in _NO_PROXY_VARS if _value(environ, n) == WIRE_NO_PROXY]
    if _value(environ, _CA_VAR) == str(wire_ca_path(environ)):
        found.append(_CA_VAR)
    if found and _value(environ, _HINT_VAR):
        found.append(_HINT_VAR)
    return tuple(found)


def _caller_network(environ: Mapping[str, str]) -> str:
    """Why the caller's own network settings rule the wire out, or "".

    The precedence, defined: a proxy the caller set — any spelling Claude Code
    reads — wins and the wire is not used, because the wire connects upstream
    itself and would route around the caller's proxy without a word. So does a
    CA bundle of the caller's own, because NODE_EXTRA_CA_CERTS names one file
    and the wire's would replace it. A NO_PROXY of the caller's own modifies no
    proxy once theirs is absent, so the wire's list replaces it.
    """
    name, value, ours = proxy_routing(environ)
    if value and not ours:
        return f"{name}={redact(value)} is the caller's own proxy and stays in place"
    bundle = _value(environ, _CA_VAR)
    if bundle and bundle != str(wire_ca_path(environ)):
        return f"{_CA_VAR}={bundle} is the caller's own CA bundle and stays in place"
    return ""


# ── the auth-failover marker (bin/grip-auth-failover) ────────────────────

def auth_failover_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """The marker bin/grip-auth-failover writes, at the path ~/.zshrc and the
    relay read: ``$GRIP_AUTH_FAILOVER_MARKER``, else
    ``<claude dir>/state/session/auth-failover.json``."""
    environ = os.environ if env is None else env
    override = _value(environ, _ENV_FAILOVER)
    if override:
        return Path(override)
    return get_claude_dir() / "state" / "session" / "auth-failover.json"


@dataclass(frozen=True)
class FailoverMarker:
    """Whether the operator's API-key bucket is selected for new sessions."""

    engaged: bool
    by: str = ""


def auth_failover_state(path: Path) -> FailoverMarker:
    """Read the marker as ``lib/relay_dispatcher.py`` does: engaged only when
    its ``bucket`` field is ``api-key``.

    Fail-safe in the OPPOSITE direction to the brake, on purpose. An unreadable
    brake is engaged, because stopping is the safe side; an unreadable failover
    marker is not, matching the relay's own rule (no spurious failover). In
    this module the marker changes no route: a wired-by-proxy launch scrubs the
    key either way, and the marker only decides whether that launch says it was
    not applied, and what ``explain`` reports.
    """
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return FailoverMarker(False)
    if not isinstance(info, dict) or info.get("bucket") != "api-key":
        return FailoverMarker(False)
    return FailoverMarker(True, by=redact(str(info.get("by") or "")))


def _marker_not_applied_note(marker: FailoverMarker) -> str:
    """The visible line a wired-by-proxy launch prints while the marker is engaged."""
    who = f" ({marker.by})" if marker.by else ""
    return (f"[grip-infinite] auth-failover marker not applied{who}: a wired session "
            "runs on the signed-in subscription, because Remote Control refuses "
            "API-key sign-ins and carrying the key would defeat the route")


def family_launch(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is this Claude Code launch on a model family (``gg++ claude --kimi``)?

    Such a launch goes through bin/grip's ``_grip_family_env``, which in
    gateway mode refuses unless ``ANTHROPIC_BASE_URL`` is set, and HAL serves
    the family only behind its gateway — so it keeps the base-URL route
    whatever the wire says. Any non-empty ``GRIP_HARNESS_MODEL`` counts, a
    claude-* id included: the family helper refuses those itself with an
    accurate message, which a proxy rung would replace with a misleading
    "nothing routed this launch" refusal.
    """
    environ = os.environ if env is None else env
    return bool(_value(environ, _ENV_FAMILY))


# ── the launch decision ──────────────────────────────────────────────────

#: How each reason reaches HAL. A reason absent here is direct.
_MODE_BY_REASON = {
    "wired-by-proxy": "proxy", "off-via-wire": "proxy",
    "wired": "base-url", "already-wired": "base-url", "off-via-gateway": "base-url",
}


@dataclass(frozen=True)
class Decision:
    """The launch-time verdict, and why."""

    wire: bool
    reason: str
    detail: str = ""
    env: dict = field(default_factory=dict)
    #: Variables this launch must REMOVE from its environment: the ambient
    #: base URL when the proxy rung or the brake detaches it, an inherited
    #: wire, and the ambient credentials when a wired launch takes
    #: subscription custody (module docstring).
    unset: tuple = ()
    #: True only on the base-URL rung of an Anthropic session: kept while the
    #: wire is built, removed by ADR-0016 Step 7. A model family is never
    #: transitional — the base URL is its permanent route.
    transitional: bool = False
    #: Extra visible lines this launch owes the operator (the auth bucket).
    notes: tuple = ()

    @property
    def wire_mode(self) -> str:
        """``proxy``, ``base-url`` or ``direct``: how this launch reaches HAL."""
        return _MODE_BY_REASON.get(self.reason, "direct")

    @property
    def routes_gateway(self) -> bool:
        """Does this launch's traffic cross OUR gateway, however it got there?

        Distinct from ``wire``, which is an ACTION flag: whether this launch
        must set its route itself. A launch that arrived already pointed at
        the gateway sets nothing, so ``wire`` is correctly False — but it is
        emphatically not direct, and a consumer branching on ``wire`` alone
        would conclude that it was. Read from ``wire_mode``, the single table
        of which reason routes where.
        """
        return self.wire_mode == "base-url"

    def summary(self) -> str:
        if self.wire:
            verb = "wire"
        else:   # never "direct" when it demonstrably is not
            verb = {"proxy": "via-wire", "base-url": "via-gateway"}.get(
                self.wire_mode, "direct")
        base = f"{verb} ({self.reason})"
        return f"{base}: {self.detail}" if self.detail else base


def decide(
    env: Optional[Mapping[str, str]] = None,
    *,
    marker: Optional[Path] = None,
    probe: Optional[Callable[[str], bool]] = None,
    wire_probe: Optional[Callable[[Mapping[str, str]], WireCheck]] = None,
    failover: Optional[Path] = None,
) -> Decision:
    """Decide how this launch reaches HAL, and return the env delta.

    ``probe`` (gateway liveness), ``wire_probe`` (a WireCheck for the env) and
    ``failover`` (the auth-failover marker's path) are injectable, so tests
    never touch a socket or the operator's real state. The order of checks is
    the order of authority: the master switch, the model family, the brake,
    the caller's own routing, then liveness — the wire before the gateway.
    """
    environ = os.environ if env is None else env
    if not infinite_requested(environ):
        return _decide_off(environ)
    brake = brake_state(marker)
    if family_launch(environ):
        return _decide_family(environ, brake, probe)
    return _decide_claude(environ, brake, probe, wire_probe, failover)


def _decide_off(environ: Mapping[str, str]) -> Decision:
    """The switch is off: change nothing, probe nothing, report the truth.

    The switch being off does not make an inherited route stop routing.
    Measured 2026-08-24: a session descending from a four-day-old shell
    carried the RETIRED always-on base URL while GRIP_INFINITE never reached
    it — gateway audit rows proved the traffic crossed :4010 while this branch
    reported "direct". An inherited wire is the same case one port up. Pure
    string comparison: the no-probe-when-off invariant is intact.
    """
    preset, routed = preset_routing(environ)
    if routed:
        return Decision(False, "off-via-gateway",
                        f"{_ENV_FLAG} is not 1, but {_ENV_BASE}={redact(preset)} "
                        "still routes this launch through the gateway — the seam "
                        "never ran, so no custody scrub and no receipt")
    name, value, ours = proxy_routing(environ)
    if ours:
        return Decision(False, "off-via-wire",
                        f"{_ENV_FLAG} is not 1, but {name}={redact(value)} still "
                        "routes this launch through the wire — the seam never ran, "
                        "so no custody scrub and no receipt")
    return Decision(False, "off", f"{_ENV_FLAG} is not 1 (opt-in per machine)")


def _decide_family(environ: Mapping[str, str], brake: BrakeState,
                   probe: Optional[Callable[[str], bool]]) -> Decision:
    """A model family: exactly the W0 base-URL route, brake included.

    The brake outranks the preset: on a machine whose profile exports the
    gateway URL ambiently, "brake engaged" must actually detach the launch or
    the operator gets a false safety signal (AGORA #5020 round 2).
    """
    preset, ours = preset_routing(environ)
    if brake.engaged:
        if ours:
            return Decision(False, "brake", f"{_brake_text(brake)}; detaching {_ENV_BASE}",
                            unset=(_ENV_BASE,))
        if preset:
            return Decision(False, "brake", f"{_brake_text(brake)}; NOTE "
                            f"{_ENV_BASE}={redact(preset)} still routes this launch")
        return Decision(False, "brake", _brake_text(brake))
    if preset and not ours:
        return Decision(False, "preset", f"{_ENV_BASE} already set to {redact(preset)}")
    return _base_url_rung(environ, preset, ours, probe)


def _base_url_rung(environ: Mapping[str, str], preset: str, ours: bool,
                   probe: Optional[Callable[[str], bool]]) -> Decision:
    """The gateway by base URL: already there, wired now, or not answering."""
    if ours:
        # Whoever set it is routing deliberately; we do not second-guess them
        # — but we do report where it actually goes rather than saying
        # "direct" while the traffic crosses the gateway (AGORA #5020 r3).
        return Decision(False, "already-wired",
                        f"{_ENV_BASE}={redact(preset)} — this launch already routes "
                        "via the gateway; nothing to change")
    url = gateway_base(environ)
    if not (probe or gateway_alive)(url):
        return Decision(False, "gateway-down", f"nothing listening on {redact(url)}")
    scrub = tuple(v for v in _SCRUB_VARS if _value(environ, v))
    return Decision(True, "wired", redact(url), {_ENV_BASE: url}, unset=scrub)


def _decide_claude(environ: Mapping[str, str], brake: BrakeState,
                   probe: Optional[Callable[[str], bool]],
                   wire_probe: Optional[Callable[[Mapping[str, str]], WireCheck]],
                   failover: Optional[Path]) -> Decision:
    """An Anthropic session: the wire, else the gateway, else direct and badged."""
    if brake.engaged:
        return _unwitnessed_by_brake(environ, brake)
    preset, ours = preset_routing(environ)
    if preset and not ours:
        return Decision(False, "preset", f"{_ENV_BASE} already set to {redact(preset)}")
    owned = _caller_network(environ)
    check = WireCheck(False, owned) if owned else (wire_probe or check_wire)(environ)
    if check.ready:
        return _wired_by_proxy(environ, check, failover)
    fallback = _base_url_rung(environ, preset, ours, probe)
    detach = inherited_wire_vars(environ)
    if fallback.reason == "gateway-down":
        cause = f"{owned}, and no gateway answered" if owned else "no local wire or gateway"
        return Decision(False, "unwitnessed", cause, unset=detach)
    # TRANSITIONAL. Kept so main never loses the witness while the wire is
    # built: ADR-0016 Step 7 removes this rung for Claude Code. It refuses
    # Remote Control, which is the whole reason the wire exists.
    return replace(fallback, transitional=True, unset=fallback.unset + detach)


def _unwitnessed_by_brake(environ: Mapping[str, str], brake: BrakeState) -> Decision:
    """The brake on an Anthropic session: direct, badged, and detached from
    every route of ours it inherited — a brake that leaves the route in place
    is a false safety signal (AGORA #5020 round 2)."""
    preset, ours = preset_routing(environ)
    unset = ((_ENV_BASE,) if ours else ()) + inherited_wire_vars(environ)
    detail = f"brake engaged: {_brake_text(brake)}"
    if unset:
        detail += f"; detaching {', '.join(unset)}"
    if preset and not ours:
        detail += f"; NOTE {_ENV_BASE}={redact(preset)} still routes this launch"
    return Decision(False, "unwitnessed", detail, unset=unset)


def _wired_by_proxy(environ: Mapping[str, str], check: WireCheck,
                    failover: Optional[Path]) -> Decision:
    """Rung 1. The base URL is unset so it stays Anthropic's (Remote Control
    keeps working), and both ambient credentials are unset UNCONDITIONALLY:
    set or not, empty or not, whatever the auth-failover marker says. A wired
    session runs on the signed-in subscription, and Remote Control refuses
    API-key sign-ins. An engaged marker earns one visible line saying it was
    not applied, and changes nothing else."""
    path = failover if failover is not None else auth_failover_path(environ)
    marker = auth_failover_state(path)
    notes = (_marker_not_applied_note(marker),) if marker.engaged else ()
    return Decision(True, "wired-by-proxy", check.proxy, wire_env(check),
                    unset=(_ENV_BASE,) + _SCRUB_VARS, notes=notes)


# ── Bash commands go direct (GRIP#7460) ──────────────────────────────────
#
# Claude Code runs CLAUDE_ENV_FILE before every Bash command, in that command's
# own shell, and nothing else reads it: Claude Code's own traffic, hooks and MCP
# servers keep the launch environment. Measured 2026-10-05: with a file that
# unsets the proxy, a headless session's Bash child had no proxy while the
# control kept 127.0.0.1:4011. So on the proxy rung, commands reach GitHub,
# package registries and the web directly, and only Claude Code's own traffic
# rides the wire. While the wire's egress brake is engaged (bin/grip-wire-brake
# on), commands keep the proxy and the wire refuses unknown hosts for them too.
# The script is written under state/session/ (gitignored), never pointed at a
# tracked file: hooks may append to CLAUDE_ENV_FILE, and the launch rewrites it.

_ENV_FILE_VAR = "CLAUDE_ENV_FILE"

BASH_ENV_SCRIPT = """\
# Written by lib/grip_infinite.py for Claude Code sessions behind the HAL wire
# (GRIP#7460). Claude Code runs this before every Bash command: commands go
# direct, while Claude Code's own traffic stays on the wire. With the wire's
# egress brake engaged (bin/grip-wire-brake on), commands keep the wire too.
if [ ! -e "${HAL_WIRE_EGRESS_BRAKE_MARKER:-${CLAUDE_DIR:-$HOME/.claude}/state/session/wire-egress-brake.json}" ]; then
    unset HTTPS_PROXY https_proxy NODE_EXTRA_CA_CERTS
fi
"""

_CALLER_ENV_FILE_NOTE = ("[grip-infinite] CLAUDE_ENV_FILE is the caller's own and stays in "
                         "place, so Bash commands ride the wire too")


def bash_env_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """Where the Bash-env script lives: state/session/ under CLAUDE_DIR."""
    environ = os.environ if env is None else env
    base = _value(environ, "CLAUDE_DIR")
    root = Path(base).expanduser() if base else get_claude_dir()
    return root / "state" / "session" / "wire-bash-env.sh"


def _write_bash_env(path: Path) -> bool:
    """Write the script atomically, unless it is already exactly right."""
    try:
        if path.is_file() and path.read_text() == BASH_ENV_SCRIPT:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(BASH_ENV_SCRIPT)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def with_bash_env(d: Decision, env: Optional[Mapping[str, str]] = None, *,
                  write: Optional[Callable[[Path], bool]] = None) -> Decision:
    """On the proxy rung, add CLAUDE_ENV_FILE so that Bash commands go direct.

    A CLAUDE_ENV_FILE of the caller's own wins: it stays in place, Bash commands
    ride the wire, and one visible line says so. Anything that stops the script
    being written leaves the decision exactly as it was.
    """
    if d.reason != "wired-by-proxy":
        return d
    environ = os.environ if env is None else env
    path = bash_env_path(environ)
    current = _value(environ, _ENV_FILE_VAR)
    if current and current != str(path):
        return replace(d, notes=d.notes + (_CALLER_ENV_FILE_NOTE,))
    if not is_wire_line(f"{_ENV_FILE_VAR}={path}") or not (write or _write_bash_env)(path):
        return d
    return replace(d, env={**d.env, _ENV_FILE_VAR: str(path)})


def remote_control(d: Decision, env: Optional[Mapping[str, str]] = None) -> str:
    """Will Remote Control work for this launch? It refuses every base URL
    except Anthropic's, so the answer is the launch's effective base URL."""
    environ = os.environ if env is None else env
    if _ENV_BASE in d.env:
        base = d.env[_ENV_BASE]
    elif _ENV_BASE in d.unset:
        base = ""
    else:
        base = _value(environ, _ENV_BASE)
    try:
        host = (urlsplit(base).hostname or "").lower()
    except ValueError:
        host = ""
    if not base or host == "api.anthropic.com":
        return "yes"
    return (f"no — {_ENV_BASE}={redact(base)} reaches this session, and Remote "
            "Control refuses any base URL but Anthropic's")


# ── credential custody ───────────────────────────────────────────────────

#: Public token prefix → (kind, who is billed). Prefixes only; no secret is
#: read past its first characters, kept, or logged.
CREDENTIAL_CLASSES = (
    ("sk-ant-oat", "oauth", "subscription (OAuth / CCH)"),
    ("sk-ant-api", "api-key", "per-token API credits"),
)


def classify_credential(name: str, value: str) -> dict:
    """Describe a credential by its public prefix, without retaining it."""
    token = value[7:].strip() if value.lower().startswith("bearer ") else value
    for prefix, kind, bills in CREDENTIAL_CLASSES:
        if token.startswith(prefix):
            return {"header": name.lower(), "prefix": prefix, "kind": kind, "bills": bills}
    # An unrecognised token gets NO prefix recorded: for a custom or future
    # format the first characters are entropy drawn from the secret itself,
    # and this dict is written to disk (AGORA #5020 round 2).
    return {"header": name.lower(), "prefix": "", "kind": "unknown", "bills": "unknown"}


@dataclass(frozen=True)
class CustodyHazard:
    """Gateway routing WITHOUT the wire's custody scrub.

    The seam exists so that a launch crossing the gateway does so on the
    operator's subscription: it scrubs ``ANTHROPIC_API_KEY`` /
    ``ANTHROPIC_AUTH_TOKEN`` precisely because an ambient key wins over the
    OAuth login (H-INF-3). This is the arrangement where the routing arrives
    WITHOUT that scrub — strictly worse than either endpoint, since it takes
    the gateway path while dropping the guarantee the path was built for.
    """

    present: bool
    via_gateway: bool = False
    credential: str = ""
    bills: str = ""
    detail: str = ""


def custody_hazard(env: Optional[Mapping[str, str]] = None) -> CustodyHazard:
    """Is this environment on the gateway with an un-scrubbed ambient credential?

    Deliberately BLIND to ``GRIP_INFINITE``, because the hazard is reachable
    from both sides of that switch and a detector gated on it would miss half
    its own subject:

    * switch OFF, an inherited preset still routing (measured on blackbook
      2026-08-24 — a shell four days older than the D5 arming);
    * switch ON, the ``already-wired`` branch returning early on a preset it
      did not set and therefore does not scrub.

    Pure: reads the mapping, compares endpoints, classifies a credential by
    its public prefix. No socket, no disk — safe to call from any branch,
    including the one whose contract is that it never probes.
    """
    environ = os.environ if env is None else env
    preset, routed = preset_routing(environ)
    if not routed:
        return CustodyHazard(False)
    for var in _SCRUB_VARS:
        value = _value(environ, var)
        if value:
            info = classify_credential(var, value)
            return CustodyHazard(
                present=True,
                via_gateway=True,
                credential=var,
                bills=info["bills"],
                detail=(f"{redact(preset)} routes through the gateway while {var} "
                        f"is set — a launch the seam did not scrub bills "
                        f"{info['bills']}"),
            )
    return CustodyHazard(False, via_gateway=True)


def headless_custody(env: Optional[Mapping[str, str]] = None) -> dict:
    """What a headless ``claude -p`` spawned from this env would bill against.

    Interactive Claude Code asks before honouring an ambient API key; the
    headless path does not, which is why a relay or background launch can end
    up on per-token billing while the operator's own window is on the
    subscription.
    """
    environ = os.environ if env is None else env
    for var in _SCRUB_VARS:
        value = _value(environ, var)
        if value:
            info = classify_credential(var, value)
            info["source"] = var
            return info
    return {
        "header": "",
        "prefix": "",
        "kind": "login",
        "bills": "whatever `claude /login` holds (subscription when OAuth)",
        "source": "",
    }


# ── launch receipts (D5 soak evidence) ───────────────────────────────────

def _receipt_path() -> Path:
    return get_claude_dir() / "state" / "hal-infinite" / "wire.jsonl"


def receipt_row(d: Decision, session_id: str = "", interactive: Optional[bool] = None) -> dict:
    """One launch receipt.

    ``session_id`` is the id bin/grip handed Claude Code with ``--session-id``
    — a launch that resumes, continues or backgrounds a session cannot name
    one before Claude Code starts, and then it is recorded as null, never
    guessed. ``harness`` is constant because only Claude Code launches reach
    this helper: every other harness keeps its own base-URL wiring.
    ``interactive`` is launch-mode evidence for T4; legacy callers leave it
    unknown rather than silently including print/background launches.
    """
    return {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reason": d.reason,
        "wired": d.wire,
        "base_url": redact(gateway_base()),
        "session_id": session_id if _UUID_RE.fullmatch(session_id or "") else None,
        "harness": "claude-code",
        "interactive": interactive,
        "wire_mode": d.wire_mode,
        "transitional": d.transitional,
    }


def _append_receipt(row: dict) -> None:
    """One JSONL row per launch decision. Fail-open: never blocks a launch."""
    try:
        p = _receipt_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass


# ── D5 soak coverage ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SoakCoverage:
    """Is the D5 soak measuring anything, or only appearing to?

    The soak's evidence is ``wire.jsonl``, and a receipt is written only on
    the ``--launch-env`` path — which ``bin/grip`` runs only when the flag is
    already 1. So a session that never receives the flag writes nothing, and
    an empty ledger is ambiguous by construction: it reads identically for
    "nobody launched" and "every launch bypassed the seam". Counting sessions
    beside receipts is what separates them.
    """

    since: str
    sessions: int
    receipts: int
    undatable: int = 0
    #: False when the transcript root could not be listed at all. A soak that
    #: could not look must never report the same thing as a soak that looked
    #: and found nothing -- that is the confident zero this surface exists to
    #: stop producing, turned on itself.
    root_readable: bool = True

    @property
    def measuring(self) -> bool:
        """True only if launches happened AND the ledger saw some of them."""
        return self.sessions > 0 and self.receipts > 0

    @property
    def denominator_suspect(self) -> bool:
        """More receipts than sessions — the session count is understated.

        There is no join key between a ledger row and a transcript, so this
        compares two counts over one window rather than matching launches to
        sessions. Receipts outrunning sessions is therefore not impossible
        data; it means the walk missed sessions the ledger saw, and the
        percentage below would be meaningless if printed.
        """
        return self.receipts > self.sessions

    #: Exit code per state. 2 is left to its conventional meaning (bad usage),
    #: which the argument parser owns.
    _EXIT = {"unreadable-root": 4, "unmeasured": 5, "understated": 3,
             "empty": 0, "blind": 1, "measuring": 0}

    @property
    def state(self) -> str:
        """The single classification of this run.

        summary() and the exit code both read THIS, rather than each walking
        its own ladder — two ladders is how a run came to print "the session
        count is UNDERSTATED" while exiting with the code for "could not read
        the root" (AGORA #5036 round 4).
        """
        if not self.root_readable:
            return "unreadable-root"
        if self.sessions == 0 and self.undatable:
            return "unmeasured"
        if self.denominator_suspect:
            return "understated"
        if self.sessions == 0:
            return "empty"
        return "measuring" if self.measuring else "blind"

    @property
    def exit_code(self) -> int:
        return self._EXIT[self.state]

    def summary(self) -> str:
        if not self.root_readable:
            return (f"could not read the transcript root — this is NOT a "
                    f"measurement, and {self.receipts} receipt(s) since "
                    f"{self.since} were counted without anything to compare them to")
        note = f" ({self.undatable} transcript(s) undatable, excluded)" if self.undatable else ""
        if self.undatable > self.sessions:
            note += (" — WEAK: more transcripts could not be dated than were "
                     "counted, so the denominator is a lower bound")
        if self.state == "unmeasured":
            return (f"no session could be DATED since {self.since}, but "
                    f"{self.undatable} transcript(s) are present and "
                    f"undatable — this is not an empty window, it is an "
                    f"unmeasured one; {self.receipts} receipt(s) in the same window")
        if self.denominator_suspect:
            # Never "nothing to measure yet" while the ledger holds evidence:
            # that is the same confident zero, earned by ignoring a file, that
            # this whole surface exists to stop reporting.
            return (f"{self.receipts} receipts but only {self.sessions} session(s) "
                    f"dated since {self.since} — the session count is UNDERSTATED, "
                    f"not the ledger{note}")
        if self.sessions == 0:
            return f"no sessions started since {self.since} — nothing to measure yet"
        pct = 100.0 * self.receipts / self.sessions
        verdict = "measuring" if self.measuring else "BLIND — the ledger saw none of them"
        return (f"{self.receipts}/{self.sessions} sessions left a receipt "
                f"({pct:.0f}%) since {self.since} — {verdict}{note}")


#: Line types that are a session actually doing something, as opposed to the
#: harness recording bookkeeping around it. Measured over 1,522 local
#: transcripts, 233 opened with a timestamped NON-event line
#: (queue-operation, attachment, pr-link), so trusting the first stamp of any
#: kind dates those sessions by their bookkeeping. The observed skew is small
#: -- 13ms to ~2s, since the header is written as the session starts -- but a
#: resumed session has no such bound, and preferring the event costs nothing.
_EVENT_TYPES = frozenset({"user", "assistant", "system"})

#: How far into a transcript to look for the first timestamped event. A
#: transcript may open with metadata lines (mode, agentId, leafUuid, aiTitle,
#: summary) that carry no timestamp; measured over 1,522 local transcripts,
#: 269 did, and 114 of those carried a timestamp further down. Bailing at
#: line 1 discarded every one of them as undatable.
_HEADER_SCAN_LINES = 100


def _first_event_time(path: Path) -> Optional[_dt.datetime]:
    """When a session began: the first timestamped event in its transcript.

    Deliberately not mtime: a transcript is rewritten on every turn, so mtime
    dates the latest activity of a session that may long predate the window
    and would inflate the denominator.

    Untimestamped leading lines are SKIPPED rather than treated as undatable
    — they are metadata headers, not events. The scan is bounded so a large
    transcript carrying no timestamps at all cannot be read end to end; past
    the bound the file is reported undatable rather than guessed at.
    """
    fallback = None
    with path.open(encoding="utf-8") as fh:
        for seen, line in enumerate(fh):
            if seen >= _HEADER_SCAN_LINES:
                break
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue   # a malformed line is not the session's start time
            if not isinstance(row, dict):
                continue   # a bare JSON string or array is not an event
            stamp = row.get("timestamp")
            if not stamp:
                continue
            # A timestamped bookkeeping line dates the harness, not the
            # session. Keep the first one only as a fallback for transcripts
            # that contain no event at all within the scan bound.
            if row.get("type") not in _EVENT_TYPES:
                fallback = fallback if fallback is not None else stamp
                continue
            if stamp:
                try:
                    # One corrupt event stamp must not condemn the file: a
                    # later line may date it perfectly well, and the
                    # bookkeeping branch above is already this tolerant.
                    # Opposite robustness policies in one function is how the
                    # two branches would drift apart.
                    return _as_utc(stamp)
                except (ValueError, TypeError):
                    continue
    return _as_utc(fallback) if fallback else None


def _as_utc(stamp: str) -> _dt.datetime:
    """Parse an ISO stamp, reading a missing offset as UTC.

    One convention for BOTH the transcript walk and the ledger. They cover the
    same window, so two conventions would let a real in-window receipt be
    dropped while an equivalent transcript stamp was counted -- enough on its
    own to flip a measuring soak to BLIND.
    """
    parsed = _dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


def soak_coverage(
    since: _dt.datetime,
    *,
    receipts: Optional[Path] = None,
    projects_root: Optional[Path] = None,
) -> SoakCoverage:
    """Compare receipts written against sessions actually started since ``since``.

    Transcripts that cannot be read or parsed are counted into ``undatable``
    and excluded from BOTH totals rather than silently dropped: a coverage
    figure that quietly discards its unknowns is the shape that reports a
    confident zero it did not earn.
    """
    ledger = _receipt_path() if receipts is None else receipts
    root = ((get_claude_dir() / "projects") if projects_root is None
            else projects_root)

    written = 0
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                # json.loads happily returns a list or a str; .get on either
                # raises AttributeError, which no guard here caught, so one
                # such row aborted the whole count.
                stamp = row.get("ts") if isinstance(row, dict) else None
                # A row whose timestamp is malformed, or naive where `since`
                # is aware, is a row we cannot place in the window — skip it
                # rather than let one bad line abort the whole count. The
                # comparison must sit inside the guard: fromisoformat accepts
                # a naive string happily and only the >= raises.
                if stamp and _as_utc(stamp) >= since:
                    written += 1
            except (ValueError, TypeError, AttributeError):
                continue
    except (OSError, UnicodeDecodeError):
        pass   # no ledger yet is zero receipts, not an error

    started = undatable = 0
    root_readable = True
    try:
        if not root.is_dir():
            raise OSError(f"not a directory: {root}")
        transcripts = sorted(root.rglob("*.jsonl"))
    except OSError:
        transcripts, root_readable = [], False
    for path in transcripts:
        try:
            begun = _first_event_time(path)
            if begun is None:
                undatable += 1
            elif begun >= since:
                started += 1
        except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
            undatable += 1

    return SoakCoverage(since=since.isoformat(timespec="seconds"),
                        sessions=started, receipts=written,
                        undatable=undatable, root_readable=root_readable)


# ── machine + operator surfaces ──────────────────────────────────────────
#
# ``--launch-env`` is what ``bin/grip`` consumes at launch. It prints ZERO OR
# MORE protocol lines, each exactly one of:
#     ANTHROPIC_BASE_URL=<url>                  (the base-URL rung)
#     HTTPS_PROXY=http://127.0.0.1:<port>       (the proxy rung; https_proxy too)
#     NO_PROXY=<comma list>                     (the proxy rung; no_proxy too)
#     NODE_EXTRA_CA_CERTS=<absolute .pem path>  (the proxy rung)
#     CLAUDE_ENV_FILE=<absolute .sh path>       (the proxy rung: Bash goes direct)
#     API_TIMEOUT_MS=<ms>, and the three CLAUDE_*_TIMEOUT_MS=<ms>  (the proxy rung)
#     CLAUDE_CODE_GATEWAY_HINT_HEADERS=1        (the proxy rung: the wire reads the hints)
#     UNSET <allowlisted variable>              (remove this from the launch env)
# and nothing else, so the shell side stays a dumb validator: any line that
# does not match its strict shapes is rejected loudly and NOTHING is applied
# (all-or-nothing — a malformed line can never strip auth without wiring).
# No output means a plain direct launch. There is deliberately no eval'd
# ``--export`` mode: shell evaluating subprocess output was retired for a
# validated line protocol (AGORA #5022 rounds 2-3).

def _scrub_note(d: Decision, environ: Mapping[str, str]) -> str:
    """The ambient credentials this launch actually removes: set here AND unset.

    The proxy rung unsets both on every launch, so naming them all would claim
    a scrub that removed nothing; only what the environment carried is named.
    """
    scrubbed = [v for v in d.unset if v in _SCRUB_VARS and v in environ]
    return f" (custody: scrubbing {', '.join(scrubbed)})" if scrubbed else ""


def launch_notices(d: Decision, env: Optional[Mapping[str, str]] = None) -> list:
    """The visible lines a launch prints for its decision, in order."""
    environ = os.environ if env is None else env
    if d.reason == "wired-by-proxy":
        lines = [f"[grip-infinite] riding the wire at {d.detail}{_scrub_note(d, environ)} "
                 "— the base URL stays Anthropic's, so Remote Control works"]
    elif d.reason == "wired":
        lines = [f"[grip-infinite] riding the witnessed rails at "
                 f"{redact(gateway_base())}{_scrub_note(d, environ)}"]
    elif d.reason == "unwitnessed":
        lines = [f"[grip] unwitnessed: {d.detail}; this session leaves no HAL record"]
    elif d.reason == "gateway-down":
        lines = ["[grip-infinite] GRIP_INFINITE=1 but the gateway did not answer "
                 "/health — launching DIRECT (fail-open)"]
    elif d.reason == "off":
        lines = []
    else:
        lines = [f"[grip-infinite] {d.summary()}"]
    return lines + list(d.notes)


def protocol_lines(d: Decision) -> list:
    """The line protocol bin/grip validates: removals first, then exports."""
    return ([f"UNSET {k}" for k in d.unset]
            + [f"{k}={v}" for k, v in sorted(d.env.items())])


def _session_id_arg(args: list) -> str:
    """``--session-id <uuid>`` from the helper's own argv, or ""."""
    if "--session-id" not in args:
        return ""
    at = args.index("--session-id") + 1
    value = args[at].strip().lower() if at < len(args) else ""
    return value if _UUID_RE.fullmatch(value) else ""


def _cli_probe(_rest: list) -> int:
    try:
        ok = gateway_alive(gateway_base())
    except Exception:
        ok = False
    print("alive" if ok else "down")
    return 0 if ok else 1


def _cli_launch_env(rest: list) -> int:
    # Never let a decision error cost a launch: no output means direct.
    try:
        d = with_bash_env(decide())
    except Exception:
        return 0
    interactive = True if "--interactive" in rest else False if "--non-interactive" in rest else None
    row = receipt_row(d, _session_id_arg(rest), interactive)
    receipt = threading.Thread(target=_append_receipt, args=(row,), daemon=True)
    receipt.start()
    receipt.join(0.5)  # bounded: a wedged filesystem cannot hold the launch
    for line in launch_notices(d):
        sys.stderr.write(line + "\n")
    for line in protocol_lines(d):
        print(line)
    return 0


def _cli_soak(rest: list) -> int:
    since_arg = rest[0] if rest else ""
    try:
        # Through the SAME helper the ledger and transcript walks use: the CLI
        # is a third reader of one window timestamp, and a third convention is
        # how "2026-08-24T01:08:00Z" came to be counted in a file and rejected
        # on the command line.
        since = (_as_utc(since_arg) if since_arg
                 else _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7))
    except ValueError:
        sys.stderr.write(f"soak: not an ISO timestamp: {since_arg}\n")
        return 2
    cov = soak_coverage(since)
    print(f"soak     : {cov.summary()}")
    print(f"ledger   : {_receipt_path()}")
    # Exit codes are a scripting contract, so they must not collide: 2 is left
    # to its conventional meaning (bad usage), which the parser above returns.
    #   0 measuring, or nothing started yet    3 denominator understated
    #   1 BLIND: launches happened, none seen  4 could not read the root
    return cov.exit_code


def _auth_line(d: Decision) -> str:
    """The auth-failover marker, and whether THIS launch applies it."""
    path = auth_failover_path()
    marker = auth_failover_state(path)
    who = marker.by or "by hand"
    if not marker.engaged:
        state = "not engaged"
    elif d.reason == "wired-by-proxy":
        state = (f"ENGAGED ({who}), not applied: a wired session runs on the "
                 "signed-in subscription")
    elif "ANTHROPIC_API_KEY" in d.unset:
        state = (f"ENGAGED ({who}), not applied: the base-URL route scrubs the key "
                 "(GRIP#7274, which closes in ADR-0016 Step 5)")
    else:
        state = f"ENGAGED ({who}): the API-key bucket is selected for new sessions"
    return f"auth     : {state} [{path}]"


def _explain_route_lines(d: Decision, wire: WireCheck, reachable: bool) -> list:
    mode = d.wire_mode
    if d.transitional:
        mode += (" (transitional: kept until the wire runs on this host, removed "
                 "by ADR-0016 Step 7; this route refuses Remote Control)")
    # Determine the true failover status - this will be easy to change when failover lands
    # Currently shows 'none' until proper failover implementation is available
    failover_status = 'none'  # Truthful status - no actual failover implemented yet
    return [
        f"decision : {d.summary()}",
        f"wire mode: {mode}",
        f"remote   : Remote Control {remote_control(d)}",
        f"failover : {failover_status}",
        f"wire     : {redact(wire_base())} — {wire.detail}",
        f"gateway  : {redact(gateway_base())} — {'alive' if reachable else 'not answering'}",
        _auth_line(d),
    ]


def _explain_switch_lines() -> list:
    marker = brake_marker_path()
    brake = brake_state(marker)
    shown = f"ENGAGED — {brake.reason or 'manual'}" if brake.engaged else "off"
    return [f"flag     : {_ENV_FLAG}={os.environ.get(_ENV_FLAG, '') or '(unset)'}",
            f"brake    : {shown}",
            f"marker   : {marker}"]


def _explain_custody_lines(d: Decision) -> list:
    cust = headless_custody()
    note = f" [from {cust['source']}]" if cust["source"] else ""
    lines = [f"custody  : headless `claude -p` bills {cust['bills']}{note}"]
    hazard = custody_hazard()
    if hazard.present:
        # custody_hazard stays pure — env only, no disk — so it cannot see the
        # brake or the wire. When this launch detaches the base URL, the
        # crossing it describes will not happen, and saying "UNSCRUBBED WIRE"
        # beside "detaching" describes one environment two contradictory ways.
        if _ENV_BASE in d.unset:
            lines.append(f"hazard   : would be an unscrubbed wire — {hazard.detail}; "
                         f"this launch detaches it ({d.reason})")
        else:
            lines.append(f"hazard   : UNSCRUBBED WIRE — {hazard.detail}")
    lines += [f"unsets   : {k}" for k in d.unset]
    lines += [f"exports  : {k}={redact(v)}" for k, v in sorted(d.env.items())]
    return lines


def _cli_explain(_rest: list) -> int:
    url = gateway_base()
    try:
        # Probe once and decide on THAT answer, so the decision line and the
        # liveness lines can never disagree about one moment. A malformed URL
        # raises here, and is reported as undecidable rather than a traceback.
        reachable = gateway_alive(url)
        wire = check_wire()
        d = decide(probe=lambda _u: reachable, wire_probe=lambda _e: wire)
    except Exception as exc:
        print(f"decision : direct (undecidable): {redact(str(exc))}")
        print(f"flag     : {_ENV_FLAG}={os.environ.get(_ENV_FLAG, '') or '(unset)'}")
        print(f"gateway  : {redact(url)} — unreachable (bad URL)")
        return 0
    lines = (_explain_route_lines(d, wire, reachable) + _explain_switch_lines()
             + _explain_custody_lines(d))
    for line in lines:
        print(line)
    return 0


_CLI = {
    "--probe": _cli_probe,
    "--launch-env": _cli_launch_env,
    "soak": _cli_soak,
    "explain": _cli_explain,
    "--explain": _cli_explain,
    "status": _cli_explain,
}


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    handler = _CLI.get(args[0] if args else "explain")
    if handler is None:
        print(__doc__)
        return 2
    return handler(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
