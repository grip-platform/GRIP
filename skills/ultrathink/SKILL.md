---
name: ultrathink
description: Always-active reasoning protocol layering GRIP's six mechanical HALT guards on top of Claude Code's native extended thinking. Use when invoking /ultrathink or when a task needs mandatory user consultation gates. /ultrathink followed by a question also runs a three-vendor deliberation (Fable 5.1, GPT-6 Astra, DeepSeek v4 Pro) that debates and falsifies before the main thread synthesises.
---

# Ultrathink

Always-active reasoning protocol with mandatory user consultation. Harnesses Claude
Code's native extended thinking (`alwaysThinkingEnabled`) as substrate, adds GRIP's
6 mechanical guards on top.

## Status

- **Version**: 4.1.0
- **Status**: Active (PARAMOUNT)
- **Auto-loads**: At session start
- **Scope**: Reasoning protocol for ANY project type

## Architecture (CC Auto-Harness Pattern)

```
Layer 0: Claude Code extended thinking (alwaysThinkingEnabled: true)
  → Adaptive reasoning depth per task (auto-budgeted by CC)
  → Thinking blocks visible in output

Layer 1: GRIP Ultrathink (this playbook)
  → 6 guards that HALT on uncertainty
  → Confidence guard integration (lib/confidence_gate.py)
  → Mandatory AskUserQuestion protocol
  → Workflow phases with checkpoints

Layer 2: Trio deliberation (only on an explicit /ultrathink <question>)
  → Fable 5.1 + GPT-6 Astra + DeepSeek v4 Pro, three vendors, in parallel
  → Round 2: each seat falsifies the other two; the main thread synthesises
```

Claude Code provides the substrate (extended thinking). GRIP provides the guards
(mechanical enforcement). The delta: CC thinks deeper, GRIP ensures it stops and
asks when uncertain.

## The Six Guards (HALT Triggers)

All guards trigger: HALT + AskUserQuestion (MANDATORY)

| Guard | Trigger | Mechanical Enforcement |
|-------|---------|----------------------|
| 1 | Confidence below threshold | `lib/confidence_gate.py` — 3-tier cascade (heuristic → fast council → full council) |
| 2 | Uncertainty detected | Hedging language patterns in transcript |
| 3 | Assumption identified | "Assuming the user wants..." |
| 4 | Destructive action | delete, overwrite, publish, commit, deploy, send |
| 5 | Multi-step task | Checkpoint every 3 steps |
| 6 | UI/UX decision | icon, colour, layout, typography, spacing, component |

### Guard 1: Confidence Measurement (Gen 491)

Guard 1 is now mechanically enforced via `lib/confidence_gate.py`, wired into
`context-gate-hook.py` at pipeline position 8. Three tiers:

- **Tier 0 (heuristic, <5ms)**: Pattern-based uncertainty detection. Always runs.
- **Tier 1 (fast council, ~2-5s)**: Two-provider HAL council. Fires on low heuristic.
- **Tier 2 (full council, ~10-30s)**: Multi-provider weighted median. Fires on borderline.

This is no longer a prompt instruction — it's a deterministic mechanism that runs
outside model weights. The confidence guard cannot be persuaded.

## PARAMOUNT Integration

AskUserQuestion is NEVER optional when any guard triggers. See `asking-users`
playbook for full protocol.

### Key Incidents

- **Gen 191**: Website incident — violated Guard 6, removed brand icon without asking
- **Gen 193**: YAGNI refactor — expanded from 3 guards to 6
- **Gen 218**: `asking-users` PARAMOUNT playbook integration
- **Gen 491**: Guard 1 mechanised via HAL council confidence measurement

## Design Principles

**YSH (You Should Have)**: The dialectical inverse of YAGNI. When a pattern has
proven itself 3 times, abstract it NOW — don't wait.

## Workflow Phases

| Phase | Action | Key Step |
|-------|--------|----------|
| 1. Understand | Identify assumptions | Clarify if needed |
| 2. Plan | Enumerate approaches | Evaluate tradeoffs |
| 3. Verify | Confirm approach | AskUserQuestion |
| 4. Execute | Implement | Test and validate |
| 5. Confirm | Demonstrate results | Verify satisfaction |

## Trio deliberation (V>> 2026-10-03)

> "Amend /ultrathink to always use Fable 5.1 and GPT-6 Astra and DeepSeek v4 Pro in
> parallel and reasoning together, and debating and falsifying ... Remember RAILLM ...
> Going from SOTA frontier models -> replacing one, or go one level lower -> etc."
> -- V>>, 2026-10-03

### When it runs, and what "always" means

"Always" is read as: **whenever /ultrathink deliberates**. That is an explicit
`/ultrathink <question>`, or a deliberate library call from another skill
(`lib.ultrathink_trio.deliberate`). It does **not** mean a three-model council on every
turn. The always-active half of /ultrathink, the six HALT guards above, is unchanged. A
council on every prompt would cost more attention, latency and quota than it returns.
This is an interpretation, and V>> can overrule it.

Bare `/ultrathink` still prints this protocol and calls nothing.

### The three seats, and why these three

| Seat | First rung | Vendor |
|------|-----------|--------|
| `fable` | Fable 5.2 | Anthropic |
| `astra` | GPT-6 Astra on the Codex subscription, max effort | OpenAI |
| `deepseek` | DeepSeek v4 Pro through HAL | DeepSeek |

Three frontier models from three vendors. A model checking a model from its own vendor
shares that vendor's blind spots (`rules/exogenous-anchor-law.md`), so the seats that
answer are always three distinct vendors.

### Two rounds, then synthesis in the main thread

1. **Round 1, positions.** The seats answer in parallel and independently. Each puts its
   position first, gives its evidence, and names the observation that would prove it wrong.
2. **Round 2, debate and falsify.** Each seat receives the other two positions and, for
   each, writes CLAIM, EVIDENCE, REFUTER (what would show it wrong) and VERDICT
   (REFUTED, WEAKENED or STANDS).
3. **Synthesis, by the main thread, never a fourth model call.** The tool prints a brief
   laying out the positions, the falsifications and where the seats disagree. The main
   thread writes the synthesis: each claim stays with the seat that made it,
   disagreements are settled on evidence or named as open, and positions are never
   averaged into a compromise.

Why the main thread: when the session runs on Fable, the Fable seat **is** the main
thread, and a second `claude -p` call would spend the weekly limit twice (the vendor said
of Fable 5.1 that it can use up to half the weekly limit). So in a Fable session the Fable
seat makes no call: pass `--session-fable`, or set `GRIP_SESSION_MODEL` to a Fable id, and
optionally put your own round-1 position in `--fable-position-file` so the other two
seats can test it. The sealed record names the Fable that `GRIP_SESSION_MODEL` names
(`claude-fable-5-2[1m]` is recorded as `claude-fable-5-2`), or the first Fable rung when
none is named. Outside a Fable session (Codex, OpenCode, Opus) the Fable seat runs as a
`claude -p` subprocess on the subscription login.

### The fallback ladder (RAILLM, per seat)

Each seat climbs its own ladder. A rung that fails is recorded and the next one tried; a
rung whose precondition is missing (no API key) is recorded as skipped; a seat that runs
out is recorded as degraded. This is RAILLM, the Redundant Array of Independent LLMs
(`~/.hal/docs/RAILLM.md`, on the AI-syscall shim of
`docs/adr/0001-ai-syscall-canonical-shim.md`), applied seat by seat.

| Seat | Rung 1 | Rung 2 | Rung 3 | Rung 4 |
|------|--------|--------|--------|--------|
| fable | `fable-5.2` (claude-fable-5-2) | `fable-5.1` (claude-fable-5-1) | `opus-5.5` (claude-opus-5-5) | none |
| astra | `astra-codex` (gpt-6-astra, Codex subscription) | `astra-hal` (openai/gpt-6-astra through HAL; paid, so only with `GRIP_CODEX_ALLOW_PAID=1` and an OpenAI key) | `grok-4.6` (xAI) | `gemini-carbon` (gemini-4-carbon) |
| deepseek | `deepseek-v4-pro` (through HAL) | `deepseek-flash` (through HAL) | `groq-gpt-oss-120b` (Groq) | `gemini-flash` (gemini-2.5-flash) |

- The Fable seat has no third rung: an Anthropic seat never falls to another vendor
  silently. It is recorded as degraded instead.
- **Vendor disjointness is enforced in code** (roundtable guard F-1). A rung that would
  put a second seat on a vendor already seated is refused before any call, and the ladder
  moves on. Vendors are read off the **model's maker**: Groq's gpt-oss-120b is an OpenAI
  model, so it is refused while Astra holds OpenAI.
- Fallbacks are assigned in waves, in seat order, so the rung a seat lands on never
  depends on which call happened to finish first.
- Round 2 starts each seat on the rung that answered round 1, and only ever goes down.
- An answer that a known different vendor served is refused under the rung's name.

### Refuse rather than fabricate

Fewer than two seats answering is a refusal (`UltrathinkUnavailable`, roundtable guard
F-3), and nothing is written. One seat is returned only when asked for
(`--single-seat-ok`), and it is labelled "single seat, not a council". A missing seat is
never invented.

### Cost posture

- **Astra** rides the Codex ChatGPT subscription's 5-hour window. A 429 or "out of
  credits" means the window is full: it is recorded, the seat falls back, and nobody tops
  up (no new paid spend until about 2026-10-11). The fallback skips `astra-hal`, which
  bills the OpenAI API key, unless `GRIP_CODEX_ALLOW_PAID=1` is set (the same opt-in as
  `bin/grip-codex-ladder`), and the footer names the skip
  (`rules/gpt-on-the-subscription-never-credits.md`).
- **DeepSeek** is cheap pay-per-token through HAL.
- **Fable** is the session itself when you run on Fable; otherwise one `claude -p` call
  per round on the subscription.

A full run takes minutes, not seconds: Astra at max effort took 3m10s for a single
answer (measured 2026-10-03).

### Records and seals

- The full record (question, positions, falsifications, every rung tried) is written to
  `$CLAUDE_DIR/state/ultrathink/<sha256>.json`, which is local and gitignored.
- It is sealed with a sha256 over canonical JSON (roundtable guard F-2): the same inputs
  give the same digest, and `lib.ultrathink_trio.recompute(record)` shows any edit.
- The shared IDR chain gets one leaf carrying only the sha256 and seat/rung metadata,
  never the question or a position (`rules/operator-anonymity.md`).

### How to run it

```bash
bin/grip-ultrathink "Should we shard the embeddings table?"
bin/grip-ultrathink --session-fable --fable-position-file my-view.md "<question>"
bin/grip-ultrathink --json --rounds 1 "<question>"
```

Exit 0 means it deliberated; 2 means it refused (fewer than two seats, or a vendor
clash); 64 means a bad request. The footer names the rung that answered for each seat,
its latency, any degraded seat and why, the record path and the IDR leaf. A live run
spends Astra's window, so the first live run is V>>'s to make.

### Falsification

Wrong if, over the next ~10 trio runs: the round-2 falsifications never change what the
synthesis concludes compared with the round-1 positions alone (then the debate is
ceremony, and `--rounds 1` should become the default); or the ladder never once stands in
for a dead seat while adding latency (then it is weight without work); or the main
thread's synthesis proves measurably worse than a fourth-model judge would have been
(then the weekly-limit trade should be revisited).

## CC Substrate Configuration

```json
// settings.json (already configured)
{
  "alwaysThinkingEnabled": true,
  "outputStyle": "Explanatory"
}

// config/grip.json
{
  "extended_thinking": true
}
```

Adaptive thinking (auto-budgeted) is preferred over fixed `budget_tokens` — Claude
adjusts reasoning depth per task automatically. GRIP's guards add the safety layer
that CC's native thinking doesn't provide.

### Model + effort harness (CC 2.1.154 → 2.1.197)

CC now exposes native effort tiers and thinking-budget controls; ultrathink routes
over them as substrate (adopt-native, keep GRIP's guards on top):

| Task shape | Model + effort | Native control |
|-----------|----------------|----------------|
| Routine reasoning / coding | Sonnet 5 (CC 2.1.197 default, 1M) | default |
| Surgery / VERIFY / hardest architecture | Opus 4.8 at `/effort xhigh` | `/effort xhigh` (CC 2.1.154) |
| Frontier-generation onboarding opt-in | Opus 4.8 or Fable 5 (Claude 5) | `/model` picker |
| Budget-bounded thinking | any | `/config thinking=false`, `MAX_THINKING_TOKENS=N`, `--thinking disabled` (CC 2.1.181 / 2.1.166) |

The delta ultrathink defends: CC picks a model + effort; GRIP's six guards still HALT
on uncertainty regardless of tier. A cheaper default (Sonnet 5) never relaxes the
confidence / destructive / UI guards — they are mechanical, not model-conditioned.
Canonical model facts: `config/model_catalog.yaml` + `.claude/CLAUDE.md`.

## ★ Insight Block Format (R0-aligned, MANDATORY)

When the Explanatory output style emits `★ Insight ─────` blocks, every
block must follow this format. This is the canonical contract — the auto-memory
hook (`hooks/auto-memory-hook.py`) and the insight harvester
(`lib/insight_collector.py`) both depend on the structure.

### Quality Criteria

1. **Cap: 2-3 bullets per block.** More than that signals the model is
   summarising, not insighting. Split into separate `★ Insight` blocks if
   genuinely distinct insights need to be captured.

2. **Evidence anchor in every bullet.** Each bullet must point to a specific
   file path, line number, observed test name, or observed output. Banned:
   "this suggests", "it seems", "in general", "best practice". Required:
   `lib/foo.py:42`, "test_bar passes", "log line 'X' fired N times".

3. **R0 falsification condition for any generalised claim.** If a bullet
   says "X is always Y" or "X never Y", it must explicitly state what
   observation would disprove the claim. Specific empirical observations
   (single-instance facts) are exempt — they ARE their own falsification.

### Banned Patterns (R0 axis 1 — testability)

- Surface-level analogies that map theoretical frameworks to operational
  steps without ground truth (e.g. "this is the Fibonacci of X")
- "Anti-pattern" stated universally from a single observed instance
  (Rule 16 — Insight Quality Gate)
- "Obviously" / "clearly" / "of course" — these are tells for unfounded
  generalisation
- Numeric trajectories fabricated to suggest mathematical structure where
  none exists (Rule 16)

### Valid Block Example

```
★ Insight ─────────────────────────────────────
- hooks/auto-memory-hook.py:99 scans the entire transcript per Stop
  event, not just the last turn — recovered 5 insights in a session
  where the previous "last-turn-only" implementation captured 0
- This would be wrong if the dedup layer (lib/insight_collector.py:43)
  failed under repeated insight strings across the full scan; current
  test_insight_collector.py:153 covers exact-match dedup but not
  near-duplicate semantic dedup
─────────────────────────────────────────────────
```

### Invalid Block Example (R0 violations marked)

```
★ Insight ─────────────────────────────────────
- Silent sys.exit(0) is always an anti-pattern   ← R0 axis 5: scope dishonesty
- The system clearly works better with caching   ← R0 axis 1: not testable
- This pattern is cleaner and more maintainable  ← R0 axis 2: no evidence
- It would obviously scale better                ← banned word + no falsification
─────────────────────────────────────────────────
```

### Cross-Reference

The full R0 specification lives in `rules/r0-critical-thinking.md`. That
file IS the source of truth for the six-axis test (Testability, Technical
Rigour, Novelty, Precision, Scope Honesty, Red Flags). This block format
is the operational application of those axes inside the Explanatory output
style.

## Related Playbooks

- `asking-users` — PARAMOUNT source of truth for AskUserQuestion protocol
- `creating-wizards` -- multi-step wizard flows, sibling of `asking-users`
- `hitl` -- dispatch contract that chooses between them
- `context-gate` — 85% context threshold monitoring
- `confidence_gate.py` — Mechanical confidence measurement (Guard 1)
- `rules/r0-critical-thinking.md` — Canonical R0 six-axis specification

## Changelog

| Version | Gen | Changes |
|---------|-----|---------|
| 4.1.0 | 2026-10-03 | Trio deliberation on an explicit `/ultrathink <question>`: Fable 5.1, GPT-6 Astra and DeepSeek v4 Pro in parallel, then debate and falsify, with a per-seat RAILLM ladder; the six guards unchanged |
| 4.0.0 | 491 | CC auto-harness: native extended thinking as substrate, Guard 1 mechanised via HAL council, plain-language terminology |
| 3.0.0 | 220 | Expanded to 6 guards, `asking-users` integration |
| 2.0.0 | 193 | YAGNI refactor, expanded from 3 guards |
| 1.0.0 | 107 | Initial creation |

---


