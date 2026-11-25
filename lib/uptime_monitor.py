"""HAL-free (zero-Anthropic) public-surface uptime monitor.

Pure-stdlib periodic health check for GRIP's public-facing surfaces —
your-gateway.example.com, the grip-web
showcase (your-gateway.example.com), the your-gateway.example.com relay health endpoint,
and the api.happi.md HAPPI proxy. Zero LLM calls, zero Anthropic
dependency: this module never imports ``lib.hal_llm_adapter`` or any
provider SDK — it only ever speaks raw HTTP via ``urllib``.

Each surface has its own CORRECT liveness definition, never a blanket
"200 is healthy" (see ``rules/operational-context.md`` "GRIP VPS" table):

- Most surfaces: a plain HTTP status check (default: 200 is healthy).
- ``api.happi.md``: a bare-path 404 is EXPECTED and healthy — the HAPPI
  proxy is route-shaped. A 200 here would be the ANOMALY, not the 404.
- ``your-gateway.example.com/health``: a JSON health body is required, with
  ``slack``/``discord``/``web`` all connected. ``whatsapp:false`` is
  STRUCTURAL (the VPS bridge is Hetzner-IP-blocked by design) and must
  never trigger an alert.

Public API::

    from uptime_monitor import run_once, DEFAULT_SURFACES, load_surfaces

    result = run_once(
        surfaces=DEFAULT_SURFACES,
        probe_fn=_http_probe,
        notify_fn=my_notify,
        state_path=Path("state/uptime-monitor/state.json"),
        log_path=Path("logs/uptime-monitor.jsonl"),
    )

Alerting is edge-triggered (state-change only, per
``rules/lifecycle-closed-or-expired.md`` — a persistent outage never
spams): the first down tick alerts once, every following down tick is
silently deduped, and recovery alerts exactly once. First-seen-healthy
seeds state silently (no false "it's up!" noise on a fresh install).

Alerts post to #grip-updates via the existing ``slack_transport`` shim
(syscall-doctrine — no hand-rolled Slack API call in this module).

Run directly (the cron/systemd-timer entry point)::

    python3 lib/uptime_monitor.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()

__all__ = [
    "Surface",
    "ProbeResult",
    "DEFAULT_SURFACES",
    "classify",
    "load_surfaces",
    "run_once",
    "compose_alert",
    "main",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    """One monitored public surface and its CORRECT liveness definition."""

    name: str
    url: str
    expected_statuses: frozenset[int] = frozenset({200})
    require_true: tuple[str, ...] = ()
    mode: str = "status"  # "status" | "json_health"


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one HTTP probe. ``status is None`` means unreachable."""

    status: int | None
    body: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Canonical surface registry — rules/operational-context.md + port-registry.md
# ---------------------------------------------------------------------------

DEFAULT_SURFACES: tuple[Surface, ...] = (
    Surface(name="happiverse", url="https://your-gateway.example.com"),
    Surface(name="grip-web-showcase", url="https://your-gateway.example.com"),
    Surface(
        name="channel-health",
        url="https://your-gateway.example.com/health",
        mode="json_health",
        require_true=("slack", "discord", "web"),
    ),
    Surface(
        # A bare-path 404 is EXPECTED here — route-shaped proxy, not an
        # outage (verified 2026-06-23, rules/operational-context.md).
        # MUTATION ANCHOR: flipping this to {200} inverts the liveness
        # check and is caught by tests/test_uptime_monitor.py.
        name="api.happi.md",
        url="https://api.happi.md",
        expected_statuses=frozenset({404}),
    ),
)

_SURFACES_OVERRIDE_PATH = CLAUDE_DIR / "state" / "uptime-monitor" / "surfaces.json"
DEFAULT_STATE_PATH = CLAUDE_DIR / "state" / "uptime-monitor" / "state.json"
DEFAULT_LOG_PATH = CLAUDE_DIR / "logs" / "uptime-monitor.jsonl"
_GRIP_UPDATES_CHANNEL = "C0EXAMPLE01"  # #grip-updates, CodeTonight workspace


# ---------------------------------------------------------------------------
# Liveness classification — per-surface, never a blanket 200 check
# ---------------------------------------------------------------------------


def classify(surface: Surface, result: ProbeResult) -> tuple[bool, str]:
    """Return ``(healthy, detail)`` for one probe, per the surface's OWN rule."""
    if result.status is None:
        return False, f"unreachable: {result.error or 'no response'}"
    if surface.mode == "json_health":
        return _classify_json_health(surface, result)
    return _classify_status(surface, result)


def _classify_status(surface: Surface, result: ProbeResult) -> tuple[bool, str]:
    expected = ",".join(str(code) for code in sorted(surface.expected_statuses))
    healthy = result.status in surface.expected_statuses
    return healthy, f"HTTP {result.status} (expected {expected})"


def _classify_json_health(surface: Surface, result: ProbeResult) -> tuple[bool, str]:
    status_ok, detail = _classify_status(surface, result)
    if not status_ok:
        return False, detail
    try:
        body = json.loads(result.body or "")
    except (json.JSONDecodeError, TypeError):
        return False, f"invalid JSON body from {surface.url}"
    down = [key for key in surface.require_true if not _connected(body, key)]
    if down:
        return False, f"{','.join(down)} not connected"
    return True, f"{','.join(surface.require_true)} connected"


def _connected(body: object, key: str) -> bool:
    """Read a platform's connected flag — supports nested and flat body shapes."""
    value = body.get(key) if isinstance(body, dict) else None
    if isinstance(value, dict):
        return bool(value.get("connected"))
    return bool(value)


# ---------------------------------------------------------------------------
# The prober — pure stdlib, never raises
# ---------------------------------------------------------------------------


def _http_probe(url: str, timeout: float = 10.0) -> ProbeResult:
    """Pure-stdlib GET probe. Never raises — every failure becomes a ProbeResult."""
    req = urllib.request.Request(url, headers={"User-Agent": "grip-uptime-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            return ProbeResult(status=status, body=body)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — body is best-effort only
            body = None
        return ProbeResult(status=e.code, body=body)
    except urllib.error.URLError as e:
        return ProbeResult(status=None, error=f"URLError:{e.reason}")
    except Exception as e:  # noqa: BLE001 — a probe must never crash the monitor
        return ProbeResult(status=None, error=f"{type(e).__name__}:{e}")


# ---------------------------------------------------------------------------
# Edge-triggered alerting — state-change only (rules/lifecycle-closed-or-expired.md)
# ---------------------------------------------------------------------------


def _transition(prev: bool | None, healthy: bool) -> bool | None:
    """Edge-trigger table: alert only on a state CHANGE, never steady state."""
    if prev is None:
        return False if not healthy else None  # first-seen: down alerts once, up seeds silent
    if prev == healthy:
        return None  # steady state — dedup
    return healthy  # True = recovered, False = newly down


def compose_alert(transitions: Iterable[tuple[Surface, bool, str]]) -> tuple[str, list[dict]]:
    """Render one Slack message covering every transition in this tick."""
    lines = [
        f"{'RECOVERED' if healthy else 'DOWN'}  {surface.name} — {detail}"
        for surface, healthy, detail in transitions
    ]
    text = "GRIP public-surface uptime:\n" + "\n".join(lines)
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "GRIP public-surface uptime"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    return text, blocks


# ---------------------------------------------------------------------------
# State + log persistence
# ---------------------------------------------------------------------------


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"surfaces": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _append_log(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # a logging failure must never take down the monitor


# ---------------------------------------------------------------------------
# Surface registry override (optional; falls back to DEFAULT_SURFACES)
# ---------------------------------------------------------------------------


def load_surfaces(path: Path) -> tuple[Surface, ...]:
    """Read a surfaces override JSON; fall back to DEFAULT_SURFACES on any issue."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SURFACES
    if not isinstance(raw, list):
        return DEFAULT_SURFACES
    try:
        return tuple(_surface_from_dict(entry) for entry in raw)
    except (TypeError, KeyError):
        return DEFAULT_SURFACES


def _surface_from_dict(entry: dict) -> Surface:
    kwargs: dict = {"name": entry["name"], "url": entry["url"]}
    if "expected_statuses" in entry:
        kwargs["expected_statuses"] = frozenset(entry["expected_statuses"])
    if "require_true" in entry:
        kwargs["require_true"] = tuple(entry["require_true"])
    if "mode" in entry:
        kwargs["mode"] = entry["mode"]
    return Surface(**kwargs)


# ---------------------------------------------------------------------------
# One probe tick
# ---------------------------------------------------------------------------


def run_once(
    *,
    surfaces: Iterable[Surface],
    probe_fn: Callable[[str], ProbeResult] = _http_probe,
    notify_fn: Callable[..., dict] | None = None,
    state_path: Path | None = None,
    log_path: Path | None = None,
    now: float | None = None,
) -> dict:
    """Probe every surface once, alert on state-change only, persist + log.

    Never raises: a crashing probe or a crashing notify degrades to a
    recorded failure, not an exception — the monitor cannot depend on the
    thing it monitors, or on Slack, to stay alive.
    """
    now = time.time() if now is None else now
    state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
    log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH

    prev_surfaces = _load_state(state_path).get("surfaces", {})
    results: dict[str, dict] = {}
    new_state: dict[str, bool] = dict(prev_surfaces)
    internal_transitions: list[tuple[Surface, bool, str]] = []

    for surface in surfaces:
        probe_result = _safe_probe(probe_fn, surface.url)
        healthy, detail = classify(surface, probe_result)
        results[surface.name] = {"healthy": healthy, "detail": detail}
        transition = _transition(prev_surfaces.get(surface.name), healthy)
        if transition is not None:
            internal_transitions.append((surface, transition, detail))
        new_state[surface.name] = healthy

    alerted = _send_alert(internal_transitions, notify_fn)
    _save_state(state_path, {"surfaces": new_state, "updated_at": now})
    transitions_public = [(s.name, val, detail) for s, val, detail in internal_transitions]
    _append_log(
        log_path,
        {"ts": now, "results": results, "transitions": transitions_public, "alerted": alerted},
    )
    return {"alerted": alerted, "transitions": transitions_public, "results": results}


def _safe_probe(probe_fn: Callable[[str], ProbeResult], url: str) -> ProbeResult:
    """A crashing probe_fn IS a down signal, never an unhandled exception."""
    try:
        return probe_fn(url)
    except Exception as e:  # noqa: BLE001 — see docstring
        return ProbeResult(status=None, error=f"probe_exception:{type(e).__name__}:{e}")


def _send_alert(
    transitions: list[tuple[Surface, bool, str]],
    notify_fn: Callable[..., dict] | None,
) -> bool:
    """Post one batched alert for this tick's transitions. Never raises."""
    if not transitions or notify_fn is None:
        return False
    text, blocks = compose_alert(transitions)
    try:
        notify_fn(text=text, blocks=blocks)
        return True
    except Exception:  # noqa: BLE001 — a Slack outage must not crash monitoring
        return False


# ---------------------------------------------------------------------------
# Production wiring — cron / systemd-timer entry point
# ---------------------------------------------------------------------------


def _slack_notify(*, text: str, blocks: list[dict] | None = None) -> dict:
    """Production notifier — posts to #grip-updates via the slack_transport shim.

    Reuses lib/slack_transport.py (syscall-doctrine: no hand-rolled Slack API
    call in this module). Only touched by main(); run_once() never imports
    slack_transport directly, so tests stay hermetic (zero network).
    """
    from slack_transport import post

    return post(
        workspace="codetonight",
        channel_id=_GRIP_UPDATES_CHANNEL,
        text=text,
        blocks=blocks,
    )


def main() -> int:
    """Cron/systemd-timer entry point — one probe tick against every surface.

    Always exits 0: a monitored surface being down is the thing this tool
    exists to DETECT and report (via the Slack alert), not a failure of the
    monitor's own execution — a non-zero cron/systemd exit would duplicate
    that same signal as noisy mail/journal spam.
    """
    surfaces = load_surfaces(_SURFACES_OVERRIDE_PATH)
    result = run_once(
        surfaces=surfaces,
        probe_fn=_http_probe,
        notify_fn=_slack_notify,
        state_path=DEFAULT_STATE_PATH,
        log_path=DEFAULT_LOG_PATH,
    )
    down = sorted(name for name, r in result["results"].items() if not r["healthy"])
    print(
        f"[uptime-monitor] {len(result['results'])} surfaces, "
        f"{len(down)} down ({', '.join(down) or 'none'}), "
        f"alerted={result['alerted']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
