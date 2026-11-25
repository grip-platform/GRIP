---
name: curriculum-design
description: Structure learning paths with clear objectives, prerequisites, modules, and assessments. Use when building training material, an onboarding path, or a technical curriculum.
---

# Curriculum Design

Structure learning paths with clear objectives, prerequisites, and assessments.

## Curriculum Structure

### Learning Objective Framework (SMART)

| Component | Question | Example |
|-----------|----------|---------|
| **S**pecific | What exactly will learner do? | "Write a REST API endpoint" |
| **M**easurable | How will we know they succeeded? | "API returns correct JSON" |
| **A**chievable | Is this realistic for the level? | Appropriate for skill level |
| **R**elevant | Why does this matter? | "Required for web development" |
| **T**ime-bound | When should they achieve it? | "By end of module 3" |

### Bloom's Taxonomy for Technical Learning

| Level | Verbs | Example Objective |
|-------|-------|-------------------|
| **Remember** | Define, list, recall | "List the HTTP methods" |
| **Understand** | Explain, describe, summarize | "Explain REST principles" |
| **Apply** | Use, implement, execute | "Implement a GET endpoint" |
| **Analyze** | Compare, debug, diagnose | "Debug a 500 error" |
| **Evaluate** | Assess, critique, recommend | "Evaluate API design trade-offs" |
| **Create** | Design, build, architect | "Design a complete API" |

## Curriculum Template

```markdown
# Course: [Title]

## Overview
**Duration**: [X hours/days/weeks]
**Level**: [Beginner | Intermediate | Advanced]
**Prerequisites**: [List requirements]

## Learning Outcomes
By completing this course, learners will be able to:
1. [Outcome 1 - Bloom level]
2. [Outcome 2 - Bloom level]
3. [Outcome 3 - Bloom level]

## Module Structure

### Module 1: [Title]
**Duration**: [X hours]
**Objectives**:
- [Objective 1]
- [Objective 2]

**Topics**:
1. [Topic 1]
2. [Topic 2]

**Activities**:
- [Activity 1]: [Description]
- [Activity 2]: [Description]

**Assessment**:
- [How will mastery be verified?]

### Module 2: [Title]
[Same structure...]

## Assessment Strategy

| Type | Purpose | When | Weight |
|------|---------|------|--------|
| Quiz | Knowledge check | After each module | 20% |
| Exercise | Skill application | During modules | 40% |
| Project | Integration | End of course | 40% |

## Resources
- [Resource 1]: [Description]
- [Resource 2]: [Description]
```

## Learning Mode Templates

### Mode 1: Balanced

```text
Theory (30%) → Practice (40%) → Application (30%)

Module structure:
1. Concept introduction (15 min)
2. Worked example (10 min)
3. Guided practice (20 min)
4. Independent practice (15 min)
5. Real-world application (15 min)
6. Review and Q&A (10 min)
```

### Mode 2: Exam Prep

```text
Recall (40%) → Practice (40%) → Testing (20%)

Module structure:
1. Key concept summary (10 min)
2. Flashcard review (15 min)
3. Practice problems (25 min)
4. Timed quiz (15 min)
5. Error analysis (10 min)
6. Spaced repetition scheduling (5 min)
```

### Mode 3: Theory-Focused

```text
Concepts (50%) → Deep Dive (30%) → Connections (20%)

Module structure:
1. Big picture overview (15 min)
2. Core concept deep dive (25 min)
3. Mental model building (15 min)
4. Concept connections (15 min)
5. Reflection and synthesis (10 min)
```

### Mode 4: Project-Based

```text
Brief (10%) → Build (70%) → Reflect (20%)

Module structure:
1. Project brief and requirements (10 min)
2. Research and planning (15 min)
3. Implementation (45 min)
4. Testing and iteration (20 min)
5. Demo and reflection (15 min)
6. Feedback and next steps (10 min)
```

## Prerequisite Mapping

````markdown
## Skill Tree: [Domain]

```text
Level 1: Fundamentals
├── Concept A (required)
└── Concept B (required)

Level 2: Core Skills
├── Skill C (requires: A)
├── Skill D (requires: A, B)
└── Skill E (requires: B)

Level 3: Advanced
├── Advanced F (requires: C, D)
└── Advanced G (requires: D, E)
```

### Prerequisite Check
Before starting [Module], verify:
- [ ] Learner can [prerequisite skill 1]
- [ ] Learner can [prerequisite skill 2]
- [ ] Learner understands [concept]
````

## Assessment Design

### Formative (During Learning)

| Type | Purpose | Example |
|------|---------|---------|
| Check-in | Spot confusion early | "Rate your confidence 1-5" |
| Quick quiz | Test recall | Multiple choice on key terms |
| Teach-back | Verify understanding | "Explain to me..." |
| Code review | Apply knowledge | Review sample code |

### Summative (End of Unit)

| Type | Purpose | Example |
|------|---------|---------|
| Project | Integrate skills | Build complete feature |
| Exam | Test breadth | Timed assessment |
| Portfolio | Demonstrate growth | Collected work samples |
| Presentation | Communicate learning | Explain and demo |

## ut++ Integration

| Gate | Trigger | Action |
|------|---------|--------|
| Gate 1 | Designing curriculum | Verify learning objectives clear |
| Gate 3 | Assumptions about learner level | Assess before proceeding |
| Gate 5 | Multi-module design | Checkpoint after each module |

## AskUserQuestion Triggers

**MANDATORY** when:
- Starting curriculum design (clarify goals)
- Choosing learning mode
- Designing assessments

```text
Question: "What learning mode fits best for this content?"
Header: "Mode"
Options:
- "Balanced" - Mix of theory, practice, application
- "Exam prep" - Focus on retention and testing
- "Theory-focused" - Deep conceptual understanding
- "Project-based" - Learn by building
```

```text
Question: "What's the primary outcome for this curriculum?"
Header: "Outcome"
Options:
- "Job skill" - Practical, immediately applicable
- "Certification" - Pass specific exam
- "Exploration" - General understanding, no pressure
```

## Related Skills

- `pedagogical-patterns` - Teaching techniques
- `reasoning-with-ai` - Structured thinking
- `explanation-frameworks` - How to explain concepts

---


