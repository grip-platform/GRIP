# /learn

RSI learning loop: detect patterns, consolidate insights, generate rules.

## Usage

```
/learn              # Full pipeline (4 steps)
/learn consolidate  # Step 1 only
/learn candidates   # Step 2 only
/learn rules        # Step 3 only
/learn heatmap      # Step 4 only
```

## Pipeline

### Step 1: Consolidate and Ingest

```bash
python3 lib/learning_detector.py consolidate
python3 lib/learning_detector.py ingest
```

Merges 3+ insights with >50% Jaccard overlap. Ingests pending agent insights from `learning/insights.jsonl`.

### Step 2: Scan and List Candidates

```bash
python3 lib/learning_detector.py scan
python3 lib/learning_detector.py list
```

**AskUserQuestion** if candidates exist.

### Step 3: Generate Rules

```bash
python3 lib/learning_detector.py generate-rules
```

Regenerates `rules/learned-insights.md` from approved candidates and proven insights (seen 3+ times). Auto-loaded every session. Capped at 30 rules.

### Step 4: Heatmap Check

```bash
python3 lib/improvement_analyzer.py check
```

**AskUserQuestion** if `[IMPROVEMENT HEATMAP]` signal.

## Outputs

- `rules/learned-insights.md` (auto-loaded every session)
- `skills/*/SKILL.md` (from approved candidates)

## Before generating anything new: `/mine-patterns`

This pipeline generates *new* candidates. Run `/mine-patterns` first, because most of what
GRIP needs is already written down and simply cannot be reached.

Measured 2026-09-01: **221 feedback records exist and 190 of them (86%) have no pointer in
`MEMORY.md`** — and the whole corpus is 343 unreachable of 451, the feedback slice being a
subset with a worse share than the rest. A dispatched subagent inherits `MEMORY.md` and the safety core and nothing
else (`rules/binding-memory-records.md`), so those lessons cannot fire. Generating more
candidates without closing that gap adds to a pile that is already mostly unreachable.

`/mine-patterns` splits findings three ways, and only one of them is new work here:

- **A** genuinely new → a candidate for this pipeline.
- **B** recurs *despite* an existing memory or rule → the fix is **mechanical**, never
  another rule. Writing it down is what already failed. This is YSH at N=2.
- **C** learned but unreachable → **compete for a scarce slot**. Computed free, with no
  model at all, but the obvious fix is not available: `lib/unified_memory_brief.py:608` caps
  the index at `MEMORY_LINE_LIMIT = 195` lines (pre-existing platform behaviour, not
  enforced by the mining tooling) and 137 are already used, so there is room for
  about **58 pointers against 343 unreachable records**. Reachability is rival — past the
  cap, a pointer added is a pointer evicted. The useful output is a ranking of which
  lessons have earned a slot, not a pile of pointers that cannot fit.

**This is the reason to run it before generating.** Adding candidates to a corpus whose
index is 70% full, where most existing records already cannot be reached, buys less than
promoting the right ones into the slots that remain.

It counts **distinct sessions** rather than observations, so a candidate arriving here with
N=2 has two conversations behind it, not two mentions in one.

## Related Skills

- `autonomous-learning`
- `self-improvement-engine`
- `insights-pipeline`
- `mine-patterns` (session/history mining — run before generating candidates)
