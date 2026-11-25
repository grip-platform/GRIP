"""LLM Usage Ledger — provenance-verified, IDR-style signed chain of every
GRIP LLM call's ``{provider, model}``.

Origin: 2026-06-08. Closes the observability gap surfaced when V>> asked why
Claude Code's usage panel showed 98.8% Opus: that panel measures only the CC
harness (Opus-direct), while HAL's multi-provider fan-out (``grip_infer`` ->
groq/gemini/qwen/deepseek/xai/...) was invisible. ``config/providers.yaml``
declared the ladder and ``~/.hal/state/hal/limits.json`` proved use, but no
per-call ledger existed to *count* it. This module is that ledger + a
one-glance dashboard + an IDR-style ``verify``.

DRY by composition — NO new crypto:
* each call is appended via ``lib.audit_chain.sign_and_append`` (HMAC-SHA256
  hash-chain — the SAME primitive ``lib.idr_forest`` composes);
* integrity is checked via ``lib.audit_chain.verify_chain``;
* USD cost via ``lib.hal.cost_tracker.calculate_cost`` (pricing not duplicated).

Tamper-evidence (exogenous-anchor-law): editing, reordering, or deleting any
recorded line flips ``verify`` to BROKEN — a usage number you cannot falsify is
telemetry, not proof. Green can go red.

Wiring: ``lib.hal_llm_adapter`` calls :func:`record_usage` at the two
provider-resolution points (grip_infer transport success + each direct-provider
success), so every provider that actually ran is counted exactly once.
Fail-open: a telemetry error NEVER breaks an inference — the call already
happened, so losing its telemetry must not turn a success into a failure.

CLI::

    python3 -m lib.llm_usage_ledger dashboard   # one-glance per-provider/model
    python3 -m lib.llm_usage_ledger verify      # IDR-style verdict (exit 1 = BROKEN)
    python3 -m lib.llm_usage_ledger record P M [in] [out]   # manual/testing
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

_CHAIN_ENV = "GRIP_LLM_USAGE_CHAIN"


def chain_path() -> Path:
    """Resolve the usage-chain JSONL path. ``GRIP_LLM_USAGE_CHAIN`` overrides
    (tests / relocation); default is GRIP-owned state."""
    override = os.environ.get(_CHAIN_ENV)
    if override:
        return Path(override).expanduser()
    return get_claude_dir() / "state" / "llm-usage" / "usage-chain.jsonl"


# ---------------------------------------------------------------------------
# Emit — append one signed, hash-chained usage record (fail-open, never raises)
# ---------------------------------------------------------------------------


def record_usage(
    provider: Optional[str],
    model: Optional[str],
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    audited: bool = False,
    idr_ref: object = None,
    caller: Optional[str] = None,
    path: Optional[str] = None,
) -> None:
    """Append ONE signed, hash-chained usage record. Best-effort; NEVER raises.

    Called from ``lib.hal_llm_adapter`` at each provider-resolution point so
    every real provider call is counted exactly once. ``path`` names the
    adapter rung that served ("primary" = grip_infer, "legacy" = direct chain).
    A telemetry failure here must not break an inference that already happened —
    every failure path is swallowed.
    """
    try:
        entry = {
            "ts": time.time(),
            "provider": provider or "(unknown)",
            "model": model or "(unknown)",
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cost_usd": _cost(model, input_tokens, output_tokens),
            "audited": bool(audited),
            "idr_ref": _short_idr(idr_ref),
            "caller": caller or "",
            "path": path or "",
        }
        _append_signed(entry)
    except Exception:
        pass


def _cost(model: Optional[str], input_tokens: int, output_tokens: int) -> float:
    """USD via the canonical cost engine (reused, not duplicated). 0.0 on miss."""
    try:
        from lib.hal.cost_tracker import calculate_cost
        return round(
            calculate_cost(model or "", int(input_tokens or 0), int(output_tokens or 0)),
            6,
        )
    except Exception:
        return 0.0


def _short_idr(idr_ref: object) -> str:
    """Short re-verifiable IDR handle (mirrors HALResult._idr_short)."""
    if isinstance(idr_ref, dict):
        return (idr_ref.get("sha256") or "")[:16]
    if isinstance(idr_ref, str):
        return idr_ref[:16]
    return ""


def _append_signed(entry: dict) -> None:
    """Compose the canonical signed-chain primitive (same crypto as IDR)."""
    from lib.audit_chain import sign_and_append
    p = chain_path()
    p.parent.mkdir(parents=True, exist_ok=True)  # so the first record signs, not falls back
    sign_and_append(entry, p)


# ---------------------------------------------------------------------------
# Fold — aggregate the chain into a one-glance report (pure reducer)
# ---------------------------------------------------------------------------


def fold(path: Optional[Path] = None) -> dict:
    """Aggregate the chain into ``{total, by_provider, by_model}``. Pure."""
    p = path or chain_path()
    by_provider: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    total = 0
    for entry in _iter_entries(p):
        total += 1
        _accumulate(by_provider, entry.get("provider") or "(unknown)", entry)
        _accumulate(by_model, entry.get("model") or "(unknown)", entry)
    return {"total": total, "by_provider": by_provider, "by_model": by_model}


def entries(path: Optional[Path] = None) -> Iterator[dict]:
    """Every well-formed record in the chain, oldest first, NOT verified.

    For readers that need single records rather than fold()'s totals, such as
    lib/spend_ledger's ten-day window. Run verify() first when the numbers must
    be trusted rather than summarised.
    """
    return _iter_entries(path or chain_path())


def _iter_entries(p: Path) -> Iterator[dict]:
    """Yield each well-formed JSONL record; blank/garbage lines skipped."""
    if not p.exists():
        return
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _accumulate(bucket: dict, key: str, entry: dict) -> None:
    """Fold one record into ``bucket[key]`` (in-place)."""
    b = bucket.setdefault(
        key,
        {"calls": 0, "input_tokens": 0, "output_tokens": 0,
         "cost_usd": 0.0, "audited": 0, "last_ts": 0.0},
    )
    b["calls"] += 1
    b["input_tokens"] += int(entry.get("input_tokens") or 0)
    b["output_tokens"] += int(entry.get("output_tokens") or 0)
    b["cost_usd"] += float(entry.get("cost_usd") or 0.0)
    if entry.get("audited"):
        b["audited"] += 1
    ts = float(entry.get("ts") or 0.0)
    if ts > b["last_ts"]:
        b["last_ts"] = ts


# ---------------------------------------------------------------------------
# Verify — IDR-style chain integrity (reuses audit_chain.verify_chain)
# ---------------------------------------------------------------------------


def verify(path: Optional[Path] = None) -> dict:
    """IDR-style chain verification. Returns ``audit_chain.verify_chain``'s
    verdict ``{valid, total_entries, signed_entries, unsigned_entries,
    first_break, break_reason, last_sequence}``.

    Editing / reordering / deleting any recorded line sets ``valid=False`` —
    the usage numbers are tamper-evident, not merely asserted.
    """
    from lib.audit_chain import verify_chain
    return verify_chain(path or chain_path())


# ---------------------------------------------------------------------------
# Render — the one-glance dashboard
# ---------------------------------------------------------------------------


def render_dashboard(path: Optional[Path] = None) -> str:
    """One-glance per-provider + per-model usage, headed by the verify verdict."""
    p = path or chain_path()
    report = fold(p)
    verdict = verify(p)
    status = "VERIFIED" if verdict.get("valid") else "BROKEN"
    lines = [
        "LLM USAGE — provenance-verified (IDR-style signed chain)",
        f"Chain: {status} · {report['total']} calls · "
        f"seq {verdict.get('last_sequence', 0)} · {p}",
    ]
    if not verdict.get("valid"):
        lines.append(
            f"  TAMPER: first break seq {verdict.get('first_break')} "
            f"— {verdict.get('break_reason')}"
        )
    lines += ["", _table("By provider", report["by_provider"], report["total"])]
    lines += ["", _table("By model", report["by_model"], report["total"])]
    return "\n".join(lines)


def _table(title: str, bucket: dict, total: int) -> str:
    """Calls-descending table: calls / share% / audited / cost$ / last-seen."""
    rows = sorted(bucket.items(), key=lambda kv: kv[1]["calls"], reverse=True)
    out = [f"{title:<24}{'calls':>7}{'share':>8}{'audited':>9}{'cost$':>11}  last seen"]
    if not rows:
        out.append("  (no calls recorded yet)")
        return "\n".join(out)
    for name, b in rows:
        share = (b["calls"] / total * 100) if total else 0.0
        out.append(
            f"  {name[:21]:<22}{b['calls']:>6}{share:>7.1f}%"
            f"{b['audited']:>9}{b['cost_usd']:>11.4f}  {_fmt_ts(b['last_ts'])}"
        )
    return "\n".join(out)


def _fmt_ts(ts: float) -> str:
    """Epoch -> 'YYYY-MM-DD HH:MM' UTC, or '-' when absent."""
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) + "Z"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_verify() -> int:
    v = verify()
    ok = bool(v.get("valid"))
    print(
        f"LLM usage chain: {'VERIFIED' if ok else 'BROKEN'} · "
        f"{v.get('signed_entries', 0)}/{v.get('total_entries', 0)} signed · "
        f"seq {v.get('last_sequence', 0)}"
    )
    if not ok:
        print(f"  first break seq {v.get('first_break')} — {v.get('break_reason')}")
    return 0 if ok else 1


def _cmd_record(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: ... record <provider> <model> [in_tokens] [out_tokens]",
              file=sys.stderr)
        return 2
    record_usage(
        argv[2], argv[3],
        input_tokens=int(argv[4]) if len(argv) > 4 else 0,
        output_tokens=int(argv[5]) if len(argv) > 5 else 0,
        caller="cli",
    )
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "dashboard"
    if cmd == "dashboard":
        print(render_dashboard())
        return 0
    if cmd == "verify":
        return _cmd_verify()
    if cmd == "record":
        return _cmd_record(argv)
    print("usage: python3 -m lib.llm_usage_ledger [dashboard|verify|record ...]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
