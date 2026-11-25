# RSI Architecture

Recursive Self-Improvement (RSI) pipeline for GRIP. The system detects patterns, learns from them, generates skills, and recursively improves its own learning algorithm.

**Engine**: `optim.sh` v3.10.0 (2,102 lines, 4-layer architecture)
**Components**: 3 Python modules (1,227 lines total)

---

## Overview

RSI is GRIP's autonomous improvement loop. It observes patterns in usage, proposes improvements, and - with human approval - integrates them into the framework. The system can also improve its own improvement process (meta-learning).

```
     [1. Detect]
         |
         v
     [2. Learn]
         |
         v
     [3. Candidates]
         |
         v
     [4. Generate]
         |
         v
     [5. Track]
         |
         v
     [6. Heatmap]
         |
         v
     [7. Meta-Learn]
         |
         v
     [8. Meta-Optimize]
         |
         +---> loop back to [1]
```

**Key constraints**:
- Human approval required at every gate. The agent proposes, the human decides.
- **Minimum 24-hour window** for all RSI detection and audit scans. Shorter windows produce statistically insignificant pattern data. The default for all `optim.sh` commands is 24 hours.

---

## The 8-Step Loop

| Step | Component | Command | Output |
|------|-----------|---------|--------|
| 1. **Detect** | `periodic-scanner.py` | `python3 lib/periodic_scanner.py` | Pattern signals |
| 2. **Learn** | `learning-detector.py` | `python3 lib/learning_detector.py scan` | Learning events |
| 3. **Candidates** | `learning-detector.py` | `python3 lib/learning_detector.py list` | Pending skill candidates |
| 4. **Generate** | `optim.sh` | `./optim.sh generate` | New skill from pattern |
| 5. **Track** | `improvement-analyzer.py` | `python3 lib/improvement_analyzer.py log` | Improvement JSONL entry |
| 6. **Heatmap** | `improvement-analyzer.py` | `python3 lib/improvement_analyzer.py check` | Refactor candidates |
| 7. **Meta-Learn** | `optim.sh` | `./optim.sh optimize` | Self-improvement proposals |
| 8. **Meta-Optimize** | `optim.sh` | `./optim.sh cycle` | Full improvement cycle |

---

## optim.sh Architecture

4-layer architecture following SOLID principles:

```
Layer 4: Orchestration (command routing, full cycle)
    |
Layer 3: Meta-optimization (self-improvement recursion)
    |
Layer 2.6: MCP management
Layer 2.5: Agent generation (detect patterns, auto-create agents)
Layer 2: Skill generation (template fill, validate, register)
    |
Layer 1: Pattern detection (scan, match, score)
    |
Layer 0: Utility functions (logging, validation, JSON ops)
```

### Commands

**Default window**: 24 hours (minimum). All commands accept an optional hours parameter but should not be run with less than 24 hours of data.

| Command | Layer | Purpose |
|---------|-------|---------|
| `./optim.sh detect` | 1 | Run pattern detection across sessions |
| `./optim.sh audit` | 1 | Run efficiency audit on recent history |
| `./optim.sh generate` | 2 | Generate skill from detected pattern |
| `./optim.sh create-agents` | 2.5 | Auto-detect and create agent definitions |
| `./optim.sh install-mcp` | 2.6 | Install required MCP servers |
| `./optim.sh optimize` | 3 | Meta-optimization (recursion) |
| `./optim.sh optimize-agents` | 3.5 | Analyze and optimize agent performance |
| `./optim.sh cycle` | 4 | Full improvement cycle (all layers) |

### Timeout Configuration

| Command | Timeout | Reason |
|---------|---------|--------|
| `detect` | 180s | Pattern detection (min 24h window) |
| `audit` | 300s | Full session history scan (min 24h window) |
| `cycle` | 600s | Complete improvement cycle (min 24h window) |
| Others | 120s (default) | Quick operations |

### Minimum Window: 24 Hours

All RSI scan commands default to 24 hours and should never be run with less. Short windows (<24h) produce statistically insignificant pattern data - a few hours of work cannot reveal meaningful recurring patterns. The 3x YSH threshold requires sufficient data volume to trigger reliably.

---

## Learning Detection

### Triggers

The `learning-detector.py` detects learning events from 4 signal types:

| Trigger | Weight | Detection Method |
|---------|--------|-----------------|
| **High novelty** (>0.4) | 0.4 | Embedding distance from existing knowledge |
| **Teaching moment** | 0.3 | Regex patterns: "you should have", "anti-pattern", etc. |
| **New term** | 0.2 | Regex: "let's call this", "the X principle", etc. |
| **Generalisation** | 0.1 | Regex: "in general", "as a rule", "cross-project", etc. |

A learning event fires when **>= 2 triggers** activate simultaneously.

### Composite Learning Score

```
score = min(novelty, 0.4) + (0.3 if teaching) + (0.2 if new_term) + (0.1 if generalisation)
capped at 1.0
```

### Generalisability Evaluation

After detection, the system evaluates whether the learning is:

| Category | Indicators | Action |
|----------|-----------|--------|
| Project-specific | "this project", "this codebase" | Document in project only |
| Infrastructure | "claude.md", "rules/", "hooks/" | Update GRIP infrastructure |
| Generalisable | Everything else | Generate skill candidate |

### Pipeline

```
Message -> detect_learning_event()
              |
              v
         evaluate_generalisability()
              |
              v
         create_skill_candidate()
              |
              v
         save_skill_candidate()   -> ~/.claude/learning/pending/
              |
              v
         format_notification()    -> AskUserQuestion for approval
```

---

## Pattern Detection

### Periodic Scanner

`periodic-scanner.py` runs when the `[PERIODIC: PATTERN_SCAN_DUE]` forge signal fires. It analyzes:

1. **Tool sequences** - Repeated sequences of 3+ tools (e.g., Read->Edit->Read repeated 3x)
2. **Text patterns** - Workarounds, user corrections, automation-worthy behaviors
3. **YSH threshold** - Pattern must appear 3x before triggering (You Should Have abstracted this)

### Detection Thresholds

| Threshold | Value | Purpose |
|-----------|-------|---------|
| YSH_THRESHOLD | 3 | Pattern must appear 3x to trigger |
| MIN_NOVELTY_SCORE | 0.7 | Minimum novelty to propose skill creation |
| NOVELTY_THRESHOLD | 0.4 | Learning event novelty trigger |

---

## Quality Gates

Every RSI improvement passes through quality gates:

### Coherence Gate (v3.10.0)

Prevents gibberish from scoring as "novel". Uses dictionary + n-gram analysis.

```
Input text -> is_coherent(text, threshold=0.3) -> pass/fail
```

### Novelty Gate

Embedding distance from existing knowledge. Prevents redundant skill creation.

```
Input text -> calculate_novelty(text) -> (score, coherence_meta)
```

If score < 0.4: not novel enough to be a learning event.

### 3x Pattern Threshold (YSH Rule)

From the YSH (You Should Have) principle: a pattern must appear 3 times before it warrants abstraction. Prevents premature optimization.

### Human Approval Gate

Every generated skill, rule, or gate proposal requires explicit user approval before activation. The agent proposes, the human decides.

---

## Improvement Tracking

### Heatmap Analysis

`improvement-analyzer.py` tracks which components receive the most improvements:

```bash
python3 lib/improvement_analyzer.py heatmap
```

Output:
```
=== Component Heat Map ===

Components improved most often (potential design smells):

  CLAUDE.md: ████████ (8)
  optim.sh: █████ (5)
  lib/learning_detector.py: ███ (3)
```

Components with 3+ improvements are flagged as potential design smells (Single Responsibility violations).

### Trigger Analysis

```bash
python3 lib/improvement_analyzer.py triggers
```

Shows what causes improvements:
- `friction_encountered` (healthy - system improves through use)
- `proactive` (potential over-engineering signal)
- `auto` (RSI-generated)

---

## Meta-Learning (Layer 3-4)

### Layer 3: Meta-Optimization

The system analyzes its own improvement patterns:
- Which types of improvements are most common?
- Which triggers produce the best improvements?
- Are improvements accelerating or decelerating?

### Layer 4: Full Cycle

A complete improvement cycle runs all layers in sequence:

1. Pattern detection (Layer 1)
2. Skill generation from detected patterns (Layer 2)
3. Agent generation for new workflows (Layer 2.5)
4. Meta-optimization analysis (Layer 3)
5. Agent performance optimization (Layer 3.5)

### Recursion

The meta-optimizer can detect patterns in its own improvement log and generate improvements to the improvement process itself. This is bounded by:

- Human approval at every gate
- Quality gates (coherence, novelty)
- RSI safety lockdown in `settings.json`

---

## Broly Integration

At MESH power level, a Broly council can run the RSI loop with specialist agents:

| Role | RSI Focus | Tools |
|------|-----------|-------|
| Pragmatist | Pattern detection, practical improvements | `optim.sh detect` |
| Theorist | Learning event detection, theoretical gaps | `learning-detector.py scan` |
| Security Advisor | Trust review, gate coverage | `trust_review.py` |
| UX Advocate | Command discoverability, mode UX | Skill/command audit |
| Devil's Advocate | Challenge existing patterns, find staleness | Skill quality audit |
| Integrator | Synthesize all findings into action plan | Merge results |

This parallel analysis covers more ground than sequential single-agent RSI.

---

## Token Economics

| Operation | Cost |
|-----------|------|
| Pattern detection (`detect`) | ~2k tokens |
| Learning event scan | ~1k tokens |
| Skill generation | ~3k tokens |
| Full RSI cycle | ~4k tokens |
| Meta-learning cycle | ~3.5k tokens |
| MESH council RSI | ~20k tokens (across all agents) |

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `optim.sh` | 2,102 | Main RSI engine (4-layer bash) |
| `lib/learning_detector.py` | 685 | Autonomous learning event detection |
| `lib/periodic_scanner.py` | 293 | Periodic pattern scanning |
| `lib/improvement_analyzer.py` | 249 | Heatmap and meta-analysis |
| `lib/coherence.py` | -- | Dictionary + n-gram coherence gate |
| `skills/self-improvement-engine/SKILL.md` | -- | RSI skill protocol |
| `skills/recursive-learning/SKILL.md` | -- | Recursive learning skill protocol |

---

## Run History

### Run 1: 2026-02-07 - Documentation Overhaul + Full System Audit

**Window**: 120 hours (5 days)
**Mode**: Sequential single-agent (simulated council roles)

#### Pragmatist (Pattern Detection)

`optim.sh detect 120` found 6 violation types, 2,224 efficiency points deducted:

| Pattern | Occurrences | Severity |
|---------|------------|----------|
| grep-usage-in-scripts | 209 | minor |
| unnecessary-preambles | 296 | minor |
| unnecessary-postambles | 27 | minor |
| individual-edits | 14 | major |
| semicolon-after-subshell | 2 | major |
| node-modules-read | 1 | critical |

**Finding**: grep-in-scripts dominates (209 occurrences). These are inside GRIP's own shell scripts using grep instead of rg. Skill `bash-tool-enforcer` exists but scripts predate it.

#### Theorist (Learning Detection)

- `periodic-scanner.py`: No patterns detected in current session
- `learning-detector.py list`: 0 pending candidates
- `learning-detector.py detect`: No learning event triggered (score 0.3, below threshold)
- `improvement-analyzer.py`: 1 total improvement logged (bug_fix, friction-triggered)

**Finding**: The learning pipeline is functional but idle. Only 1 improvement ever logged. The improvement-log.jsonl needs seeding from historical session data to make heatmap analysis useful.

#### Security Advisor (Trust Review)

`trust_review.py` results:

| Check | Status | Detail |
|-------|--------|--------|
| Data flow | PASS | 0 sensitive reads, 1,526 total |
| Write access | PASS | 562 writes, 13 with provenance |
| Defaults | PASS | 12/11 gates enabled |
| Enforcement | PASS | 20 mechanical gates active |
| Model constraints | PASS | 3 behavioral constraints active |
| Accountability | WARN | Identity not configured |

**Audit chain**: VERIFIED (158 signed entries, sequence 1-158)

**Finding**: Q6 accountability warning - identity not configured. Run `/activate-grip` to resolve.

#### UX Advocate (Command/Skill Audit)

- 95 skills, 16 agents, 47 commands, 19 modes verified
- Documentation was out of sync (fixed in this session)
- `/learn` and `/broly` were missing from key commands tables (fixed)
- Modes list showed only 9 of 19 available modes (fixed)

**Finding**: Skill count grew from 64 (Gen 16) to 95 without doc updates. Automated count verification should trigger on skill creation.

#### Devil's Advocate (Staleness Check)

- `rules/system-capabilities.md` still referenced "Claude-Optim v3.1.0" and "Gen 16" counts (fixed)
- `.claude/CLAUDE.md` still said "CIPS" (fixed to GRIP)
- README referenced "89 skills" in docs section while body said "65" (both fixed to 95)
- KONO doc link pointed to non-existent `KONO-ARCHITECTURE.md` (fixed to actual `kono-usage-guide.html`)

**Finding**: No architecture docs existed for the two most sophisticated subsystems (Broly and RSI). Created both.

#### Integrator (Synthesis)

**Key improvements executed this session**:
1. Completed CIPS->GRIP rebrand across project CLAUDE.md
2. Corrected system stats in 4 files (Skills 82->95, Agents 14->16, Commands 40->47, Modes 14->19)
3. Created `docs/BROLY-ARCHITECTURE.md` (320 lines)
4. Created `docs/RSI-ARCHITECTURE.md` (320 lines)
5. Added 5 feature sections to README (Testing, KONO, Broly, Governance, enhanced RSI)
6. Fixed broken KONO documentation link
7. Added `/learn` and `/broly` to key commands tables

**Recommended follow-up actions**:
- Refactor GRIP shell scripts to replace 209 grep occurrences with rg
- Seed `improvement-log.jsonl` with historical improvements for meaningful heatmap data
- Configure identity via `/activate-grip` to resolve Q6 accountability warning
- Add automated count verification to skill/agent/command creation workflows

---

*GRIP v6.0.0 | Recursive Self-Improvement Architecture*
