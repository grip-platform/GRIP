"""Shim health probe + startup guard for grip launches.

When persisted channels=enabled (or another registered shim is expected to
be live), probe each shim and render a brief stderr summary at session
start. Surfaces degraded state without blocking — the operator decides
whether to triage.

Extensible: each shim registers a ShimCheck. Today only grip-channel is
wired; future shims (HAL router, hal-server, happi-api) can register by
appending one register(ShimCheck(...)) call below.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, TextIO


@dataclass(frozen=True)
class ShimStatus:
    name: str
    healthy: bool
    summary: str
    advice: Optional[str] = None


@dataclass(frozen=True)
class ShimCheck:
    name: str
    expected_live: Callable[[], bool]
    probe: Callable[[], ShimStatus]


_REGISTRY: List[ShimCheck] = []


def register(check: ShimCheck) -> None:
    _REGISTRY.append(check)


def clear_registry() -> None:
    _REGISTRY.clear()


_BRIDGE_NAMES = ("WhatsApp", "Slack", "Discord", "Web")
_BRIDGE_KEYS = ("whatsapp", "slack", "discord", "web")


def _grip_channel_expected_live() -> bool:
    try:
        from lib.grip_channels_wizard import resolve_or_signal
    except ImportError:
        return False
    try:
        return resolve_or_signal() == "enabled"
    except Exception:
        return False


def _grip_channel_probe() -> ShimStatus:
    try:
        from lib.grip_channels_wizard import fetch_health
        h = fetch_health()
    except Exception as exc:
        return ShimStatus(
            name="grip-channel",
            healthy=False,
            summary=f"channels: probe FAILED ({type(exc).__name__})",
            advice="run `grip channels` to triage",
        )

    if not h.online:
        return ShimStatus(
            name="grip-channel",
            healthy=False,
            summary="channels: daemon OFFLINE (:3101 unreachable)",
            advice="check `pm2 list` — restart with `pm2 restart grip-channel`",
        )

    states = [getattr(h, key).get("connected", False) for key in _BRIDGE_KEYS]
    connected = sum(1 for s in states if s)
    parts = [f"{name}:{'OK' if ok else 'X'}"
             for name, ok in zip(_BRIDGE_NAMES, states)]
    summary = f"channels: {' '.join(parts)} ({connected}/4 bridges)"

    if connected == 4:
        return ShimStatus(name="grip-channel", healthy=True, summary=summary)

    advice = ("run `grip channels` to triage"
              if connected == 0 else
              "run `grip channels --probe-only` for details")
    return ShimStatus(
        name="grip-channel",
        healthy=connected >= 1,
        summary=summary,
        advice=advice,
    )


register(ShimCheck(
    name="grip-channel",
    expected_live=_grip_channel_expected_live,
    probe=_grip_channel_probe,
))


def run_all_checks() -> List[ShimStatus]:
    results: List[ShimStatus] = []
    for check in _REGISTRY:
        try:
            if not check.expected_live():
                continue
        except Exception:
            continue
        try:
            results.append(check.probe())
        except Exception as exc:
            results.append(ShimStatus(
                name=check.name,
                healthy=False,
                summary=f"{check.name}: probe raised {type(exc).__name__}",
                advice=None,
            ))
    return results


def render_startup_summary(stream: Optional[TextIO] = None) -> int:
    target = stream if stream is not None else sys.stderr
    results = run_all_checks()
    if not results:
        return 0
    any_degraded = False
    for r in results:
        marker = "[grip]" if r.healthy else "[grip] !"
        target.write(f"{marker} {r.summary}\n")
        if not r.healthy:
            any_degraded = True
            if r.advice:
                target.write(f"[grip]   -> {r.advice}\n")
    return 1 if any_degraded else 0


def main(argv: Optional[List[str]] = None) -> int:
    return render_startup_summary()


if __name__ == "__main__":
    raise SystemExit(main())
