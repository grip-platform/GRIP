# DFI-FIB Kernel: Operator Guide

**DFI-FIB** stands for Direct-First Improvement with Fibonacci depth control.
It is the recursive self-improvement loop at the heart of GRIP's converge system.

The core idea in one sentence: **write one improvement step, and the kernel handles
looping, caching, and knowing when to stop.**

---

## The Meta-Principle

> design[0] abstraction makes design[N+1] trivial.

When you design the first iteration of a loop correctly — one function that takes a
state and returns an improved state — every subsequent consumer of that loop gets the
caching, depth cap, and fixed-point detection for free. You never rewrite the loop;
you only write the improvement step.

This is why `InnerspeakerGo` exists: it is the one correct loop. All GRIP subsystems
that need iterative improvement (converge, ultrado, slack-update quality scoring) plug
in their own improvement step and inherit the rest.

---

## Usage Pattern

### Step 1 — Write your improvement function

```python
from lib.improve_kernel import InnerspeakerGo, ImproveResult, fingerprint

def my_step(state: str, depth: int) -> ImproveResult[str]:
    """One improvement pass. Must be deterministic for a given state."""
    improved = apply_one_fix(state)
    return ImproveResult(
        state=improved,
        metrics={"quality": score(improved)},
        depth=depth,
        fingerprint=fingerprint(improved),
    )
```

The function receives the current state and the current depth. It returns an
`ImproveResult` containing the new state, any metrics you care about, and the
fingerprint of the new state.

### Step 2 — Define your threshold

```python
threshold = {"quality": ("ge", 0.9)}  # stop when quality >= 0.9
```

Operators: `"ge"` (greater-or-equal), `"le"` (less-or-equal). Multiple conditions
are ANDed — all must pass before the kernel stops.

### Step 3 — Run the kernel

```python
kernel = InnerspeakerGo(converge=my_step, threshold=threshold)
result = kernel("initial state")
print(result.state, result.depth, result.metrics)
```

That is the complete integration. One constructor call, one invocation.

---

## How the Kernel Stops

The kernel stops as soon as **any** of three conditions is true:

1. **Threshold passed** — `result.passes(threshold)` returns `True`. Normal exit.
2. **Fixed point detected** — the fingerprint before and after a step are identical,
   meaning the improvement function produced no change. Further iterations would be
   infinite loops. The fingerprint is persisted to `data/converge-fixed-points.jsonl`
   so future runs skip this state immediately.
3. **Depth cap reached** — default maximum is 13 iterations (Fibonacci number F(7),
   matching the RSI wave cap). Override with `max_depth=N` in the constructor.

---

## Fingerprint LRU Semantics

`fingerprint(state)` computes the first 16 hex characters of SHA-256 over the
normalised state. It works on strings, bytes, and any JSON-serialisable object.

The kernel maintains an in-process dictionary (`_cache`) keyed by fingerprint.
When the same state is encountered again within a session:

- If the cached result already passes the threshold, it is returned immediately —
  no re-computation.
- If the state is in the fixed-point registry, it is returned immediately as a
  known non-improving state.

The cache is **per-instance, in-memory only**. It does not persist across Python
restarts. The fixed-point registry **does** persist (JSONL file) and is loaded on
`__init__`, so cross-session fixed-point avoidance works without re-running the loop.

---

## Fixed-Point Registry Semantics

A fixed point is a state where `converge(state)` produces a state with the same
fingerprint as the input — no progress was made.

When detected, the kernel:
1. Adds the fingerprint to the in-process `_fixed_points` set.
2. Appends `{"fingerprint": "...", "ts": "..."}` to `data/converge-fixed-points.jsonl`.

On next run, the registry is loaded at construction time. Any state whose fingerprint
appears in the registry is short-circuited immediately, skipping the improvement call
entirely. This is the primary mechanism for avoiding wasted compute on provably
unimprovable inputs.

---

## Telemetry

Every improvement step appends one JSON line to `logs/improve-kernel.jsonl`:

```json
{
  "ts": "2026-04-17T12:00:00Z",
  "input_fingerprint": "a1b2c3d4e5f60001",
  "output_fingerprint": "f6e5d4c3b2a10002",
  "depth": 3,
  "outcome": "threshold_pass",
  "metrics": {"quality": 0.93}
}
```

`outcome` is one of `"fixed_point"`, `"threshold_pass"`, or `"recursing"`.
Use this log to diagnose loops that reach the depth cap without passing the threshold.

---

## When NOT to Use the Kernel

The kernel adds value only when you need **iterative refinement** of a state.
Do not use it for:

- **One-shot computations** — if your function produces a correct result on the
  first call, there is no loop to manage. Call the function directly.
- **Stateless transforms** — if each call is independent (no state carried forward),
  the LRU cache provides no benefit and adds overhead.
- **Side-effect-heavy steps** — `converge` is assumed to be pure relative to the
  kernel (same input → same output). If your step sends emails or writes to a
  database, fixed-point detection will short-circuit it incorrectly on repeated runs.
- **Short pipelines** — if you know the pipeline runs exactly N times and N is fixed,
  a plain loop is simpler and easier to reason about.

---

## Cross-References

| Resource | Purpose |
|----------|---------|
| `lib/improve_kernel.py` | Source implementation — `InnerspeakerGo`, `ImproveResult`, `fingerprint` |
| `plans/dfi-fibonacci-kernel.md` | Original design doc and sprint rationale |
| `lib/comms_update.py` | Example consumer — `SlackAdapter` quality loop (Track A) |
| `data/converge-fixed-points.jsonl` | Persistent fixed-point registry |
| `logs/improve-kernel.jsonl` | Per-step telemetry output |
