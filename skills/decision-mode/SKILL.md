---
name: decision-mode
description: Routes structured decisions through bias-aware weighted analysis with cognitive checks, the operator locking criteria and close calls through the /hitl family (/asking-users, /creating-wizards, /hitl-decision-matrix), and a council gate that decides mechanically first whether a /broly council should sit at all. Use when /mode decision is active or /decision-mode-orchestration is invoked.
status: Active
effort: medium
version: 2.0.1
triggers:
  - /mode decision
  - decision mode active
  - /decision-mode-orchestration
integrates:
  - decision-locking
  - reasoning-with-ai
  - hitl
  - asking-users
  - creating-wizards
  - hitl-decision-matrix
---

# Decision Mode

Routes structured decisions through weighted criteria analysis with cognitive bias
checks. This is the orchestration layer for `/mode decision`: the order of the
work, where the operator decides, and whether a council sits. Commitment comes from
`decision-locking/SKILL.md`; the thinking posture from `reasoning-with-ai/SKILL.md`
(articulate-first, the human's judgement stays central); the operator checkpoints
from the `/hitl` family.

Revived in-repo on 2026-10-03 from `~/.agents/skills/.archive/decision-mode/SKILL.md`
(version 1.0.0, 2026-02-09). Until then `/decision-mode-orchestration` reached this
text through two redirects into a directory outside the repository. Now
`commands/decision-mode-orchestration.md` reads this file directly and
`modes/definitions/decision-mode.yaml` loads this skill and the HITL family with it.

## Protocols

### 1. Decision Workflow

1. **FRAME** -- Define the decision (what, why, constraints, timeline)
2. **CRITERIA** -- Establish weighted evaluation criteria
3. **OPTIONS** -- Generate all viable options (minimum 2)
4. **ANALYSE** -- Score options against criteria with evidence
5. **STRESS** -- Pre-mortem, red team, reversibility check
6. **DECIDE** -- Make recommendation with confidence level
7. **LOCK** -- Document rationale for future reference

### 2. Complexity Tiers

| Tier | Options | Action |
|------|---------|--------|
| SIMPLE | 2-3, clear criteria | Skip to ANALYSE |
| MODERATE | 3-5, competing criteria | Full workflow |
| COMPLEX | 5+, uncertainty, high stakes | Full workflow + stress testing |

### 3. Cognitive Bias Checks (ANALYSE Phase)

| Bias | Check |
|------|-------|
| Anchoring | Am I overweighting the first option considered? |
| Sunk cost | Am I factoring in irrecoverable past investment? |
| Status quo | Am I favouring "do nothing" without justification? |
| Confirmation | Am I seeking evidence that supports my preference? |

### 4. Decision Quality Signals

- Can you explain the decision to a sceptic?
- Would you make the same choice with 10x the stakes?
- What would have to be true for the opposite choice to be right?

## Human in the loop -- which primitive at which gate

The `/hitl` dispatch contract (`skills/hitl/SKILL.md`) picks the primitive from the
shape of the question:

| Gate | Shape | Primitive |
|------|-------|-----------|
| FRAME has one open field | single question | `/asking-users` |
| FRAME has several open fields (what, constraints, timeline, risk tolerance) | multi-step intake | `/creating-wizards` -- one step per field; a field a preceding brainstorm's SEED already settled is not asked again |
| CRITERIA weights | several related values locked at once | `/hitl-decision-matrix` -- a default weight with rationale and a falsifier per criterion, cascade detection (changing one weight re-asks the dependants), persisted to the vision doc and `lib/hypothesis_engine.py` so a repeated decision inherits them; weights a preceding brainstorm's CONVERGE locked for the same criteria are reused from that persisted matrix, never re-asked |
| Options within 10% of each other | choose ONE of the close options (single select, no free text) | `/asking-users`; when the tie-break IS the irreversible decision, one question carries both the option list and the irreversibility statement, so the operator is never asked twice |
| Irreversible or high-stakes DECIDE | one explicit confirmation | `/asking-users` (destructive-action gate) |
| New information re-frames the decision | yes/no re-entry | `/asking-users` |
| LOCK | not an operator question | `decision-locking` plus the matrix's persistence -- the confirmation already happened at DECIDE; LOCK records, it does not ask |

The quality gates every primitive shares -- an option list the operator can act on
without re-reading the transcript, the recommended default named first, no question
whose answer is already in the session -- are in `skills/hitl/SKILL.md`.

### Mandatory HALT points

Stop and ask (through the primitive in the table above) when:

- criteria weights need the operator's input;
- options are close (within 10% score);
- the decision is irreversible or high-stakes;
- new information changes the framing;
- risk tolerance has not been stated;
- stakeholder priorities conflict.

## The council gate -- does a /broly council sit at all?

`modes/definitions/decision-mode.yaml` keeps `broly.enabled: false` as the default.
A council is not free: it costs tokens, minutes and the operator's attention, and a
cheap, reversible, internal decision is settled faster by the seven steps above with
the operator at the gates. So whether a council sits is itself decided, in the
semi-LLM-augmentation shape (`rules/llm-augmentation-pattern.md`: a deterministic
floor first, a model only where the floor is uncertain), by
`lib/decision_mode/council_gate.py`:

1. **Mechanical floor on the stakes.** Three dimensions, each scored 0, 1 or 2
   against anchored rubrics, so two readers score one decision the same way:
   - *reversibility*: 0 = fully reversible, undone within a day with no residue;
     1 = reversible at material cost (hours of work, a visible retraction);
     2 = irreversible or legally/publicly final (a sent message, a deleted record,
     a public post, a signed commitment).
   - *cost*: 0 = negligible (under an hour, or a few dollars inside the standing
     budget); 1 = within the session's mandate (a day, or spend the operator
     already approved); 2 = above the mandate (multi-day, or any paid spend beyond
     the standing budget).
   - *blast radius*: 0 = this session or this repo; 1 = the team or other GRIP
     sessions and machines; 2 = external: a client, the public, a regulator, or
     production.
   The floor returns one of three verdicts with no model call:
   - `NO_COUNCIL` when the total is at most 1 and no dimension is at 2 -- the
     workflow and the operator settle it;
   - `FULL_COUNCIL` when the total is at least 5, or reversibility is exactly 2
     AND blast radius is exactly 2 ("irreversible and external") -- a `/broly`
     council sits;
   - `UNCERTAIN` otherwise.
   The thresholds and the rubric labels are configuration
   (`config/decision-mode-council-gate.json`), not code: V>> changes them in a
   /hitl round, or a KERN calibration run sets them; no new primitive is involved.
2. **Pre-council only where the floor is uncertain.** A cheap, brief
   pre-council -- GPT-6 Astra on the Codex seat, Fable 5.1 and DeepSeek V4 Pro,
   beside the near-zero-cost `bin/grip-sovereign-council` -- answers GO or NO-GO
   for the full council with one line of reason each, under `/efficient` (brief
   answers, capped tokens). The pre-council asks the SEATS, never the operator:
   it is not a HITL gate and overlaps no question in the table above. A majority
   of the answering seats convenes the full council on GO; a split runs one more
   pass with the dissent fed back, in the `/converge` manner, and then decides,
   a second tie resolving to `NO_COUNCIL`. A pass has quorum only when at least
   two seats answer; below quorum the gate returns `NO_COUNCIL` with the reason
   `no-quorum` -- a council that two answering seats cannot justify does not sit,
   and providers being down never convenes one.
3. **Sealed first pass.** The floor's scores and verdict, and the pre-council's
   answers when it ran, are sealed as a precog IDR
   (`lib/precog/idr.py append_idr`) before any council sits, so the record shows
   why a council did or did not convene, and the second pass can read the first.

Measured contract (pinned by the gate's tests): a certain floor makes zero LLM
calls; an uncertain floor makes one pre-council and writes one IDR. A seat that
cannot answer (Astra's five-hour window full, a provider down) is recorded as absent
and never counts as a NO-GO; it simply does not count, and the quorum rule above is
what stops absences from convening a council by default.

## Workflows

### Quick (binary choice, clear criteria)

1. Frame the decision
2. Quick options analysis
3. Recommendation with rationale
4. Output

**Council**: the floor returns `NO_COUNCIL` for this shape; no `/broly`.

### Standard (multi-option with trade-offs)

1. Full 7-step workflow
2. Cognitive bias checks at ANALYSE
3. Stress testing at STRESS
4. Decision locking at LOCK
5. Output decision document

**Council**: decided by the gate; typically `UNCERTAIN`, so a pre-council recommends.

### Deep (high-stakes, complex multi-stakeholder)

1. Full workflow with extended stress testing
2. Pre-mortem analysis
3. Red team challenge
4. Multiple decision locking rounds
5. Comprehensive decision package

**Council**: the floor returns `FULL_COUNCIL` for an irreversible, external
decision; the `/broly` council sits with a devil's advocate.

## Output Style

- Decision-first format: recommendation then reasoning
- British English throughout
- No emoji
- Use tables for option comparison
- Always document: Decision + Rationale + Alternatives + Risks accepted

## Cross-references

- `skills/decision-locking/SKILL.md` -- the commitment protocol LOCK uses
- `skills/reasoning-with-ai/SKILL.md` -- the articulate-first posture
- `skills/hitl/SKILL.md` -- the dispatch contract that picks the primitive
- `skills/asking-users/SKILL.md`, `skills/creating-wizards/SKILL.md`,
  `skills/hitl-decision-matrix/SKILL.md` -- the primitives named in the table
- `lib/decision_mode/council_gate.py` -- the council gate
- `skills/brainstorm-mode/SKILL.md` -- the mode that hands over here when a
  brainstorm's CONVERGE becomes a weighted choice

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0.1 | 2026-10-03 | Wording from the Pool-B council (seal `caec8c33e6aec844`): one-field FRAME row; tie-break is single-select and merges with an irreversible confirmation; LOCK records and does not ask; brainstorm weights reused; anchored 0-2 rubrics; irreversible/external defined as exact scores; pre-council never asks the operator; quorum of two answering seats |
| 2.0.0 | 2026-10-03 | Revived in-repo (0 hops); every HALT point names its /hitl primitive; "Broly disabled" replaced by the council gate (mechanical floor, pre-council only when uncertain, sealed IDR); mode YAML loads this skill and the HITL family |
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for decision mode |
