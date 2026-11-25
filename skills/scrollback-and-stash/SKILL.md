---
name: scrollback-and-stash
description: Clear terminal scrollback (cosmetic) + snapshot current session JSONL into compressed indexed stash. Future context-fetches route through 6-tier LRU instead of full JSONL reload. Use when terminal feels cluttered, before /save at 85% gate, or to pre-warm next session.
---

# /scrollback-and-stash

Clears terminal display (cosmetic — for human eyes only) AND stashes the
current session JSONL into a compressed, indexed substrate. Future context
fetches route through a 6-tier LRU ladder (`lib/scrollback_lru.py`) instead
of re-loading the full JSONL (12-13MB per session).

## When to Use

- Terminal scrollback feels cluttered (UX cleanup)
- About to hit the 85% context gate (pre-emptive stash)
- Want next-session warm-start to be fast
- Just finished a milestone and want to pin the work into KONO-adjacent storage

## What It Does

1. `clear && printf '\e[3J'` — cosmetic terminal clear (NOT context-clear; see R0 §2)
2. Locate current session JSONL: `~/.claude/projects/<slug>/<uuid>.jsonl`
3. Sanitise via `lib/secrets_redact.py` (when implemented — currently MVP stash without sanitise; do NOT enable on sessions with secrets)
4. Fold-compress via `lib/fold_compress.fold()` (Banach SHA-256 round-trip, depth=11)
5. Index in `~/.claude/stash/index.db` with embedding key
6. Auto-prune (cold-tier archive at 30 days)
7. Print summary: `Stashed N turns, ~M tokens equivalent, retrievable via /recall`

## Runnable driver — `stash_session.py`

Steps 4-7 are executable via `skills/scrollback-and-stash/stash_session.py`
(step 1 is cosmetic-only, step 3 aspirational — see below):

```bash
python3 skills/scrollback-and-stash/stash_session.py                 # most-recent session for this cwd
python3 skills/scrollback-and-stash/stash_session.py <uuid|path> ... # named session(s)
```

**Non-verbatim by construction (safety).** Because sanitise (step 3) is NOT
wired and GRIP session JSONLs carry secret-shaped lines, the driver stashes a
*structural digest* (uuid / record + role counts / token-equiv / fold metrics /
sha) — lossy, non-recoverable — never the raw transcript. The `index.db` is
git-ignored (`*.db`) and local-only. Step 1's `clear` is a no-op on context
(R0 below) and is intentionally NOT performed by the driver.

## Retrieval Ladder (`lib/scrollback_lru.py`)

Future prompts hit the LRU ladder (Rule 0 retrieval discipline applied to
context-fetch):

```
fetch(query, depth=6) →
  tier-0 mem        (~0 tok    <1ms)
  tier-1 kono-disk  (~50 tok    5ms)
  tier-2 grip-first (~200 tok  200ms)
  tier-3 conv-search (~1k tok  800ms)
  tier-4 cc-find    (~2k tok   1.5s)
  tier-5 rg         (~5k tok   3.0s)
  baseline: full JSONL re-load ~88k tok (the loss avoided)
```

Each tier exits on `confidence ≥ 0.7`; misses escalate. EV/call (warm) is
~472 tokens vs 88k baseline → ~186× saving IF hit-rate distribution holds.
**H-LRU-1 verifies the distribution; if false, design needs revision.**

## R0 Verdict (read this — it's load-bearing)

`clear && printf '\e[3J'` is **cosmetic-only**. Claude Code reads the JSONL
session transcript on disk, NOT the terminal emulator's scrollback buffer.
The terminal clear is purely for human visual de-clutter. **Real token
saving comes from the JSONL stash + LRU indexed retrieval, NOT from `clear`.**

Falsifier H-SCROLL-CC-1 verifies via `/context` A/B (token count before vs
after clear — must be unchanged). Deadline 2026-06-07.

## Hypotheses (registered 2026-05-24)

- **H-SCROLL-CC-1** (deadline 2026-06-07): `clear` does NOT change CC context tokens.
- **H-SCROLL-1** (deadline 2026-06-24): skill reduces median session start-up tokens ≥50%.
- **H-LRU-1** (deadline 2026-06-24): tier-0+1 hit-rate ≥60% in warm cache.
- **H-LRU-2** (deadline 2026-06-24): string-hash mem-key hit-rate ≥30% (gates embedding upgrade).
- **H-STASH-1** (deadline 2026-07-23): auto-prune keeps stash dir ≤200MB after 60 days.

## Companion Artefacts

- `lib/fold_compress.py` — Banach fold operator (H-FOLD-COMP-1/2/3 deadline 2026-06-21)
- `plans/scrollback-and-stash-2026-05-24.md` — Full design + falsification clause
- `plans/fold-compress-2026-05-24.md` — Compressor design

## Pre-idle Prefix Compaction (`lib/prefix_compaction.py`)

**What it does:** Before going idle, compact the live conversation prefix so
the unavoidable post-idle cache-miss reprocess costs ~≤20k tokens instead of
~88k (≥4x cheaper).

**Honest framing — read this:**
- Prefix compaction does **NOT** prevent the Anthropic prompt-cache miss
  (5-min sliding TTL evicts on idle; that is a server-side fact).
- Prefix compaction does **NOT** warm the server-side cache.
- It only makes the unavoidable miss **cheaper** by producing a compact prefix.
- The keepalive (separate concern) is what would prevent the miss.

**API:**

```python
from lib.prefix_compaction import compact_prefix, CompactionResult

result: CompactionResult = compact_prefix(
    messages,                 # list[dict] — full conversation so far
    system="...",             # optional system prompt (always kept verbatim)
    target_tokens=20_000,     # soft target for compacted size
    keep_recent_turns=6,      # last N messages kept BYTE-IDENTICAL
)
# result.ratio         — original_tokens / compacted_tokens (≥4.0 target)
# result.compacted     — the compacted message list to submit
# result.original_tokens, result.compacted_tokens
# result.kept_verbatim_count
```

**Strategy (deterministic core, no LLM call by default):**
1. System message → always kept verbatim (first).
2. Last `keep_recent_turns` messages → kept BYTE-IDENTICAL (identity-preserved).
3. Older messages → role-tagged head/tail digest (120+80 chars, deterministic).
4. Optional `llm_summariser` callback may replace the digest for richer
   summaries — defaults to `None` (network-free, always reproducible).

**Token estimate:** `chars / 4` heuristic (OpenAI/Anthropic rule-of-thumb; ±20%).
Not for billing; good enough for a cost-reduction gate.

**Hypothesis H-COMPACT-1** (deadline 2026-06-30):
`compact_prefix` yields ≥4x token reduction on a representative ~88k-token
prefix while preserving system + last K turns byte-identical.
Falsified if: (a) measured ratio < 4.0 on the reference 88k fixture, OR
(b) any byte in a preserved message differs from the input.

## Self-Falsification

This skill is wrong if, over N≥20 sessions:
- H-LRU-1 falsifies → cost advantage gone; revert to direct JSONL load
- H-SCROLL-1 falsifies → cosmetic-only; rename to `/clear-terminal`
- Sanitisation leaks a secret in stash → immediate disable + rotation
- Cold-archive tier never accessed → collapse to single warm tier
