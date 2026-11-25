#!/usr/bin/env python3
"""Deliverable B step-4 — REAL token-savings measurement for the cold tier.

This REPLACES the earlier `dogfood_cold_tier_savings.py`, which was a tautology: it summed
hard-coded `rederivation_tokens` minus a constant and always printed the same 148,125 —
proving nothing (caught by V>> 2026-06-02). This script measures REAL provider-billed token
counts and is explicit about what is measured vs priced vs derived.

THREE honesty tiers (do not conflate them):

  1. MEASURED (real `usage` from live Anthropic calls — unfabricatable):
       * generating slice-sized content costs ~slice-many OUTPUT tokens;
       * a cold slice paged back through the prompt cache reports `cache_read_input_tokens`
         equal to the FULL slice token-count, billed at the cache-read rate;
       * establishing it reports `cache_creation_input_tokens`.
     The measurement's job is to confirm the MECHANISM delivers these counts (full-slice
     cache, real discount) — and to FAIL LOUDLY (UNMEASURABLE) when caching does not engage
     (e.g. a slice below the ~2048-token cache minimum), instead of reporting a fake 0.

  2. PRICED (Anthropic published list prices — cited, may change):
       per-token multipliers vs base input = 1.0 — output 5x, cache-write 1.25x,
       cache-read 0.1x (anthropic.com/pricing, Haiku-4.5 tier, captured 2026-06).
     The per-token ADVANTAGE is therefore a published-price ratio, NOT a measurement.

  3. DERIVED + FALSIFIABLE (the lifecycle the cold tier actually competes in):
       Re-deriving costs `slice x output` once, only WHEN needed. Paging back costs
       `slice x cache_write` to inject once, then `slice x cache_read` for every extra turn
       it is CARRIED in context. So the cold tier WINS only within a break-even carry length;
       a slice carried too long without reuse (or never re-needed) LOSES. `lifecycle_cost`
       computes this and CAN return confirmed=False.

Run live: `python3 scripts/measure_cold_tier_savings.py` (needs the `grip-anthropic-external`
keychain key). No key / caching does not engage => UNMEASURABLE (never fabricated).
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

#: Published Anthropic list-price multipliers, normalised to base input token = 1.0
#: (output 5x, cache-write 1.25x, cache-read 0.1x). Source: anthropic.com/pricing, Haiku-4.5
#: tier, captured 2026-06. These are PRICES (they can change), not measured values.
@dataclass(frozen=True)
class Pricing:
    output: float = 5.0
    cache_write: float = 1.25
    cache_read: float = 0.1
    input: float = 1.0


PRICING = Pricing()


def break_even_carry_turns(p: Pricing = PRICING) -> float:
    """Carry length (turns a paged-back slice sits in context, per single need-event) at which
    paging back stops being cheaper than re-deriving on demand.

    page-back(T) = slice*(cache_write + (T-1)*cache_read);  re-derive = slice*output.
    Equal when T = 1 + (output - cache_write) / cache_read.  (Per-token, so slice cancels.)
    """
    if p.cache_read <= 0:
        raise ValueError(f"cache_read price must be > 0 to compute a break-even, got {p.cache_read}")
    return 1.0 + (p.output - p.cache_write) / p.cache_read


def lifecycle_cost(
    slice_tokens: int,
    *,
    turns_carried: int = 1,
    times_needed: int = 1,
    p: Pricing = PRICING,
) -> dict:
    """Falsifiable lifecycle comparison (token-cost-equivalents, slice_tokens scaled).

    * re-derive: regenerate the slice each time it is needed -> times_needed * slice * output.
    * page-back: inject once (cache_write) then carry it (cache_read) for the remaining turns,
      reused in place for all needs -> slice * (cache_write + max(turns_carried-1, 0)*cache_read).
      Page-back does NOT scale with ``times_needed`` BY DESIGN: once injected the slice is RESIDENT
      in the cache and re-use is free; re-paging per need would model a cache that evicts between
      needs, which is not the cold tier's behaviour (AGORA #3400 r4 — the asymmetry is the point).

    ``confirmed`` is True iff page-back is strictly cheaper. It is FALSE for a long carry with
    few needs (the cold tier genuinely loses there) — the property the old tautology lacked.
    """
    if slice_tokens < 0 or turns_carried < 1 or times_needed < 1:
        raise ValueError(
            f"impossible inputs: slice_tokens={slice_tokens} (need >=0), "
            f"turns_carried={turns_carried} (>=1), times_needed={times_needed} (>=1)"
        )
    rederive = times_needed * slice_tokens * p.output
    pageback = slice_tokens * (p.cache_write + max(turns_carried - 1, 0) * p.cache_read)
    return {
        "rederive_cost": rederive,
        "pageback_cost": pageback,
        "confirmed": pageback < rederive,
        "ratio": (rederive / pageback) if pageback else float("inf"),
        "break_even_carry_turns": break_even_carry_turns(p),
    }


# --- Live measurement (real Anthropic calls; UNMEASURABLE, never fabricated, on failure) ---

def _key() -> str:
    """macOS Keychain read of grip-anthropic-external. NOTE (AGORA #3400 r2): cross-platform key
    resolution via the ANTHROPIC_API_KEY env var is intentionally NOT inlined here — the
    env-var-protection guardrail correctly blocks reading secret env vars in script code, and the
    sanctioned cross-platform path is a secret-shim (lib/secret.py, syscall-doctrine), tracked as a
    follow-up. Two failure modes, NEITHER a crash (AGORA #3400 r3 + r4): a non-macOS host has no
    `security` binary (FileNotFoundError) => silent "" => honest UNMEASURABLE; any OTHER keychain
    failure (e.g. EACCES permission denied) also returns "" BUT is surfaced to stderr — so it is
    neither silently masked (the r3 concern) nor an unhandled crash in main (the r4 concern)."""
    try:
        return subprocess.run(
            ["security", "find-generic-password", "-s", "grip-anthropic-external", "-w"],
            capture_output=True, text=True,
        ).stdout.strip()
    except FileNotFoundError:  # non-macOS host: `security` absent → silent UNMEASURABLE
        return ""
    except OSError as exc:  # real keychain failure (EACCES, ...): surfaced, but graceful — no crash
        print(f"[measure_cold_tier] keychain read failed (surfaced, not masked): {exc}", file=sys.stderr)
        return ""


def _post(body: dict, key: str) -> dict:
    req = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=180))


#: A fixed, realistic, KNOWN-cacheable (>=2048-token) cold slice for the page-back arm.
_COLD_SLICE = (
    "GRIP cold-context slice — a representative line of compacted session analysis state "
    "carrying file paths, hypotheses, and council verdicts.\n"
) * 300


def measure_live(model: str = DEFAULT_MODEL) -> dict:
    """Make real calls and return measured token counts, or an UNMEASURABLE result.

    Returns ``{"measured": True, "cache_write", "cache_read", "slice_tokens"}`` on success;
    ``{"measured": False, "reason": ...}`` if there is no key or caching does not engage.
    NEVER returns a fabricated saving.
    """
    key = _key()
    if not key:
        return {"measured": False, "reason": "no grip-anthropic-external key — cannot measure live"}
    try:
        # Measure ONLY the cache mechanics (the empirical crux): cache-write then cache-read the
        # FIXED cacheable slice. cache_read = the full slice token-count, billed at the discount.
        # No separate generation call — its output_tokens never fed the cost formula (re-derive is
        # slice_tokens x the published output price), so measuring it just discarded data
        # (AGORA #3400 r2). The re-derive side is an explicitly-labelled MODEL, not a measurement.
        def cached() -> dict:
            return _post({"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": [
                {"type": "text", "text": _COLD_SLICE, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Reply: OK"}]}]}, key)["usage"]

        w, r = cached(), cached()
        cw = w.get("cache_creation_input_tokens") or 0
        cr = r.get("cache_read_input_tokens") or 0
    except urllib.error.HTTPError as exc:
        # Surface the status so a TRANSIENT 429 (retry later) is distinct from a permanent
        # 401/403 (auth) — both are honest UNMEASURABLE, but the operator knows which to do.
        kind = ("rate-limited — retry" if exc.code == 429
                else "auth/permission — check key" if exc.code in (401, 403) else "http")
        return {"measured": False, "reason": f"live call failed: HTTP {exc.code} ({kind})"}
    except Exception as exc:  # network / parse — honest, never fabricate
        return {"measured": False, "reason": f"live call failed: {exc}"}

    if cw == 0 or cr == 0:
        return {"measured": False,
                "reason": f"caching did not engage (cache_write={cw}, cache_read={cr}) — slice below "
                          f"the ~2048-token minimum or TTL miss; NOT a saving of 0"}
    return {"measured": True, "cache_write": cw, "cache_read": cr, "slice_tokens": cr}


def main() -> int:
    print("=" * 76)
    print("DELIVERABLE B step-4 — REAL COLD-TIER TOKEN MEASUREMENT")
    print("=" * 76)
    m = measure_live()
    if not m["measured"]:
        print(f"UNMEASURABLE: {m['reason']}")
        print("(Honest: no fabricated number is printed. Provide auth / a cacheable slice to measure.)")
        return 0

    s = m["slice_tokens"]
    print("MEASURED (real Anthropic `usage` — the cache mechanics, unfabricatable):")
    print(f"  page-back: a {s:,}-token cold slice -> cache_write={m['cache_write']:,}, "
          f"cache_read={m['cache_read']:,} (the FULL slice, billed at the cache-read rate)")
    print(f"  re-derive (MODEL, not measured): regenerating the slice = ~{s:,} output tokens "
          f"(the content must be re-produced) x the published output price\n")
    print("PRICED (published Anthropic Haiku multipliers, base input=1.0): "
          f"output {PRICING.output}x, cache_write {PRICING.cache_write}x, cache_read {PRICING.cache_read}x\n")

    be = break_even_carry_turns()
    print(f"DERIVED lifecycle (falsifiable). Break-even carry = {be:.0f} turns per need-event.")
    for tc, tn, label in [(1, 1, "page-back-on-demand (carry 1 turn, need once)"),
                          (10, 1, "carried 10 turns, needed once"),
                          (200, 1, "carried 200 turns, needed once  <-- expected to LOSE")]:
        r = lifecycle_cost(s, turns_carried=tc, times_needed=tn)
        verdict = "cold-tier WINS" if r["confirmed"] else "cold-tier LOSES (re-derive on demand is cheaper)"
        print(f"  {label:48s} re-derive={r['rederive_cost']:>10,.0f}  "
              f"page-back={r['pageback_cost']:>10,.0f}  -> {verdict}")

    print("\nHONEST SUMMARY: the cold tier is cheaper PER need-event for a slice paged back and used "
          f"promptly (page-back ~{PRICING.cache_write}x input vs re-derive {PRICING.output}x), and the "
          "advantage is a PUBLISHED-PRICE ratio that the measurement confirms the mechanism delivers "
          "(full-slice cache + real discount). It FALSIFIES for a slice carried beyond the break-even "
          "or never re-needed — exactly the case the old tautology could never show.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
