---
name: ultraclear
description: Zero-loss alternative to /compact for a full interactive context window. Instead of paying to re-read the whole window and ending up with a lossy summary, /ultraclear partitions the conversation into HOT (keep verbatim) / COLD (offload, page back on demand) / FOLDED (already saved in memory), cheap-serialises a small bounded checkpoint, you run the native /clear, and the next session warm-reloads ONLY the small HOT manifest. Cheaper AND loses nothing. Use when the window is large and you'd otherwise reach for /compact or start fresh.
invocable_name: ultraclear
status: Active
version: 1.0.0
triggers:
  - /ultraclear
  - /ultracompact
  - /new
aliases:
  - ultraclear
  - ultracompact
see_also:
  - holistic-memory
  - saving-state
  - recall
  - scrollback-and-stash
---

# /ultraclear — clear your context window without losing anything

## The problem, in plain English

When a chat session gets long, the context window fills up. The usual fix is
`/compact`: it reads the **entire** window again and writes a short **summary**,
then throws the rest away. That has two costs you feel:

1. **It's lossy.** A summary is a smaller, fuzzier copy. Details get dropped.
   You can't get them back — the originals are gone.
2. **It's expensive.** To make the summary, the model has to re-read every token
   in the window. On a near-full window that's a big bill, every time.

`/ultraclear` does the opposite of "read everything and shrink it". It **sorts**
your conversation into three piles, **saves** the piles cheaply, and lets you
start a clean session that re-loads only the small pile it actually needs — with
**nothing lost**, because the other piles are still on disk, intact.

## How it works (the three piles)

Think of it like clearing a desk before a fresh start. You don't shred the
papers — you sort them:

- **HOT** — the papers you're actively using (the most recent turns, plus any
  turn you've pinned as load-bearing — the current task, an operator directive,
  a decision). These stay **on the desk, word-for-word**. This is the small
  bundle the next session re-loads.
- **COLD** — older papers you're not using right now. They go in a **labelled
  drawer** (a local store). They cost nothing while they sit there. If a later
  turn needs one, you pull that exact paper back out — **the original, not a
  summary**.
- **FOLDED** — papers that are **already filed** in GRIP's long-term memory (the
  holistic kernel). No need to keep a second copy on the desk; the file cabinet
  has them.

The guarantee: **HOT ∪ COLD ∪ FOLDED = every single turn.** Nothing is dropped,
nothing is summarised. That's the zero-loss invariant, and it's enforced by
tests (`tests/test_ultraclear.py`, `tests/test_ultraclear_session.py`).

## Why it's cheaper (the honest numbers)

The cost model comes from `lib/ultraclear.py` (Anthropic cache multipliers,
2026-05): re-reading a token costs `0.10x`; re-establishing a small bundle costs
`1.25x` per token.

- `/compact` re-reads the **whole window**. On a 575k-token window that's
  `575,000 × 0.10 = 57,500` base-equivalents — and you still end up with a lossy
  summary.
- `/ultraclear` re-loads only the **bounded HOT bundle** (capped, default 20k
  tokens): `20,000 × 1.25 = 25,000` — and you lose nothing.

That's a ~32,500 net saving on that example, **and** it's lossless where the
summary isn't. When the window is **small**, re-loading the bundle isn't cheaper
than just re-reading it, so `/ultraclear` reports `is_noop` and tells you to
stay as-is — it won't pretend to help when it can't.

## The honest boundary (read this)

**`/ultraclear` does not replace the native `/clear`.** The Claude Code harness
owns the live context window — a skill cannot reach in and clear it. So
`/ultraclear` **orchestrates**: it does the cheap checkpoint and prepares the
warm-reload manifest; **you** run the native `/clear`; then the next session
loads the HOT manifest. The split is deliberate and load-bearing:

| Step | Who does it |
|---|---|
| Plan HOT / COLD / FOLDED + cheap-serialise the checkpoint | `/ultraclear` (this skill) |
| Actually clear the live window | the native **`/clear`** (you run it) |
| Warm-reload the HOT manifest into the fresh session | session start |

A checkpoint is a **SERIALIZE, not an LLM summary** — it copies the HOT turns
verbatim and writes down the COLD/FOLDED ids. It costs ~0 model tokens to make,
which is the whole point.

## The flow

1. **Plan the partition.** Feed the session's turns + the holistic-kernel token
   estimate into `lib/ultraclear.py::plan_ultraclear`. It returns the HOT / COLD
   / FOLDED split and the econ (including whether it's worth doing at all).
2. **Checkpoint (cheap serialise).** Call
   `lib/ultraclear_session.py::checkpoint(turns, kernel_tokens, now=<stamp>)`.
   It returns a small JSON dict: HOT turns verbatim, COLD ref-ids, FOLDED ids,
   and the econ. Write it to `state/ultraclear/<session>.json`. (Pass `now`
   explicitly — the helper never reads the clock, so checkpoints are
   deterministic and testable.)
3. **Clear.** You run the native **`/clear`** (or start a fresh session). The
   COLD drawer and the FOLDED file-cabinet stay on disk, untouched.
4. **Warm-reload.** At session start, `reload_manifest(checkpoint)` returns the
   HOT turns — only the small bundle — to load back in. A cache keepalive keeps
   it warm; any COLD turn pages back **losslessly** by its ref-id the moment a
   later turn references it.

```bash
# Plan + inspect the econ (is it even worth it?)
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python - <<'PY'
from lib.ultraclear import plan_ultraclear
from lib.ultraclear_capture import build_turns_from_transcript

# A real session JSONL lives at ~/.claude/projects/<slug>/<session-id>.jsonl
turns, kernel_tokens = build_turns_from_transcript("<path to session .jsonl>")
plan = plan_ultraclear(turns, kernel_tokens=kernel_tokens)
print(plan.is_noop, plan.net_savings, plan.manifest_tokens)
PY
```

## When to use

- The window is large and you'd otherwise hit `/compact` or start fresh.
- You want to clear the desk **without losing the details** a summary would drop.
- You're between waves of long work and want a cheap, lossless checkpoint.

## When NOT to use

- Small window → `is_noop` is `True`; just keep going.
- You explicitly want a short human-readable digest of the session → that's a
  summary job (`/recall`, `/holistic`), not `/ultraclear`.

## What's in scope

- **In scope:** the partition planner (`lib/ultraclear.py`) and the
  serialise/reload helper (`lib/ultraclear_session.py`) — both pure, stdlib-only,
  network-free, deterministic.
- **Also in scope, and shipped:** the transcript adapter
  (`lib/ultraclear_capture.py`). `build_turns_from_transcript(path)` reads a live
  Claude Code session JSONL into ordered `Turn` objects and returns
  `(turns, kernel_tokens)`, which hand straight to `plan_ultraclear`. It reads
  from disk but is otherwise stdlib-only, network-free and deterministic. Two
  hooks drive it — `hooks/ultraclear-checkpoint-stop.py` on Stop and
  `hooks/ultraclear-reload-sessionstart.py` at session start — both registered
  in `settings.json`. Injecting your own `Turn`s still works exactly as before:
  the adapter is an additional front-end, and it changes none of the guarantees
  above.

## See also

- `/holistic` — the long-term memory kernel that FOLDED turns live in.
- `/save` — persist every memory subsystem (a checkpoint is lighter and
  window-focused; `/save` is the full durable persist).
- `/scrollback-and-stash` — cosmetic scrollback clear + JSONL stash; complements
  `/ultraclear`'s context-window focus.
