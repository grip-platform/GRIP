---
name: concise-communication
description: Eliminates unnecessary preambles and postambles that waste tokens
status: Active
effort: low
auto_generated: true
generation_date: 2025-12-03T10:35:53Z
source_pattern: unnecessary-preambles
version: 2.0.0
triggers:
  - Starting responses with "I'll now" or "Let me"
  - Ending responses with summaries of what was just done
  - Verbose announcements before actions
---

# Concise Communication

**Purpose:** Eliminate token waste from unnecessary preambles and postambles.

**Severity:** minor
**Token Impact:** 50-300 tokens per instance

---

## Problem

Starting responses with announcements or ending with summaries wastes tokens:

### Preamble Waste

| Phrase | Tokens | Value |
|--------|--------|-------|
| "I'll now read the file..." | ~8 | ZERO |
| "Let me check that for you..." | ~7 | ZERO |
| "I'm going to implement..." | ~6 | ZERO |
| "First, I'll need to..." | ~6 | ZERO |

### Postamble Waste

| Phrase | Tokens | Value |
|--------|--------|-------|
| "In summary, I have..." | ~20-50 | ZERO |
| "I've successfully completed..." | ~15 | ZERO |
| "Done. To summarize..." | ~30-100 | ZERO |

**Impact:** 5 preambles × 8 tokens = 40 tokens. Across 50 messages = 2000 tokens wasted.

---

## Solution

**Start with action. End when action completes. No announcements.**

### Banned Phrases

#### Preambles (Never Use)

- "I'll now..."
- "I will now..."
- "Let me..."
- "I'm going to..."
- "First, I'll..."
- "Allow me to..."
- "I'll start by..."

#### Postambles (Never Use)

- "In summary..."
- "To summarize..."
- "I have completed..."
- "I've finished..."
- "Done."
- "Successfully completed."
- "That's done."

### Correct Pattern

```text
# WRONG
"I'll now read the CLAUDE.md file to understand the project structure."
[Read tool]
"I've successfully read the file. In summary, it contains..."

# RIGHT
[Read tool]
The file shows X, Y, Z.
```

---

## Examples

### Before (Anti-Pattern)

```text
User: What's in package.json?

Claude: Let me read that file for you to see what dependencies are listed.
[Read tool]
I've successfully read the package.json file. In summary, it contains the following dependencies: React 18.2.0, TypeScript 5.0.0, and several dev dependencies including Jest and ESLint. The project appears to be a modern React application with TypeScript support.
```

**Token cost:** ~80 wasted tokens

### After (Correct)

```text
User: What's in package.json?

[Read tool]
Dependencies: React 18.2.0, TypeScript 5.0.0
Dev deps: Jest, ESLint
```

**Token cost:** ~15 tokens (80% reduction)

---

## Detection

### Preamble Regex

```regex
(I'll now|I will now|Let me|I'm going to|First, I'll)
```

### Postamble Regex

```regex
(In summary|To summarize|I have completed|I've finished|Done\.|Successfully completed)
```

### Thresholds

- Preambles: 5 occurrences triggers skill
- Postambles: 5 occurrences triggers skill

---

## Implementation

This skill is enforced via:

1. **Pattern Detection** - `./optim.sh detect` (MONITORING)
2. **efficiency-rules.md Rule 4** - Concise Communication
3. **CLAUDE.md Output Style** - Action-first guidance

### efficiency-rules.md Reference

```markdown
## Rule 4: Concise Communication

- No preambles ("I'll now...", "Let me...")
- No postambles (summaries unless asked)
- Start with action, end when action completes
- Minimal explanation unless user asks
```

### CLAUDE.md Reference

```text
; ◈ CONCISE COMMUNICATION
¬preamble("I'll now...") ⫶ ¬postamble ⫶ action-first ⫶ end.complete
```

---

## Exceptions

Summaries ARE appropriate when:

1. User explicitly asks: "Can you summarize what you did?"
2. Complex multi-step operation needs confirmation
3. User is learning/onboarding and needs explanation

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.0 | 2026-01-26 | Regenerated with real content from RSI pipeline |
| 1.0.0 | 2025-12-03 | Auto-generated stub from pattern detection |

---


