# /converge Quick Reference

## Semantic Prefix Patterns

Type what you want. GRIP infers type and depth from natural language.

```
/converge fix the auth middleware          → depth 3, code
/converge 5: fix the auth middleware       → depth 5, code
/converge explain recursion simply         → depth 3, explanation
/converge 2: explain recursion             → depth 2, explanation
/converge design the notification API      → depth 3, design
/converge 4: >90 design the API schema     → depth 4, threshold 90, design
/converge audit the payment flow           → depth 3, analysis
/converge brainstorm names for the feature → depth 3, creative
```

## Prefix Pattern Syntax

| Pattern | Meaning | Example |
|---|---|---|
| `N:` | Set depth to N | `5: fix the auth bug` |
| `>N` | Set criterion threshold to N | `>85 design the schema` |
| keywords (see below) | Auto-detect task type | `refactor the parser` |

## Intent Detection Keywords

| Type | Trigger keywords |
|---|---|
| code | fix, refactor, test, implement, build |
| explanation | explain, teach, describe, clarify, summarize |
| design | design, architect, schema, plan, structure |
| analysis | audit, investigate, review, analyze, inspect |
| creative | write, draft, brainstorm, create, compose |
| general | (fallback when no keywords match) |

## Flag Reference

All flags remain available and take precedence over inferred values:

| Flag | Effect | Default |
|---|---|---|
| `--depth N` | Set max iterations | 3 |
| `--criterion "..."` | Override criterion text | auto-detect |
| `--show-work` | Emit per-depth scores | off |
| `--code` | Wire to `converge_tracked()` | off |

## Task Type → Scoring Dimensions

| Type | Dim 1 (40%) | Dim 2 (30%) | Dim 3 (30%) |
|---|---|---|---|
| code | correctness | design | simplicity |
| explanation | clarity | accuracy | brevity |
| design | completeness | coherence | elegance |
| analysis | evidence | insight | actionability |
| creative | originality | fitness | polish |
| general | quality | completeness | conciseness |

## Score Reference

| Score | Meaning |
|---|---|
| 90-100 | Criterion met — stop and return |
| 70-89 | Good, targeted improvements remain |
| 50-69 | Mediocre, structural issues present |
| 0-49 | Poor, switch strategy entirely |

## Common Examples

```
/converge fix the null pointer in user service
/converge 5: refactor the billing module
/converge explain async/await to a junior engineer
/converge 2: summarize this PR for the changelog
/converge design a rate-limiting schema
/converge 4: >88 architect the multi-tenant isolation layer
/converge audit the authentication flow for security gaps
/converge investigate why the genome fitness is decaying
/converge brainstorm names for the new convergence metric
```

## Parse Precedence

1. Explicit flags (highest priority)
2. Positional depth (`N:`) and threshold (`>N`)
3. Semantic intent keywords
4. Defaults (depth=3, type=general)
