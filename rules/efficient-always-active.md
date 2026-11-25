# /efficiency Always Active (PARAMOUNT)

Origin: V>> 2026-07-02 (verbatim): *"new canonical rule for GRIP on Claude Code --
/efficient is a skill that must **ALWAYS BE ACTIVE**!!!!"* Rule-at-N=1 per
`knowledge-maturation-functor.md` (operator-declared PARAMOUNT). The skill
(`skills/efficiency/SKILL.md`, v2.0.0; renamed from `efficient` on 2026-10-28, with
`/efficient` redirecting) remains the detailed mechanics + meter; this rule flips it
from opt-in wrapper to standing default, push-based every session. Its loop-side
half, that a fleet, loop or workflow you start is watched and never left stuck or
burning, is `workflows-never-stuck-never-burning.md` (V>> 2026-10-28).

## The Law

> **Every turn in every GRIP session executes under the /efficiency discipline by
> default: minimum token cost, zero dropped facts.** The skill's core law governs
> always — *reasoning is cheap, reads and output are the cost*: think maximally,
> read minimally, emit once.

Standing obligations (the skill's E-protocol, now default-on):

- **E1 retrieval ladder** (= Rule 0 GRIP-First, applied mechanically): session
  context → KONO → known paths → targeted Grep → Explore LAST, documented reason
  per escalation. Exit at the first resolving tier; never read what you can
  already state.
- **E2 compressed ingestion**: targeted `Read(offset, limit)` over whole-file
  reads; `lib/fold_compress.fold()` for >~300-line inputs; RTK-routed Bash
  (`bash-safety.md`); re-reading an unchanged file forbidden (Rule 2 cache).
- **Depth-0 main-thread bias**: no agent/council below the
  `dispatch-economics.md` gates (capability, ~3:1 useful-work:floor, chain-first,
  depth ladder); leanest agent type when dispatch IS justified.
- **Batched tool calls**: independent calls in ONE message
  (`parallel-default-over-either-or.md`).
- **Single-pass correct-by-construction writes**: Rule 18 JIT — no
  write-then-fix churn.
- **E0 budget hypothesis**: durable loops pre-register
  `{tool_calls, files_read, fix_iterations, subagents}` via `lib/efficient_meter`
  / the hypothesis engine; small tasks state the prediction inline. An explicit
  `/efficient` invocation now means "re-assert + METER this run", not "switch
  efficiency on".

## What this rule never overrides

Token savings never outrank: the Auto-Clarity rails (security /
irreversible-action confirmations / multi-step clarity), Rule 17 user-facing
surfaces (channel relays, client/legal content, commit messages, PR bodies stay
full plain English), verify-canonical + R0 + prove-it evidence obligations, and
every mechanical gate. The lossless clause binds (`canonical-output-style.md`):
efficiency = fewer tokens for the SAME facts and rigour — never fewer facts.

## Falsification

Wrong if, over ~30 sessions it governs: (a) the always-on discipline measurably
drops facts or rigour (lossless violated → tighten the discipline, never drop the
rule); (b) the depth-0 / read-minimal bias suppresses a dispatch or verification
whose absence costs more downstream than the tokens saved (mis-tuned →
recalibrate against `dispatch-economics.md`, keep the default); (c)
tokens-per-deliverable does not improve vs the pre-rule baseline (inert →
delete). Track: E0 verdicts in `state/efficient/runs.jsonl`, weekly
tokens/deliverable, facts-dropped incidents (target 0), operator efficiency
corrections (target 0).

## Scope

Every session, mode, subagent, and autonomous loop on Claude Code. Sits beside
`efficiency-rules.md` (Rules 0/2/18), `dispatch-economics.md`,
`parallel-default-over-either-or.md`, and `canonical-output-style.md` (lossless
+ Auto-Clarity). Mirror into `~/.hal/rules/` if HAL sessions gain an equivalent
wrapper.
