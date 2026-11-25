---
name: auto-mode
description: LLM-inferred mode and skill selection. Uses free external models (Gemini Flash, Groq) to semantically analyse prompts and activate optimal mode stacks. Replaces keyword matching with genuine inference.
status: Active
effort: high
version: 1.0.0
---

# Auto Mode — LLM-Inferred Context Loading

Analyses any prompt using free external LLMs to select the optimal mode stack,
skill set, and command pattern. Proven 5/0 vs keyword matching on edge cases.

## Usage

```bash
/mode auto "build a secure REST API with tests"
# -> Activates: code + security + testing (~15k budget)

python3 lib/auto_mode_selector.py "your prompt here"
python3 lib/auto_mode_selector.py select "prompt" --provider groq
python3 lib/auto_mode_selector.py overrides
```

## Architecture

```
User prompt
    |
    v
N6: Complexity estimator (simple -> Flash, complex -> Pro)
    |
    v
N1: Cache check (1,553x speedup on repeat)
    |  miss
    v
N2: Inject learned override patterns into prompt
    |
    v
N5: Query LLM (Gemini Flash free / Groq free / Gemini Pro)
    |  N7: failover on error
    v
Parse structured JSON response
    |
    v
N4: Add secondary concerns (auth -> security, code -> testing)
    |
    v
N9: Deduplicate skills across stacked modes
    |
    v
N8: Record selection metrics for /evo
    |
    v
Activate modes via mode-loader.sh
```

## Features (N1-N9)

| # | Feature | Impact |
|---|---------|--------|
| N1 | Caching | 1,553x speedup on repeat prompts |
| N2 | Override Learning | Self-correcting from user feedback (3x threshold) |
| N3 | On-Demand Mode Creation | Proposes new modes when none fit |
| N4 | Secondary Concerns | Auto-adds security for auth, testing for code |
| N5 | Tiered Routing | Flash (free) for simple, Pro for complex |
| N6 | Complexity Estimator | Auto-detects multi-domain prompts |
| N7 | Provider Failover | Gemini down -> Groq (zero downtime) |
| N8 | Selection Metrics | /evo tracks selection quality |
| N9 | Skill Deduplication | Removes overlapping skills across modes |

## Council Evidence

- Architecture council (6 claims): 0/6 survived — converge() is active kernel, not universal
- Keyword auto-mode (4 claims): 0/4 survived — keywords are surface-level
- LLM auto-mode (4 claims): **L4 ALIVE** — "cheap model selects expensive context" survives
- Novel proposals (3 claims): **3/3 ALIVE** — evaluate mode validated N1, N2, N3

## A/B Test Results

| Prompt | Keywords | Semantic | Winner |
|--------|----------|----------|--------|
| API latency spike | research + code + learning | analysis + research + review + synthesis | SEM |
| Contract + IP clauses | legal + security + writing | legal | SEM |
| Standup prep | planning + review | planning + writing | SEM |
| Webhook dropping | code + security + legal | analysis + architect + code + testing | SEM |
| GRIP presentation | demo + security + review + legal | demo + writing + teaching | SEM |

**Semantic: 5/5. Keywords: 0/5.** Keywords have a false positive problem (e.g. "IP" matches both legal and security).

## Key Files

| File | Purpose |
|------|---------|
| `lib/auto_mode_selector.py` | Core library (N1-N9) |
| `modes/definitions/auto-mode.yaml` | Mode definition |
| `lib/mode-loader.sh` | Bash integration (`/mode auto`) |
| `learning/mode-overrides.jsonl` | N2 override learning data |
| `learning/mode-selection-metrics.jsonl` | N8 metrics |
| `cache/mode-selection-cache.json` | N1 cache |

## Related

- `mode` — Mode management skill
- `multi_llm_council.py` — Council library (evaluation foundation)
- `design-principles` — SOLID, GRASP, DRY, KISS principles
