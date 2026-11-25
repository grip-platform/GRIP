# /broly-auto

Quick alias for `/converge broly auto`.

## Usage

```bash
/broly-auto                          # Same as: /converge broly auto
/broly-auto <task>                   # Same as: /converge broly auto <task>
/broly-auto --criterion-mesh <task>  # Convergence criterion is an LLM-council vote
```

## Implementation

Direct alias. Skips baseline capture (use `/rsi` for full setup).

## Per-agent LLM assignment

Each broly role (`analyst` / `explorer` / `reviewer` / `pragmatist` / `devil` /
`integrator`) maps to a specific HAL provider/model via `lib/broly_council.py`.
Defaults use a 4-provider non-Anthropic mix (Gemini + xAI + DeepSeek + Groq) —
single-provider councils suffer from family-wide blind spots, so genuine
cross-model deliberation matters without consuming the weekly CCH pool.

Operators override via `~/.claude/config/broly-roles.json` (template at
`config/broly-roles.json.example`):

```json
{
  "assignments": [
    {"role": "analyst",     "model": "gemini/gemini-2.5-flash", "weight": 1.0},
    {"role": "explorer",    "model": "xai/grok-4.20-0309-non-reasoning", "weight": 1.0},
    {"role": "reviewer",    "model": "deepseek/deepseek-v4-pro", "weight": 1.5},
    {"role": "pragmatist",  "model": "groq/openai/gpt-oss-120b", "weight": 1.0},
    {"role": "devil",       "model": "xai/grok-4.20-0309-non-reasoning", "weight": 2.0},
    {"role": "integrator",  "model": "gemini/gemini-2.5-flash", "weight": 1.0}
  ]
}
```

Broly's output prints which model played each role:

```
[broly council]
  analyst    →  gemini/gemini-2.5-flash
  explorer   →  xai/grok-4.20-0309-non-reasoning
  reviewer   →  deepseek/deepseek-v4-pro  [weight 1.5]
  pragmatist →  groq/openai/gpt-oss-120b
  devil      →  xai/grok-4.20-0309-non-reasoning  [weight 2.0]
  integrator →  gemini/gemini-2.5-flash
```

## Criterion mesh (`--criterion-mesh`)

```
/broly-auto --criterion-mesh "<claim>"
```

The convergence criterion itself is evaluated by a mini-council: each role's
assigned LLM votes `met` / `unmet` / `inconclusive` (with confidence). Aggregation:

1. **Devil's veto** — if `devil` votes `unmet` with confidence > 0.85, verdict is
   `unmet` regardless of majority. Guards against optimistic agreement.
2. **Weighted score** — `Σ(weight × confidence × verdict_score) / Σ(weight)`, where
   `verdict_score` is `met=1.0`, `inconclusive=0.5`, `unmet=0.0`.
3. **Verdict band** — `met` if score ≥ 0.66, `unmet` if ≤ 0.34, else `inconclusive`.

Example output (vetoed case):

```
[criterion mesh] verdict: UNMET (devil's veto, confidence 0.91)
  Sonnet      (analyst)     met           (0.78)
  Grok 3      (explorer)    met           (0.65)
  Opus        (reviewer)    met           (0.82)
  Llama       (pragmatist)  inconclusive  (0.55)
  Opus        (devil)       UNMET         (0.91)  ← veto
  Opus        (integrator)  met           (0.71)
```

Harder to game than a single-model criterion — different models have different
blind spots. The veto threshold (0.85) is calibrated so devil only blocks when it
is *strongly* confident the claim isn't met; weak doubts don't override majorities.

## Related

- `lib/broly_council.py` — `BrolyRoleAssignment`, `dispatch_role`, `evaluate_criterion_mesh`
- `config/broly-roles.json.example` — operator-editable role/model defaults
- `skills/broly-meta-agent/SKILL.md` — full broly protocol
