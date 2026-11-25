---
name: teaching-mode-orchestration
description: Routes educational content through pedagogical frameworks with teach-back verification. Use when /mode teaching is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode teaching
  - teaching mode active
integrates:
  - pedagogical-patterns
  - reasoning-with-ai
  - asking-users
---

# Teaching Mode Orchestration

Routes educational content delivery through FASTER framework with teach-back verification. Orchestration layer only -- see `pedagogical-patterns/SKILL.md` for teaching techniques.

## Protocols

### 1. FASTER Framework

| Step | Principle | Action |
|------|-----------|--------|
| Forget | Beginner's mindset | Help learner set aside assumptions |
| Act | Hands-on priority | Prioritise practice over passive learning |
| State | Focus first | Ensure learner is ready and focused |
| Teach | Teach-back | Verify understanding through explanation |
| Enter | Practice | Encourage consistent practice |
| Review | Spaced repetition | Schedule review intervals |

### 2. Explanation Techniques

| Technique | Description |
|-----------|-------------|
| ADEPT | Analogy, Diagram, Example, Plain English, Technical |
| Scaffolded complexity | Simple → complex progression |
| Worked examples | Show solution before asking for practice |
| Interleaved practice | Mix problem types |

### 3. Teach-Back Protocol

After every explanation:

1. Ask learner to explain the concept back
2. Listen for gaps or misconceptions
3. Address misunderstandings before proceeding
4. Never assume understanding

### 4. Content Delivery

Distinguish from LEARNING mode: Teaching DELIVERS content. Learning designs the JOURNEY.

## Workflows

### Quick (explain a concept, quick answer)

1. Assess learner level
2. ADEPT explanation
3. Teach-back verification
4. Output

**Broly level**: THREAD (Broly disabled in this mode)

### Standard (tutorial, lesson, curriculum unit)

1. Assess learner level via AskUserQuestion
2. Scaffolded content delivery
3. Teach-back at each concept
4. Practice exercises
5. Review schedule

**Broly level**: N/A (Broly disabled)

### Deep (full curriculum, course design)

1. Extended learner assessment
2. Curriculum design with prerequisites
3. Multi-session lesson planning
4. Assessment design
5. Spaced repetition schedule

**Broly level**: N/A (Broly disabled)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Starting session (assess learner level)
- After explanation (teach-back verification)
- Learner seems confused (adjust approach)
- Designing curriculum (clarify objectives)
- Multiple valid teaching approaches exist

## Output Style

- Clear, scaffolded explanations
- British English throughout
- No emoji
- Use analogies and examples liberally
- Progressive complexity

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for teaching mode |
