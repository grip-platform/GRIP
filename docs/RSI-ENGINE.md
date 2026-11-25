# RSI Engine Architecture

Recursive Self-Improvement engine for GRIP. Detects usage patterns, tracks them cross-session, and generates skills when patterns reach statistical significance.

## Why RSI Exists

Systems that don't learn from their own usage stagnate. Manual improvement requires a human to notice a pattern, decide it matters, and write a fix. RSI automates the first two steps and assists the third.

GRIP's approach: **detect patterns -> track cross-session -> generate skills -> validate quality -> deploy**.

## Core Principle: YSH (You Should Have)

The 3x threshold rule prevents premature abstraction:

1. Pattern detected once: logged, no action
2. Pattern detected twice: tracked, still no action
3. Pattern detected three times: **abstraction is now justified** - generate a skill

Cross-session tracking in `pattern-counts.json` ensures the threshold is meaningful. A pattern hitting 3x across separate sessions represents real, recurring friction - not a one-off anomaly.

## 4-Layer Architecture

```
Layer 3: Meta-Optimization
  improvement-analyzer.py heatmap | optim.sh optimize
  "Which components need the most attention?"

Layer 2: Skill Generation
  optim.sh generate | optim.sh auto-generate
  Quality gates: coherence, novelty, 70% minimum score

Layer 1: Pattern Detection
  patterns.json (31 definitions) | optim.sh detect | periodic-scanner.py
  "What anti-patterns keep recurring?"

Layer 0: Data Collection
  rsi-auto-collector.py | rsi-posttool-hook.py | operation-counter.py
  "What happened during this session?"
```

Each layer feeds the one above it. Layer 0 produces raw data, Layer 1 finds patterns in that data, Layer 2 generates fixes for confirmed patterns, Layer 3 optimizes the system itself.

## Data Flow

```
Tool Calls (every tool invocation)
    |
    v
+-------------------+     +---------------------+
| operation-counter  |     | rsi-posttool-hook   |
| (PreToolUse)       |     | (PostToolUse)       |
| - count ops        |     | - detect commits    |
| - emit forge gates |     | - detect GRIP edits |
| - trigger syncs    |     | - detect skills     |
+--------+----------+     +----------+----------+
         |                            |
         |    calls in background     |
         |         +------------------+
         |         |
         v         v
+------------------------+
| rsi-auto-collector.py  |
| - on-commit            |
| - on-edit              |
| - on-event             |
| - on-session-end       |
| - sync-metrics         |
+---------+--------------+
          |
          | writes to
          v
+-------------------+     +--------------------+
| improvement-      |     | metrics.jsonl      |
| log.jsonl         |     | (events, syncs)    |
+--------+----------+     +---------+----------+
         |                          |
         +------------+-------------+
                      |
                      v
          +-----------------------+
          | optim.sh detect       |
          | periodic-scanner.py   |
          +-----------+-----------+
                      |
                      v
          +------------------------+
          | pattern-counts.json    |
          | (cross-session counts) |
          +-----------+------------+
                      |
                      | count >= 3
                      v
          +------------------------+
          | optim.sh generate      |
          | Quality gates:         |
          |  - coherence (dict+ng) |
          |  - novelty (embedding) |
          |  - score >= 70%        |
          +-----------+------------+
                      |
                      v
          +------------------------+
          | New skill deployed     |
          | to skills/ directory   |
          +------------------------+
```

## Files Reference

| File | Layer | Purpose |
|------|-------|---------|
| `lib/rsi_auto_collector.py` | 0 | Central automation - logs commits, edits, events, session-end sweeps |
| `hooks/rsi-posttool-hook.py` | 0 | PostToolUse detection - commits, GRIP edits, skill invocations |
| `hooks/operation-counter.py` | 0 | PreToolUse counter - forge gates, sync triggers, heatmap checks |
| `lib/seed_improvement_log.py` | 0 | One-time seeder - backfills improvement-log from git history |
| `patterns.json` | 1 | 31 pattern definitions with regex, thresholds, severity |
| `pattern-counts.json` | 1 | Cross-session tracking - count per pattern, session list |
| `lib/periodic_scanner.py` | 1 | Lightweight pattern scan triggered at operation thresholds |
| `optim.sh` | 1-3 | Main engine (2100+ lines) - detect, generate, optimize, audit |
| `lib/improvement_analyzer.py` | 3 | Heatmap analysis - identifies components needing attention |
| `improvement-log.jsonl` | data | Structured improvement entries (one per commit/edit) |
| `metrics.jsonl` | data | Event log (skill triggers, gate blocks, syncs, sweeps) |
| `skill-usage.jsonl` | data | Raw skill invocation log (synced to metrics by collector) |

## Data Formats

### improvement-log.jsonl

One entry per detected improvement (commit or GRIP edit):

```json
{
  "timestamp": "2026-02-07T10:00:00+00:00",
  "type": "efficiency_improvement",
  "component": "lib",
  "description": "add batch file read optimization",
  "trigger": "auto",
  "source": "auto_commit",
  "commit": "abc12345"
}
```

Types: `efficiency_improvement`, `skill_created`, `rule_added`, `bug_fix`, `refactor`, `documentation_update`, `gate_added`

### metrics.jsonl

Event log for all RSI activity:

```json
{
  "timestamp": "2026-02-07T10:00:00+00:00",
  "event": "skills_triggered",
  "skill": "mode",
  "trigger_source": "user",
  "source": "auto_sync"
}
```

Event types: `improvement_logged`, `skills_triggered`, `gate_effectiveness`, `session_end_sweep`, `rsi_auto_gen_ready`, `patterns_detected`

### pattern-counts.json

Cross-session pattern tracking:

```json
{
  "_meta": {
    "description": "Cross-session pattern tracking for RSI auto-generation",
    "version": "1.0.0",
    "threshold": 3
  },
  "repeated-file-reads": {
    "count": 47,
    "last_seen": "2026-02-07T15:17:51Z",
    "sessions": ["1769815672", "1769816315"],
    "skill_created": true,
    "skill_created_at": "2026-01-25T23:23:41Z"
  }
}
```

### patterns.json

Pattern definitions for detection:

```json
{
  "repeated-file-reads": {
    "description": "Reading same file multiple times without user edits",
    "regex": "pattern here",
    "threshold": 3,
    "severity": "major",
    "points": 10,
    "impact_per_occurrence": "500-2000 tokens wasted",
    "remediation": "Check conversation buffer before Read.",
    "skill_suggestion": "file-read-optimizer"
  }
}
```

## Quality Gates

Skills generated by the RSI engine pass through 5 gates before deployment:

| Gate | Mechanism | Purpose |
|------|-----------|---------|
| 3x pattern threshold | `pattern-counts.json` count >= 3 | Prevent premature abstraction |
| Coherence gate | Dictionary + n-gram scoring | Reject garbled or nonsensical content |
| Novelty gate | Embedding distance from existing skills | Prevent duplicate skills |
| Quality score | Composite >= 70% | Minimum quality bar |
| Human approval | AskUserQuestion prompt | Final review before deployment |

## Automation Integration Points

| Trigger | Hook/Script | Action |
|---------|-------------|--------|
| Session start | `hooks/startup/checks.sh` | Check `pattern-counts.json` for 3x patterns, run heatmap |
| Every tool call | `hooks/operation-counter.py` (PreToolUse) | Increment counters, check forge thresholds |
| After tool call | `hooks/rsi-posttool-hook.py` (PostToolUse) | Detect commits, GRIP edits, skill invocations |
| Every 50 ops | `operation-counter.py` | Trigger metrics sync + heatmap check |
| Session end | `hooks/session-end.sh` | Run collector `on-session-end` + `sync-metrics` |

## Configuration

| Parameter | Value | Reason |
|-----------|-------|--------|
| Minimum scan window | 24 hours | Short windows produce statistically insignificant data |
| Debounce window | 5 seconds | Prevents noisy duplicate events for same-type actions |
| Pattern threshold | 3x (YSH) | Proven pattern count before skill generation |
| Quality minimum | 70% | Composite score from coherence + novelty + structure |
| Heatmap check interval | Every 50 operations | Balance between awareness and overhead |
| Metrics sync interval | Every 50 operations + session end | Keep metrics current without per-tool overhead |

## Timeouts

| Command | Timeout | Reason |
|---------|---------|--------|
| `optim.sh audit` | 300s (5 min) | Scans all session history |
| `optim.sh cycle` | 600s (10 min) | Full detect + generate + optimize |
| `optim.sh detect` | 180s (3 min) | Pattern detection across history |
| PostToolUse hook | 10s | Must not delay tool execution |
| Session-end sweep | 10s | Must not block session exit |
| Metrics sync | 5s | Background, non-critical |
