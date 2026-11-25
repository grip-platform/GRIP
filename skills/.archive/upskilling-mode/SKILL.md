---
name: upskilling-mode-orchestration
description: Routes meta-skill creation through recursive learning with quality gates. Use when /mode upskilling is active.
status: Active
effort: high
version: 1.0.0
triggers:
  - /mode upskilling
  - upskilling mode active
integrates:
  - skill-creation-best-practices
  - creating-skills
  - recursive-learning
  - autonomous-learning
  - self-improvement-engine
  - meta-improvement-switch
  - asking-users
---

# Upskilling Mode Orchestration

Routes meta-skill creation through recursive learning pipeline with quality gates. Orchestration layer only -- see `skill-creation-best-practices/SKILL.md` for quality standards and `creating-skills/SKILL.md` for the creation wizard.

## Protocols

### 1. Skill Creation Pipeline

1. **DETECT** -- Pattern emerges from conversation (3x YSH threshold)
2. **VALIDATE** -- Check against skill-creation-best-practices
3. **PROPOSE** -- AskUserQuestion for approval (Gate A)
4. **GENERATE** -- Create SKILL.md with quality score >= 70%
5. **INTEGRATE** -- Add to skill registry
6. **META-LEARN** -- Update detection heuristics

### 2. Quality Gates (PARAMOUNT)

| Gate | Trigger | Requirement |
|------|---------|-------------|
| Gate A | Proposal | AskUserQuestion MANDATORY |
| Gate B | Auto-generation | Quality >= 70% |
| Gate C | Autonomous discovery | Quality >= 70% + review |

### 3. Periodic Self-Check

Every 15-20 operations, scan for:

1. Repeated patterns (3x threshold for YSH)
2. Novel problem-solving approaches
3. Workarounds that should be permanent
4. User corrections indicating missing skills

When pattern detected:
- Assess novelty score (0.0 - 1.0)
- If novelty >= 0.7: Propose skill creation
- If novelty < 0.7 but useful: Log for future consideration

### 4. Recursive Learning Loop

```text
pattern.detect → skill.propose → user.approve → skill.create
     ↓                                              ↓
heuristic.update ←←←←←←←←←←←←←←←←←←←←←←←← pattern.generalise
```

## Workflows

### Quick (single skill from explicit request)

1. User provides skill spec
2. Validate against best practices
3. Generate with quality check
4. Output

**Broly level**: THREAD (Broly disabled in this mode)

### Standard (pattern-driven skill creation)

1. Pattern detection triggers creation
2. Full pipeline (DETECT → META-LEARN)
3. Quality gates at each stage
4. Integration and registry update

**Broly level**: N/A (Broly disabled)

### Deep (recursive meta-improvement)

1. Full RSI pipeline activation
2. Self-improvement-engine for pattern detection
3. Recursive-learning for heuristic updates
4. Meta-improvement-switch for infrastructure capture
5. Multi-skill creation with cross-references

**Broly level**: N/A (Broly disabled)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Skill proposal (Gate A -- always)
- Quality review (Gate C -- autonomous discovery)
- Pattern novelty borderline (0.5-0.7)
- Skill conflicts with existing skill
- Meta-improvement switch activation

## Output Style

- Skill-specification format
- British English throughout
- No emoji
- Include quality scores
- Reference skill-creation-best-practices

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for upskilling mode |
