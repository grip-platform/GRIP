---
name: design-smell-response
description: PARAMOUNT skill for responding to [IMPROVEMENT HEATMAP] signals. Defines protocol for reviewing components improved 3+ times, SRP analysis, refactor decisions, and effectiveness verification.
status: Active
effort: medium
version: 1.0.0
priority: PARAMOUNT
triggers:
  - '[IMPROVEMENT HEATMAP] signal'
  - component improved 3+ times
  - design smell detected
integrates:
  - improvement-tracking
  - design-principles
  - decision-locking
  - asking-users
  - creating-wizards
  - hitl
  - hitl-decision-matrix
---

# Design Smell Response

**PARAMOUNT**: Protocol for responding to `[IMPROVEMENT HEATMAP]` signals.

This skill is **THE MISSING LINK** that closes the RSI loop.

## When This Fires

The `[IMPROVEMENT HEATMAP]` signal is emitted when a component has been improved 3+ times:

```
[IMPROVEMENT HEATMAP] Components need refactor: lib/learning.sh, hooks/context-gate
```

## Response Protocol

### Step 1: Acknowledge Signal

Do NOT ignore this signal. It indicates potential design smell - likely SRP violation.

```text
[DESIGN SMELL DETECTED]
Component: {component_name}
Improvements: {count}
```

### Step 2: Analyse Component

For each flagged component:

1. Count total improvements: `python3 lib/improvement_analyzer.py heatmap`
2. Review improvement types (bug fixes? features? refactors?)
3. Check if improvements touch the same area repeatedly

### Step 3: SRP Assessment

Ask: "Does this component have a single responsibility?"

| Signal | Likely Issue | Action |
|--------|--------------|--------|
| 3+ bug fixes | Complexity/fragility | Simplify or split |
| 3+ features | Scope creep | Extract new module |
| 3+ refactors | Unclear responsibility | Clarify boundaries |

### Step 4: Decision Gate (AskUserQuestion MANDATORY)

```text
Question: "Component {X} improved {N} times. Review for refactor?"
Header: "Refactor"
Options:
- "Yes, analyse now" - Proceed to SRP review
- "Defer" - Log decision, continue current work
- "Mark as expected" - Component is complex by design (e.g. CLAUDE.md)
```

**PARAMOUNT**: Never proceed without explicit user choice.

### Step 5: If Refactoring

1. **Apply design-principles** (SOLID, especially SRP)
2. **Split component** if multiple responsibilities found
3. **Log refactor**:
   ```bash
   python3 lib/improvement_analyzer.py log refactor {component} "Split into X and Y"
   ```
4. **Lock decision**:
   ```
   /decision-lock "Refactored {component} because {reason}"
   ```

### Step 6: Verify Effectiveness

After refactor, monitor:

- Do improvements continue clustering on the same component?
- Did split components reduce individual heat?
- Did the refactor address root cause or just symptoms?

## Decision Matrix

| Heat Level | Improvement Types | Recommendation |
|------------|-------------------|----------------|
| 3-4 | Mixed | Review but likely OK |
| 5+ | Bug fixes | Likely fragile - split |
| 5+ | Features | Likely scope creep - extract |
| 5+ | Refactors | Unclear design - clarify |
| 3+ | Same area | Strong SRP signal - split |

## Expected Complex Components

Some components are legitimately complex by design:

| Component | Why Complex | Mark as Expected |
|-----------|-------------|------------------|
| `CLAUDE.md` | Core protocol, many integrations | Yes |
| `session-start.sh` | Orchestrates many systems | Yes |
| `context-gate-hook.py` | 10 enforcement gates | Review but likely OK |

## Feedback Loop

```
[IMPROVEMENT HEATMAP] → Response → Refactor → Log → Heatmap Recalculates
                                                           ↓
                                                    Heat Decreases
                                                           ↓
                                                    Loop Closes
```

This is the final segment that closes the RSI loop.

## Integration with design-principles

When SRP review triggers, load the full design-principles skill:

```text
/design-principles

Focus on:
- S: Single Responsibility (primary concern)
- D: Dependency Inversion (may reveal hidden coupling)
```

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~500 |
| AskUserQuestion | ~150-200 |
| Analysis context | ~200-400 |

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Auto-refactor without asking | Always use AskUserQuestion |
| Ignore the signal | Always acknowledge and respond |
| Split everything | Some complexity is necessary |
| Forget to log | Always log the refactor decision |

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 1.0.0 | 253 | Initial creation as PARAMOUNT skill - closes RSI loop |

---

**
