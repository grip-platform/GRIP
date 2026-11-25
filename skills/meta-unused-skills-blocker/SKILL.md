---
name: meta-unused-skills-blocker
description: Detect and archive skills with zero usage to reduce context bloat
status: Active
effort: medium
auto_generated: true
generation_date: 2026-02-14T17:20:34Z
source_pattern: meta-unused_skills
version: 1.1.0
triggers:
  - meta-unused_skills pattern detected
---

# Meta Unused Skills Blocker

**Purpose:** Detect and archive skills with zero usage to reduce context bloat

**Severity:** Low
**Token Impact:** High (500-2000 tokens per unused skill loaded)

---

## Problem

RSI-generated skills with low usage rate (0.00) accumulate in the skills directory. Each unused skill:
- Consumes 500-2000 tokens when discovered by glob/grep
- Adds noise to skill search results
- Dilutes signal in skill analytics

### Anti-Pattern

This pattern was detected by the RSI engine as a recurring inefficiency.

**Impact:** 10+ unused skills = 5k-20k wasted tokens per session

---

## Solution

Archive skills with zero usage after 30 days:

```bash
# Run monthly cleanup
python3 ~/.claude/lib/skill_analytics.py archive-unused --min-age-days 30
```

Skills moved to `~/.claude/skills/.archived/` with usage metadata preserved.

### Correct Pattern

1. Monitor skill usage via `skill-usage.jsonl`
2. Auto-archive skills with 0 invocations after 30 days
3. Keep skills with at least 1 usage in last 90 days
4. Manual restore: `mv ~/.claude/skills/.archived/skill-name ~/.claude/skills/`

---

## Detection

### Source Pattern

Pattern: `meta-unused_skills`
Command: `./optim.sh detect`

### Thresholds

- 3x occurrences triggers YSH auto-generation
- 0 invocations + 30 days age = archive candidate

---

## Implementation

This skill is enforced via:

1. **Pattern Detection** - `./optim.sh detect`
2. **RSI Pipeline** - Auto-generated on 2026-02-14T17:20:34Z
3. **Skill Analytics** - Usage tracking in `skill-usage.jsonl`

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-14T17:20:34Z | Auto-generated from RSI pattern detection |
| 1.1.0 | 2026-02-14 | Completed with archive strategy and token impact |
