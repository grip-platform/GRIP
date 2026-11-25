"""Mesh-tailnet-setup idempotence kernel — the Banach fixed-point proof.

GRIP's cross-machine session mesh (grip-session-mesh: Layer-1 WebSocket bus,
Layer-2 Tailscale relay, Layer-3 SQLite presence MCP) must be brought up on
EVERY Tailnet machine so all sessions are concurrently context-aware. The setup
is run repeatedly — once per machine, and re-run whenever a machine reboots,
gains a peer, or a team member onboards. Re-running MUST be safe.

This kernel is dogfood-domain #3 of GRIP's idempotent-no-op / Banach
fixed-point primitive — the SAME proof shape as `lib/git_reconcile.py`
(`/git-solve`, git divergence) and `lib/scope_contamination.py` (cross-org
content), transplanted to a third, non-git, operational domain:

    git-solve:           a merge is CLEAN  iff merge-tree is a no-op.
    scope-contamination: content is CLEAN  iff sanitising it is a no-op.
    here (mesh setup):   a machine is SET-UP iff running setup is a no-op.

So "set up" is PROVEN, never asserted: ``apply(observed) == observed``. The
non-empty *delta* between the observed state and its applied (converged) form
IS the remaining work — concrete `PendingAction`s a script (or operator) can
execute, not a vague "the mesh might be down". That is the whole value: a
no-op proof gives you the exact missing steps for free, exactly as
`git merge-tree` gives you the exact conflict hunks for free.

Fixed-point framing (honest about its depth)
--------------------------------------------
Model the machine's mesh-readiness as a point in state-space: a mapping from
each required ``Check`` key to whether it is satisfied. The setup operator F
satisfies every check, so it maps ANY state to the all-satisfied state — F is a
*constant* map, hence a (trivial) contraction whose unique fixed point is the
all-satisfied state. A machine is CONVERGED iff it is already there, i.e.
``F(state) == state``; idempotence ``F(F(x)) == F(x)`` is then immediate and is
the formal content of "re-running setup is safe".

Because F is constant, the no-op equality ``F(observed) == observed`` is
DEFINITIONALLY just "every required check is satisfied" — it is the clean
fixed-point *formulation* (parity with the sibling kernels), NOT an independent
contraction proof. The load-bearing, Goodhart-resistant content lives in the
``pending`` list, derived INDEPENDENTLY from ``CHECK_SPECS`` (not from F's
output) and cross-checked against the verdict by an assert in ``check`` — that
independence, plus ``test_required_keys_anchors_the_spec`` pinning the exact
required-key tuple, is what kills the dropped-check / hardcoded mutants. (In the
observed-dependent sibling ``scope_contamination.sanitise`` the apply-operator
IS non-trivial — it returns the leaked-span residue — so there the no-op test is
itself the proof; here that weight is carried by the pending list.)

Purity
------
This module is the mechanical floor ONLY. It performs NO I/O — it never probes
ports, reads files, starts daemons, or mutates anything. It takes an *observed*
state (a plain mapping the caller assembles by probing the machine) plus the
required peer set, and returns a verdict with the exact pending actions. The
*execution* of those actions stays with the caller (``scripts/mesh-tailnet-setup.sh``),
exactly as `scope_contamination.check` leaves redaction to its caller. Pure
stdlib; every test runs hermetically against in-memory observed states.

The ``CHECK_SPECS`` table below is the single source of truth for what "set up"
means; the bash setup script keys its probes off the same check ids, so the
proof and the executor can never silently disagree about the target state.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Mapping

# --- The target spec: what "fully set up" means -----------------------------

# Platform tokens a check applies to. "*" = every platform.
_ALL = "*"


@dataclass(frozen=True)
class CheckSpec:
    """One required property of a mesh-ready machine (the desired state).

    ``key``      opaque id shared with the executor script's probes.
    ``describe`` plain-English statement of the satisfied state (for reports).
    ``action``   the remediation the executor runs when this check is unmet —
                 a concrete command/step, never a vague instruction.
    ``platforms`` tuple of platform tokens this check applies to ("*" = all).
                 A check that does not apply to the running platform is treated
                 as satisfied-by-vacuity (it can never be pending there), so the
                 fixed point is platform-correct without per-OS forks elsewhere.
    """

    key: str
    describe: str
    action: str
    platforms: tuple[str, ...] = (_ALL,)

    def applies_to(self, platform: str) -> bool:
        return _ALL in self.platforms or platform in self.platforms


# Single source of truth for the mesh-tailnet invariant. Order is the natural
# bring-up order (deps first) so a printed plan reads as a runnable sequence.
CHECK_SPECS: tuple[CheckSpec, ...] = (
    CheckSpec(
        "websockets_dep",
        "Python can import 'websockets' (the Layer-2 relay's only dependency)",
        "pip install websockets  (into the GRIP venv)",
    ),
    CheckSpec(
        "presence_built",
        "Layer-3 presence MCP server resolves at "
        "mcp-servers/grip-session-mesh/presence/dist/src/index.js and is valid JS",
        "git submodule update --init mcp-servers/grip-session-mesh && "
        "npm ci --prefix mcp-servers/grip-session-mesh/presence && "
        "npm run build --prefix mcp-servers/grip-session-mesh/presence "
        "(or symlink an existing built clone)",
    ),
    CheckSpec(
        "token",
        "Bus/relay bearer token present at ~/.grip-session-mesh/token (mode 0600)",
        "python3 relay/mesh_relay.py init-token",
    ),
    CheckSpec(
        "bus_running",
        "Layer-1 WebSocket bus answers http://127.0.0.1:9474/health",
        "start the bus (deploy/launchd/install-macos.sh, or "
        "npm start --prefix server)",
    ),
    CheckSpec(
        "relay_running",
        "Layer-2 Tailscale relay is listening on :9475",
        "start the relay (python3 relay/mesh_relay.py connect, via launchd)",
    ),
    CheckSpec(
        "peers_configured",
        "every required Tailnet peer IP is present in ~/.grip-session-mesh/peers.json",
        "python3 relay/mesh_relay.py remote add <peer-ip>  (per missing peer)",
    ),
    CheckSpec(
        "launchd_loaded",
        "the three com.grip.mesh-{bus,relay,client} LaunchAgents are loaded "
        "so the mesh survives reboot",
        "deploy/launchd/install-macos.sh",
        platforms=("macos",),
    ),
)


# --- Verdict shapes ---------------------------------------------------------

@dataclass(frozen=True)
class PendingAction:
    """One unmet check — a concrete next step toward the fixed point."""

    key: str
    describe: str
    action: str


@dataclass(frozen=True)
class SetupVerdict:
    """Result of comparing an observed machine state against the target spec.

    ``converged`` is True iff applying the setup is a no-op — i.e. there are
    zero pending actions, i.e. the observed state already IS the fixed point.
    This is the idempotent-no-op proof: readiness is demonstrated, not asserted.

    ``pending`` are the concrete actions still required (empty when converged).
    ``applied`` is the observed state advanced by the setup operator F — equal
    to the observed input exactly when ``converged`` is True.
    """

    converged: bool
    platform: str
    pending: tuple[PendingAction, ...]
    applied: dict[str, bool]
    observed: dict[str, bool]
    rationale: str

    @property
    def pending_keys(self) -> tuple[str, ...]:
        return tuple(p.key for p in self.pending)


# --- Core: normalise, apply the setup operator, prove the no-op -------------

def required_keys(platform: str) -> tuple[str, ...]:
    """The check keys that apply to ``platform`` (the target's coordinates)."""
    return tuple(c.key for c in CHECK_SPECS if c.applies_to(platform))


def _truthy(value: object) -> bool:
    """Coerce a shell-ish probe value to bool (\"false\"/\"0\"/\"\" -> False)."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _normalise(observed: Mapping[str, object], platform: str) -> dict[str, bool]:
    """Project an observed mapping onto exactly the platform's required keys.

    A key that does not apply to this platform is dropped. A required key that
    is absent from ``observed`` is treated as NOT satisfied (conservative: an
    unprobed requirement is assumed unmet, so the setup will (re)assert it).
    """
    return {k: _truthy(observed.get(k)) for k in required_keys(platform)}


def apply_setup(observed: Mapping[str, object], platform: str) -> dict[str, bool]:
    """The setup operator F: satisfy every required check.

    Maps ANY observed state to the unique all-satisfied state for this platform
    in a single step — the contraction whose fixed point is "fully set up".
    Pure: returns a new mapping, mutates nothing. Because the result is always
    all-True over the required keys, F is idempotent by construction
    (``apply_setup(apply_setup(x)) == apply_setup(x)``); the tests prove it
    rather than trusting this comment.
    """
    return {k: True for k in required_keys(platform)}


def is_fixed_point(observed: Mapping[str, object], platform: str) -> bool:
    """True iff running setup is a no-op here (the Banach fixed-point test)."""
    return apply_setup(observed, platform) == _normalise(observed, platform)


def _rationale(converged: bool, platform: str, pending: tuple[PendingAction, ...]) -> str:
    """Plain-English explanation of the verdict (the no-op / delta story)."""
    if converged:
        return (
            f"running mesh setup on this {platform} machine is a no-op — every "
            f"required layer is already up; CONVERGED (fixed point)."
        )
    keys = ", ".join(p.key for p in pending)
    return (
        f"running mesh setup would change {len(pending)} check(s) [{keys}] — "
        f"machine is NOT yet at the mesh fixed point."
    )


def check(observed: Mapping[str, object], platform: str) -> SetupVerdict:
    """Prove whether ``observed`` is a SET-UP machine via the no-op test.

    Convergence is decided by ``apply_setup(observed) == observed`` — the same
    shape as ``git merge-tree`` reporting "no conflict": a no-op exactly when
    the input already holds the target state.

    Examples:
        >>> full = {k: True for k in required_keys("linux")}
        >>> check(full, "linux").converged
        True
        >>> check({**full, "bus_running": False}, "linux").pending_keys
        ('bus_running',)
    """
    normalised = _normalise(observed, platform)
    applied = apply_setup(observed, platform)
    pending = tuple(
        PendingAction(spec.key, spec.describe, spec.action)
        for spec in CHECK_SPECS
        if spec.applies_to(platform) and not normalised[spec.key]
    )
    converged = applied == normalised
    # LOAD-BEARING GOODHART GUARD: `pending` is derived INDEPENDENTLY from
    # CHECK_SPECS + `normalised` (NOT from `applied`); this assert cross-checks it
    # against the no-op verdict. That independence is what kills a vacuous-apply or
    # dropped-check mutant — do NOT "simplify" pending to derive from `applied`,
    # which would collapse the cross-check (see the module docstring).
    assert converged == (len(pending) == 0), "no-op proof disagrees with pending set"
    return SetupVerdict(
        converged=converged,
        platform=platform,
        pending=pending,
        applied=applied,
        observed=normalised,
        rationale=_rationale(converged, platform, pending),
    )


# --- Thin I/O shell (the only impure part) ----------------------------------

def _render(verdict: SetupVerdict) -> str:
    """Human + machine readable verdict for the executor script / operator."""
    head = "CONVERGED" if verdict.converged else "PENDING"
    lines = [f"[mesh-setup] {head} ({verdict.platform}): {verdict.rationale}"]
    for p in verdict.pending:
        lines.append(f"  - {p.key}: {p.describe}")
        lines.append(f"      -> {p.action}")
    return "\n".join(lines)


def _read_state(state_file: str | None) -> str:
    if state_file:
        from pathlib import Path
        return Path(state_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def _detect_platform() -> str:
    import platform as _p
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(
        _p.system(), "linux"
    )


def _verdict_json(verdict: SetupVerdict) -> str:
    return json.dumps({
        "converged": verdict.converged,
        "platform": verdict.platform,
        "pending": [
            {"key": p.key, "describe": p.describe, "action": p.action}
            for p in verdict.pending
        ],
    }, indent=2)


def main(argv: list[str] | None = None) -> int:
    """CLI: read an observed-state JSON, print the verdict, exit 0 iff converged.

    The bash setup script probes the machine, pipes the observed JSON in, and
    branches on the exit code: 0 = nothing to do (no-op), 1 = pending printed.
    ``--spec`` prints the required check keys (so the script and kernel agree).
    """
    import argparse

    parser = argparse.ArgumentParser(description="mesh-tailnet-setup idempotence kernel")
    parser.add_argument("--platform", default=_detect_platform(),
                        help="macos|linux|windows (default: autodetect)")
    parser.add_argument("--state-file", help="observed-state JSON path (default: stdin)")
    parser.add_argument("--spec", action="store_true",
                        help="print the required check keys for the platform and exit")
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = parser.parse_args(argv)

    if args.spec:
        print("\n".join(required_keys(args.platform)))
        return 0

    raw = _read_state(args.state_file)
    observed = json.loads(raw) if raw.strip() else {}
    verdict = check(observed, args.platform)
    print(_verdict_json(verdict) if args.json else _render(verdict))
    return 0 if verdict.converged else 1


__all__ = [
    "CheckSpec",
    "CHECK_SPECS",
    "PendingAction",
    "SetupVerdict",
    "required_keys",
    "apply_setup",
    "is_fixed_point",
    "check",
]


if __name__ == "__main__":
    raise SystemExit(main())
