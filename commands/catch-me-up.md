---
description: Reason about reasoning -- catalogue achievements, falsify each claim, explain in plain language.
disable-model-invocation: false
---

# /catch-me-up -- Reasoning Audit

Systematic session retrospective: catalogues achievements, tests each claim,
explains everything in plain language.

## Usage

```bash
/catch-me-up           # Audit current session
/reflect               # Alias
/reasoning-audit       # Alias
```

## Implementation

Load and execute the reasoning-audit skill:

```
Skill(skill="reasoning-audit")
```

Or run the module directly:

```bash
python3 lib/reasoning_audit.py
python3 lib/reasoning_audit.py --json
python3 lib/reasoning_audit.py --idr            # decision-chain mode: "why did I decide X / what led here" over the signed IDR chain
python3 lib/reasoning_audit.py --idr --idr-limit 10 --json
```

## What It Does

1. **Catalogue** achievements from session context, git log, completed tasks
2. **Classify** each as discovery, implementation, application, observation, or hypothesis
3. **Falsify** each claim with category-appropriate tests
4. **Translate** to plain language (no jargon)
5. **Persist** validated claims; register hypotheses
