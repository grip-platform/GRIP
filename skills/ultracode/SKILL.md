---
name: ultracode
description: Harness- and provider-agnostic idempotent workflow kernel (lib/ultracode) — content-addressed DAG state (Banach f∘f=f, a repeat run makes 0 new provider calls), the four primitives (dispatch / monotone verify / exogenous synthesize / bounded recurse), CoT/ToT/FoT reasoning topologies, and a HAPPI envelope seam so the same workflow yields the identical Result under any harness. Use when a workflow needs deterministic replay, provider-swap freedom (Pool-B default), or cross-harness portability. NOT the native Claude Code `ultracode` keyword (the Workflow-tool trigger / effort tier) — GRIP's kernel is the delta built on top of those.
status: Active
---

# /ultracode — the idempotent workflow kernel

## Namespace (read this first — two things share the name)

Claude Code natively owns `ultracode` twice: the **dynamic-workflow trigger keyword**
(typing it opts a prompt into the Workflow tool) and the **`/effort ultracode` tier**
(CC 2.1.154+). GRIP's decision (V>> HITL 2026-07-12,
`feedback_ultracode_native_namespace_reconcile`): **adopt-native-and-build-more** —
this skill WRAPS those as infrastructure and never fights them. What the native layer
does not provide — and this kernel does — is the delta:

- **Idempotency by construction** — `content_hash(task, state)` memoisation; a repeated
  `(task, state)` makes ZERO new provider calls (Banach f∘f = f). Native workflows re-run.
- **Provider agnosticism** — work enters only through an injected `Adapter`
  (DeterministicAdapter for tests/CI, HALAdapter for Pool-B inference). Broly's
  criterion-mesh consumer separately enforces non-Anthropic role assignments
  before dispatch.
- **Harness agnosticism** — one HAPPI envelope, one executor, any driver: in-process,
  `python3 -m lib.ultracode.harness` headless, OpenCode, anything that speaks stdin/JSON.

### Reviewer notes (two conventions that have tripped reviews — AGORA #3949)

- `harness: universal` in `commands/*.md` frontmatter is the **GRIP command-file
  convention** (the command runs under any harness — cf. `commands/converge.md`);
  it is not a claim about this kernel's proven harness coverage. The proven set
  lives in `tests/test_ultracode_harness.py` (three distinct drivers).
- The kernel's `cache` dict is the **deliberate mutable memoisation store**;
  `State` is the immutable value. Passing a shared cache between calls is exactly
  how idempotency crosses process/call boundaries (see `lib/broly_ultracode.py`'s
  per-run file) — it is not a violation of the immutable-State pattern.

## The kernel (lib/ultracode/ — pure stdlib, no GRIP imports)

| Module | Provides |
|---|---|
| `kernel.py` | `content_hash` · immutable `State`/`Node` DAG · idempotent `run()` · `Adapter` seam |
| `primitives.py` | `dispatch` (fan-out) · `verify` (MONOTONE: l2 can only tighten l1, never rescue) · `synthesize` (exogenous judge) · `recurse` (bounded refine-to-criterion) |
| `topology.py` | `chain` (CoT) · `tree` (ToT beam search) · `forest` (FoT + judge) · `solve(cfg.topology)` router |
| `adapters.py` | `DeterministicAdapter` ($0, portable) · `HALAdapter` (Pool B, availability-guarded) |
| `workflows.py` | named-workflow registry — envelopes stay pure data; both harnesses resolve the same name |
| `harness.py` | HAPPI seam: `make_envelope` / `parse_envelope` (fail-closed) / `execute_envelope` (THE single executor) / headless `main()` (NDJSON started/delta/completed, exit 0 on completed) |

## Proven hypotheses (all green; tests in tests/test_ultracode_*.py)

- **H-UC-IDEM** — repeat `(task, state)` ⇒ cached Result, 0 new calls. Falsified if any repeat fires a call.
- **H-UC-PROVIDER** — same `dispatch()` unchanged over deterministic AND live HAL Pool-B adapters. Falsified if a verdict flips on adapter swap alone.
- **H-UC-HARNESS** — identical content-addressed `result_hash` in-process vs headless subprocess through one shared executor. Falsified if a harness-specific code path is ever required.

## Usage

```python
from lib.ultracode.harness import execute_envelope, make_envelope

env = make_envelope("teach_quiz", {"subject": "x"})          # deterministic, $0
env_live = make_envelope("teach_quiz", {"subject": "x"}, adapter="hal")  # Pool B
result = execute_envelope(env)                                # {output, result_hash, calls, …}
```

```bash
# headless — any harness, any language, stdin JSON in, NDJSON events out
echo '{"v":"happi/1.1","id":"x","cmd":"ultracode.solve","args":[{"q":1}],
       "flags":{"workflow":"echo_chain","adapter":"deterministic"}}' \
  | PYTHONPATH=$HOME/.claude python3 -m lib.ultracode.harness
```

Add a workflow = one entry in `workflows.py` (pure functions only — determinism is
what makes the cross-harness proof a hash comparison instead of an opinion).

## Composition

- **/ultrawin** — keeps orchestration *structure* on the native Workflow tool; routes
  *cognition* through Pool B. This kernel is the deterministic replay/idempotency layer
  either can call.
- **`lib/converge.py`** — the named GRIP loop kernel (`rules/loop-engineering.md`);
  `recurse` is its bounded refine shape over the ultracode adapter seam.
- **/converge-fleet** — the multi-agent shape of that same bounded refine: parallel expert
  lanes whose per-round learnings are pooled and fed forward. It runs on the Workflow tool
  and calls this kernel for idempotent replay; it does not reimplement `recurse`.
- **HAPPI.md** — the envelope contract `harness.py` implements (accepts `happi/1.x`).
- **Pool discipline** — `rules/pool-a-offload-routing.md`: HALAdapter routes
  through `lib/hal_llm_adapter.py` → `grip_infer`. The generic adapter does not
  prove billing class; Broly's consumer rejects explicit `anthropic/*`
  assignments before dispatch and uses a non-Anthropic default roster.

## Falsification (of the skill itself)

Wrong if, over the next ~10 workflow builds: hand-rolled iterate-until-done loops keep
appearing where this kernel fits (the skill is inert decoration — fold into /converge
docs and delete); OR the idempotency cache masks a legitimately-changed task (hash
collision or stale-cache incident — tighten `content_hash`, never drop the invariant);
OR the namespace note fails to prevent a native-keyword/kernel confusion incident
(sharpen the note; at N≥2 add a mechanical disambiguation surface).

## Lineage

Graduated 2026-07-13 from the private dogfood repo CodeTonight-SA/grip-teach
(P0 `ee40de9` → P1 `e96ab48` → P2 `09ddc77` → P3 `f21cecb` → P4 `fa93ed9`;
35/35 tests green there; live Pool-B dogfood leg verified: 4 real HAL calls, one per
stage). Design sealed by a non-Anthropic council: `grip-sovereign-council --verify
bf75f66e72ccdba5`. Plan: grip-teach `plans/PLAN-ultracode.md`.
