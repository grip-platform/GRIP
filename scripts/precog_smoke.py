#!/usr/bin/env python3
"""PRECOG W3 token-budget probe + manual smoke runner.

Runs `orchestrate()` on 5 representative prompts that together cover the
adaptive tier ladder (LOW → MAX), captures input-token estimates per call,
computes median + max, writes a verification sidecar.

Dogfood: each smoke call is itself notarised through the orchestrator's
own IDR chain. The chain GROWS as the W3 probe runs — the substrate is
exercised by exercising it.

Verifies H707 (median input_tokens <= 8000) and partially exercises the
H703 tier-distribution claim (5 prompts hitting >= 3 distinct tiers).

Usage:
    PYTHONPATH=$HOME/.claude python3 scripts/precog_smoke.py

Output:
    learning/h707-token-budget-smoke.json
    /tmp/precog-smoke-idr.jsonl  (isolated chain — doesn't pollute the canonical)
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

# Local-import bootstrap
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib.precog import measure_input_tokens, orchestrate  # noqa: E402


# 5 representative prompts covering all 5 EffortTier branches.
# Each tuple: (label, prompt, recommended_pattern, expected_tier_band)
SMOKE_PROMPTS = [
    (
        "trivial-fastpath",
        "fix typo",
        "Direct",
        {"low"},
    ),
    (
        "low-medium",
        "should we always use this approach for production traffic",
        "Focused",
        {"low", "medium"},
    ),
    (
        "medium-high",
        ("Refactor the auth middleware to handle expired tokens. "
         "The invariant must hold: never accept a stale token without re-verifying."),
        "Focused",
        {"medium", "high"},
    ),
    (
        "code-heavy",
        ("```python\n"
         "class AuthMiddleware:\n"
         "    def login(self, token: str) -> User: pass\n"
         "    def refresh(self, token: str) -> str: pass\n"
         "```\n"
         "```python\n"
         "class TokenStore: pass\n"
         "def rotate_keys(): pass\n"
         "```"),
        "Sprint",
        {"medium", "high", "xhigh"},
    ),
    (
        "max-novel",
        ("Build a novel polymorphic dispatch system that must guarantee "
         "invariants: never violate the chain, always prove falsifiable. "
         "```python\nclass Dispatcher:\n    def emit(self, idr): pass\n```\n"
         "def helper(): pass\nclass Other: pass\n") * 3,
        "FullMesh",
        {"xhigh", "max"},
    ),
]

# Fixed kernel + budget for reproducibility
KERNEL_TEXT = (
    "GRIP precog W3 smoke · H707 token-budget probe · 2026-05-24 · "
    "kernel context: orchestrator + replay + audit_chain + hypothesis engine"
)
BUDGET = {"tokens_remaining": 800_000, "rate_limit_proximity": 0.1}

# Isolated chain + cache (don't pollute the canonical state/precog/idr.jsonl)
SMOKE_CHAIN = Path("/tmp/precog-smoke-idr.jsonl")
SMOKE_CACHE = Path("/tmp/precog-smoke-cache")

# Output sidecar
SIDECAR = _REPO_ROOT / "learning" / "h707-token-budget-smoke.json"


def _reset_smoke_state() -> None:
    """Clear the isolated chain + cache so each run is deterministic."""
    if SMOKE_CHAIN.exists():
        SMOKE_CHAIN.unlink()
    if SMOKE_CACHE.exists():
        import shutil
        shutil.rmtree(SMOKE_CACHE)


def _run_one(
    label: str, prompt: str, pattern: str, expected_tiers: set[str],
    predecessor: str | None, depth: int,
) -> dict:
    """Execute one smoke iteration; returns a per-prompt result record."""
    routing_hint = {"recommended_pattern": pattern}
    tokens = measure_input_tokens(
        prompt, kernel_text=KERNEL_TEXT,
        routing_hint=routing_hint, budget=BUDGET,
    )
    t0 = time.time()
    idr = orchestrate(
        prompt=prompt, kernel_text=KERNEL_TEXT,
        routing_hint=routing_hint, budget=BUDGET,
        predecessor_idr=predecessor, depth=depth,
        chain_path=SMOKE_CHAIN, cache_dir=SMOKE_CACHE,
    )
    elapsed_ms = (time.time() - t0) * 1000
    actual_tier = idr.decision["effort_tier"]
    return {
        "label": label,
        "prompt_len_chars": len(prompt),
        "input_tokens_estimate": tokens,
        "tier": actual_tier,
        "tier_in_expected_band": actual_tier in expected_tiers,
        "expected_band": sorted(expected_tiers),
        "idr_id": idr.id,
        "idr_depth": idr.depth,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def _summarise(results: list[dict]) -> dict:
    """Reduce per-prompt results to the H707 verification record."""
    tokens = [r["input_tokens_estimate"] for r in results]
    median_t = statistics.median(tokens)
    tiers = sorted(set(r["tier"] for r in results))
    bands_hit = sum(1 for r in results if r["tier_in_expected_band"])
    elapsed_total = sum(r["elapsed_ms"] for r in results)
    return {
        "hypothesis": "H707",
        "claim": ("PRECOG orchestrator step input-token median stays under "
                  "the 8k budget across 5 representative prompts (W3 probe)."),
        "prediction": "<= 8000",
        "results": results,
        "median_input_tokens": median_t,
        "max_input_tokens": max(tokens),
        "min_input_tokens": min(tokens),
        "tiers_exercised": tiers,
        "distinct_tier_count": len(tiers),
        "expected_band_hit_rate": bands_hit / len(results),
        "status": "confirmed" if median_t <= 8000 else "refuted",
        "elapsed_total_ms": round(elapsed_total, 2),
        "smoke_chain_path": str(SMOKE_CHAIN),
        "chain_depth_grown_from": 0,
        "chain_depth_grown_to": len(results) - 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> int:
    _reset_smoke_state()
    results: list[dict] = []
    predecessor: str | None = None
    for depth, (label, prompt, pattern, bands) in enumerate(SMOKE_PROMPTS):
        record = _run_one(label, prompt, pattern, bands, predecessor, depth)
        results.append(record)
        predecessor = record["idr_id"]

    summary = _summarise(results)
    SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"H707 {summary['status'].upper()} · "
          f"median={summary['median_input_tokens']} "
          f"max={summary['max_input_tokens']} "
          f"min={summary['min_input_tokens']} tokens · "
          f"tiers={summary['tiers_exercised']} · "
          f"band-hit={int(summary['expected_band_hit_rate'] * len(results))}/{len(results)} · "
          f"{summary['elapsed_total_ms']:.1f}ms total")
    print(f"Sidecar: {SIDECAR}")
    print(f"Smoke IDR chain: {SMOKE_CHAIN} (depth 0..{len(results) - 1})")
    return 0 if summary["status"] == "confirmed" else 1


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
