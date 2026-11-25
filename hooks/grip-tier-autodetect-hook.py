#!/usr/bin/env python3
"""SessionStart hook — self-heal GRIP plan-tier for Max-class subscriptions.

Problem it fixes: lib/grip_tier.detect_tier() floors every keyless
subscription to 'pro' (the safe pre-launch default — Max vs Pro is
indistinguishable before a session exists). bin/grip then exports
CLAUDE_CODE_AUTO_COMPACT_WINDOW=180000, capping auto-compact at 180K even on
a Max plan that INCLUDES Opus 1M for free. The symptom an operator sees: a
"1M context" model label sitting on a 180K auto-compact window (see
docs/grip-tier-1m-context.md), and a session that compacts far too early.

This hook reads Claude Code's exported CLAUDE_CODE_SUBSCRIPTION_TYPE (the
verified subscription class) and, if Max-class, persists tier=max so the NEXT
`grip` launch stops capping and lets the full Opus 1M window stand. No manual
`grip tier max` needed.

It then syncs lib/model_state_manager.py's state (context_window/extended) to
whichever tier just resolved — THIS session, not the next one. The context
gate reads that file at import time, before any tier logic runs
(lib/gates/context_gate.py get_context_limits()), so without this a Pro
session's gate ran with DEFAULT_STATE's 1M assumption rather than the tier's
real 200K ceiling until something else happened to rewrite the file.

One-session-delayed by design: the auto-compact window is fixed at launch, so
THIS session keeps its current window; the next one uncaps. That keeps
detect_tier()'s "never assert an entitlement before launch" R0 floor intact —
the upgrade fires only AFTER Claude Code has confirmed the subscription class.

The real work lives in lib/grip_tier.autodetect_and_persist (KISS — one place
owns the upgrade contract). This hook is the caller boundary; the library is
the capability boundary (same split as hooks/operator-session-tracker.py).

Fail-safe: any error (missing env, unwritable config, import failure) exits 0.
The tier-detection half is silent about it; the model-state sync half prints
one line to stderr so a new failure is visible while it beds in. Either way,
SessionStart hooks MUST never block session bootstrap.

Reversibility: removing this hook is a clean no-op — the tier config it writes
is honoured by the existing resolver, and nothing else depends on the hook. An
operator's explicit `grip tier <x>` choice is never overridden.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parent.parent  # ~/.claude/
    lib = repo / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import grip_tier  # noqa: WPS433  (lazy by design — cheap startup)

        written = grip_tier.autodetect_and_persist()
    except Exception:  # noqa: BLE001  fail-safe: hook must never block
        return 0
    if written == "max":
        print(
            "[grip-tier] Max-class plan detected — tier set to max; next "
            "launch uncaps to Opus 1M (was capped at 180K auto-compact)."
        )

    # Separate try/except from the block above: a failure here is logged,
    # never silently swallowed, because it is new and worth seeing while it
    # beds in. Still fail-open — a SessionStart hook must never block boot.
    try:
        import model_state_manager  # noqa: WPS433  (lazy by design — cheap startup)

        model_state_manager.sync_from_tier()
    except Exception as exc:  # noqa: BLE001  fail-safe: hook must never block
        print(f"[grip-tier] model-state sync failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        sys.exit(0)
