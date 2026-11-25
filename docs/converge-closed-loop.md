# Closed-Loop Convergence Substrate

A convergence loop that forgets what each wave learned is *open-loop*: it
re-discovers the same fix every run. This substrate is the **closed** loop's
memory — each wave records a structured lesson, and the next wave is handed the
folded view of all prior lessons for the same target and threads it into its own
reasoning. Learning from the previous loop, applied in the next.

Charter (V>>, 2026-06-10): *"a dynamic workflow — depth forced to increase in
Fibonacci series with automatic learning from previous convergence loop and
applying the fixes or enhancements in CLOSED-LOOP design principles."*

## What it is (plain English)

Two small, pure-Python modules plus a re-runnable dogfood:

| Module | Role |
|--------|------|
| `lib/converge_lessons.py` | The lessons store + the **fold** (the memory). |
| `lib/converge_loop.py` | The runner: Fibonacci depth ladder + lesson threading. |
| `scripts/converge_dogfood.py` | The proof — drives the kernel over a real target. |

The kernel brings **no intelligence of its own**: `run_waves` makes no LLM
calls. The `executor` is dependency-injected and brings the intelligence (an
agent, a model, a deterministic fixer, a test runner). The kernel only computes
the depth ladder, loads + threads prior lessons, records each wave's lesson, and
stops on criterion-met or `max_waves`. That keeps it deterministic and fully
unit-testable.

## The kernel contract

```text
run_waves(target, executor, max_waves) -> LoopResult
    for wave_index in range(max_waves):
        depth  = fibonacci_depth(wave_index)        # 1, 1, 2, 3, 5, 8 ...
        prior  = load_lessons(target)               # FOLDED view (<=40, deduped)
        result = executor(wave_index, depth, prior)  # the closed loop: prior threaded in
        append_lesson(... result ...)                # so the NEXT wave sees it
        if result["criterion_met"]: break
```

Executor contract: `executor(wave_index, depth, prior_lessons) -> dict` returns
at least `{"outcome": str, "lesson": str}` and may add `"fixed": bool`,
`"falsifier": str`, `"criterion_met": bool`. `criterion_met` True stops the loop
after that wave (no need to burn the remaining Fibonacci budget).

## The Banach fold property (the load-bearing invariant)

`converge_lessons.fold_text` is a deterministic reducer over the raw JSONL: it
dedupes by a semantic key (the normalised lesson text hash, **newest-wins** by
file position), bounds each target to the newest 40 lessons, and re-emits the
survivors in stable order with canonical (`sort_keys`) serialisation.

It is an **idempotent contraction — byte-identical on refold**:

```text
fold_text(fold_text(x)) == fold_text(x)      # a fixed point
fold_text("") == ""                          # the empty file is its own fixed point
```

This is exactly the contraction property `lib/holistic_synthesiser.py` relies on
(`payload_digest` there is the same idea): folding an already-folded substrate
changes nothing. It is asserted directly in `tests/test_converge_lessons.py`
(`TestBanachFold`) — and the test first proves the fold did **real work** on a
messy input (deduped + bounded + canonicalised) *before* asserting the refold is
byte-identical, so a no-op `fold_text` could not pass it.

Why it matters: the fold bounds the memory threaded into each wave (≤40 lessons,
deduped) so the loop's context never grows unbounded, and the byte-identity
guarantees compaction (`compact_file`, atomic `os.replace`) is safe to run any
number of times.

## Dogfood run record (the proof, not a claim)

`scripts/converge_dogfood.py` drove the kernel over the real **voice-hardening**
target — the three QA items from `plans/wa-voice-notes-qa-and-product.md`,
implemented in `lib/voice_note.py` in the order Q3 → Q1 → Q2:

| Wave | Depth | prior_count | Implemented | Reused from a threaded prior lesson |
|------|-------|-------------|-------------|-------------------------------------|
| 0 | 1 | 0 | **Q3** empty/whitespace guard (refuse `""` before TTS) | — (first wave) |
| 1 | 1 | 1 | **Q1** libopus pre-check → AAC ladder | W0's *guard-before-spend + DIP-seam* pattern |
| 2 | 2 | 2 | **Q2** sentence chunking + concat | W0's empty-guard per chunk **and** W1's convert ladder |

Run output (`PYTHONPATH=$HOME/.claude venv/bin/python scripts/converge_dogfood.py`):

```text
target=voice-hardening waves_run=3 criterion_met=True
  wave 0 depth=1 prior_count=0 -> implemented-Q3-empty-guard
  wave 1 depth=1 prior_count=1 -> implemented-Q1-opus-ladder
  wave 2 depth=2 prior_count=2 -> implemented-Q2-chunking
H859: 2/3 waves changed an implementation choice from a threaded prior lesson
      (prediction >=2) -> CONFIRMED
```

The resulting `state/converge/lessons.jsonl` (gitignored state — the evidence
file) held exactly three folded rows, e.g.:

```text
[voice-hardening-w0] depth=1 implemented-Q3-empty-guard:
    "Cheap input guards belong BEFORE the expensive backend ... DIP seams make
     it testable with zero network."
[voice-hardening-w1] depth=1 implemented-Q1-opus-ladder:
    "Applied the prior guard-before-spend lesson to the codec: a memoised libopus
     pre-check (DIP seam) runs ONCE before any encode, laddering to AAC ..."
[voice-hardening-w2] depth=2 implemented-Q2-chunking:
    "Reused BOTH priors: the empty-guard is applied per chunk ... and the
     opus->aac convert ladder runs on each segment before concatenation ..."
```

Each implemented change is itself covered by Goodhart-anchored tests in
`tests/test_voice_note.py` (`TestQ1OpusLadder`, `TestQ2Chunking`,
`TestQ3EmptyGuard`) — 27/27 voice tests pass, including the 14 pre-existing.

## Hypothesis (Popper — registered before coding)

**H859** (registered 2026-06-10, deadline 2026-06-17): *closed-loop lesson
threading measurably alters wave behaviour vs lesson-blind.* Metric: waves where
a threaded prior lesson changed the implementation choice. Prediction: `>=2 of 3`
dogfood waves. **Result: 2/3 — CONFIRMED.**

## Falsifier

This substrate is wrong if, **over the next 3 real targets, the lesson-threading
never changes a wave's behaviour vs a lesson-blind baseline** (prior always
`[]`). Then the loop is ceremony: delete the kernel (`converge_loop.py` +
`converge_lessons.py`) and keep `lessons.jsonl` as plain telemetry. Track via the
`changed`-count the dogfood records (`>=2/3` is the bar H859 set); a target where
all waves run identically with and without threading is a falsifying instance.

## Design discipline

stdlib only · DIP-injected executor and lessons-path seams (tests bind a temp
file, zero global state) · pure over content (the fold never depends on
wall-clock — `ts` is carried as data, ordering is by file position) ·
pure-append hot path (no torn files; compaction is a separate atomic
`os.replace`). British English, no AI attribution. Primary Author: Laurie
V>>.
