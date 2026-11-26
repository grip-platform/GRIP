---
name: context-refresh
description: Systematically rebuild mental model of any repository when starting fresh sessions. Use when session starts, user says refresh context, or invokes /refresh-context. Eliminates cold start problem in <3500 tokens.
status: Active
effort: high
deprecated: true
superseded_by: /recall
version: 3.2.0
triggers:
  - /refresh-context
  - session start
  - "refresh context"
  - "understand repo"
---

# Context Refresh Skill

**Purpose:** Rapidly establish comprehensive project understanding at session start without excessive file re-reading.

**Token Budget:** <3500 tokens per refresh (dynamic: 2000-3450 based on complexity, typical: ~2650)

**Reference:** See [reference.md](./reference.md) for detailed scripts, templates, and examples.

---

## Core Principle

**New session = amnesia. This skill = instant recall with complete semantic understanding.**

Every Claude Code session starts with zero context. Without systematic refresh:

- Re-read same files 5+ times (wastes ~5k tokens)
- Miss critical git state (staged secrets, merge conflicts)
- Overlook recent session work (duplicate effort)
- Misunderstand architecture (wrong assumptions)
- **Miss the WHY** behind changes (only see WHAT changed)

### Multi-Source Semantic Understanding

1. **Session History** (`~/.claude/projects/`) - Reveals WHY changes were made
2. **Git Commit Messages** - Shows developer's summary of WHAT was intended
3. **Git Status + File Analysis** - Shows WHAT actually exists now

By synthesizing all three sources, we achieve complete understanding and can auto-update outdated CLAUDE.md documentation.

---

## Trigger Conditions

Activate when:

- User explicitly says: "refresh context", "understand repo"
- User invokes: `/refresh-context`
- Session starts and project CLAUDE.md exists
- User asks project-specific question without prior context

---

## 9-Step Discovery Protocol

### Step 0: Identity Foundation

**PARAMOUNT**: Acknowledge identity before building mental model.

**Token Cost**: 0 (internal acknowledgment only)

---

### Step 1: Git Reality Snapshot (DCI Pre-Injected)

**PRIORITY 1**: Git state is authoritative source of truth. Pre-injected below — skip tool calls.

**Branch:** !`git branch --show-current 2>/dev/null`
**All branches:**
!`git branch -v 2>/dev/null | head -10`

**Status:**
!`git status --short 2>/dev/null | head -20`

**Recent commits:**
!`git log --oneline -10 2>/dev/null`

**Change magnitude:**
!`git diff --stat HEAD 2>/dev/null | tail -5`

**Remote sync:**
!`git rev-list --left-right --count HEAD...@{upstream} 2>/dev/null | awk '{print "ahead: "$(1)", behind: "$(2)}' || echo "no upstream"`

Use the above pre-injected data instead of running these commands manually.

**Extract**:
- Branch name (semantic inference: feat/fix → activity type)
- Commit messages (developer intent)
- Staged/unstaged/untracked files

**Critical Checks**:
- 🚨 Staged secrets (.env, credentials) → ALERT USER
- ⚠️ Merge conflicts → ALERT USER
- ⚠️ Detached HEAD → ALERT USER

**Token Cost:** ~200-250

---

### Step 2: Session History Semantic Search

**CRITICAL**: Understand WHY changes were made.

**Actions**:
1. Locate project history: `~/.claude/projects/{encoded-path}/`
2. Search with `rg` for workflow patterns
3. Score confidence based on match count

**Pattern**: Use proven `rg` patterns (not complex jq queries):
```bash
rg -i "implement|build|create|feature|fix|refactor" \
  "$HISTORY_DIR"/*.jsonl --glob '!agent-*' -C 5 | head -200
```

**Confidence Scoring**:
- 5+ match blocks → 40%
- 2-4 match blocks → 25%
- 0-1 match blocks → 10%

**Token Cost:** ~250-350

---

### Step 3: Adaptive Semantic Analysis

**Algorithm**: Stop when understanding is clear.

| Level | When | Token Cost |
|-------|------|------------|
| A: Basic | >80% confidence from history+commits | ~250 |
| B: Infer | File names provide clues | ~400 |
| C: Preview | Still unclear, read key files | ~800 |

**Level A**: Count and categorize files by type
**Level B**: Infer purpose from naming conventions
**Level C**: Preview first 10 lines of key files

**Token Cost:** ~250-800 (adaptive)

---

### Step 4a: CLAUDE.md Read & Detect

**Compare CLAUDE.md "CURRENT STATE" against git reality.**

**Actions**:
1. Extract CURRENT STATE section
2. Compare documented branch vs actual
3. Compare documented phase vs inferred phase
4. Check staleness (>7 days old)

**Decision Gate**:
```text
IF (discrepancy found) AND (confidence > 80%)
THEN: Approve auto-update
ELSE: Flag for manual review
```

**Token Cost:** ~300-400

---

### Step 4b: Execute Auto-Update

**CRITICAL**: Actually execute the update (not just propose).

**Actions**:
1. Build new CURRENT STATE section
2. Create `CLAUDE.md.backup`
3. Execute `sed` to replace section
4. Verify update succeeded
5. Rollback on failure

**Token Cost:** ~200-300

---

### Step 5: Architecture Mapping

**Git-assisted architecture detection.**

```bash
# Framework detection
fd -H -t f -e json -e js -e ts . --max-depth 2 | rg "(next|vite|webpack).config"

# File count
git ls-files | grep -E '\.(tsx?|jsx?)$' | wc -l

# Entry points
ls -la src/ pages/ app/ 2>/dev/null | head -15
```

**Token Cost:** ~200

---

### Step 6: Environment Verification

**Verify build config, detect secrets.**

```bash
ls -la *.config.{js,ts,mjs} tsconfig.json .env 2>/dev/null
git diff --cached --name-only | rg -i "(\.env|secret|credential)"
```

**Token Cost:** ~100

---

### Step 7: Mental Model Construction

**Synthesize all sources into coherent understanding.**

1. **Start with Session History (WHY)**: User intent, decisions
2. **Layer Git Commits (WHAT INTENDED)**: Developer summary
3. **Confirm with Git Status (WHAT EXISTS)**: Actual changes
4. **Validate with CLAUDE.md (CONTEXT)**: Project purpose

**Confidence Validation**:
- Merge commits detected → Reduce by 10%
- Detached HEAD → Reduce by 15%
- Excessive untracked (>50) → Warning

**Token Cost:** ~350

---

### Step 8: Enhanced Briefing

**Deliver comprehensive summary with semantic understanding.**

Output includes:
- Project identity and tech stack
- CURRENT STATE (auto-updated)
- Session history context
- Git activity summary
- Semantic synthesis (WHY + WHAT + EXISTS)
- Risks & alerts
- Ready to code commands
- Confidence score

**Token Cost:** ~500

---

## Token Budget Summary

| Step | Typical | Notes |
|------|---------|-------|
| 1. Git Snapshot | 200 | Parallel commands |
| 2. Session History | 300 | rg-based search |
| 3. Semantic Analysis | 400 | Adaptive (A/B/C) |
| 4a. CLAUDE.md Detect | 350 | Comparison logic |
| 4b. Execute Update | 250 | Actual sed execution |
| 5. Architecture | 200 | Git-assisted |
| 6. Environment | 100 | Quick check |
| 7. Mental Model | 350 | Validation included |
| 8. Briefing | 500 | Full template |
| **TOTAL** | **2650** | Dynamic: 2000-3450 |

---

## Anti-Patterns

### Don't

- ❌ Skip git state check - staged secrets are CRITICAL
- ❌ Skip session history search - reveals WHY (crucial for understanding)
- ❌ Use complex jq queries - use proven rg patterns instead
- ❌ Read entire codebase - use adaptive analysis (stop at >80%)
- ❌ Use wrong history paths - correct: `~/.claude/projects/{encoded-path}/`
- ❌ Auto-update with low confidence - only update if >80%
- ❌ Write update templates without executing - EXECUTE THE UPDATE
- ❌ Check exit codes after pipes - check if output is empty instead
- ❌ Compute values you won't use - YAGNI

### Do

- ✅ Run all git commands in parallel
- ✅ Surface session history WHY context
- ✅ Use adaptive depth algorithm
- ✅ Auto-update CLAUDE.md when confident (>80%)
- ✅ Maintain dynamic token budget (2000-3500)

---

## Integration Points

### With KONO Memory Substrate

When `/refresh-context` runs, execute comprehensive KONO recall (10 results with spatial expansion):

```bash
python3 ~/.claude/lib/kono_substrate.py recall "<project name and context>" --limit 10
```

This provides broader recall than the lightweight prompt hook (3 results, no expansion).
Include recalled memories in the mental model construction (Step 7) and briefing (Step 8).

### With chat-history-search

- History stored per-project: `~/.claude/projects/{encoded-path}/{uuid}.jsonl`
- Timestamps are ISO 8601 strings
- Use `rg` for searching (not jq)

### With file-read-optimizer

- Context refresh: Initial bulk understanding
- File-read-optimizer: Prevents re-reads during session
- Combined impact: ~5k+ token savings per session

### With self-improvement-engine

Pattern detection for context-loss:
```json
{
  "pattern": "Read\\(CLAUDE\\.md\\).*Read\\(CLAUDE\\.md\\)",
  "skill_suggestion": "context-refresh"
}
```

---

## CLAUDE.md Auto-Update Protocol

### When to Update

```text
IF (git reality != CLAUDE.md CURRENT STATE)
   AND (confidence > 80%)
THEN: Auto-update CURRENT STATE section
```

### What Gets Updated

1. **CURRENT STATE Section** (always):
   - Current Phase (from session history + branch)
   - Current Branch (from git)
   - Active Work (from synthesis)
   - Recent Additions (from file categorization)
   - Last Updated timestamp

2. **PROJECT IDENTITY** (only if pivot detected):
   - Flag for manual review

---

## Critical Learnings

- **v3.1**: Uses proven rg patterns from chat-history-search, not fragile jq queries
- **v3.2**: **EXECUTES** auto-updates with sed (never write templates without executing)
- **v3.2**: Checks output emptiness, not pipe exit codes
- **v3.2**: Comprehensive error handling prevents skill crashes

---

## Reference Material

For detailed implementations, see [reference.md](./reference.md):

- Full bash scripts for each step
- Adaptive semantic analysis levels (A/B/C)
- Complete briefing template
- Example workflows (3 scenarios)
- Full changelog history

---

**Skill Status:** ✅ Active (v3.2 - Robust & Production-Ready)
**Maintainer:** V>>
**Last Updated:** 2025-11-14


