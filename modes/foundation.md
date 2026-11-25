# Mode Foundation

PARAMOUNT skills and protocols that load with EVERY mode.

**Full architecture documentation**: See `docs/MODES-ARCHITECTURE.md`

## Inheritance Model

Foundation skills are PARAMOUNT and **cannot be overridden** by any mode:

```
[Mode Skills]        ← Stacks on top (supplements)
       ↓
[Foundation Skills]  ← ALWAYS ACTIVE (never overridden)
```

Mode-specific skills ADD functionality; they never replace foundation gates.

## Always Active PARAMOUNT Skills

| Skill | Purpose | Gate |
|-------|---------|------|
| **ut++** (ultrathink) | 6 verification gates, AskUserQuestion MANDATORY | confidence < 99.9999999% |
| **asking-users** | Bidirectional question protocol (siblings `/creating-wizards` and the `/hitl` dispatch contract that chooses between them) | destructive actions, UI/UX, assumptions |
| **context-gate** | 80% threshold monitoring | context pressure |
| **improvement-tracking** | RSI feedback loop, heatmap signals | component 3x threshold |
| **design-smell-response** | Response to [IMPROVEMENT HEATMAP] | heatmap signal emitted |
| **self-improvement-engine** | Full 10-step RSI cycle | /auto-improve |
| **recursive-learning** | Effectiveness tracking, meta-learning | skill generation |
| **autonomous-learning** | Pattern detection, dialectical reasoning | teaching moments, novelty |
| **optimizing-complexity** | BIG-O awareness, O(n^2)+ detection | Gate Q: nested loops in Python |
| **quality-guardian** | Unified DRY/KISS/BIG-O/YAGNI fast-path | Write/Edit PostToolUse |

## Efficiency Rules (Always Enforced)

1. **File Read Optimization**: Cache check (10 messages) → batch read → trust mental model
2. **Plan Evaluation Gate**: 99.9999999% confidence before execution
3. **Concise Communication**: Action-first, no preamble, no postamble
4. **Dependency Guardian**: NEVER read node_modules, .next, dist, build, venv

## Design Principles

| Principle | Application |
|-----------|-------------|
| SOLID | Clean interfaces, single responsibility |
| GRASP | Information expert, low coupling |
| DRY | Single source of truth |
| KISS | Simplest solution that works |
| YAGNI | Build when needed, not anticipated |
| YSH | Pattern 2x proven → abstract NOW, through the promotion gate |
| BIG-O | O(n log n) ceiling, justify O(n^2)+ |

## JIT Triple-Gate (PARAMOUNT — All Modes)

Gate 0 (JIT): Apply DRY/KISS/BIG-O/YAGNI/CC/ISP/DIP/Feature Envy INLINE during code generation.
Gate 1 (Pre-commit): Mechanical verification of Gate 0.
Gate 2 (Post-commit): Audit trail for evolution.

Code is correct-by-construction. JIT defaults: dispatch tables > if/else, O(n log n), functions < 30 lines as written.

## Knowledge-Work Gates (Always Active)

| Gate | Trigger | Action |
|------|---------|--------|
| Evidence Gate | Any factual claim | Require source or mark as assumption |
| Falsifiability Gate | Any prediction/claim | State what would disprove it |
| Bias Gate | Any decision | Run cognitive bias checklist |
| SMT Gate | Any analogy/mapping | Gentner score >= 60 or reject |
| Diversity Gate | Significant claim | Require >= 3 independent sources |

Mode-specific overrides:
- Brainstorm DIVERGE: Evidence + Falsifiability gates SUSPENDED (re-enabled at CONVERGE)
- Legal: Evidence gate threshold raised to CRAAP >= 18
- Incognito: All gates active but no persistence

## Enforcer Agents (Always Conceptually Active)

| Enforcer | Scope | Invocation |
|----------|-------|------------|
| DRY/KISS | Code | Mechanical (PreToolUse hook) |
| YAGNI | Code + Planning | Mechanical (PreToolUse hook) |
| YSH | Code + Planning | Advisory (post-implementation review) |
| SOLID | OOP Code | Advisory (spawn on class design) |
| GRASP | Architecture | Advisory (spawn on responsibility questions) |
| Efficiency Auditor | Workflow | Advisory (spawn at session milestones) |
| Gentner | Analogies/Mappings | Mechanical (spawn on any structural mapping claim) |

## Token Budget

Foundation load: ~1500 tokens
Mode adds: ~2000-6000 tokens depending on mode

## Security: Flow Mode

Flow mode (`/mode flow`) is HIDDEN from mode discovery and requires explicit enable:

```bash
grip flow --enable --i-understand-the-risks
```

Flow bypasses permission prompts.

## Integration

Foundation loads automatically when ANY mode activates.
Mode-specific skills stack on top of foundation.

```grip
mode.activate⟿ foundation.load ⫶ mode.skills.load
mode.deactivate⟿ mode.skills.unload ⫶ foundation.persist
```

---

**
