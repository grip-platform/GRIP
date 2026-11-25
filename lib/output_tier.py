#!/usr/bin/env python3
"""GRIP output-tier resolver + thin CLI (GRIP #2776 + #2868 ultraenhance).

Design LOCKED in drafts/output-style-ultraenhance/02-design.md. This module
is a RESOLVER ONLY: it does not transform output. The resolved tier
governs the assistant's prose register per rules/output-tier-default.md;
the precision-preservation invariant (evidence tokens never rounded at
ANY tier) is a review-and-rail discipline, not enforced here.

User-facing aliases (KISS-named, default = medium):
  super-simple → eli5 (ELI5 register, broader audiences)
  medium       → simple (DEFAULT — concise + plain + no decorative IDs)
  detailed     → caveman_plain (audit-trail mode, RSI/traceability work)

Internal names (back-compat — resolve to themselves):
  eli5, simple, caveman_plain

Resolution precedence (mirrors grip_channels_wizard --resolve):
  1. env GRIP_OUTPUT_TIER
  2. state/output-tier-config.json  {"tier": "..."}
  3. default: simple (medium) — was caveman_plain pre-#2868 W7
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Internal tier names — unchanged for back-compat with prior callers.
TIERS = ("caveman_plain", "simple", "eli5")

# User-facing alias → internal tier. Internal names resolve to themselves
# so existing consumers passing "simple" / "eli5" / "caveman_plain"
# continue to work unchanged.
TIER_ALIASES = {
    "super-simple": "eli5",
    "medium":       "simple",
    "detailed":     "caveman_plain",
    "eli5":          "eli5",
    "simple":        "simple",
    "caveman_plain": "caveman_plain",
}

DEFAULT_TIER = "simple"  # was "caveman_plain" — V>> 2026-05-21 directive
_ENV = "GRIP_OUTPUT_TIER"


def _config_path() -> Path:
    """Live config path resolved at CALL time — CLAUDE_DIR may be set
    after import; no import-time freeze (HAL #2785 Speaker C)."""
    base = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    return base / "state" / "output-tier-config.json"


def _norm(value: object) -> Optional[str]:
    """Map any input (alias or internal name) to internal tier, or None."""
    v = str(value).strip().lower()
    return TIER_ALIASES.get(v)


def _from_env() -> Optional[str]:
    """Resolve env var. Warns to stderr if set-but-invalid so silent
    fallthrough does not lose user intent (AGORA Council 2026-05-21,
    concern 1, PR #3115)."""
    raw = os.environ.get(_ENV, "")
    if raw == "":
        return None
    resolved = _norm(raw)
    if resolved is None:
        print(
            "warning: invalid %s=%r, falling back to config/default"
            % (_ENV, raw),
            file=sys.stderr,
        )
    return resolved


def _from_config(path: Path) -> Optional[str]:
    try:
        return _norm(json.loads(path.read_text()).get("tier", ""))
    except (OSError, ValueError):
        return None


def resolve(path: Optional[Path] = None) -> str:
    """Active tier: env > config > default. Never raises."""
    return _from_env() or _from_config(path or _config_path()) or DEFAULT_TIER


def set_tier(tier: str, *, set_by: str = "cli") -> int:
    """Persist tier choice. Accepts alias or internal name; stores user
    literal for readability.

    Public API (AGORA Council 2026-05-21, concern 3, PR #3115). Wizards
    and external callers SHOULD use this name. `_set_tier` retained as
    a deprecated alias for back-compat with prior internal callers."""
    internal = _norm(tier)
    if internal is None:
        print("unknown tier %r — choose: %s" % (tier, ", ".join(sorted(set(TIER_ALIASES)))))
        return 2
    cfg = _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": tier,
        "set_by": set_by,
        "set_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }
    cfg.write_text(json.dumps(payload, indent=2) + "\n")
    print("output tier set: %s (resolves to: %s)" % (tier, internal))
    return 0


# Deprecated alias — kept for back-compat with prior internal callers.
# New code should call set_tier() directly.
_set_tier = set_tier


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    accepted = sorted(set(TIER_ALIASES))
    parser = argparse.ArgumentParser(prog="grip output-tier")
    parser.add_argument(
        "tier", nargs="?",
        help="tier to set: " + " ".join(accepted) + " (omit / 'status' to query)",
    )
    parser.add_argument(
        "--resolve", action="store_true",
        help="print the resolved internal tier and exit",
    )
    args = parser.parse_args(argv)
    if args.resolve or args.tier in (None, "status"):
        print(resolve())
        return 0
    if _norm(args.tier) is None:
        print("unknown tier %r — choose: %s" % (args.tier, ", ".join(accepted)))
        return 2
    return set_tier(args.tier)


if __name__ == "__main__":
    raise SystemExit(main())
