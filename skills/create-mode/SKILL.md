---
name: create-mode
description: Guides mode and skill creation with quality gates, wizard flows, and testing protocols. Use when /mode create is active and new modes or skills are being built.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode create
  - create mode active
integrates:
  - skill-creation-best-practices
  - creating-skills
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# Create Mode Orchestration

Routes creation tasks through appropriate wizards with quality gates and testing protocols. Orchestration layer only -- see `creating-skills/SKILL.md` for the skill wizard and `skill-creation-best-practices/SKILL.md` for quality scoring.

## Protocols

### 1. Mode vs Skill Detection

First determine what the user wants to create:

| Signal | Type | Route |
|--------|------|-------|
| "create a mode", "new mode" | Mode | Mode creation workflow |
| "create a skill", "/create-skill" | Skill | creating-skills wizard |
| "add capability", "new feature" | Ambiguous | AskUserQuestion |
| "extend mode X" | Mode extension | Mode YAML edit |

**Decision criteria**:

| Question | Mode | Skill |
|----------|------|-------|
| Does it change which skills load? | Yes | No |
| Does it define a new operating context? | Yes | No |
| Is it a reusable protocol or workflow? | No | Yes |
| Does it need prompt injection text? | Yes | No |

### 2. Mode Creation (7 Components)

Every mode requires:

| # | Component | Source |
|---|-----------|--------|
| 1 | Name | Single word, lowercase |
| 2 | Description | Purpose statement with "Use when" |
| 3 | Triggers | `/mode name` + natural language phrases |
| 4 | Skills (always) | Existing skills from `~/.claude/skills/` |
| 5 | Skills (optional) | Additional skills available on demand |
| 6 | Token budget | Realistic estimate based on skill sizes |
| 7 | Prompt injection | Context-specific instructions for the mode |

Optional: Broly configuration, stacks_with, context list.

### 3. Skill Creation (6-Step Wizard)

Delegates to `creating-skills` skill which handles:

1. Collect purpose
2. Collect name (gerund form)
3. Collect triggers
4. Pre-creation validation (50/100 minimum)
5. Generate SKILL.md
6. Post-creation validation (70/100 minimum)

### 4. Quality Validation

#### Pre-Creation Gate

| Check | Points | Minimum |
|-------|--------|---------|
| Name follows convention | 10 | Required |
| Description contains "Use when" | 10 | Required |
| Purpose is actionable | 10 | Required |
| No duplicate name | 10 | Required |
| Dependencies exist | 10 | Required |
| **Total** | **50** | **All required** |

Score < 50: REJECT with specific issues listed.

#### Post-Creation Gate

| Check | Points | Threshold |
|-------|--------|-----------|
| Pre-creation checks | 50 | All pass |
| Frontmatter complete | 10 | Required |
| Section count >= 3 | 10 | Required |
| No placeholders | 10 | Required |
| Examples present | 10 | Recommended |
| Line count <= 500 | 10 | Required |
| **Total** | **100** | **70 minimum** |

Score < 70: Show warning, offer edit before registering.

## Workflows

### Quick Skill

1. Mode vs skill detection (skill confirmed)
2. Delegate to creating-skills wizard
3. Wizard handles all 6 steps
4. Output: registered skill

**Broly level**: disabled

### Standard Skill (with SKILL.md)

1. Mode vs skill detection (skill confirmed)
2. Creating-skills wizard (steps 1-4)
3. Generate SKILL.md with full structure:
   - Frontmatter, protocols, workflows, quality gates, output style
4. Post-creation validation
5. Register in SKILLS.md

**Broly level**: disabled

### Quick Mode

1. Mode vs skill detection (mode confirmed)
2. AskUserQuestion for 7 components (batched where possible)
3. Generate mode YAML
4. Validate YAML syntax
5. Save to `~/.claude/modes/definitions/`
6. Test activation

**Broly level**: disabled

### Deep Mode (with SKILL.md + Broly config)

1. Mode vs skill detection (mode confirmed)
2. AskUserQuestion for 7 components
3. AskUserQuestion for Broly configuration (level, escalation signals)
4. Generate mode YAML with Broly block
5. Generate companion SKILL.md for orchestration
6. Validate both files
7. Register mode and skill
8. Test full activation chain

**Broly level**: FORK (for parallel YAML + SKILL.md generation)

## Testing Protocol

After any creation:

| Test | Command | Pass Criteria |
|------|---------|---------------|
| Mode activation | `/mode {name}` | Mode loads without error |
| Skill invocation | `/{skill-trigger}` | Skill loads and responds |
| Cross-skill | Trigger integrated skill | Dependent skills accessible |
| Token budget | Check loaded skill sizes | Within declared budget |

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Mode vs skill unclear from user intent
- Purpose statement is vague or too broad
- Quality score borderline (65-75 range)
- Skill dependencies do not exist yet
- Multiple valid names for the same concept
- Token budget exceeds 5000 for a single mode
- Broly configuration requested but scope unclear

## Output Style

- YAML for mode definitions (valid, parseable)
- Markdown for SKILL.md files (following demo pattern)
- British English throughout
- No emoji
- Show quality scores explicitly after validation

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for create mode |
