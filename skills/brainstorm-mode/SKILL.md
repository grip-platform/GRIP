---
name: brainstorm-mode
description: Routes ideation through divergent thinking with suspended criticism, with the operator in the loop at every phase gate through the /hitl family (/asking-users, /creating-wizards, /hitl-decision-matrix). Use when /mode brainstorm is active or /brainstorm-mode-orchestration is invoked.
status: Active
effort: medium
version: 2.3.0
triggers:
  - /mode brainstorm
  - brainstorm mode active
  - /brainstorm-mode-orchestration
integrates:
  - reasoning-with-ai
  - hitl
  - asking-users
  - creating-wizards
  - hitl-decision-matrix
---

# Brainstorm Mode

Routes ideation through divergent thinking phases with suspended criticism during
generation. This is the orchestration layer for `/mode brainstorm`: it says what
happens in which order and where the operator decides. The thinking posture comes
from `reasoning-with-ai/SKILL.md` (articulate-first, the human's intuition stays
central); the operator checkpoints come from the `/hitl` family.

Revived in-repo on 2026-10-03 from `~/.agents/skills/.archive/brainstorm-mode/SKILL.md`
(version 1.0.0, 2026-02-09). Until then `/brainstorm-mode-orchestration` reached this
text through two redirects into a directory outside the repository, and the mode
YAML loaded only `reasoning-with-ai`. Now `commands/brainstorm-mode-orchestration.md`
reads this file directly and `modes/definitions/brainstorm-mode.yaml` loads this
skill and the HITL family with it.

## Protocols

### 1. Brainstorm Workflow

1. **SEED** -- Define the challenge, constraints, and success criteria
2. **DIVERGE** -- Generate maximum ideas (quantity over quality)
3. **CLUSTER** -- Group related ideas into themes
4. **CONVERGE** -- Evaluate and select top candidates
5. **DEVELOP** -- Flesh out selected ideas into actionable concepts

### 2. Critical Rule: Suspended Criticism

During DIVERGE phase, suspend ALL criticism:

- No "but", no "however", no feasibility checks
- Pure generation only
- Evaluation happens ONLY in CONVERGE phase

### 3. Ideation Techniques

| Technique | Approach |
|-----------|----------|
| SCAMPER | Substitute, Combine, Adapt, Modify, Put to other use, Eliminate, Reverse |
| Analogical | How does nature/music/architecture solve this? |
| Constraint removal | What if budget/time/physics were unlimited? |
| Worst idea reversal | Generate terrible ideas, then invert them |
| Role storming | How would [person/company] approach this? |

### 4. Divergence Boosters

- "What's the opposite of that?"
- "What if we combined X with Y?"
- "What would a child suggest?"
- "What's the 10x version?"

Track idea count to maintain generation momentum.

## Human in the loop -- which primitive at which gate

The `/hitl` dispatch contract (`skills/hitl/SKILL.md`) chooses the primitive from
the shape of the question. Brainstorming has four gates; the first has two shapes, so
the table has five rows:

| Gate | Shape | Primitive |
|------|-------|-----------|
| SEED has one unknown (the challenge is clear, one constraint is missing) | single question | `/asking-users` |
| SEED has several unknowns (challenge, constraints AND success criteria all open) | multi-step intake | `/creating-wizards` -- one step per unknown, progressive disclosure, no question the operator has already answered |
| DIVERGE -> CONVERGE | readiness check, yes/no | `/asking-users` |
| CONVERGE criteria (several weights decided at once) | batched related decisions | `/hitl-decision-matrix` -- defaults with rationale and a falsifier per criterion, cascade detection between them, persisted so the next brainstorm inherits them; a decision-mode run that follows this brainstorm REUSES these weights for the same criteria and never asks them again |
| Top candidates before DEVELOP | pick-several confirmation | `/asking-users` with `multiSelect` |

The quality gates every primitive shares -- an option list the operator can act on
without re-reading the transcript, a recommended default named first, no question
whose answer is already in the session -- are in `skills/hitl/SKILL.md` and apply
unchanged here. A brainstorm that asks the operator the same thing twice has
failed the gate, not the operator.

### Mandatory HALT points

Stop and ask (through the primitive in the table above) when:

- the seed or challenge needs clarification;
- moving from DIVERGE to CONVERGE (confirm readiness -- the operator may want more ideas);
- the evaluation criteria for CONVERGE need the operator's weights;
- top candidates are selected (confirm before DEVELOP spends effort on them).

## Workflows

### Quick (5 ideas for X, single-domain)

1. Quick seed clarification (`/asking-users`, one question at most)
2. Direct divergent generation
3. Numbered output

**Broly level**: THREAD (de-escalated)

### Standard (feature ideation, solution exploration)

1. Full SEED -> DIVERGE -> CLUSTER -> CONVERGE -> DEVELOP workflow
2. Suspended criticism during DIVERGE
3. Visual clustering in CLUSTER phase
4. Selection via CONVERGE with criteria the operator weighted (`/hitl-decision-matrix`)
5. Actionable output from DEVELOP

**Broly level**: FORK (default)

### Deep (multi-domain brainstorm, 50+ ideas)

1. Multi-perspective ideation via Broly CLUSTER:
   - Technical perspective agent
   - Business perspective agent
   - Design perspective agent
   - User perspective agent
2. Cross-domain idea combination
3. Comprehensive ideation report

**Broly level**: CLUSTER (escalated). The fan-out is the operator's to brake:
`lib/mode-loader.sh broly status` shows the current trigger, and `/broly` runs it
explicitly when the trigger is manual.

**`/ultraideate` is a second deep-workflow option** that spares the Anthropic
weekly limit entirely: it runs DIVERGE through GRIP's zero-Anthropic sovereign
council instead of a Broly Claude-agent fan-out, scores every idea against
five operator criteria with TypeSafe's Jev (CONVERGE), and combines the
scores in code before the operator picks (COMBINE). Reach for it instead of
Broly CLUSTER when 40+ ideas are wanted off the Anthropic budget; the full
protocol and its honest limits -- Jev ranks, it does not judge truth, and the
operator's pick is the decision -- are in `skills/ultraideate/SKILL.md`.

### Full loop (the machine stages, with you between them)

Use this when an idea must survive before anyone builds it. The Workflow script API
has no call that asks the operator mid-run, so each machine stage returns to the main
thread and the operator decides there.

1. **SEED** -- Run the gates in the table above.
2. **DIVERGE** (machine) -- Run `/ultraideate`, which spends no Claude limit.
3. **You pick** -- Choose with `/asking-users` and `multiSelect`. Everything not picked is dropped.
4. **ATTACK** (machine) -- Climb the ladder in `rules/reasoning-lenses.md` for each pick, and stop at the first kill. A claim that gates an irreversible call, or one you ask to have attacked hardest, goes to `.claude/workflows/attack-fanout.js`, which is capped and spends Claude agents. Arm `bin/grip-fleet-watch` when you launch it.
5. **You pick** -- See each idea's fate: KILLED, SURVIVED SO FAR or UNTESTABLE. Only you take an idea forward.
6. **DEVELOP** -- A session with the repo builds what you picked.

`/brainstorm` (`skills/brainstorm/SKILL.md`) runs this loop end to end in one command.
It adds CLUSTER, a DECIDE step through `/hitl-decision-matrix` and the `/simple`
rendering. This file stays the protocol, and that one is the runner.

## Output Style

- Numbered ideas throughout
- British English throughout
- No emoji
- Visual clustering for themes
- Quantity-focused during DIVERGE, quality-focused during CONVERGE

## Cross-references

- `skills/reasoning-with-ai/SKILL.md` -- the articulate-first posture this mode runs in
- `skills/hitl/SKILL.md` -- the dispatch contract that picks the primitive
- `skills/asking-users/SKILL.md`, `skills/creating-wizards/SKILL.md`,
  `skills/hitl-decision-matrix/SKILL.md` -- the primitives named in the table
- `skills/decision-mode/SKILL.md` -- where a brainstorm's CONVERGE hands over when
  the choice between candidates is itself a weighted decision

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.3.0 | 2026-11-24 | Points to `/brainstorm`, the one-shot runner of the full loop |
| 2.2.0 | 2026-11-02 | The full loop: SEED, DIVERGE, you pick, ATTACK, you pick, DEVELOP, with the capped `attack-fanout` workflow for the hardest attacks |
| 2.1.0 | 2026-10-12 | `/ultraideate` named as a second deep-workflow option (Jev plan Phase 5.5) -- spares the Anthropic weekly limit |
| 2.0.1 | 2026-10-03 | Wording from the Pool-B council (seal `caec8c33e6aec844`): four gates with five rows said plainly; CONVERGE weights reused by a following decision-mode run |
| 2.0.0 | 2026-10-03 | Revived in-repo (0 hops); every HALT point names its /hitl primitive; mode YAML loads this skill and the HITL family |
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for brainstorm mode |
