---
name: batch-edit-enforcer
description: Enforces batch MultiEdit operations instead of multiple individual Edit calls
status: Active
effort: low
auto_generated: true
generation_date: 2025-12-08T23:26:00Z
source_pattern: individual-edits
version: 2.0.0
triggers:
  - 3+ sequential Edit calls to the same file
  - Multiple Edit calls that could be batched
  - Pattern of individual file modifications
---

# Batch Edit Enforcer

**Purpose:** Enforce batch editing to reduce token overhead.

**Severity:** major
**Token Impact:** 500 tokens per individual Edit vs 800 tokens for batch

---

## Problem

Making multiple individual Edit calls instead of batching wastes tokens:

1. **Token overhead** - Each Edit call costs ~500 tokens in tool call/result
2. **Lost context** - Individual edits don't show the full picture
3. **Slower execution** - Multiple round trips vs single operation
4. **Risk of conflicts** - Interleaved edits can cause merge issues

### Cost Comparison

| Approach | 6 File Changes | Token Cost |
|----------|---------------|------------|
| 6 individual Edits | 6 × 500 = 3000 | HIGH |
| 1 batch operation | ~800 | LOW |

---

## Solution

**Use batch operations for 3+ related file changes.**

### When to Batch

- Modifying 3+ files with related changes
- Applying the same pattern across multiple files
- Refactoring that touches multiple locations
- Renaming that affects imports

### Implementation Methods

#### Method 1: Parallel Tool Calls

Make multiple Edit calls in a single assistant message:

```text
[Single message with multiple tool calls]
Edit file1.ts: change X
Edit file2.ts: change X
Edit file3.ts: change X
```

#### Method 2: CLI Bulk Operations

Use shell commands for pattern-based changes:

```bash
# Replace across files with rg + sed
rg -l "oldPattern" --type ts | xargs sed -i '' 's/oldPattern/newPattern/g'

# Or with fd
fd -e ts -x sed -i '' 's/oldPattern/newPattern/g' {}
```

#### Method 3: Single MultiEdit Tool

When available, use MultiEdit for atomic batch operations.

---

## Decision Tree

```text
Making file changes?
├── 1-2 files → Individual Edit is fine
├── 3+ files, SAME change → CLI bulk operation (rg | xargs sed)
├── 3+ files, DIFFERENT changes → Parallel Edit calls in one message
└── Complex refactor → Consider staged approach with clear commits
```

---

## Examples

### Before (Anti-Pattern)

```text
# 6 separate messages with individual Edits
Message 1: Edit src/a.ts - add import
Message 2: Edit src/b.ts - add import
Message 3: Edit src/c.ts - add import
Message 4: Edit src/d.ts - add import
Message 5: Edit src/e.ts - add import
Message 6: Edit src/f.ts - add import
```

### After (Correct - Parallel)

```text
# Single message with all Edits
Edit src/a.ts - add import
Edit src/b.ts - add import
Edit src/c.ts - add import
Edit src/d.ts - add import
Edit src/e.ts - add import
Edit src/f.ts - add import
```

### After (Correct - CLI Bulk)

```bash
# Single CLI command
rg -l "from 'old'" --type ts | xargs sed -i '' "s/from 'old'/from 'new'/g"
```

---

## Implementation

### Context-Gate Hook (Gate 7 variant)

Monitors for sequential Edit patterns and suggests batching.

### Detection Regex

```regex
Edit\(|Edit tool
```

Threshold: 6 occurrences in recent history triggers this skill.

---

## Integration

This skill is enforced via:

1. **Pattern Detection** - `./optim.sh detect` (MONITORING)
2. **efficiency-rules.md Rule 3** - Implementation Directness

### efficiency-rules.md Reference

```markdown
## Rule 3: Implementation Directness

ALWAYS choose the most direct path.

### Good Pattern
1. MultiEdit with all changes in one operation

### Exception
Only use intermediate scripts when:
- User explicitly requests it
- Operation is truly one-time and complex (>50 line regex)
```

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-26 | Regenerated with real content from RSI pipeline |
| 1.0.0 | 2025-12-08 | Auto-generated stub from pattern detection |

---


