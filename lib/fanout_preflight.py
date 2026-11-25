"""Refuse to launch a fan-out whose cost nobody has seen.

On 2026-09-17 a 38-agent workflow consumed 9.48M subagent tokens in 2h13m and
cost $303.96 in usage credits. Nothing announced the cost before, during, or
after. The operator found out from his account page.

The defect was never "the fleet was expensive" -- breadth is sometimes worth
paying for. It was that the decision to pay was never presented to anyone. This
module makes the estimate a precondition of launching.

WHY A SEPARATE COMMAND RATHER THAN A CHECK INSIDE THE WORKFLOW

A Workflow script is JavaScript evaluated by the harness runner with no
filesystem access, so it cannot read whether usage credits are enabled. It can
enforce a token ceiling (budget.remaining()) and it should -- but it cannot know
whether exceeding that ceiling fails or bills. That fact lives on disk, so the
check that needs it lives here, and runs before the launch.

THE TWO CLOCKS, because this is what made the spend invisible

A Max plan's WEEKLY allowance and its 5-HOUR SESSION allowance are separate.
A burst large enough to exhaust the session allowance bills to credits while the
weekly bar still reads 23%. Anyone watching the weekly number sees nothing wrong.

LIVE USAGE, added because the estimate above is a guess and the account's own
answers are not

Everything above prices a fan-out from an AGENT COUNT -- a guess at what it will
cost. It never looks at what the account has already spent. Two incidents this
guess could not have caught either way: nine Opus lanes burned a five-day-out
weekly allowance in 14 hours (2026-09-22), and seventeen opus-pinned lanes died
204 seconds after launch, having spent 3.46M tokens, when the 5-hour SESSION
allowance -- separate from the weekly one, and shared by every session on the
account regardless of model -- ran out from under them
(`feedback_parallel_fable_agents_exhaust_session_limit.md`). The operator's
standing rule since (`feedback_subscription_first_billing_default.md`):
autonomous work may use at most half of each subscription window.
:func:`live_usage` reads HAL's own account ledger -- the same headers
api.anthropic.com sends back on every answer, already parsed and stored by
``lib.hal.account_ledger`` -- to check that cap against what each account has
ACTUALLY used, not what this fan-out is estimated to add.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:  # lib/ on sys.path: bin/grip-fanout-preflight, hooks/fanout-preflight-gate.py
    import grip_tier
except ImportError:  # imported as lib.fanout_preflight
    from lib import grip_tier

# Below this, a fan-out is small enough that announcing it is noise. Chosen so
# the 2026-09-17 run (38 agents, ~250k/agent) clears it by two orders of
# magnitude, while a routine 3-agent review does not trip it.
DEFAULT_THRESHOLD_TOKENS = 1_000_000

# What one discovery-or-verify agent costs, order of magnitude. Measured from
# the 2026-09-17 run: 9,476,597 subagent tokens / 38 agents = 249,384. Rounded
# down deliberately -- an estimate that flatters the run is worse than useless.
DEFAULT_TOKENS_PER_AGENT = 250_000

# The line on a Pro plan is ONE agent's worth, so any fan-out of two or more
# agents is checked. A Pro plan has one Claude account and a small five-hour
# allowance; several subagents spend it in minutes, and a warning cannot give
# spent allowance back (operator decision, HITL 2026-10-15). Max and API keep
# the original line. Tied to the per-agent estimate on purpose: if that number
# moves, "two or more agents" must still be what crosses the pro line.
PRO_THRESHOLD_TOKENS = DEFAULT_TOKENS_PER_AGENT

_TIER_THRESHOLDS = {
    "pro": PRO_THRESHOLD_TOKENS,
    "max": DEFAULT_THRESHOLD_TOKENS,
    "api": DEFAULT_THRESHOLD_TOKENS,
}

# V>>'s standing rule (feedback_subscription_first_billing_default.md,
# 2026-10-01): autonomous work may use at most half of each subscription
# window. An account has headroom when its 5-hour utilization, and its weekly
# one whenever that is known, are both at or under this fraction.
LIVE_HEADROOM_FRACTION = 0.5

# The largest reading HAL's ledger can hold: it accepts a fraction from 0 to 100
# (_FRACTION_RANGE in ~/.hal/lib/hal/account_ledger.py). Twin of
# _FRACTION_CEILING in lib/subscription_room.py and _PERCENT_CEILING in
# lib/statusline_limits.py; a test holds all three readers to the same answer.
_FRACTION_CEILING = 100.0


class FanoutRefused(Exception):
    """Raised when a fan-out must not launch without an explicit decision."""


def estimate_tokens(agents: int, tokens_per_agent: int = DEFAULT_TOKENS_PER_AGENT) -> int:
    """Total subagent tokens a fan-out of this size is expected to consume."""
    if agents < 0:
        raise ValueError("agents cannot be negative")
    return agents * tokens_per_agent


def threshold_for_tier(tier: Optional[str]) -> int:
    """The token line a fan-out is checked against on this plan tier.

    pro -> PRO_THRESHOLD_TOKENS (one agent's worth); max and api ->
    DEFAULT_THRESHOLD_TOKENS. Anything else -- None (no tier declared), 'auto',
    an unknown name -- keeps DEFAULT_THRESHOLD_TOKENS, the line that existed
    before tiers, so an undeclared machine behaves exactly as it did.
    """
    return _TIER_THRESHOLDS.get(tier or "", DEFAULT_THRESHOLD_TOKENS)


def agents_for(lanes: int, rounds: int, verify_per_finding: int = 0) -> int:
    """Agent count for the converge-fleet shape: lanes x rounds, plus verifiers.

    The 2026-09-17 run was 6 lanes x 2 rounds + 26 verifiers = 38, which is what
    this returns for (6, 2, 26).
    """
    if min(lanes, rounds, verify_per_finding) < 0:
        raise ValueError("counts cannot be negative")
    return lanes * rounds + verify_per_finding


def extra_usage_enabled(claude_dir: Optional[Path] = None) -> Optional[bool]:
    """Whether overflow BILLS rather than fails. None when it cannot be read.

    None is deliberately not False. "I could not tell" and "it is off" lead to
    opposite decisions, and conflating them is how a silent spend happens.
    """
    base = claude_dir or Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    for candidate in (base.parent / ".claude.json", base / ".claude.json"):
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        acct = data.get("oauthAccount")
        if isinstance(acct, dict) and "hasExtraUsageEnabled" in acct:
            value = acct["hasExtraUsageEnabled"]
            return value if isinstance(value, bool) else None
    return None


def _default_ledger_path() -> Path:
    """``$HAL_HOME/state/hal/account-ledger.json`` (default ``~/.hal``).

    Mirrors ``lib.hal.account_ledger.default_path()`` exactly, without
    importing it: GRIP reads HAL's documented ledger FILE, never HAL's code --
    the least-coupled source, per the build brief this came from.
    """
    home = os.environ.get("HAL_HOME") or os.path.expanduser("~/.hal")
    return Path(home) / "state" / "hal" / "account-ledger.json"


def _ledger_accounts(ledger_path: Path) -> Optional[Dict[str, dict]]:
    """Every account HAL's ledger has observed, by label.

    None when the file cannot be trusted -- missing, unreadable, not JSON, or
    not shaped like a ledger -- so a caller can tell "nothing to report" apart
    from "every account reports zero use".
    """
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    accounts = raw.get("accounts")
    if not isinstance(accounts, dict):
        return None
    return {label: entry for label, entry in accounts.items()
            if isinstance(label, str) and isinstance(entry, dict)}


def _ledger_fraction(value: object) -> Optional[float]:
    """``value`` as a fraction of its window, or None. Over 1 when the window is
    over its limit (HAL recorded 1.01 on 2026-10-10): such a reading is known and
    has no headroom, so it refuses rather than falling back to the estimate. A
    value HAL's ledger cannot hold (above ``_FRACTION_CEILING``, negative, not a
    number) is unknown -- never clamped, and never read as 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= _FRACTION_CEILING else None


def _epoch(value: object) -> Optional[int]:
    """``value`` as a whole-second epoch timestamp, or None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _window_reading(entry: dict, window_name: str) -> Tuple[Optional[float], Optional[int]]:
    """(utilization as a fraction, reset as epoch seconds) for one window of one
    account's ledger entry. Either may be None."""
    quota = entry.get("quota")
    windows = quota.get("windows") if isinstance(quota, dict) else None
    window = windows.get(window_name) if isinstance(windows, dict) else None
    if not isinstance(window, dict):
        return None, None
    return _ledger_fraction(window.get("utilization")), _epoch(window.get("reset"))


def _when_utc(epoch: Optional[int]) -> str:
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(fraction: Optional[float]) -> str:
    """Whole percent, rounded UP so a reading just over the 50% line never
    displays on the allowed side of it: 0.501 shows as 51%, not 50%."""
    return "unknown" if fraction is None else f"{math.ceil(round(fraction * 100, 6))}%"


def _current(fraction: Optional[float], reset: Optional[int], now: float) -> Optional[float]:
    """A reading whose recorded reset has passed describes a window that is
    already over: unknown until fresh traffic refreshes the ledger, never
    still spent. Otherwise the first fan-out after an idle reset is refused."""
    if reset is not None and reset <= now:
        return None
    return fraction


def live_usage(ledger_path: Optional[Path] = None,
               now: Optional[float] = None) -> Tuple[List[str], Optional[bool]]:
    """One plain line per account HAL's ledger has observed, and whether any
    of them still has at most ``LIVE_HEADROOM_FRACTION`` of its 5-hour window
    used -- and of its weekly window, whenever that is known.

    Returns ``(lines, any_has_headroom)``. Each line names an account's label
    and its 5-hour and weekly (7-day) use -- never a token; HAL's own ledger
    keeps only a hash digest of one, never the value. A reading whose reset
    time has already passed counts as unknown. ``any_has_headroom`` is
    ``True`` or ``False`` once at least one account's 5-hour utilization is a
    known, current number; it is ``None`` -- deliberately never ``False`` by
    omission -- when the ledger is missing, unreadable, or records no account
    with a usable current 5-hour reading, so the caller falls back to the
    estimate-only rule rather than refusing on nothing.
    """
    accounts = _ledger_accounts(ledger_path or _default_ledger_path())
    if not accounts:
        return [], None

    now = time.time() if now is None else now
    lines: List[str] = []
    any_known = False
    any_headroom = False
    for label in sorted(accounts):
        five_hour, five_hour_reset = _window_reading(accounts[label], "five_hour")
        seven_day, seven_day_reset = _window_reading(accounts[label], "seven_day")
        five_hour = _current(five_hour, five_hour_reset, now)
        seven_day = _current(seven_day, seven_day_reset, now)
        lines.append(
            f"{label}  5h {_pct(five_hour)} (resets {_when_utc(five_hour_reset)})"
            f"  7d {_pct(seven_day)} (resets {_when_utc(seven_day_reset)})"
        )
        if five_hour is not None:
            any_known = True
            if _has_headroom(five_hour, seven_day):
                any_headroom = True

    return lines, (any_headroom if any_known else None)


def _has_headroom(five_hour: float, seven_day: Optional[float]) -> bool:
    """At most half of EACH window used: the 5-hour reading, and the weekly one
    when it is known. An account at 100% of its week has no headroom however
    idle its 5-hour window looks -- measured 2026-10-08, when one pooled account
    read 5h 0% and 7d 100% and was serving only on paid extra usage."""
    if five_hour > LIVE_HEADROOM_FRACTION:
        return False
    return seven_day is None or seven_day <= LIVE_HEADROOM_FRACTION


def preflight(
    agents: int,
    *,
    tokens_per_agent: int = DEFAULT_TOKENS_PER_AGENT,
    threshold: Optional[int] = None,
    accepted: bool = False,
    claude_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> dict:
    """Decide whether this fan-out may launch, and say why either way.

    Two independent reasons can refuse, and either is enough: the ESTIMATE
    rule (the fan-out's own size is over threshold, and overflow would bill or
    that cannot be determined) and the LIVE-USAGE rule (HAL's ledger shows
    every account it has observed already past half its 5-hour window, so
    there is no account with headroom left for more autonomous work). The
    live-usage rule is checked first and is independent of this fan-out's
    size: once every account is past half, autonomous work is capped
    regardless of how small the next request is
    (``feedback_subscription_first_billing_default.md``). Either refusal is
    overridden by ``accepted``.

    With no ``threshold``, the line comes from the plan tier the operator
    declared (``grip_tier.resolve_configured``: GRIP_TIER, else the tier
    config), through :func:`threshold_for_tier`. No declared tier keeps the
    original line, and tier auto-detection -- a keychain read -- never runs here.

    A live ledger that is missing, unreadable, or has no usable 5-hour
    reading never blocks: the live-usage rule then takes no position, and only
    the estimate rule decides -- unchanged from before this rule existed.
    """
    tier = grip_tier.resolve_configured()
    if threshold is None:
        threshold = threshold_for_tier(tier)
    estimate = estimate_tokens(agents, tokens_per_agent)
    billing = extra_usage_enabled(claude_dir)
    over = estimate > threshold
    usage_lines, headroom = live_usage(ledger_path)
    live_blocks = headroom is False

    report = {
        "agents": agents,
        "tier": tier,
        "estimated_tokens": estimate,
        "threshold": threshold,
        "over_threshold": over,
        "extra_usage_enabled": billing,
        "accepted": accepted,
        "live_usage_lines": usage_lines,
        "live_headroom_available": headroom,
        "may_launch": True,
        "reason": "",
    }

    if live_blocks and not accepted:
        report["may_launch"] = False
        report["reason"] = (
            "REFUSED: HAL's live ledger shows every account it has observed "
            f"already past half its 5-hour or weekly window (none with both at "
            f"or under {LIVE_HEADROOM_FRACTION:.0%} used), and autonomous work is "
            "capped at half of each window "
            "(feedback_subscription_first_billing_default.md). Re-run with "
            "accepted=True (CLI: --accept-spend) if the spend is worth it, or "
            "wait for a window to reset."
        )
        return report

    live_note = (
        " Live 5-hour or weekly usage is also past half on every observed "
        "account, but explicitly accepted."
        if live_blocks
        else ""
    )

    if not over:
        report["reason"] = (
            f"{agents} agents, ~{estimate:,} tokens estimated -- under the "
            f"{threshold:,} announce threshold."
        ) + live_note
        return report

    if billing is False:
        report["reason"] = (
            f"{agents} agents, ~{estimate:,} tokens estimated. Over the "
            f"{threshold:,} threshold, but usage credits are OFF, so an overflow "
            f"fails rather than bills. Proceeding."
        ) + live_note
        return report

    if accepted:
        report["reason"] = (
            f"{agents} agents, ~{estimate:,} tokens estimated, and overflow "
            f"{'bills to usage credits' if billing else 'MAY bill -- could not read the credits flag'}. "
            f"Explicitly accepted by the caller."
        ) + live_note
        return report

    report["may_launch"] = False
    detail = (
        "usage credits are ENABLED, so work beyond the 5-hour session allowance "
        "BILLS rather than failing"
        if billing
        else "the usage-credits flag could NOT be read, so whether overflow bills "
        "is unknown -- treated as billing"
    )
    report["reason"] = (
        f"REFUSED: {agents} agents, ~{estimate:,} tokens estimated, over the "
        f"{threshold:,} threshold, and {detail}. On 2026-09-17 a run this shape "
        f"cost $303.96 unannounced. Re-run with accepted=True (CLI: --accept-spend) "
        f"if the cost is worth it, or turn usage credits off so an overflow fails "
        f"instead of billing."
    )
    return report
