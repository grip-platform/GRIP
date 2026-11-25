---
name: efficiency
description: The be-all-and-end-all token optimiser and fleet watch. Executes any task at minimum token cost without dropping facts — tier-0 retrieval before any read, fold-compressed ingestion for large inputs, depth-0 main-thread bias, single-pass correct-by-construction writes, batched tool calls, a pre-registered token-budget hypothesis verified at completion — and keeps every fleet, loop and workflow it starts watched, so nothing sits stuck or burns tokens. Use when the operator invokes /efficiency (formerly /efficient), demands "least tokens possible" / "optimal efficiency", asks whether the fleets are stuck or burning, or wraps another command for maximum efficiency.
status: Active
effort: low
version: 2.0.0
triggers:
  - "/efficiency"
  - "/efficient"
  - "least tokens"
  - "token optimiser"
  - "optimal efficiency"
  - "fleet watch"
  - "stuck or burning"
integrates:
  - fold_compress.py
  - delight_v2.py
  - hypothesis_engine.py
  - fleet_watch.py
see_also:
  - converge
  - dispatch
  - caveman
  - tldr
  - recall
  - hypothesis-driven-development
  - fleet-inspection
  - loop
---

# /efficiency — Token Optimiser and Fleet Watch

`/efficiency <task>` — run any task at minimum token cost. `/efficiency /converge <task>` wraps another command (the wrapped command's protocol runs under E0–E6 discipline). `/efficiency watch` runs one fleet-watch tick (E6) and reports it. Renamed from `/efficient` on 2026-10-28 (v2.0.0) when the watch joined it; `/efficient` redirects here.

Core law: **reasoning is cheap, reads and output are the cost.** Think maximally (ultrathink — front-load all reasoning before the first tool call), read minimally, emit once.

## Protocol

### E0 — Budget hypothesis (pre-register, falsifiable)

One line BEFORE work: predicted `{tool_calls, files_read, fix_iterations, subagents}`.
Falsified if any actual axis exceeds 1.5× its prediction. Durable loops register via
`PYTHONPATH=$HOME/.claude python3 lib/hypothesis_engine.py register ...`; small tasks state it inline.
Hypothesis-driven development applied to the session itself: a run that cannot predict its own cost does not understand its task yet — keep thinking, not reading.

**Meter — wiring the prediction and actuals:**

```python
# Before work — one call
python3 -c "
import sys; sys.path.insert(0,'$HOME/.claude/lib')
from efficient_meter import record_prediction
record_prediction('RUN_ID', {'tool_calls': 14, 'files_read': 3, 'fix_iterations': 1, 'subagents': 0})
"

# After work — one call; prints the receipt
python3 -c "
import sys; sys.path.insert(0,'$HOME/.claude/lib')
from efficient_meter import record_actuals, receipt
v = record_actuals('RUN_ID', {'tool_calls': 12, 'files_read': 2, 'fix_iterations': 0, 'subagents': 0, 'fold_ratio': 0.42})
print(receipt(v))
"
# → E0 HELD · 12/14 calls · 2 reads · 0 agents · fold x0.42
```

Verdicts append atomically to `state/efficient/runs.jsonl` — the measurement substrate for the falsification clause below.

### E1 — Retrieval ladder (Rule 0, applied mechanically)

Session context → KONO → known paths → targeted Grep → Explore (justified escalation only, documented reason per tier). Exit at the FIRST tier that resolves. Never read what you can already state.

### E2 — Compressed ingestion

| Input | Action |
|---|---|
| Large markdown/log (> ~300 lines) | `lib/fold_compress.fold(bytes, depth=11)` → reason over the folded fixed point (`FoldResult.text`); expand only load-bearing regions via targeted `Read(offset, limit)` |
| Bash output | RTK auto-filters (PreToolUse hook 11); `rtk proxy` only when debugging the filter itself |
| Any file | Targeted `Read(offset, limit)` over full-file reads when the region is known |
| Re-read of an unchanged file | Forbidden (Rule 2 cache) |

`fold()` is a Banach contraction (κ ≤ 0.6 empirical, SHA-256 idempotence anchor) — folding is lossless on structure, idempotent by construction (`is_fixed_point`), and `compression_ratio` quantifies the gain per ingestion. The same depth-11 cap governs this skill's convergence budget.

### E2b — Statistical tool-output compression (optional rung, flag-gated)

**When to invoke:** large JSON/array or log-shaped tool results (> ~500 tokens) that `fold()` cannot substantially compress (dense JSON has no whitespace entropy to strip).

**Shim:** `lib/headroom_shim.compress_tool_output(messages, min_tokens=500)` → `CompressResult`.

```python
from lib.headroom_shim import compress_tool_output
result = compress_tool_output(tool_messages)
if not result.passthrough:
    # Use result.messages (compressed) in context;
    # result.tokens_saved / result.compression_ratio quantify the gain.
    pass
```

**Measured savings (HEADROOM 0.24.0, this machine):**

| Content | Saving | Transform |
|---|---|---|
| JSON array/object (120+ records) | 50–63% | SmartCrusher → schema+CSV |
| Repetitive log lines (200+ lines) | ~98% | search/dedup |
| Python source, prose | 0% (protected by HEADROOM router) | passthrough |

**Fail-open contract:** any error (missing 3.13 interpreter, `_core` missing, subprocess timeout, HEADROOM exception) returns `passthrough=True` with the original messages unchanged — the caller never sees a hard failure.

**Do NOT apply to:**
- Bash stdout (RTK already owns that boundary)
- Markdown GRIP reads (`fold()` already handles structural redundancy)
- Code or prose (HEADROOM's router protects these; applying both is redundant)

**Interpreter note (load-bearing):** HEADROOM requires Python ≤ 3.13 (no cp314 wheel as of 2026-06-11). The shim runs the scoped interpreter at `~/CodeTonight/vendor/headroom/.venv313/bin/python`. If that path is absent, the shim fails-open silently. Revisit when cp314 wheels ship.

**Exogenous anchor (H-E2B):** `tests/test_headroom_shim.py` asserts real savings on a fixed JSON fixture AND losslessness-on-retrieval — the rung is killed if HEADROOM ever silently drops a load-bearing fact. Eval evidence: `drafts/headroom-eval-2026-06-11.md`.

### E3 — Execution shape

- Depth-0 main thread by default; dispatch ONLY when useful work ≥ 3× ambient floor (`rules/dispatch-economics.md` Gate 2).
- Batch ALL independent tool calls in one block; dependent calls wait.
- CBC single-pass writes (Rule 18): correct at write time, target 0 fix iterations.
- No temp scripts (Rule 4); Edit/Write directly.

### E4 — Output

Canonical-output-style Layer 2: concise, plain, lossless. Evidence tokens (paths, SHAs, counts, error strings) verbatim; decorative IDs collapse to context-bearing phrases. Rule 17 rails non-overridable.

### E5 — Verify + report (exogenous anchor)

Criterion = tests / CI / external verdict — never self-score (`rules/exogenous-anchor-law.md`).
Report actuals vs the E0 prediction. Shadow metric: `lib/delight_v2.py snapshot` efficiency
score — may only LOWER confidence in the run, never raise it (scientific-method shadow rule).

### E6 — Never stuck, never burning (the fleet watch)

The law (`rules/workflows-never-stuck-never-burning.md`, PARAMOUNT, V>> 2026-10-28): **a fleet, loop, workflow or background agent you start is watched until it ends, and it is stopped before the next tick once it is hot or stuck.** E0 to E5 save what a turn spends; E6 saves what a run nobody reads spends. The 273-agent Workflow of 2026-10-26 spent 32 points of the weekly limit on one tweet reply because nothing watched it.

1. **Arm the watch in the same turn you start the work.** A `/loop 10m` on the watch prompt, or `bin/grip-fleet-watch` on a timer. For a Workflow, name the agent cap and the stall timeout before the first dispatch (`rules/fan-out-spends-the-weekly-limit.md`).
2. **Give every loop a stop condition and a tick budget before it runs.**
3. **Every ten minutes while anything you started still runs**, run `bin/grip-fleet-watch` (`lib/fleet_watch.py`). It lists every transcript written in the window with its growth since the last tick, flags HOT above 5 MB in one tick, flags STUCK when the same tool error repeats five times in a growing tail, lists headless harness processes (or says `unknown` when it cannot read the process list; on Windows the list comes from psutil), and ends with `QUIET` or `ACT`. Exit codes: `0` quiet, `1` act, `2` no projects root. The baseline it measures growth against is kept per session (`state/fleet-watch/sizes-<session>.json`, keyed on `CLAUDE_CODE_SESSION_ID` or the terminal pane), so two sessions ticking at once never read each other's, and every tick appends one JSON line to `logs/fleet-watch.jsonl`. `--json` for an agent; `--window`, `--hot-mb`, `--state`, `--log` and `--root` to tune.
4. **On ACT, stop only what you started, before the next tick**: TaskStop for a background agent, CronDelete for a loop, the loop's own stop for a Workflow. Anything else is reported by session id and pid, never killed (`memory/feedback_kill_only_processes_you_started.md`).
5. **Quote the last verdict in every report on fleet or loop work.** A watch nobody reports is theatre.

`agents/efficiency-meta-agent.md` runs the tick and writes the report when the main session would rather not spend its own context on it. It has no stop authority of its own.

**Falsifier for E6:** over ~20 watched runs, an ACT that was wrong more often than right (raise the thresholds or require two consecutive ACT ticks; never drop the watch), or a burn the two signals could not see (add the signal that would have seen it; the memory records above name the shapes seen so far).

## Why this works (the theory, honestly)

Each claim below carries evidence or a stated falsifier. Decorative analogies are absent — the SMT-coherent mappings only (`rules/canonical-output-style.md`).

### Shannon source coding — fold-compression removes statistical redundancy, not information

Shannon's source-coding theorem states that a lossless code cannot compress below the source entropy. `lib/fold_compress.py`'s `fold()` targets exactly that gap: whitespace normalisation and adjacent-line dedup remove zero-entropy structure (bytes that were predictable from their neighbours), leaving the information content intact. This is why the lossless clause holds: `fold()` never changes a unique byte sequence, it only collapses redundant ones. Evidence: `is_fixed_point()` is a one-call probe; folding a fixed point returns byte-identical output (SHA-256 anchored). **Falsifier:** find any input where `fold(fold(x)).text != fold(x).text` — that is a lossless-clause violation and a hard kill for E2. (H-FOLD-COMP-1, deadline 2026-06-21.)

### MDL / Kolmogorov framing — the budget hypothesis prefers the shortest program that explains the task

Minimum Description Length (MDL) says the best model of data is the one that compresses it most. Applied to task execution: the E0 budget prediction IS a description-length prior on the run. A run that overshoots its predicted resource envelope by more than 1.5× was not modelling its own cost — the description length of the task was underestimated. This is the grounding for the falsification bound: 1.5× is not arbitrary, it is the threshold where the prior and the posterior diverge enough to reject the model. **Falsifier:** if over 10 runs the falsification rate exceeds 20%, the 1.5× bound is too tight for the task distribution — raise it to 2.0× and re-evaluate. (Prediction theatre vs real compression.)

### E3 depth-0 bias — speculative-decoding analogy (and where it breaks)

Speculative decoding in LLMs works by drafting tokens cheaply then verifying with the full model in one pass — cheap draft, expensive verify-once. E3 uses the same shape: do all reasoning at depth-0 (cheap, already-loaded context, no ambient floor overhead), then dispatch only when the work passes Gate 2 (≥ 3× ambient floor justified). The analogy is structurally sound on the draft-cheap / verify-once axis. **Where it breaks:** speculative decoding has a rejection sampler — a principled probabilistic gate. E3 has no rejection sampler; the Gate 2 threshold is a heuristic ratio, not a proof of optimality. Do not extend the analogy beyond "prefer cheap first, escalate only when the gain is provably large." **Falsifier:** if depth-0 direct execution consistently produces more fix iterations than dispatched agents on the same task class, the cost model is wrong and E3 should bias toward dispatch for that class.

### Banach fixed-point — E2 ingestion convergence is guaranteed

`fold()` is a contraction mapping on the metric space of UTF-8 markdown byte strings (empirical κ ≤ 0.6, H-FOLD-COMP-1). Banach's theorem guarantees a unique fixed point reachable in finitely many iterations — the `depth=11` cap is not arbitrary: with κ ≤ 0.6, the error after 11 steps is ≤ 0.6^11 ≈ 4×10⁻³ of the initial distance, well within idempotence. Evidence: the `converged` flag on `FoldResult` is the constructive witness — it fires when successive SHA-256 digests match. **Falsifier:** any input where `converged=False` at `depth=11` and the output is not a fixed point — that falsifies the κ ≤ 0.6 claim and requires either raising the depth cap or acknowledging a non-contracting input class. (H-FOLD-COMP-2.)

## Efficiency criterion (mechanical)

satisfied ⇔ `subagents == 0` (unless Gate-2-justified) AND `fix_iterations ≤ 1` AND `files_read ≤ 2× files_modified` AND E0 hypothesis not falsified.

## Dogfood origin

First live run: 2026-06-10 fork-sync protected-branch bugfix — this skill was created and applied in the same `/converge` run (depth 0/11, 0 subagents). The run's own E0 prediction and actuals are the skill's first data point.

## Falsification

Delete this skill if, over the next 10 invocations: it shows no measured reduction in {tool calls, files read, fix iterations} versus comparable un-wrapped runs (`rules/loop-priority-external-over-theatre.md` — optimiser machinery is theatre until its savings are observed); OR its E0 budget hypotheses are falsified > 50% of the time (prediction theatre); OR fold-ingestion ever drops a load-bearing fact (lossless clause violated — that is a hard kill, not a tune).

Measurement substrate: `state/efficient/runs.jsonl` is the canonical ledger. The "10 invocations" clause above is falsifiable only if the meter has been wired (E0 section) — unmetered runs do not count toward the falsification tally.
