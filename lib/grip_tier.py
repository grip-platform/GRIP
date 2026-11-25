#!/usr/bin/env python3
"""GRIP plan-tier resolver + thin CLI.

Origin: 2026-05-28. A former client contact (Pro plan) found GRIP unusable because Claude
Code's 1M-context window is credit-gated on every subscription plan
(Pro: 1M requires usage credits; Max/Team/Enterprise: Opus 1M included
but Sonnet 1M still credit-gated; only API/pay-as-you-go gets 1M free).
Verified by reading Claude Code's own source — the context-window gate is
SERVER-SIDE entitlement (`ff()` resolver in the CC bundle returns 200000
for a non-entitled principal; the server returns the real `contextLimit`
in a 400 and the client adapts down). No client env var raises the
ceiling. See docs/grip-tier-1m-context.md for the full source trace.

Dated note (2026-10-15): that trace is from the Opus 4.6 / Sonnet 4.6 era
(its eligibility set enumerates `opus-4-6`/`opus-4-7`). It is not wrong
about the server-side gate, but it is no longer the current entitlement
picture: vendor pages fetched 2026-10-15 settle the current generation
instead — Claude Code gives Pro the full 1M window by default on every
current model (Opus 5/5.5, Sonnet 5/5.5, Haiku 5.5, Fable 5/5.1). See
docs/measurements/2026-10-15-pro-plan-vendor-facts.md. The 200K envelope
below is GRIP's own CHOICE on Pro, to protect the single shared
five-hour-and-weekly allowance, not an entitlement GRIP is working around.

This module is a RESOLVER ONLY (same contract as lib/output_tier.py): it
does not call any API. It resolves which plan-tier GRIP should assume,
and exposes the per-tier *advisory* settings GRIP applies so a Pro user
gets a working session inside 200K instead of hitting the wall.

The plan-tier is orthogonal to output_tier (prose register). This is
about the context-window/economics envelope, not how verbose GRIP is.

Tiers (user-facing, default = auto):
  pro   — subscription Pro. Assume 200K usable. Compact early
          (CLAUDE_CODE_AUTO_COMPACT_WINDOW ~180K), lean on KONO/HOLISTIC
          memory, route heavy-context work to HAL non-Anthropic providers.
  max   — subscription Max/Team/Enterprise. Opus 1M included; Sonnet 1M
          still needs credits. Assume 1M on Opus, 200K-compact on Sonnet.
  api   — API key / pay-as-you-go. Full 1M on all models, no gating.
  auto  — DEFAULT. Detect from environment (Anthropic API key present →
          api; else subscription → pro as the safe floor). Never assumes
          an entitlement the principal may not have.

Resolution precedence (mirrors lib/output_tier.py / grip_channels_wizard):
  1. env GRIP_TIER
  2. state/grip-tier-config.json  {"tier": "..."}
  3. auto-detection (api if an Anthropic API key is present, else pro)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Internal tier names — these are also the user-facing names (KISS: no
# alias indirection needed here, unlike output_tier's super-simple/medium).
TIERS = ("pro", "max", "api", "auto")

DEFAULT_TIER = "auto"
_ENV = "GRIP_TIER"

# Name of the env var whose PRESENCE (not value) signals API-key auth.
# We deliberately never read its value — only test membership in os.environ
# — so this module exposes zero secret material (env-var-protection-safe by
# construction; see rules/no-guardrail-evasion.md — this is the legit-code
# form, presence-only, not an obfuscated read).
_API_KEY_ENV_NAME = "ANTHROPIC_API_KEY"

# Per-tier advisory envelope. These are what GRIP APPLIES once a tier is
# resolved — surfaced to the session-start path and the wizard. They are
# advisory (a Pro user CAN still buy credits); GRIP never fabricates an
# entitlement. `auto_compact_window` is the value GRIP suggests for
# CLAUDE_CODE_AUTO_COMPACT_WINDOW; it is clamped by Claude Code itself to
# min(value, real_model_window), so on a 200K principal it resolves to
# 200K-safe regardless. `route_heavy_to_hal` flags whether long-context
# grunt work (big reads, corpus search) should prefer a HAL non-Anthropic
# provider to spare the Anthropic context budget.
#
# `server_context_mgmt` records that Claude Code's server-side
# context-management beta (`context-management-2025-06-27`, "BZ8") is ACTIVE
# for this session. VERIFIED-CANONICAL twice:
#   - GATE (cli-formatted.js L156001-156003): BZ8 is pushed for EVERY
#     first-party Claude-4 request with NO subscription/credit gate. `i7()`
#     (subscription auth) and BZ8 are orthogonal (never co-occur) → free on
#     ALL tiers, distinct from the credit-gated 1M window. (TRUE.)
#   - PAYLOAD (cli-formatted.js `C85` L504723-504734): the context_management
#     body emits ONLY `{type:"clear_thinking_20251015", keep:"all"}`, and
#     ONLY when extended thinking is on. It clears PRIOR-TURN THINKING BLOCKS,
#     never tool results, and does NOT summarise. With thinking off the key
#     is dropped entirely.
# CORRECTION (broly mesh council, 2026-05-29): an earlier note here claimed
# BZ8 "clears stale tool-results / stretches 200K". That was FALSIFIED by
# C85 — it is a thinking-block janitor, marginal, not a capacity extender.
# So `server_context_mgmt: True` means "the (free, thinking-clear) beta is
# active and GRIP doesn't disable it" — NOT "Pro's window is materially
# bigger". GRIP cannot enable it harder (no enabling env var; only
# CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS disables it — guarded by
# test_grip_never_disables_experimental_betas). The genuine free
# capacity-extender is `compact-2026-01-12` (server-side SUMMARISATION),
# which CC's runtime does NOT send — a HAL-direct opportunity, not a CC
# session lever. Full trace: docs/grip-tier-1m-context.md.
# The full 1M context window (max/api entitlement). Single source for every
# envelope site below (#3509 DRY follow-up to PR #3507).
_FULL_WINDOW = 1_000_000

TIER_ENVELOPE = {
    "pro": {
        "assumed_window": 200_000,
        "auto_compact_window": 180_000,
        "route_heavy_to_hal": True,
        "server_context_mgmt": True,
        # What `grip-tier pro` and the wizard's Step 5.65 APPLY through
        # lib/pro_launch_defaults.py: the launch pin (lib/model_pin, the plain
        # vendor id) and the effort level (settings.json env + effortLevel).
        # Only Pro carries these two keys: on max and api the reference
        # profile's max effort and the operator's own pin stay untouched.
        "launch_model": "claude-sonnet-5-5",
        "launch_effort": "medium",
        "context_note": "BZ8 (free) clears prior-turn THINKING blocks "
                        "server-side when thinking is on (keep:all) — a small "
                        "input-token trim, NOT a window extension. Claude Code "
                        "already gives Pro the full 1M window by default on "
                        "current models (see "
                        "docs/measurements/2026-10-15-pro-plan-vendor-facts.md); "
                        "GRIP chooses 200K anyway, compacting at 180K + leaning "
                        "on KONO memory, to protect the single Pro allowance "
                        "instead of spending it on a bigger window.",
        "blurb": "Pro plan — GRIP operates at 200K by choice, compacting "
                 "early + using memory so sessions survive. Claude Code "
                 "already gives Pro the full 1M window by default on "
                 "current models; GRIP stays at 200K to protect your "
                 "single five-hour-and-weekly allowance.",
    },
    "max": {
        "assumed_window": _FULL_WINDOW,
        # CC's UNSET default is R85=180_000 (see docs/grip-tier-1m-context.md
        # Finding "CC local auto-compact … default R85=180000"), NOT the model
        # window — so leaving this None compacts at 180K even on a 1M plan
        # (the "premature compaction" bug). Assert 1M explicitly; CC min's it
        # with the real window (Finding 5), so it's strictly safe: 1M when
        # entitled, real-window-safe (e.g. 200K) otherwise.
        "auto_compact_window": _FULL_WINDOW,
        "route_heavy_to_hal": False,
        "server_context_mgmt": True,
        "context_note": "BZ8 (free, orthogonal to subscription) ships on Max "
                        "identically to Pro — clears prior-turn thinking "
                        "blocks only. Max's capacity advantage is Opus 1M "
                        "(separate credit-included path), NOT BZ8.",
        "blurb": "Max/Team/Enterprise — Opus 1M included. (Sonnet 1M still "
                 "needs usage credits.)",
    },
    "api": {
        "assumed_window": _FULL_WINDOW,
        "auto_compact_window": _FULL_WINDOW,  # explicit 1M (CC min's with real window; unset would default to 180K)
        "route_heavy_to_hal": False,
        "server_context_mgmt": True,
        "context_note": "BZ8 (free) active. UNCLAIMED HEADROOM: HAL can send "
                        "context_management.edits / compact-2026-01-12 "
                        "(server-side summarisation) directly — CC's runtime "
                        "never sends it, so it's not a duplicate of BZ8.",
        "blurb": "API / pay-as-you-go — full 1M on all models, no gating.",
    },
}


def _config_path() -> Path:
    """Live config path resolved at CALL time — CLAUDE_DIR may be set
    after import (mirrors lib/output_tier.py)."""
    base = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    return base / "state" / "grip-tier-config.json"


def _norm(value: object) -> Optional[str]:
    """Map input to a known tier, or None."""
    v = str(value).strip().lower()
    return v if v in TIERS else None


def _from_env() -> Optional[str]:
    """Resolve env var. Warns to stderr if set-but-invalid so a typo does
    not silently fall through (same discipline as output_tier)."""
    raw = os.environ.get(_ENV, "")
    if raw == "":
        return None
    resolved = _norm(raw)
    if resolved is None:
        print(
            "warning: invalid %s=%r, falling back to config/auto-detect"
            % (_ENV, raw),
            file=sys.stderr,
        )
    return resolved


def _from_config(path: Path) -> Optional[str]:
    # AttributeError: valid JSON that is not an object (a list, a number) has
    # no .get, and a malformed config must read as "not set", never crash.
    try:
        return _norm(json.loads(path.read_text()).get("tier", ""))
    except (OSError, ValueError, AttributeError):
        return None


def _api_key_present() -> bool:
    """True iff an Anthropic API key env var is set and non-empty.

    Presence-only: reads membership + emptiness, never the secret value
    (env-var-protection-safe; the guardrail's own recommended `test -n`
    shape, expressed in Python)."""
    return bool(os.environ.get(_API_KEY_ENV_NAME, "").strip())


# Process-memo for the keychain read: [] = not yet read, [val] = cached (val may be
# None). resolve()/detect_tier()/envelope() can each fire per session, but the OAuth
# subscription class is stable within a process — so read `security` at most ONCE.
_OAUTH_SUB_CACHE: list = []


def _keychain_oauth_subscription_type() -> Optional[str]:
    """The VERIFIED `subscriptionType` from the macOS Claude OAuth credential
    (keychain `Claude Code-credentials`), lower-cased, or None.

    Reads the subscription class the OAuth token itself asserts — the same verified
    entitlement `autodetect_and_persist` reads post-launch — so it is NOT asserting
    an UNverified entitlement (R0-safe). **macOS-only** (`security` CLI): on any
    other platform it short-circuits to None with NO subprocess. **Cached** per
    process (value is stable within a session — no repeated subprocess). Fail-safe:
    no CLI / entry absent / parse error / token expired → None.
    """
    if _OAUTH_SUB_CACHE:
        return _OAUTH_SUB_CACHE[0]
    import sys
    if sys.platform != "darwin":
        _OAUTH_SUB_CACHE.append(None)  # `security` is macOS-only — never spawn elsewhere
        return None
    import subprocess
    import json
    import time
    sub: Optional[str] = None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            oauth = json.loads(r.stdout).get("claudeAiOauth", {})
            exp = oauth.get("expiresAt")
            if not (exp and int(exp) / 1000 <= time.time()):  # not expired
                sub = str(oauth.get("subscriptionType", "")).strip().lower() or None
    except Exception:
        sub = None  # any keychain/format drift → fail-safe to None (falls through to env/floor)
    _OAUTH_SUB_CACHE.append(sub)
    return sub


def _oauth_max_subscription_present() -> bool:
    """True iff a VALID Max-class Claude OAuth credential is present."""
    sub = _keychain_oauth_subscription_type()
    return bool(sub and any(k in sub for k in _MAX_CLASS_SUBSCRIPTIONS))


def detect_tier() -> str:
    """Auto-detect the safe tier from the environment.

    Order (council acc474 follow-up): a VERIFIED Max OAuth subscription wins —
    a stored Max credential is the real entitlement, so don't under-claim it
    (this also stops a stray ANTHROPIC_API_KEY fallback from mislabelling a Max
    subscription as 'api'). Else API-key auth → 'api' (full 1M). Else the
    principal is on a subscription whose class we cannot verify → 'pro' as the
    SAFE FLOOR (compact early rather than overrun). An operator can always pin
    GRIP_TIER explicitly.

    R0: still never asserts an UNverified entitlement. The 'max' branch reads the
    OAuth token's own verified subscriptionType (fail-safe → falls through); the
    'pro' floor remains worst-case-correct for an unverifiable subscription.
    """
    if _oauth_max_subscription_present():
        return "max"
    if _api_key_present():
        return "api"
    return "pro"


def resolve(path: Optional[Path] = None) -> str:
    """Active tier: env > config > auto-detect. Never raises. Resolves
    'auto' through detection so callers always get a concrete tier."""
    chosen = _from_env() or _from_config(path or _config_path()) or DEFAULT_TIER
    if chosen == "auto":
        return detect_tier()
    return chosen


def resolve_configured(path: Optional[Path] = None) -> Optional[str]:
    """Active tier from EXPLICIT sources only — env GRIP_TIER, else the
    persisted tier config. Returns a concrete tier (pro/max/api), or None
    when nothing concrete is configured.

    Unlike resolve(), this NEVER calls detect_tier() and NEVER falls back to
    DEFAULT_TIER ('auto'; an explicit 'auto' in either source also yields
    None here, for the same reason). detect_tier() shells out to the macOS
    `security` CLI (via _keychain_oauth_subscription_type) — fine for a
    one-off wizard or session-start check, wrong for a hot path like the
    context gate, which re-resolves on every PreToolUse tool call. Callers
    there use this function and supply their own non-detecting fallback
    (e.g. the full 1M window) when it returns None.
    """
    chosen = _from_env() or _from_config(path or _config_path())
    return chosen if chosen in ("pro", "max", "api") else None


def configured_assumed_window(path: Optional[Path] = None) -> Optional[int]:
    """The configured tier's assumed window, or None when no concrete tier is
    configured. One home for the "resolve the tier without detection, then
    read its assumed_window" step that lib/gates/context_gate.py and
    lib/grip_config.py each carried as four lines of their own after
    GRIP#8324 (the duplication AGORA named on that PR). Never calls
    detect_tier(): see resolve_configured().
    """
    tier = resolve_configured(path)
    if not tier:
        return None
    assumed = envelope(tier).get("assumed_window")
    return int(assumed) if assumed else None


def envelope(tier: Optional[str] = None) -> dict:
    """Return the advisory envelope for a tier (resolving 'auto'/None).
    Concrete tiers (pro/max/api) map directly; 'auto' resolves first.

    Local safety clamp (#3509 Speaker C): the exported compact window never
    exceeds the assumed server window. Claude Code already mins the exported
    value with the real window, but the property should hold here too rather
    than relying on downstream behaviour.
    """
    t = tier or resolve()
    if t == "auto":
        t = detect_tier()
    env = dict(TIER_ENVELOPE.get(t, TIER_ENVELOPE["pro"]))
    acw, asw = env.get("auto_compact_window"), env.get("assumed_window")
    if acw and asw and acw > asw:
        env["auto_compact_window"] = asw
    return env


def set_tier(
    tier: str,
    *,
    set_by: str = "cli",
    quiet: bool = False,
    path: Optional[Path] = None,
    apply_launch_defaults: bool = False,
) -> int:
    """Persist tier choice. Public API (mirrors output_tier.set_tier).

    quiet=True suppresses the stdout confirmation — the SessionStart self-heal
    hook uses it so only the hook's own one-line status reaches the bootstrap
    surface (Rule 9: hooks emit status, not noise).
    path overrides the config location (testability + reuse by
    autodetect_and_persist); defaults to the live _config_path().
    apply_launch_defaults=True also applies the tier's launch defaults
    (lib/pro_launch_defaults.py): on pro, the claude-sonnet-5-5 pin and
    medium effort, so a Pro install launches on what README-FIRST promises.
    The CLI and the wizard pass it; the SessionStart self-heal does not, so
    an autodetected upgrade never writes the live settings file. A failure
    there is reported on stderr and never fails the tier set."""
    internal = _norm(tier)
    if internal is None:
        print("unknown tier %r — choose: %s" % (tier, ", ".join(TIERS)))
        return 2
    cfg = path or _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": internal,
        "set_by": set_by,
        "set_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }
    cfg.write_text(json.dumps(payload, indent=2) + "\n")
    # A tier change re-arms the first-run banner so the NEW tier's news (e.g. a
    # Pro->Max upgrade) shows once. Best-effort — never block a tier set.
    try:
        _banner_marker_path().unlink(missing_ok=True)
    except OSError:
        pass
    resolved = detect_tier() if internal == "auto" else internal
    if not quiet:
        print("GRIP tier set: %s (resolves to: %s)" % (internal, resolved))
    if apply_launch_defaults:
        _apply_launch_defaults(resolved, quiet=quiet)
    return 0


def _apply_launch_defaults(tier: str, *, quiet: bool) -> None:
    """Pin the tier's launch model and effort (lib/pro_launch_defaults.py).

    Best-effort: a tier set must never fail because the pin or the settings
    file could not be written; the reason goes to stderr instead."""
    try:
        lib_dir = str(Path(__file__).resolve().parent)
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        from pro_launch_defaults import apply as _apply

        _apply(tier, quiet=quiet)
    except Exception as exc:  # noqa: BLE001 -- report, never block the tier set
        print("grip tier: launch defaults not applied: %s" % exc, file=sys.stderr)


# ---------------------------------------------------------------------------
# SessionStart self-heal: upgrade a Max-class subscription off the pro floor.
# ---------------------------------------------------------------------------
# Claude Code exports the verified subscription class to hook subprocesses as
# CLAUDE_CODE_SUBSCRIPTION_TYPE (docs/grip-tier-1m-context.md Finding 6 — it is
# CLI-written + server-validated display/telemetry, NOT a credential). We read
# it ONLY to UPGRADE the pre-launch safe floor, never to assert a downgrade.
_SUBSCRIPTION_ENV = "CLAUDE_CODE_SUBSCRIPTION_TYPE"

# Subscription classes that INCLUDE Opus 1M at no extra credit cost
# (docs/grip-tier-1m-context.md conclusion table: Max / Team / Enterprise).
# Substring-matched case-insensitively so plan variants ("max_20x", "max_5x",
# "team", …) all resolve. Pro / unknown / empty never match → the pro
# safe-floor is preserved (worst-case-correct).
_MAX_CLASS_SUBSCRIPTIONS = ("max", "team", "enterprise")


def _subscription_type() -> str:
    """CC's reported subscription class, lowercased ('' if unset).

    Value read of a NON-secret display/telemetry env var (Finding 6 confirms
    it is CLI-written + server-validated, never an entitlement secret), so it
    is env-var-protection-safe — no credential crosses a process boundary."""
    return os.environ.get(_SUBSCRIPTION_ENV, "").strip().lower()


def autodetect_and_persist(path: Optional[Path] = None) -> Optional[str]:
    """SessionStart self-heal: upgrade a Max-class subscription off 'pro'.

    detect_tier() floors every keyless subscription to 'pro' because it CANNOT
    tell Max from Pro before a session exists (R0: never assert an entitlement
    pre-launch). Once a session is live, Claude Code exports the verified
    subscription class; this reads it and — IF it is Max-class AND the operator
    has made no explicit choice — persists tier=max so the NEXT `grip` launch
    stops capping CLAUDE_CODE_AUTO_COMPACT_WINDOW at 180K and lets the full
    Opus 1M window stand.

    One-session-delayed by construction (the auto-compact window is read at
    launch), so it never weakens the pre-launch floor — it only corrects it
    forward. Pure UPGRADE: never downgrades, never clobbers an explicit
    `grip tier` choice, idempotent once on max.

    Returns the tier written ('max'), or None for a no-op.
    """
    if not any(k in _subscription_type() for k in _MAX_CLASS_SUBSCRIPTIONS):
        return None  # Pro / unknown / not reported → keep the safe floor

    # An explicit env override wins at resolve() anyway — don't fight it.
    if _norm(os.environ.get(_ENV, "")) in ("pro", "max", "api"):
        return None

    cfg = path or _config_path()
    try:
        existing = json.loads(cfg.read_text())
    except (OSError, ValueError):
        existing = {}
    tier = _norm(existing.get("tier", ""))
    set_by = str(existing.get("set_by", ""))

    # Respect an explicit operator choice; idempotent once already on max.
    if set_by == "cli" and tier in ("pro", "max", "api"):
        return None
    if tier == "max":
        return None

    set_tier("max", set_by="autodetect", quiet=True, path=cfg)
    return "max"


def _status_line() -> str:
    """Human-readable resolved tier + its one-line blurb (for wizard /
    session-start surfacing)."""
    t = resolve()
    env = envelope(t)
    return "GRIP tier: %s — %s" % (t, env["blurb"])


def banner_text(tier: Optional[str] = None) -> str:
    """One-line first-run tier banner (the show-once delight surface for #2147).

    Pure presentation of data that already exists in TIER_ENVELOPE — the 200K /
    180K figures are DERIVED from the resolved envelope's windows (single source;
    a mutation of assumed_window/auto_compact_window flows through here, never
    drifts). Distinct from `_status_line` (wizard/status) on purpose: this is the
    Pro-parity 'your protection is real + invisible' message #2147 surfaces."""
    t = tier or resolve()
    if t == "auto":
        t = detect_tier()
    env = envelope(t)
    asw, acw = env.get("assumed_window"), env.get("auto_compact_window")
    wk = "%dK" % (asw // 1000) if asw else "?"
    if t == "pro":
        ck = "%dK" % (acw // 1000) if acw else "?"
        return ("GRIP · Pro plan (%s) — compacts at %s + memory so sessions "
                "survive. Need 1M? add credits at claude.ai/settings/usage."
                % (wk, ck))
    if t == "max":
        return "GRIP · Max/Team/Enterprise — Opus 1M included (Sonnet 1M needs usage credits)."
    if t == "api":
        return "GRIP · API / pay-as-you-go — full 1M on all models, no gating."
    return "GRIP · %s — %s" % (t, env.get("blurb", ""))


def _banner_marker_path() -> Path:
    """Show-once marker for the first-run banner. Deliberately a state/**/*.json
    path so the EXISTING .gitignore rule covers it — a clone never ships it
    pre-seen (the suppression bug a bare extensionless marker would cause)."""
    base = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    return base / "state" / "session" / "tier-banner-seen.json"


def _show_banner_if_unseen() -> int:
    """Print the first-run tier banner exactly ONCE, then record a show-once
    marker. Silent if the marker already exists. The marker write is best-effort
    (worst case the banner shows again — never a launch failure). Returns 0."""
    marker = _banner_marker_path()
    if marker.exists():
        return 0
    print(banner_text())
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"shown_at": datetime.now(timezone.utc).isoformat()}) + "\n"
        )
    except OSError:
        pass
    return 0


def _build_parser():
    """Construct the `grip tier` argument parser (extracted from main so main
    stays a thin dispatcher under the KISS line threshold)."""
    import argparse

    parser = argparse.ArgumentParser(prog="grip tier")
    parser.add_argument(
        "tier", nargs="?",
        help="tier to set: " + " ".join(TIERS) + " (omit / 'status' to query)",
    )
    parser.add_argument(
        "--resolve", action="store_true",
        help="print the resolved concrete tier (pro/max/api) and exit",
    )
    parser.add_argument(
        "--envelope", action="store_true",
        help="print the resolved tier's advisory envelope as JSON and exit",
    )
    parser.add_argument(
        "--compact-window", action="store_true",
        help="print ONLY the resolved auto_compact_window integer (empty line "
             "if the tier has none) and exit — for shell consumption with no "
             "pipe/JSON parse (bin/grip launch path)",
    )
    parser.add_argument(
        "--gate-max", action="store_true",
        help="print ONLY the resolved tier's assumed_window integer (empty "
             "line if the tier has none) and exit — for shell consumption "
             "with no pipe/JSON parse (bin/grip launch path; feeds "
             "CONTEXT_GATE_MAX so the context gate's CRITICAL threshold "
             "scales off the tier's real window instead of assuming 1M)",
    )
    parser.add_argument(
        "--banner", action="store_true",
        help="print the one-line first-run tier banner and exit (the show-once "
             "delight surface bin/grip emits on first launch)",
    )
    parser.add_argument(
        "--banner-if-unseen", action="store_true",
        help="print the banner ONLY if the show-once marker is absent, then write "
             "it (the bin/grip first-launch call — silent on every later launch)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.banner:
        print(banner_text())
        return 0
    if args.banner_if_unseen:
        return _show_banner_if_unseen()
    if args.resolve:
        print(resolve())
        return 0
    if args.envelope:
        print(json.dumps(envelope(), indent=2))
        return 0
    if args.compact_window:
        # Single-purpose machine output: the integer, or an empty line.
        # No JSON, no second process — bin/grip captures this directly.
        acw = envelope().get("auto_compact_window")
        print(acw if acw else "")
        return 0
    if args.gate_max:
        # Same contract as --compact-window: the integer, or an empty line.
        asw = envelope().get("assumed_window")
        print(asw if asw else "")
        return 0
    if args.tier in (None, "status"):
        print(_status_line())
        return 0
    if _norm(args.tier) is None:
        print("unknown tier %r — choose: %s" % (args.tier, ", ".join(TIERS)))
        return 2
    return set_tier(args.tier, apply_launch_defaults=True)


if __name__ == "__main__":
    raise SystemExit(main())
