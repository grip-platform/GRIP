"""Auto-compact policy — OPT-IN, machine-global "auto-compact + auto-continue
at ~90% context" decision (PARAMOUNT, SAFETY-SENSITIVE).

Relationship to PARAMOUNT Rule 8 (context gate = HALT-and-ask, never
auto-serialize) and ``rules/rsi-loop-no-yield.md`` (harness auto-compact is
already the relief valve for autonomous loops):

  This module is **purely ADDITIVE**. When disabled — which is the DEFAULT,
  and ALSO the state when the config file is ABSENT or invalid — it returns
  :data:`HALT` and the caller's behaviour is *byte-for-byte identical* to
  today's Rule-8 HALT-and-ask. It NEVER weakens Rule 8 for a non-enabled
  session. It is consulted through a thin read-only seam; it flips no default
  ON; removing the flag restores HALT (reversible by construction).

Correct-by-construction safety invariants (each is a test in
``tests/test_auto_compact_policy.py``):

  1. ``enabled=False`` OR config absent/invalid  → :data:`HALT` at/above the
     gate, exactly like today. DEFAULT-OFF == current behaviour.
  2. ``enabled=True`` AND ``is_autonomous`` AND ``context_pct >= threshold``
     → :data:`CHECKPOINT_THEN_COMPACT`. Checkpoint is named FIRST in the verb
     — the caller MUST preserve state before compacting; that ordering is the
     CBC property that makes auto-continue lossless.
  3. ``context_pct < threshold``                  → :data:`CONTINUE`.
  4. Reversibility: deleting the flag / config restores :data:`HALT`.

Non-autonomous (interactive) sessions ALWAYS get :data:`HALT` at/above
threshold regardless of ``enabled`` — an interactive operator is present to
choose compact / serialize+fresh / push-through (Rule 8). The opt-in only
changes behaviour for autonomous loops, where the harness auto-compact is
already the sanctioned relief valve.

Design: pure (no I/O at decision time), DIP-injected (the config loader is a
parameter — tests inject a dict, production reads JSON), hermetic (no network,
no NET global mutable state — the lazy claude_dir bootstrap snapshots and
restores ``sys.path``; see :func:`_resolve_claude_dir`). ``policy_decision``
itself never touches the filesystem or ``sys.path``; only the convenience
:func:`load_policy_config` reads JSON, and it fails CLOSED (any read/parse
error → ``enabled=False``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

# --- Decision constants ---------------------------------------------------
# String values (not an IntEnum) so callers and telemetry stay self-describing
# and a wrong comparison fails loudly rather than silently coercing.
HALT = "HALT"
CHECKPOINT_THEN_COMPACT = "CHECKPOINT_THEN_COMPACT"
CONTINUE = "CONTINUE"

#: All admissible return values of :func:`policy_decision`.
DECISIONS = frozenset({HALT, CHECKPOINT_THEN_COMPACT, CONTINUE})

#: Default opt-in threshold (percent). Deliberately HIGHER than the gate's own
#: 85% CONTEXT_CRITICAL — the policy is a separate, operator-chosen "~90%"
#: auto-continue knob, not a re-tuning of the gate.
DEFAULT_THRESHOLD = 90.0

def _resolve_claude_dir() -> Path:
    """Resolve the GRIP install dir LAZILY, NET side-effect-free.

    The path-portability shim (``rules/path-portability.md``) lives in
    ``lib/claude_dir.py``; importing it needs ``lib/`` on ``sys.path``. Two
    side-effects are avoided: (1) doing the ``sys.path.insert`` at MODULE-import
    time would mutate global state for EVERY consumer — so the bootstrap is
    deferred to HERE, on first config resolution; (2) even here we SNAPSHOT and
    RESTORE ``sys.path`` (the claude_dir module is cached in ``sys.modules`` after
    the first import, so the path entry is never needed again), leaving global
    state exactly as found. Net result: importing this module, calling the pure
    :func:`policy_decision`, AND resolving a config path all leave ``sys.path``
    unchanged. Falls back to ``$CLAUDE_DIR`` / ``~/.claude`` if the shim is
    unavailable.
    """
    lib_dir = str(Path(__file__).resolve().parent)
    added = lib_dir not in sys.path
    if added:
        sys.path.insert(0, lib_dir)
    try:
        from claude_dir import get_claude_dir  # noqa: E402

        return get_claude_dir()
    except Exception:  # pragma: no cover - shim always present in GRIP tree
        env = os.environ.get("CLAUDE_DIR", "").strip()
        return Path(env) if env else (Path.home() / ".claude")
    finally:
        # Restore sys.path to exactly what we found — only remove what THIS call
        # added (never another caller's entry). The import above cached
        # claude_dir in sys.modules, so dropping the path entry is safe.
        if added and lib_dir in sys.path:
            sys.path.remove(lib_dir)


def _default_config_path() -> Path:
    """Resolve the durable config path LAZILY — never at import time.

    Defers to :func:`_resolve_claude_dir` (which also defers the
    ``sys.path``/``claude_dir`` bootstrap), so neither importing this module nor
    calling the pure :func:`policy_decision` ever touches ``sys.path``, the
    filesystem, or ``Path.home()``. A broken home/env only surfaces when a caller
    actually loads config, and is then absorbed fail-closed by
    :func:`load_policy_config`. Gitignored per ``state/**/*.json``.
    """
    return _resolve_claude_dir() / "state" / "auto-compact-config.json"


def policy_decision(
    context_pct: float,
    *,
    enabled: bool,
    threshold: float = DEFAULT_THRESHOLD,
    is_autonomous: bool,
) -> str:
    """Return the auto-compact policy decision for the current context state.

    This is a PURE function — no I/O, no globals, deterministic in its
    arguments. Resolution order (first match wins):

      * ``enabled`` is falsy                       → :data:`HALT`
        (DEFAULT-OFF — identical to today's Rule-8 HALT-and-ask).
      * ``context_pct`` below ``threshold``        → :data:`CONTINUE`
        (plenty of runway; do not interfere).
      * at/above ``threshold`` but NOT autonomous  → :data:`HALT`
        (interactive operator present — Rule 8 HALT-and-ask is preserved).
      * at/above ``threshold`` AND autonomous      → :data:`CHECKPOINT_THEN_COMPACT`
        (checkpoint state FIRST, then let the harness compact + continue).

    Args:
        context_pct: Current context usage as a percentage. Contract domain is
            ``0..100``; outside it (or NaN/unparseable) is a sensor error ⇒ HALT
            (never auto-compact on a garbage reading).
        enabled: The opt-in flag. Falsy ⇒ behaviour byte-identical to today.
        threshold: Percent at/above which auto-compact may engage. Default 90.
            Must lie in ``(0, 100]``; a degenerate threshold (<=0, >100, or
            NaN/unparseable) is treated as OFF ⇒ HALT — whether it reaches here
            from a direct caller or the (already range-validated) config loader.
        is_autonomous: Whether an autonomous loop is the actor. Interactive
            sessions never auto-compact (Rule 8).

    Returns:
        One of :data:`HALT`, :data:`CHECKPOINT_THEN_COMPACT`, :data:`CONTINUE`
        (always a member of :data:`DECISIONS`).
    """
    # Invariant 1 — DEFAULT-OFF: a disabled policy is the current HALT, always.
    # This branch is checked FIRST so no enabled-only logic can ever change the
    # behaviour of a non-enabled session.
    if not enabled:
        return HALT

    # Defensive: a non-finite or NaN threshold must not silently enable
    # auto-compact at every percentage. Treat a broken threshold as OFF.
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        return HALT
    if thr != thr:  # NaN
        return HALT
    # Out-of-contract threshold: <=0 would make every pct "at/above" (compact
    # always); >100 is unreachable. Treat as OFF ⇒ HALT rather than risk
    # compacting on a degenerate threshold. (config-loaded thresholds are already
    # range-validated; this guards direct callers of policy_decision.)
    if thr <= 0.0 or thr > 100.0:
        return HALT

    try:
        pct = float(context_pct)
    except (TypeError, ValueError):
        # Unknown usage ⇒ be conservative ⇒ HALT (never auto-compact blind).
        return HALT
    if pct != pct:  # NaN
        return HALT
    # Out-of-contract context_pct: the documented domain is 0..100. A value
    # outside it is a sensor/caller error, not a real context level — HALT (the
    # same conservative posture as the unparseable branch above).
    if pct < 0.0 or pct > 100.0:
        return HALT

    # Invariant 3 — below threshold: leave the loop alone.
    if pct < thr:
        return CONTINUE

    # At/above threshold. Interactive ⇒ Rule 8 HALT-and-ask (operator present).
    if not is_autonomous:
        return HALT

    # Invariant 2 — enabled + autonomous + at/above threshold:
    # checkpoint FIRST (lossless), then compact + continue.
    return CHECKPOINT_THEN_COMPACT


def load_policy_config(
    *,
    path: Path | None = None,
    reader: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Load the durable opt-in policy config, failing CLOSED (OFF).

    DIP seam: ``reader`` is injectable so tests need not touch the filesystem.
    Production default reads :data:`CONFIG_PATH` (or ``path``) as JSON.

    Treats ABSENT, unreadable, or invalid config as fully OFF — returns a
    normalised dict ``{"enabled": False, "threshold": DEFAULT_THRESHOLD}``.
    Any present-but-malformed field degrades to its safe default; the function
    NEVER raises.

    Returns:
        ``{"enabled": bool, "threshold": float}`` — always safe to consume.
    """
    safe_default = {"enabled": False, "threshold": DEFAULT_THRESHOLD}

    try:
        # Resolve the default path INSIDE the fail-closed try: a broken home/env
        # resolution (get_claude_dir → RuntimeError) must degrade to OFF, never
        # propagate. An explicit ``path`` or ``reader`` bypasses resolution.
        cfg_path = path if path is not None else _default_config_path()
        if reader is not None:
            raw = reader(cfg_path)
        else:
            if not Path(cfg_path).exists():
                return dict(safe_default)
            raw = Path(cfg_path).read_text()
        data = json.loads(raw)
    except Exception:
        # ABSENT / unreadable / invalid JSON / unresolvable path ⇒ OFF.
        return dict(safe_default)

    if not isinstance(data, dict):
        return dict(safe_default)

    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        # Only the literal boolean ``true`` enables — never a truthy string/int.
        enabled = False

    threshold = data.get("threshold", DEFAULT_THRESHOLD)
    try:
        threshold = float(threshold)
        if threshold != threshold or threshold <= 0 or threshold > 100:
            threshold = DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD

    return {"enabled": enabled, "threshold": threshold}


def consult(
    context_pct: float,
    *,
    is_autonomous: bool,
    config: dict[str, Any] | None = None,
    config_loader: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """Read-only seam helper: load the (opt-in) config then decide.

    This is the single entry point a caller (e.g. the context gate) uses to
    consult the policy WITHOUT flipping any default. With no config present it
    returns :data:`HALT`, so wiring this in changes nothing until an operator
    explicitly writes ``state/auto-compact-config.json`` with ``enabled:true``.

    Args:
        context_pct: Current context usage percentage.
        is_autonomous: Whether an autonomous loop is the actor.
        config: Pre-loaded config dict (tests inject this directly).
        config_loader: Zero-arg loader (defaults to :func:`load_policy_config`).

    Returns:
        A member of :data:`DECISIONS`.
    """
    if config is None:
        loader = config_loader or load_policy_config
        try:
            config = loader()
        except Exception:
            config = {"enabled": False, "threshold": DEFAULT_THRESHOLD}

    enabled = bool(config.get("enabled", False))
    threshold = config.get("threshold", DEFAULT_THRESHOLD)
    return policy_decision(
        context_pct,
        enabled=enabled,
        threshold=threshold,
        is_autonomous=is_autonomous,
    )


def __getattr__(name: str) -> Any:
    """Lazily resolve module-level ``CONFIG_PATH`` (PEP 562).

    Keeping ``CONFIG_PATH`` out of import-time evaluation means importing this
    module never forces ``get_claude_dir()`` (hence ``Path.home()``) to resolve —
    the pure :func:`policy_decision` stays importable even on a broken host.
    Accessing ``auto_compact_policy.CONFIG_PATH`` resolves it on demand.
    """
    if name == "CONFIG_PATH":
        return _default_config_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "HALT",
    "CHECKPOINT_THEN_COMPACT",
    "CONTINUE",
    "DECISIONS",
    "DEFAULT_THRESHOLD",
    # CONFIG_PATH is intentionally NOT a star-export: it resolves lazily on
    # attribute access via __getattr__ (PEP 562). Listing it here would make
    # `from ... import *` / static analysers resolve a name that exists only on
    # demand. `auto_compact_policy.CONFIG_PATH` still works for direct callers.
    "policy_decision",
    "load_policy_config",
    "consult",
]
