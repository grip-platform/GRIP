---
name: evo-tool-limitation-calibrator
description: "Distinguishes genuine tool limitations from intentional GRIP gate blocks during /evo calibration. Provides evidence keywords that dynamically enrich the calibration pipeline."
status: Active
effort: low
version: 1.0.0
lineage: genesis:evo:facet:tool_limitation
friction_type: tool_limitation
evidence_keywords:
  - "guardrail"
  - "infra test gate"
  - "tool substitution"
  - "worktree isolation"
  - "push blocked"
  - "permission gate"
  - "budget gate"
  - "push gate"
  - "git push gate"
  - "context gate"
  - "blocked by hook"
  - "pretooluse hook"
  - "concurrent session"
---

# evo-tool-limitation-calibrator

Genesis micro-skill that closes the /evo feedback loop for `tool_limitation` events.

## Problem

`tool_limitation` has a 31.5% false-flag rate — the highest among major friction types.
Many events classified as "tool couldn't do X" are actually "GRIP intentionally prevented X"
via guardrails, permission hooks, or budget gates.

## Evidence Patterns

### True Friction (keep as friction)

These indicate the tool genuinely could not complete the task:

- "MCP server not responding" — external dependency failure
- "tool does not support this operation" — genuine capability gap
- "command not found" — missing binary or path issue
- "file too large to read" — hard system limit

### False Flags (GRIP working as designed)

These are intentional blocks, not tool failures:

- "GUARDRAIL: TOOL SUBSTITUTION" — hook redirecting to better tool
- "GUARDRAIL: INFRA TEST GATE" — requiring tests before push
- "GUARDRAIL: WORKTREE ISOLATION" — enforcing concurrent safety
- "GUARDRAIL: GIT PUSH GATE" — protected branch enforcement
- "PreToolUse hook blocking" — any gate denying a tool call
- "permission gate" / "budget gate" — resource controls

## How It Works

The `evidence_keywords` in this skill's frontmatter are read by `lib/evo.py`
at calibration time via `load_genesis_evidence()`. They are matched against
friction event detail text using the same `match_evidence()` function as
hardcoded evidence sets. Weight emerges from keyword density — no special
treatment.

This is additive only: genesis evidence can increase false-flag detection
but never decrease it. The devil's advocate (Tier 4) still challenges
high FF rates with weak evidence.

## Lifecycle

1. Created by `/evo` proposal (FF rate > 30% for `tool_limitation`)
2. Evidence keywords extracted from frontmatter at next `/evo` run
3. If keywords improve calibration accuracy: gene fitness rises
4. If keywords over-fit (match everything): devil's advocate corrects
5. If gene never invoked after nursery period: natural fitness decay
