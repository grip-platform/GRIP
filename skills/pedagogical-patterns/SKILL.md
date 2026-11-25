---
name: pedagogical-patterns
description: Evidence-based teaching methods built on the FASTER framework for technical education. Use when writing a tutorial, explaining a hard concept, or designing technical teaching material.
---

# Pedagogical Patterns

Evidence-based teaching methodologies adapted from the FASTER framework for technical education.

## FASTER Framework

| Letter | Principle | Application |
|--------|-----------|-------------|
| **F**orget | Beginner's mindset | Suspend assumptions, fresh perspective |
| **A**ct | Active learning | Hands-on practice, not passive reading |
| **S**tate | Optimal focus | Remove distractions, prime engagement |
| **T**each | Teach to learn | Explain concepts to retain them |
| **E**nter | Consistent practice | Regular sessions, deliberate practice |
| **R**eview | Spaced repetition | Scheduled review to fight forgetting |

## Core Pedagogical Patterns

### 1. Teach-Back Method

The most effective retention technique: have the learner explain back.

```grip
teach_back⟿ {
    1. Present concept clearly
    2. Ask: "Can you explain this in your own words?"
    3. Listen for understanding gaps
    4. Clarify misconceptions
    5. Confirm accurate understanding
}
```

**Key phrases**:
- "Walk me through your understanding of..."
- "If you had to explain this to a colleague..."
- "What would you tell someone who asked about..."

### 2. Scaffolded Complexity

Build from simple to complex with deliberate steps:

| Level | Complexity | Example |
|-------|------------|---------|
| 1 | Concept | What is a database index? |
| 2 | Mechanics | How does a B-tree index work? |
| 3 | Application | When should you add an index? |
| 4 | Analysis | How do you diagnose missing indexes? |
| 5 | Synthesis | Design indexing strategy for this schema |

### 3. Worked Examples

Show complete solutions before asking for independent work:

````markdown
## Worked Example: API Rate Limiting

### Problem
Implement rate limiting for an API endpoint.

### Solution (Step by Step)

**Step 1**: Choose algorithm (token bucket)
```javascript
// Token bucket: allows burst + sustained rate
const bucket = { tokens: 100, lastRefill: Date.now() };
```

**Step 2**: Implement refill logic
[...]

**Step 3**: Check and consume tokens
[...]

### Your Turn
Now implement rate limiting for `/api/search` with different limits.
````

### 4. Interleaved Practice

Mix different problem types rather than blocked practice:

```grip
interleave⟿ {
    AVOID: A, A, A, B, B, B, C, C, C
    PREFER: A, B, C, A, C, B, B, A, C

    reason: Forces retrieval and discrimination
    benefit: Better long-term retention
}
```

### 5. Spaced Repetition

Schedule reviews at expanding intervals:

| Review | Interval | Content |
|--------|----------|---------|
| 1 | Same day | Key concepts |
| 2 | Next day | Concepts + first application |
| 3 | 3 days | Application problems |
| 4 | 1 week | Mixed problems |
| 5 | 2 weeks | Advanced application |

### 6. Desirable Difficulties

Introduce productive struggle:

| Difficulty | Example | Benefit |
|------------|---------|---------|
| Generation | Write before seeing answer | Deeper encoding |
| Retrieval | Recall without notes | Strengthens memory |
| Variation | Different contexts | Better transfer |
| Spacing | Spread over time | Reduces forgetting |

## Explanation Frameworks

### ELI5 (Explain Like I'm 5)

Strip to essentials using analogies:

> **Database index**: Like a book's index. Instead of reading every page to find "recursion," you look in the back, find "recursion: page 47," and go directly there.

### ADEPT Method

| Step | Purpose | Example |
|------|---------|---------|
| **A**nalogy | Familiar comparison | "Like a dictionary's alphabetical order" |
| **D**iagram | Visual representation | [Show B-tree structure] |
| **E**xample | Concrete instance | "SELECT WHERE user_id = 5" |
| **P**lain English | Simple statement | "Indexes make searches faster" |
| **T**echnical | Precise definition | "B+ tree with O(log n) lookup" |

### Feynman Technique

1. Choose concept to learn
2. Explain as if to a child
3. Identify gaps in explanation
4. Review and simplify
5. Repeat until clear

## ut++ Integration

| Gate | Trigger | Action |
|------|---------|--------|
| Gate 1 | Explaining complex topic | Verify learner's prior knowledge |
| Gate 5 | Multi-concept lesson | Checkpoint understanding every 3 concepts |
| Gate 6 | Choosing explanation method | Ask about learning preference |

## AskUserQuestion Triggers

**MANDATORY** when:
- Starting teaching session (assess level)
- After explanation (verify understanding)
- Learner seems confused (adjust approach)

```text
Question: "Before we dive in, what's your familiarity with [topic]?"
Header: "Level"
Options:
- "New to this" - Start from fundamentals
- "Some exposure" - Review basics, then advance
- "Experienced" - Focus on advanced/nuanced aspects
```

```text
Question: "Just explained [concept]. How would you summarize it?"
Header: "Check"
Options:
- "Let me try" - Learner attempts teach-back
- "Need clarification" - Review with different approach
- "Got it, move on" - Proceed to next concept
```

## Anti-Patterns

| Anti-Pattern | Problem | Solution |
|--------------|---------|----------|
| Info dump | Overwhelms learner | Chunk into digestible pieces |
| Jargon cascade | Loses beginner | Define terms, use analogies |
| Passive lecture | Low retention | Include active exercises |
| No verification | Assumed understanding | Regular teach-back checks |
| Fixed pace | One-size-fits-none | Adapt to learner feedback |

## Related Skills

- `curriculum-design` - Structure learning paths
- `reasoning-with-ai` - Think-aloud modeling
- `forging-skills` - Practice through doing

---


