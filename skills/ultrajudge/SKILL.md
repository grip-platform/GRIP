---
name: ultrajudge
description: Multi-model verdict council with role assignment (devil/pragmatist/futurist/integrator/historian). N models render verdicts under assigned perspectives, weighted into a decision matrix with explicit dissents. Joins /ultrathink, /ultraplan, /ultrareview, /ultraread as the verdict limb of the ultra* family.
command: /ultrajudge
effort: medium
version: 1.0.0
triggers:
  - /ultrajudge
  - ultrajudge
---

# UltraJudge — Multi-Model Verdict Council

N models render verdicts on a question or proposal, each assigned a distinct
perspective role. Results are weighted into a decision matrix with explicit dissents
and a synthesised recommendation.

Joins `/ultrathink`, `/ultraplan`, `/ultrareview`, `/ultraread` as the **verdict**
limb of the `ultra*` family.

## Why This Exists

When a decision has high stakes or genuine uncertainty, a single model's answer
is one voice. Different roles inject structured perspective bias — the devil's
advocate argues hardest against, the pragmatist evaluates cost/benefit, the
futurist traces second-order consequences. The integrator synthesises across
all of them. The result is a decision matrix that surfaces the strongest case
against (not just the majority view) alongside a weighted recommendation.

## Roles

| Role | Bias | Default model |
|---|---|---|
| `devil` | Find the strongest case AGAINST | opus |
| `pragmatist` | Does this actually matter? Cost/benefit | sonnet |
| `futurist` | Second-order consequences over 12-24 months | grok |
| `integrator` | Synthesise across the other roles | llama-3.3-70b |
| `historian` | Has this been tried? What happened? | opus (round-robin) |

## How It Works

1. **Parse question** — the proposal or decision to evaluate.
2. **Resolve roles and models** — from `--roles`/`--models` flags or defaults.
3. **Pair roles to models** — round-robin if role count exceeds model count.
4. **Dispatch in parallel** — each model receives the question with its role
   injected into the system prompt to steer perspective.
5. **Compute weighted verdict** — `Σ(weight × confidence × verdict_score)`,
   where verdict_score is +1 (proceed), 0 (rethink), -1 (abandon).
   Normalised to 0-1. Deterministic for fixed inputs.
6. **Synthesise** — decision table, dissents section, integrator recommendation.
7. **Write deliverable** — Markdown to `state/ultrajudge/<slug>-<timestamp>.md`.

## Usage

```bash
/ultrajudge "Should we adopt the syscall doctrine repo-wide?"
/ultrajudge --roles devil,pragmatist,futurist "<question>"
/ultrajudge --models opus,grok,sonnet,llama "<question>"
/ultrajudge --weights '{"devil": 2.0, "pragmatist": 1.5}' "<question>"
```

Via CLI directly:
```bash
python3 -m lib.ultrajudge "Should we migrate embeddings to ollama?"
python3 -m lib.ultrajudge --weights '{"devil": 2.0}' "Merge auto-rsi and rsi-optimal?"
```

## What Gets Written

`state/ultrajudge/<slug>-<timestamp>.md` containing:

- **Weighted verdict** — PROCEED / RETHINK / ABANDON with confidence %
- **Decision table** — role, model, verdict, confidence, one-line reasoning
- **Dissents** — full reasoning from roles that disagreed with the majority
  (devil's advocate always surfaced here when it dissents)
- **Recommendation** — integrator's synthesis, or weighted summary if no integrator

## Weighting

Default: all roles weight 1.0 (equal). Custom weights via `--weights` JSON map.
`{"devil": 2.0}` doubles the devil's vote — useful when failure cost is high.

The weighted confidence formula:
```
raw = Σ(w_i × conf_i × score_i) / Σ(w_i × conf_i)
normalised = (raw + 1) / 2    # maps [-1,1] → [0,1]
label = "proceed" if raw > 0.1 else "abandon" if raw < -0.1 else "rethink"
```

## HAL Integration

Dispatch is LIVE by default: each (role, model) seat makes one real provider
call through `lib/hal_llm_adapter.HALLLMAdapter.infer(model=..., audit=False)`
with the question in the prompt, and the deliverable's Model column names the
model HAL **actually served** (server-reported), never merely the one
requested. `ULTRAJUDGE_USE_HAL=0` selects the labelled stub path (hermetic
tests, explicit offline runs).

A seat whose live call fails degrades to the stub, and every stub-sourced row
self-identifies (`-stub` model label, `[stub]` reasoning) with a banner above
the verdict table. If EVERY seat degrades on the live path, the run REFUSES
(`HALUnavailableError`, exit 2) instead of emitting a canned verdict —
"I'd rather it emit nothing than a fabricated PROCEED" (V>>).

## Falsification

This skill is wrong if:
0. Two materially different questions produce byte-identical verdict bodies
   (the question is inert — the 2026-08-17 defect; covered by
   `test_question_changes_the_verdict` and `test_question_reaches_the_prompt`).
1. All models produce identical verdicts regardless of role assignment (role
   steering not working — test by comparing devil output vs pragmatist output
   for the same question via `test_role_steering_visible`).
2. Weighted-verdict math is non-deterministic for fixed inputs (would mean
   floating-point ordering dependency — covered by `test_weighted_verdict_split`).
3. Dissents section appears when all verdicts are unanimous (false positive —
   covered by `test_dissent_section_only_appears_when_real_dissent`).

## Related

- `/ultrathink` — single-model deep reasoning before action
- `/ultraplan` — context-aware remote planning session
- `/ultrareview` — parallel specialist code review
- `/ultraread` — multi-model comprehension council
- `lib/ultrajudge.py` — orchestrator source
- `lib/ultraread.py` — comprehension council source
