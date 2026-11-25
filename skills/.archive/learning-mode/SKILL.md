---
name: learning-mode-orchestration
description: Routes personal learning pathway design through assessment and spaced repetition. Use when /mode learning is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode learning
  - learning mode active
integrates:
  - pedagogical-patterns
  - curriculum-design
  - asking-users
---

# Learning Mode Orchestration

Routes personal learning pathway design through gap analysis and spaced repetition scheduling. Orchestration layer only -- see `pedagogical-patterns/SKILL.md` for learning principles and `curriculum-design/SKILL.md` for pathway templates.

## Protocols

### 1. Learning Pathway Workflow

1. **ASSESS** -- Determine current knowledge level and learning style
2. **TARGET** -- Define specific, measurable learning objectives
3. **GAP** -- Map distance between current state and target
4. **PATH** -- Design sequenced steps (prerequisites first)
5. **RESOURCE** -- Curate resources matched to learner level
6. **SCHEDULE** -- Build spaced repetition and practice schedule
7. **TRACK** -- Define progress checkpoints and mastery criteria

### 2. Learning Principles

| Principle | Application |
|-----------|-------------|
| Feynman test | Can you explain it simply? If not, you don't understand it |
| 80/20 rule | What 20% of concepts unlock 80% of capability? |
| Confidence mapping | Rate understanding 1-5 per concept, focus on 1-2s |
| Interleaved practice | Mix problem types (don't block-practice) |
| Desirable difficulty | Slightly beyond comfort zone = optimal learning |

### 3. Spaced Repetition Schedule

| Day | Review |
|-----|--------|
| 1 | Initial learning |
| 2 | First review |
| 4 | Second review |
| 7 | Third review |
| 14 | Fourth review |
| 30 | Long-term retention check |

### 4. Active Recall Techniques

- Practice problems before reading solutions
- Teach-back: Explain as if teaching someone else
- Elaborative interrogation: "Why does this work?"
- Self-testing: Generate and answer own questions

Distinguish from TEACHING mode: Learning designs the JOURNEY. Teaching delivers content.

## Workflows

### Quick (single concept gap, quick resource)

1. Quick level assessment
2. Targeted resource recommendation
3. Output

**Broly level**: THREAD (Broly disabled in this mode)

### Standard (skill development, study plan)

1. Full 7-step pathway workflow
2. Confidence mapping per concept
3. Resource curation matched to level
4. Spaced repetition schedule
5. Progress checkpoints

**Broly level**: N/A (Broly disabled)

### Deep (certification prep, career transition)

1. Comprehensive assessment
2. Multi-domain gap analysis
3. Extended learning pathway
4. Resource library curation
5. Long-term schedule with milestones

**Broly level**: N/A (Broly disabled)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Starting session (assess current level)
- Learning objectives unclear or too broad
- Multiple valid learning paths exist
- Checkpoint reached (verify understanding before advancing)
- Resource preferences needed (video vs text vs hands-on)

## Output Style

- Learner-centric, encouraging but direct
- British English throughout
- No emoji
- Structured pathways with clear milestones
- Progress tracking format

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for learning mode |
