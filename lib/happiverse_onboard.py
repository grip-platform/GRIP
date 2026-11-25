"""happiverse_onboard — decide what a machine can become, from probed facts.

A machine that answers on no port cannot be measured or configured remotely; a
human has to be at it once. This module holds the part that does NOT need a
human: given the facts a one-line bootstrap reports back, decide whether that
machine can carry a full GRIP node, only a lightweight one, or nothing yet — and
say exactly which floor failed, rather than "it did not work".

Pure. No I/O, no network, no subprocess, so the decision is testable without a
second machine — which matters here, because the machines this exists for are
precisely the ones that cannot be reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

FULL, LIGHT, BLOCKED = "full-node", "lightweight-node", "blocked"

# GRIP's own floors, each traceable to a real requirement rather than a guess:
#   python  — lib/ uses 3.10+ syntax throughout (match statements, X | Y unions)
#   macos   — Tailscale's current macOS client requires 13+; below that the node
#             can still be reached over plain SSH but cannot self-enable
#             Tailscale SSH, which is the whole point of the bootstrap
MIN_PYTHON = (3, 10)
MIN_MACOS = 13


def _ver(text: str) -> tuple:
    """Leading dotted integers of a version string; () when there are none."""
    out = []
    for part in str(text or "").strip().split("."):
        # LEADING digits only. Filtering every digit out of the part turns the
        # "0rc1" of "3.10.0rc1" into 1, reporting a patch level that does not
        # exist — caught by the parametrised case, not by the floor checks,
        # which would have passed either way.
        digits = ""
        for c in part:
            if not c.isdigit():
                break
            digits += c
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


@dataclass
class MachineFacts:
    """What the one-line bootstrap reports back from the target machine."""
    nickname: str
    os_name: str = ""          # "macOS", "Linux"
    os_version: str = ""       # "26.4", "12.7.6"
    arch: str = ""             # "arm64", "x86_64"
    python_version: str = ""   # "3.14.7"
    has_tailscale: bool = False
    tailscale_ssh_enabled: bool = False
    # Plain SSH we can actually USE: the daemon answers AND this machine's key
    # is authorised. Reachability is the OR of this and Tailscale SSH — see the
    # blocker logic below for why assuming Tailscale SSH was wrong.
    plain_ssh_authorised: bool = False
    has_grip_checkout: bool = False
    mesh_agents_loaded: int = 0


def decide(facts: MachineFacts) -> dict:
    """What this machine can be, and precisely what stands in the way.

    Every blocker is returned with the floor it failed and the remedy, because
    "lightweight only" without a named reason is the kind of verdict that gets
    re-investigated from scratch six weeks later."""
    blockers: list = []
    warnings: list = []

    if not facts.has_tailscale:
        blockers.append({
            "floor": "tailscale", "found": "absent",
            "remedy": "install Tailscale and join the tailnet",
        })
    elif not (facts.tailscale_ssh_enabled or facts.plain_ssh_authorised):
        # BOTH routes are named, because Tailscale SSH is not always available:
        # measured 2026-09-22 on two Macs running the sandboxed App Store build,
        # which ships no SSH server at all, so `tailscale set --ssh` fails by
        # design there. An earlier version offered only that command, which
        # would have sent the operator to run something that cannot succeed,
        # and classified a machine answering on port 22 as unreachable.
        blockers.append({
            "floor": "ssh", "found": "no usable route",
            "remedy": "authorise a key (ssh-copy-id) or, on a non-sandboxed "
                      "Tailscale build, tailscale set --ssh",
        })

    py = _ver(facts.python_version)
    if not py:
        blockers.append({"floor": "python", "found": "absent or unreadable",
                         "remedy": "install python3 >= %d.%d" % MIN_PYTHON})
    elif py < MIN_PYTHON:
        blockers.append({
            "floor": "python", "found": facts.python_version,
            "remedy": "install python3 >= %d.%d (GRIP's lib/ uses 3.10+ syntax)" % MIN_PYTHON,
        })

    if facts.os_name.lower() == "macos":
        osv = _ver(facts.os_version)
        if osv and osv[0] < MIN_MACOS:
            warnings.append({
                "floor": "macos", "found": facts.os_version,
                "note": f"below {MIN_MACOS}: the current Tailscale client may not run, so "
                        "this machine may not be able to enable Tailscale SSH itself, "
                        "so reach it with an authorised key instead",
            })

    # A machine is reachable-and-measurable once EITHER SSH route works; being a
    # FULL node additionally needs an interpreter GRIP's code actually parses.
    ssh_blocked = any(b["floor"] in ("tailscale", "ssh") for b in blockers)
    py_blocked = any(b["floor"] == "python" for b in blockers)

    if ssh_blocked:
        verdict = BLOCKED
    elif py_blocked:
        verdict = LIGHT
    else:
        verdict = FULL

    return {
        "nickname": facts.nickname,
        "verdict": verdict,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": _next_action(verdict, blockers),
    }


def _next_action(verdict: str, blockers: Sequence[dict]) -> str:
    if verdict == BLOCKED and blockers:
        return blockers[0]["remedy"]
    if verdict == LIGHT:
        return ("reachable: mesh presence and health can be measured remotely; a full "
                "node needs the python floor met first")
    return "reachable: the remaining legs can be measured and configured remotely"
