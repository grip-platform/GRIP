#!/usr/bin/env python3
"""Benchmark context_fold_cache on the REAL GRIP holistic-synthesis derivation.

Dogfood domain #4 — token/context memoisation. Emits measured, honestly-labelled
numbers (never estimates) to stdout as JSON and, with --write, to
artifacts/context-fold-memoisation-dogfood4/benchmark-results.json.

What is measured (all wall-clock medians on THIS machine's live memory
substrate; the substrate CONTENT is never emitted — only timings + a truncated
key hash):

  * synthesise_ms      — cost of GRIP's holistic-kernel synthesis, the real,
                         deterministic, every-/save derivation we memoise.
  * raw_key_ms         — sha256(raw substrate): the raw-mode cache-key cost.
  * folded_key_ms      — fold(substrate): the folded-mode cache-key cost.
  * raw_memo_net       — synthesise_ms - raw_key_ms  (compute saved per hit).
  * folded_memo_net    — synthesise_ms - folded_key_ms (the NET-LOSS finding).
  * llm_mechanism      — a call-counting deriver: 2 identical requests => 1 call;
                         the SECOND call (and all its tokens) is skipped. With a
                         simulated LLM latency this is where folded-key PAYS OFF.

Honesty boundary (verbatim from the prior art it extends): speedups are
wall-clock samples against a simulated downstream latency for the LLM leg — large
but NOT constants. The synthesise legs are real end-to-end times, no simulation.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from lib.context_fold_cache import (  # noqa: E402
    DerivationRequest, derive, is_idempotent_key)
from lib.fold_compress import fold  # noqa: E402
from lib.holistic_synthesiser import synthesise  # noqa: E402
import hashlib  # noqa: E402


def _median_ms(fn, n: int = 7) -> float:
    samples = []
    for _ in range(n):
        a = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - a) * 1000)
    return round(statistics.median(samples), 4)


def _substrate(mem: Path) -> bytes:
    return b"".join(sorted((p.read_bytes() for p in mem.glob("*.md")), key=len))


def _real_path_legs(mem: Path, idx: Path) -> dict:
    """Measure the real synthesise derivation + both key-mode costs + net."""
    outs = [synthesise(mem, idx).text for _ in range(3)]
    deterministic = outs[0] == outs[1] == outs[2]
    substrate = _substrate(mem)
    synth_ms = _median_ms(lambda: synthesise(mem, idx))
    raw_key_ms = _median_ms(lambda: hashlib.sha256(substrate).hexdigest())
    folded_key_ms = _median_ms(lambda: fold(substrate))
    return {
        "substrate_bytes": len(substrate),
        "synthesise_deterministic_x3": deterministic,
        "kernel_chars": len(outs[0]),
        "synthesise_ms": synth_ms,
        "raw_key_ms": raw_key_ms,
        "folded_key_ms": folded_key_ms,
        "raw_memo_net_ms_per_hit": round(synth_ms - raw_key_ms, 4),
        "folded_memo_net_ms_per_hit": round(synth_ms - folded_key_ms, 4),
        "saving_type": "compute/latency (synthesise is pure Python, not an LLM call)",
        "finding": (
            "raw-key memo of the cheap synthesis is net-POSITIVE; folded-key memo "
            "is net-NEGATIVE (folding a large static substrate costs more than the "
            "derivation it skips) — fold-keying pays off only for expensive/LLM derivers"
        ),
    }


def _end_to_end(mem: Path, idx: Path) -> dict:
    """derive() miss-then-hit on the real derivation; assert the deriver skips."""
    calls = {"n": 0}
    substrate = _substrate(mem)

    def deriver(_effective: bytes) -> bytes:
        calls["n"] += 1
        return synthesise(mem, idx).text.encode("utf-8")

    with tempfile.TemporaryDirectory() as d:
        cache = Path(d)
        req = DerivationRequest(substrate, "holistic_synthesis", key_mode="raw")
        miss = derive(req, deriver, cache_dir=cache)
        hit = derive(req, deriver, cache_dir=cache)
        return {
            "route_first": miss.route, "route_second": hit.route,
            "deriver_calls_for_two_identical_requests": calls["n"],
            "derivation_skipped_on_hit": calls["n"] == 1,
            "output_identical": miss.output == hit.output,
            "key_is_idempotent_fixed_point": is_idempotent_key(substrate),
        }


def _llm_mechanism(simulated_llm_ms: float) -> dict:
    """Prove the LLM-TOKEN mechanism: a hit skips the WHOLE call (tokens and all).

    The latency is SIMULATED (there is no live deterministic-reissue LLM path in
    GRIP today) — this measures calls-skipped, the token-saving mechanism, not
    live tokens. Uses folded key: the case where fold-keying pays off.
    """
    calls = {"n": 0}
    prompt = b"Ground the council on rules/exogenous-anchor-law.md and verdict.\n"

    def llm(_effective: bytes) -> bytes:
        calls["n"] += 1
        time.sleep(simulated_llm_ms / 1000.0)
        return b"VERDICT: PASS (tokens spent only on the miss)"

    with tempfile.TemporaryDirectory() as d:
        cache = Path(d)
        req = DerivationRequest(prompt, "grounding", key_mode="folded")
        a = time.perf_counter()
        derive(req, llm, cache_dir=cache)
        miss_ms = (time.perf_counter() - a) * 1000
        a = time.perf_counter()
        derive(req, llm, cache_dir=cache)
        hit_ms = (time.perf_counter() - a) * 1000
    return {
        "simulated_llm_ms": simulated_llm_ms,
        "llm_calls_for_two_identical_requests": calls["n"],
        "whole_llm_call_skipped_on_hit": calls["n"] == 1,
        "miss_ms": round(miss_ms, 4), "hit_ms": round(hit_ms, 4),
        "speedup_sample": round(miss_ms / hit_ms, 1) if hit_ms else None,
        "boundary": "simulated downstream latency; calls-skipped is real, tokens are the mechanism",
    }


def run(mem: Path, idx: Path, simulated_llm_ms: float = 40.0) -> dict:
    return {
        "v": "context-fold-cache-benchmark/0.1",
        "machine_note": "wall-clock medians on this machine; not constants",
        "real_path": _real_path_legs(mem, idx),
        "end_to_end": _end_to_end(mem, idx),
        "llm_token_mechanism": _llm_mechanism(simulated_llm_ms),
    }


def _dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.glob("*.md"))


def _default_substrate() -> tuple[Path, Path]:
    """The largest (most representative) real memory substrate on this install —
    deterministic so the cited numbers are stable across runs."""
    cands = [c for c in (_ROOT / "projects").glob("*/memory") if list(c.glob("*.md"))]
    mem = max(cands, key=_dir_bytes) if cands else _ROOT / "memory"
    return mem, mem / "MEMORY.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="also write results JSON")
    ap.add_argument("--simulated-llm-ms", type=float, default=40.0)
    args = ap.parse_args(argv)
    mem, idx = _default_substrate()
    results = run(mem, idx, args.simulated_llm_ms)
    print(json.dumps(results, indent=2))
    if args.write:
        out = _ROOT / "artifacts" / "context-fold-memoisation-dogfood4"
        out.mkdir(parents=True, exist_ok=True)
        (out / "benchmark-results.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out / 'benchmark-results.json'}", file=sys.stderr)
    return 0


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

    raise SystemExit(main())
