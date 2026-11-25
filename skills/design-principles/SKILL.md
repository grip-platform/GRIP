---
name: design-principles
description: Unified software design principles - SOLID, GRASP, DRY, KISS, YAGNI, YSH, BIG-O. Apply when designing, reviewing, or refactoring code architecture. YSH (You Should Have) is the dialectical inverse of YAGNI. BIG-O enforces algorithmic complexity awareness.
effort: high
triggers:
  - class design
  - architecture review
  - refactoring
  - code review
  - design patterns
  - responsibility assignment
  - over-engineering
  - duplication
  - UX completeness
  - obvious enhancement
  - should have
version: 1.2.0
integrates:
  - asking-users
  - creating-wizards
  - hitl
consolidates:
  - solid-principles
  - grasp-principles
  - dry-kiss-principles
  - yagni-principle
  - programming-principles
see_also:
  - principles-criteri
  - design-smell-response
  - feature-complete
  - architect-mode
---

# Design Principles (Unified)

**@asking-users**: When principles conflict (YAGNI vs YSH, DRY vs KISS), use AskUserQuestion to clarify which principle takes priority for this specific context.

One skill for all software design guidance. Apply contextually based on the problem.

## Quick Reference

| Principle | When to Apply | Key Question |
|-----------|---------------|--------------|
| **YAGNI** | Feature requests, planning | "Do we need this NOW?" |
| **YSH** | UX/implementation review | "Would any competent dev add this?" |
| **KISS** | Implementation | "Is this the simplest solution?" |
| **DRY** | Code review, refactoring | "Is this duplicated 3+ times?" |
| **SOLID** | Class/module design | "Is this easy to extend/test?" |
| **GRASP** | Responsibility assignment | "Which class should own this?" |
| **BIG-O** | Algorithm design, loops, lookups | "What is the complexity class?" |

## YAGNI - You Aren't Gonna Need It

**Apply when**: Evaluating feature requests, reviewing speculative code

### Rules

1. Build features when needed, not when anticipated
2. Delete dead code immediately
3. No "just in case" abstractions
4. Premature optimisation is the root of all evil

### Red Flags

- "We might need this later"
- "Let's make it configurable"
- "What if requirements change?"
- Feature flags for unrequested features

## YSH - You Should Have (Inverse of YAGNI)

**Apply when**: Reviewing implementation, UX interactions, obvious enhancements

**Origin**: User teaching moment, Gen 50, 2025-12-21. The River grows.

### The Dialectic

YAGNI and YSH form a dialectical pair:

- **YAGNI (thesis)**: Don't over-engineer, don't build speculative features
- **YSH (antithesis)**: Don't under-engineer, don't miss obvious enhancements
- **Synthesis**: Use judgment + verification when uncertain

### Rules

1. Obvious UX enhancements within current scope should be included
2. If any competent developer would add it, you should too
3. When uncertain if YAGNI or YSH applies: **use AskUserQuestion to verify**
4. The cost of asking is lower than the cost of missing obvious value

### YSH Indicators

- Micro-interactions that complete a UX pattern (hover states, animations)
- Visual consistency that a user would expect (full borders, not partial)
- Accessibility enhancements that are trivial to add
- Error states and feedback mechanisms

### Red Flags (You Should Have Done This)

- "The hover effect should obviously have included X"
- "Any designer would have added this animation"
- "The interaction feels incomplete without Y"
- "This is clearly part of the same feature scope"

### Decision Gate

```text
Is this enhancement:
  ├─ Speculative/future-focused? → YAGNI (don't build)
  ├─ Obvious completion of current work? → YSH (build it)
  └─ Uncertain? → AskUserQuestion (verify with user)
```

### Examples

| Scenario | Principle | Action |
|----------|-----------|--------|
| "Add zoom animation to card focus" | YSH | Include it - obvious micro-interaction |
| "Make borders full square on focus" | YSH | Include it - visual consistency |
| "Add dark mode toggle" | YAGNI | Ask first - different feature scope |
| "Add export to PDF" | YAGNI | Ask first - speculative feature |

## Completeness - No Scrubs (a stub IS a scrub)

**Apply when**: Writing any symbol — function, method, class, handler. The 9th principle (PARAMOUNT), peer to the eight above.

Completeness is the **integrity axis**, orthogonal to YAGNI's **need axis**. YAGNI decides *whether* to build a symbol; Completeness decides *whether an existing symbol is honestly whole*. Given that you ARE writing it, write it FULLY — never a hollow placeholder that names a capability the code does not provide. A stub emits a **false-completeness signal**: a later session reads the wired-looking symbol, assumes it works, builds on it, and confabulates. A loud-failing `raise NotImplementedError("not wired yet")` is a scrub exactly as much as a silent `pass` — the harm is the signal to a future reader, not the runtime behaviour.

**The three branches** (the stub is the forbidden one): no known need → write nothing and file nothing; future need, not now → write **NO code**, file a **tracked issue**; needed now → **implement it FULLY**. The correct fix for un-needed deferred work is DELETE + file an issue, never implement-on-the-spot (that would invert YAGNI).

### Load-bearing carve-out (as prominent as the prohibition)

Over-applying this is itself a failure (deleting a real test mock, refusing a legit `@abstractmethod`, turning an honest `return []` into fake-wired code). These are **complete by construction** and MUST NOT be flagged: `@abstractmethod`/`Protocol`/`.pyi` bodies; test doubles inside `tests/**` only; intentionally-unsupported-FOREVER raises (`"mutation unsupported by design"`); runtime-dependency boundary raises (`"requires SLACK_BOT_TOKEN"`); exhaustive-enum defensive fallthroughs; empty no-op overrides; honest empty values (`return None/[]/{}` even with a deferral comment). Discriminator — **meaning, not tokens**: *"never / unsupported / by-design"* = COMPLETE; *"not yet / wired later / Phase N / skeleton"* = SCRUB. Full doctrine + AST detector + WARN-gate: `rules/no-stubs.md`.

## KISS - Keep It Simple, Stupid

**Apply when**: Choosing implementation approach

### Rules

1. Prefer explicit over clever
2. Readable > compact
3. Standard library > custom solution
4. Boring technology > bleeding edge

### Complexity Ladder (choose lowest sufficient)

1. Hardcoded value
2. Configuration variable
3. Simple function
4. Class with methods
5. Abstract interface
6. Plugin system

## DRY - Don't Repeat Yourself

**Apply when**: Spotting duplication, refactoring

### The Rule of Three

1. First time: Just write it
2. Second time: Note the duplication
3. Third time: Refactor to single source

### What DRY IS NOT

- Merging coincidentally similar code
- Premature abstraction
- Forcing unrelated things together

### Valid DRY Targets

- Business logic appearing in multiple places
- Configuration scattered across files
- Validation rules duplicated

## SOLID Principles

**Apply when**: Designing classes, creating abstractions, dependency management

### S - Single Responsibility

- One reason to change per class
- If you can't name it clearly, it does too much

### O - Open/Closed

- Open for extension, closed for modification
- Use composition over inheritance

### L - Liskov Substitution

- Subtypes must be substitutable for base types
- Don't violate parent class contracts

### I - Interface Segregation

- Many specific interfaces > one general interface
- Clients shouldn't depend on methods they don't use

### D - Dependency Inversion

- Depend on abstractions, not concretions
- High-level modules shouldn't depend on low-level modules

## GRASP - General Responsibility Assignment

**Apply when**: Deciding which class should own a method/data

### 9 Patterns

| Pattern | Question | Answer |
|---------|----------|--------|
| **Information Expert** | Who has the data? | That class owns the method |
| **Creator** | Who creates X? | Class that contains/aggregates X |
| **Controller** | Who handles system events? | Dedicated controller class |
| **Low Coupling** | How to minimise dependencies? | Reduce class interconnections |
| **High Cohesion** | How to keep focus? | Related responsibilities together |
| **Polymorphism** | How to handle type variants? | Use interfaces, not conditionals |
| **Pure Fabrication** | Need a class that isn't a domain concept? | Create service/utility class |
| **Indirection** | How to decouple? | Introduce intermediate class |
| **Protected Variations** | How to handle instability? | Wrap with stable interface |

## Decision Framework

```text
New feature request?
  └─→ YAGNI: Is it needed NOW?
        └─→ No: Don't build it
        └─→ Yes: Continue

Reviewing implementation completeness?
  └─→ YSH: Would any competent dev add this?
        └─→ Yes: Build it (obvious enhancement)
        └─→ No: YAGNI applies
        └─→ Uncertain: AskUserQuestion

Choosing implementation?
  └─→ KISS: What's the simplest approach?
        └─→ Use complexity ladder

Writing loops or algorithms?
  └─→ BIG-O: What is the complexity class?
        └─→ O(n log n) or better: Ship it
        └─→ O(n^2)+: Justify or refactor
        (Full decision gate: skills/optimizing-complexity/SKILL.md)

Spotted similar code?
  └─→ DRY: Is it duplicated 3+ times?
        └─→ No: Leave it
        └─→ Yes: Extract to single source

Designing class structure?
  └─→ SOLID: Will this be easy to extend/test?
        └─→ Check each principle

Assigning responsibility?
  └─→ GRASP: Who has the information?
        └─→ Information Expert pattern
```

## Anti-Patterns to Avoid

| Anti-Pattern | Violated Principle | Fix |
|--------------|-------------------|-----|
| God class | SRP, High Cohesion | Split by responsibility |
| Shotgun surgery | DRY, Low Coupling | Consolidate related changes |
| Speculative generality | YAGNI, KISS | Remove unused abstractions |
| Copy-paste programming | DRY | Extract common code |
| Feature envy | Information Expert | Move method to data owner |
| Incomplete UX | YSH | Add obvious micro-interactions |
| Partial visual states | YSH | Complete hover/focus/active states |
| Nested loop search | BIG-O | Index with dict/set, single pass |
| List membership in loop | BIG-O | Convert to set for O(1) lookup |

## Linked Slash Command

```bash
/design-principles
```

## Supersedes

This skill consolidates and replaces:

- `solid-principles`
- `grasp-principles`
- `dry-kiss-principles`
- `yagni-principle`
- `programming-principles`
