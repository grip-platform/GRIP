#!/usr/bin/env python3
"""Dogfood A/B proof of prompt-cache-keepalive against the REAL Anthropic API.

Two distinct cached prefixes idle past the ~5-min TTL:
  ARM-A (baseline)   — no keepalive; resume after idle must RE-WRITE (cache_creation).
  ARM-B (keepalive)  — real PromptCacheKeepalive touches it sub-TTL; resume must be a
                       warm READ (cache_read, cache_creation == 0).

Distinct prefixes => distinct server-side cache entries, so B's touches cannot warm A
(the control stays a true control). Uses the installed prompt-cache-keepalive package +
GRIP's grip_cache_keepalive.build_cache_touch (DI: real urllib post_body + a cache_control
marker injected), so this exercises the published library AND the GRIP shim — real dogfood.

Hypothesis H-CACHE-DOGFOOD:
  keepalive touches (interval < TTL) over a >TTL idle keep the prefix warm — resume is a
  cache_read; the same idle without keepalive forces a cache_creation re-write.
Falsified if: B-resume cache_creation > 0 (failed to keep warm) OR A-resume cache_creation
  == 0 (no penalty exists -> solves a non-problem at this TTL).

Run: python3 scripts/dogfood-prompt-cache-keepalive.py [idle_seconds] [--quick]
Records: data/prompt-cache-dogfood.jsonl  (one JSON line per run)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

CLAUDE_DIR = Path(os.path.expanduser("~/.claude"))
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

from prompt_cache_keepalive import KeepaliveConfig, PromptCacheKeepalive  # noqa: E402
from prompt_cache_keepalive.cost_model import CacheEconomics, net_savings, breakeven_resume_probability  # noqa: E402
import grip_cache_keepalive as shim  # noqa: E402

API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-4-8"
RECORD = CLAUDE_DIR / "data" / "prompt-cache-dogfood.jsonl"

# ~4k-token stable prefix (>= any model's min-cacheable: Opus/Sonnet 1024, Haiku 2048).
_BASE = "GRIP prompt-cache-keepalive dogfood. Stable cacheable prefix line. " * 500
PREFIX_A = _BASE + " // ARM-A-BASELINE-NO-KEEPALIVE"
PREFIX_B = _BASE + " // ARM-B-KEEPALIVE-TOUCHED"


def _key() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", "grip-anthropic-external", "-w"],
        capture_output=True, text=True, timeout=10,
    )
    return (r.stdout or "").strip()


_KEY = _key()


def post_body(body: dict) -> dict:
    """Injected transport: real Anthropic POST, returns the usage dict. No key logging."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        API, data=data,
        headers={"x-api-key": _KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r).get("usage", {})


def mark(body: dict) -> dict:
    """Injected apply_cache_control: turn a string system into a cache_control-marked block."""
    s = body.get("system")
    if isinstance(s, str):
        body["system"] = [{"type": "text", "text": s, "cache_control": {"type": "ephemeral"}}]
    return body


def call(prefix_text: str, user: str = ".") -> dict:
    """One real request re-using `prefix_text` as the cached system prefix."""
    body = {"model": MODEL, "max_tokens": 1, "system": prefix_text,
            "messages": [{"role": "user", "content": user}]}
    mark(body)
    return post_body(body)


def _cc(u: dict) -> int:
    return int(u.get("cache_creation_input_tokens", 0) or 0)


def _cr(u: dict) -> int:
    return int(u.get("cache_read_input_tokens", 0) or 0)


def main() -> int:
    quick = "--quick" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    idle = float(args[0]) if args else (60.0 if quick else 420.0)
    cfg = KeepaliveConfig(ttl_seconds=300.0, margin_seconds=30.0) if not quick \
        else KeepaliveConfig(ttl_seconds=30.0, margin_seconds=10.0)
    interval = cfg.interval()
    t0 = time.time()
    log = lambda m: print(f"[t+{int(time.time()-t0):>4}s] {m}", flush=True)

    log(f"idle={idle}s interval={interval}s model={MODEL} prefixchars={len(PREFIX_A)}")
    # --- establish both cache entries (cache_creation = the write) ---
    a0, b0 = call(PREFIX_A), call(PREFIX_B)
    log(f"WRITE  A: creation={_cc(a0)} read={_cr(a0)} | B: creation={_cc(b0)} read={_cr(b0)}")
    # --- immediate confirm both are now warm (sanity: read > 0) ---
    a1, b1 = call(PREFIX_A), call(PREFIX_B)
    log(f"WARM?  A: read={_cr(a1)} creation={_cc(a1)} | B: read={_cr(b1)} creation={_cc(b1)}")
    prefix_tokens = max(_cc(a0), _cr(a1))  # measured real prefix size in tokens

    # --- keepalive thread on B only; A idles untouched ---
    touch_b = shim.build_cache_touch(post_body, system=PREFIX_B, model=MODEL, apply_cache_control=mark)
    ka = PromptCacheKeepalive(touch_b, cfg)
    deadline = t0 + idle - max(20.0, interval * 0.25)  # stop touching shortly before resume
    ka_thread = threading.Thread(target=ka.run, kwargs={"is_active": lambda: time.time() < deadline}, daemon=True)
    log(f"idling {idle}s — keepalive touching B every {interval}s; A untouched (control)...")
    ka_thread.start()
    while time.time() - t0 < idle:
        time.sleep(2)
    ka_thread.join(timeout=10)
    log(f"keepalive stats: touches={ka.stats.touches_made} failed={ka.stats.touches_failed} "
        f"read_tokens={ka.stats.cache_read_tokens}")

    # --- resume both after the idle ---
    a2, b2 = call(PREFIX_A), call(PREFIX_B)
    log(f"RESUME A(baseline): creation={_cc(a2)} read={_cr(a2)} | B(keepalive): creation={_cc(b2)} read={_cr(b2)}")

    # --- verdict (by token MAGNITUDE vs the measured prefix, not bare >0) ---
    # A full re-write re-caches ~the whole prefix (cache_creation ~= prefix_tokens). A warm
    # resume reads ~the whole prefix (cache_read ~= prefix_tokens) with only trivial
    # incremental creation (the differing message tail — the smoke run showed 3 tokens,
    # which is NOT a re-write). Hence magnitude thresholds, not `> 0`.
    pt = prefix_tokens or 1
    is_warm = lambda u: _cr(u) >= 0.9 * pt and _cc(u) <= 0.1 * pt
    is_rewrite = lambda u: _cc(u) >= 0.9 * pt
    baseline_rewrote = is_rewrite(a2)
    keepalive_warm = is_warm(b2)
    confirmed = baseline_rewrote and keepalive_warm
    falsified_reason = None
    if not keepalive_warm:
        falsified_reason = f"keepalive arm did NOT stay warm (resume read={_cr(b2)} creation={_cc(b2)} vs prefix={pt})"
    elif not baseline_rewrote:
        falsified_reason = (f"baseline arm did NOT re-write (resume read={_cr(a2)} creation={_cc(a2)} vs prefix={pt}) "
                            "— idle may be below the real provider TTL")

    econ = CacheEconomics(prefix_tokens=prefix_tokens or 4000)
    saving = net_savings(econ, idle_seconds=idle, interval_seconds=interval,
                         max_touches=cfg.max_touches, resume_probability=1.0)
    breakeven = breakeven_resume_probability(econ, idle, interval, cfg.max_touches)

    result = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hypothesis": "H-CACHE-DOGFOOD",
        "model": MODEL, "idle_seconds": idle, "interval_seconds": interval,
        "prefix_tokens_measured": prefix_tokens,
        "arm_baseline_resume": {"cache_creation": _cc(a2), "cache_read": _cr(a2)},
        "arm_keepalive_resume": {"cache_creation": _cc(b2), "cache_read": _cr(b2)},
        "keepalive_touches": ka.stats.touches_made,
        "keepalive_touch_read_tokens": ka.stats.cache_read_tokens,
        "verdict": "CONFIRMED" if confirmed else "FALSIFIED",
        "falsified_reason": falsified_reason,
        "cost_model": {
            "prefix_tokens": econ.prefix_tokens,
            "net_savings_token_equiv_resume_p1": round(saving, 1),
            "breakeven_resume_probability": round(breakeven, 4),
            "rewrite_cost": round(econ.rewrite_cost(), 1),
            "read_cost": round(econ.read_cost(), 1),
        },
        "quick_mode": quick,
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    with open(RECORD, "a") as f:
        f.write(json.dumps(result) + "\n")

    print("\n=== DOGFOOD VERDICT ===", flush=True)
    print(json.dumps(result, indent=2), flush=True)
    print(f"\nrecorded -> {RECORD}", flush=True)
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
