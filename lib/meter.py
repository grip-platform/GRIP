#!/usr/bin/env python3
"""
GRIP /meter — deterministic cross-harness usage fuel-gauge (wraps ccusage).

Renders the operator's coding-CLI usage (claude / codex / opencode / amp / …)
as a fuel-gauge delight plus six views. Pure stdlib + subprocess; no LLM, so
the output is replayable and honest (never hallucinated).

Views:
  1. FUEL GAUGE      — total cost as a fuel tank (needs GRIP_METER_BUDGET)
  2. TREND           — cost sparkline over the window, with the peak day
  3. HARNESS SPLIT   — claude vs codex vs opencode vs … (bar chart)
  4. MODEL MIX       — top models by cost
  5. CACHE           — tokens read vs written (the "cache carried it" number)
  6. SESSIONS        — per-day session sparkline

Usage:
  python3 -m lib.meter [--days 30] [--budget USD]

Env:
  GRIP_METER_BUDGET   monthly USD budget (drives the gauge %; unset = no %)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

ORANGE = "\033[38;5;208m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _run(*args: str) -> dict:
    p = subprocess.run(
        ["ccusage", *args, "--json", "--no-color"],
        capture_output=True, text=True, timeout=90,
    )
    try:
        return json.loads(p.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def _spark(values: list[float]) -> str:
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return "".join("▄" for _ in values)
    return "".join(
        blocks[int((v - lo) / (hi - lo) * (len(blocks) - 1))] for v in values
    )


def _bar(label: str, value: float, maxv: float, width: int = 22) -> str:
    n = int(value / maxv * width) if maxv > 0 else 0
    return f"  {label:<14} {'█' * n}{'░' * (width - n)}  ${value:,.0f}"


def _daily_rows(data: dict) -> list[dict]:
    return data.get("daily") or []


def _render_fuel(total_cost: float, budget: float) -> None:
    if budget:
        pct = total_cost / budget * 100
        color = GREEN if pct < 60 else (YELLOW if pct < 85 else RED)
        tank = min(20, int(pct / 5))
        gauge = "█" * tank + "░" * (20 - tank)
        print(f"  {BOLD}FUEL{RESET}  {color}{gauge}{RESET}  {color}{pct:5.1f}%{RESET}")
        print(f"        spent ${total_cost:,.2f} of ${budget:,.0f} budget")
    else:
        print(f"  {BOLD}FUEL{RESET}  ${total_cost:,.2f} total "
              f"(set GRIP_METER_BUDGET for a gauge)")


def _render_trend(daily_costs: list[float]) -> None:
    print(f"  {BOLD}TREND{RESET} {ORANGE}{_spark(daily_costs)}{RESET}  "
          f"peak ${max(daily_costs or [0]):,.0f}/day")


def _render_harness(agent_cost: dict[str, float]) -> None:
    if not agent_cost:
        return
    mx = max(agent_cost.values()) or 1
    print(f"  {BOLD}HARNESS{RESET}")
    for name in sorted(agent_cost, key=agent_cost.get, reverse=True):
        print(_bar(name, agent_cost[name], mx))


def _render_models(model_cost: dict[str, float]) -> None:
    if not model_cost:
        return
    print(f"  {BOLD}MODELS{RESET}")
    for name, cost in sorted(model_cost.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        print(f"    {name:<24} ${cost:,.2f}")


def _render_cache(cache_read: int, cache_create: int) -> None:
    print(f"  {BOLD}CACHE{RESET} {_fmt_tokens(cache_read)} read · "
          f"{_fmt_tokens(cache_create)} written — the cache carried "
          f"{GREEN}{_fmt_tokens(cache_read)}{RESET} tokens")


def _render_sessions(session_by_day: dict[str, int]) -> None:
    if not session_by_day:
        return
    print(f"  {BOLD}SESSIONS{RESET} {sum(session_by_day.values())} total · per-day "
          f"{_spark([float(session_by_day.get(d, 0)) for d in sorted(session_by_day)])}")


def _sum_tokens(rows: list[dict]) -> tuple:
    total_cost = cache_read = cache_create = input_tokens = output_tokens = 0
    for r in rows:
        total_cost += r.get("totalCost") or 0
        cache_read += r.get("cacheReadTokens") or 0
        cache_create += r.get("cacheCreationTokens") or 0
        input_tokens += r.get("inputTokens") or 0
        output_tokens += r.get("outputTokens") or 0
    return total_cost, cache_read, cache_create, input_tokens, output_tokens


def _sum_agent_cost(rows: list[dict]) -> dict[str, float]:
    agent_cost: dict[str, float] = {}
    for r in rows:
        for a in (r.get("agents") or []):
            name = a.get("agent") or "unknown"
            agent_cost[name] = agent_cost.get(name, 0) + (a.get("totalCost") or 0)
    return agent_cost


def _sum_model_cost(rows: list[dict]) -> dict[str, float]:
    model_cost: dict[str, float] = {}
    for r in rows:
        for m in r.get("modelBreakdowns") or []:
            name = m.get("modelName") or "unknown"
            model_cost[name] = model_cost.get(name, 0) + (m.get("cost") or 0)
    return model_cost


def _sum_sessions(since: str) -> dict[str, int]:
    session_by_day: dict[str, int] = {}
    for s in _run("session", "--since", since).get("session") or []:
        d = (s.get("date") or s.get("startedAt") or "")[:10]
        if d:
            session_by_day[d] = session_by_day.get(d, 0) + 1
    return session_by_day


def _aggregate(rows: list[dict], since: str) -> dict:
    total_cost, cache_read, cache_create, input_tokens, output_tokens = _sum_tokens(rows)
    return {
        "total_cost": total_cost, "cache_read": cache_read,
        "cache_create": cache_create, "input_tokens": input_tokens,
        "output_tokens": output_tokens, "agent_cost": _sum_agent_cost(rows),
        "model_cost": _sum_model_cost(rows), "session_by_day": _sum_sessions(since),
    }


def _positive_days(text: str) -> int:
    """argparse type for --days: refuse a window with no days in it.

    `--days 0` or `--days -3` puts `--since` in the future, ccusage returns no
    rows, and the report then blamed a working ccusage install (GRIP#6798).
    """
    try:
        days = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--days must be a whole number, got {text!r}") from None
    if days < 1:
        raise argparse.ArgumentTypeError(f"--days must be 1 or more, got {days}")
    return days


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP /meter usage fuel-gauge")
    parser.add_argument("--days", type=_positive_days, default=30)
    parser.add_argument("--budget", type=float,
                        default=float(os.environ.get("GRIP_METER_BUDGET", 0) or 0))
    args = parser.parse_args()

    # `ccusage --since` is inclusive, so today plus days - 1 earlier days is
    # exactly --days calendar days; `days=args.days` summed one day too many.
    since = (datetime.now() - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    rows = _daily_rows(_run("daily", "--by-agent", "--since", since))
    if not rows:
        print(f"{DIM}No ccusage data (is ccusage installed and used by any harness?).{RESET}")
        sys.exit(0)

    data = _aggregate(rows, since)
    daily_costs = [r.get("totalCost") or 0 for r in rows[-args.days:]]

    print(f"{BOLD}╭── GRIP /meter — {args.days}-day usage ─────────────────────────╮{RESET}")
    _render_fuel(data["total_cost"], args.budget)
    _render_trend(daily_costs)
    _render_harness(data["agent_cost"])
    _render_models(data["model_cost"])
    _render_cache(data["cache_read"], data["cache_create"])
    _render_sessions(data["session_by_day"])
    print(f"  {DIM}input {_fmt_tokens(data['input_tokens'])} · output {_fmt_tokens(data['output_tokens'])} "
          f"· {len(data['agent_cost'])} harness(es){RESET}")
    print(f"{BOLD}╰────────────────────────────────────────────────────────────╯{RESET}")


if __name__ == "__main__":
    main()
