"""Benchmark PromptFoldCache against the three plan scenarios.

Reproduces artifacts/banach-fold-memoisation-ultraplan/ §Evidence:
  S1 exact whitespace variant  -> second call routes exact_hit
  S2 near wording, gated reuse -> second call routes semantic_hit
  S3 unsafe near disabled      -> second call is a miss (2 model calls)

The downstream model call is SIMULATED (deterministic 40ms sleep), so the
speedups are wall-clock samples against that latency — large but not
constants (measured precedents: 976.2x OpenCode/GPT-5.5 cold->warm;
696.8x/310.3x plan simulation; fib-memoise spread 659.6x–1444.4x).

Usage:
  venv/bin/python3 scripts/benchmark_prompt_fold_cache.py          # plan-style lines
  venv/bin/python3 scripts/benchmark_prompt_fold_cache.py --json   # CI-parseable
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.prompt_fold_cache import PromptCacheRequest, run_or_get  # noqa: E402

SIMULATED_LATENCY_S = 0.04

S1_A = b"Summarise this line for the   morning report.\r\n\r\n\r\n\r\nKeep it short.  \n\n\n"
S1_B = b"Summarise this line for the morning report.\n\nKeep it short.\n"
S2_A = b"Summarise the quarterly revenue figures for the finance team meeting today.\n"
S2_B = b"Summarise the quarterly revenue figures for the finance team meeting tomorrow.\n"


def make_model():
    calls = []

    def model(folded: bytes) -> bytes:
        calls.append(folded)
        time.sleep(SIMULATED_LATENCY_S)
        return b"MODEL-OUTPUT:" + folded[:24]

    return model, calls


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def run_scenario(scenario_id: str, first: PromptCacheRequest,
                 second: PromptCacheRequest) -> dict:
    with tempfile.TemporaryDirectory(prefix="pfc-bench-") as tmp:
        cache = Path(tmp)
        model, calls = make_model()
        d1, t1 = timed(lambda: run_or_get(first, model, cache_dir=cache))
        d2, t2 = timed(lambda: run_or_get(second, model, cache_dir=cache))
        return {
            "id": scenario_id,
            "first_route": d1.route,
            "route": d2.route,
            "same_key": d1.exact_key == d2.exact_key,
            "model_calls": len(calls),
            "saved_calls": 2 - len(calls),
            "speedup": round(t1 / t2, 1) if t2 > 0 else float("inf"),
            "near_score": (round(d2.semantic_score, 3)
                           if d2.semantic_score is not None else None),
        }


def run_all() -> dict:
    scenarios = [
        run_scenario(
            "S1",
            PromptCacheRequest(S1_A, task_type="summarise", risk_class="low"),
            PromptCacheRequest(S1_B, task_type="summarise", risk_class="low"),
        ),
        run_scenario(
            "S2",
            PromptCacheRequest(S2_A, task_type="summarise", risk_class="low",
                               allow_semantic=True),
            PromptCacheRequest(S2_B, task_type="summarise", risk_class="low",
                               allow_semantic=True),
        ),
        run_scenario(
            "S3",
            PromptCacheRequest(S2_A, task_type="summarise", risk_class="low",
                               allow_semantic=True),
            PromptCacheRequest(S2_B, task_type="summarise", risk_class="low",
                               allow_semantic=False),
        ),
    ]
    return {
        "v": "prompt-fold-cache-benchmark/0.1",
        "simulated_latency_ms": SIMULATED_LATENCY_S * 1000,
        "scenarios": scenarios,
    }


def print_plan_style(report: dict) -> None:
    for s in report["scenarios"]:
        print(f"{s['id']} SAME_KEY {s['same_key']}")
        print(f"{s['id']} SECOND_ROUTE {s['route']}")
        if s["near_score"] is not None:
            print(f"{s['id']} NEAR_SCORE {s['near_score']}")
        print(f"{s['id']} MODEL_CALLS {s['model_calls']}")
        print(f"{s['id']} SPEEDUP {s['speedup']}x")
        print()


def main(argv: list[str]) -> int:
    report = run_all()
    expected = {"S1": "exact_hit", "S2": "semantic_hit", "S3": "fresh"}
    ok = all(s["route"] == expected[s["id"]] for s in report["scenarios"])
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        print_plan_style(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
