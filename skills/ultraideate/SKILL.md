---
name: ultraideate
description: Many ideas from outside models, ranked by Jev, picked by the operator. The ultra* family's ideation limb -- brainstorm mode's deep-workflow option that does not spend the Anthropic weekly limit. Use when invoking /ultraideate, or when brainstorm mode's DIVERGE needs 40+ ideas without a Broly agent fan-out.
---

# /ultraideate

DIVERGE, CONVERGE, COMBINE -- in that order, with the operator deciding last.

1. **DIVERGE.** GRIP's zero-Anthropic sovereign council
   (`lib/sovereign_council.py`, `DEFAULT_SPEAKERS`: deepseek, xai, astra, groq)
   brainstorms the topic, criticism suspended. No model argues against an idea
   here; that happens nowhere in this skill at all, by design -- only Jev's
   per-criterion scores, in CONVERGE, and the operator's own judgement at the
   end.
2. **CONVERGE.** TypeSafe's Jev, through HAL's `decide`, scores every idea
   against five criteria -- novelty, delight, measurability, feasibility, data
   safety -- as batched Score questions.
3. **COMBINE.** Code, never a model, takes Jev's five scores and the
   operator's weights (equal, until a real weighted matrix exists -- see
   "Weights" below) and produces one ranking.
4. The operator picks from the ranked shortlist. DEVELOP (brainstorm mode's
   next phase) continues with the PICKED ideas only.

Backing module: `lib/ultraideate.py`. CLI: `~/.grip/venv/bin/python -m
lib.ultraideate rank "<topic>" [--ideas-file FILE | --seeds-file FILE] [--top N]
[--json] [--seal]`, `... picks <run_id> [ID ...] --topic "<topic>" [--k K] [--seal]`
to record (and seal) the operator's pick, and `... report [--log-file FILE] [--json]`
for the measure.

## The honest limits (state these to the operator, not just to yourself)

- **Jev ranks. It does not judge truth.** A Score answer is a probability
  distribution over five ordinal levels from one model, with no calibration on
  file (`calibrated: false` on every answer) -- it is a machine reading, not a
  verdict, and this skill's own shortlist says so on every line.
- **The ideas themselves come from outside models, and their TEXT could steer
  Jev.** A published adversarial benchmark reports that appending a single
  unverified opinion flipped 12.1% of a decision model's answers. An idea
  phrased persuasively is not thereby a better idea.
- **So the ranking is advice, and the operator's pick is the decision this
  skill actually measures** (`record_picks` / `report`, below) -- never treat
  Jev's order as a verdict to execute without a human choosing from it.
- **A re-run of the same topic will not always rank identically.** A live
  check (`docs/measurements/2026-10-12-ultraideate-live-run.md`) found real
  jitter in Jev's own Score answers to the exact same batched request --
  small (often 0.005-0.03), but enough to occasionally flip a criterion
  across the "uncertain" line and move an idea's rank. This is Jev, not
  COMBINE: the combining step is provably deterministic given the same
  scores.
- Jev's criteria are batched Score questions, never a safety gate and never
  asked to approve anything; nothing here blocks on Jev, and a failed or
  partial Jev call just leaves an idea's criteria unscored rather than guessed,
  with the reason named. A run in which NOTHING was scored says so in its note
  (the distinct reasons, such as `no_answer:decide_unreachable` or
  `converge_off`), records no shortlist for the tick, and returns an empty run
  id: a set ranked by divergence order alone is not advice (GRIP#8824).

## The protocol, step by step

1. **Run DIVERGE + CONVERGE together:**
   `~/.grip/venv/bin/python -m lib.ultraideate rank "<topic>" --top 10 --json`
   from the GRIP repository root. The topic is the operator's own words --
   it goes through the decision-model privacy floor before anything is sent
   anywhere (`lib/decision_privacy.py`), same as every other Jev caller.
   A council seat going quiet (the subscription Astra/Codex seat is routinely
   out of quota) is expected and not a reason to stop; read the printed note
   for how many speakers actually answered. A speaker's reasoning block
   (`<think>…</think>`) is dropped before its list is read, and an item whose
   every word the topic already holds is an echo of the prompt, not an idea.
2. **Read the JSON.** Each ranked entry carries `id` (what a pick names),
   `idea`, `combined`, `scores` (only the criteria Jev actually settled),
   `unscored` (named, never invented), `skipped`, the reason for each
   unscored criterion, and `seed` (true for an idea from `--seeds-file`). With
   `--ideas-file`, an idea the file does not attribute is `proposed_by` the
   file (`file:<name>`), never the operator.
   **If `run_id` is empty, STOP HERE.** Nothing was scored, the note says why
   (`CONVERGE scored nothing (...)`), and the entries are in the order they
   arrived, not a ranking. Tell the operator that in one line, offer to rerun
   when the reason is cleared, and do none of steps 3 to 5: no shortlist is
   shown as ranked, no pick is asked for, and `record_picks` is not called
   (it writes nothing for an empty run id either way).
3. **Show the shortlist to the operator**, each idea with its five scores (or
   "unscored: <reason>" for any it lacks) and the label **"machine reading,
   precision unmeasured"** on every line -- this is not decoration, it is the
   honest-limits statement above, rendered where the operator will actually
   read it. Say it again here, in your own words, at the exact moment you show
   the list: Jev ranked these, it did not judge whether any of them are true
   or good.
4. **Ask with ONE `AskUserQuestion` question, `multiSelect: true`**, options =
   the shown shortlist (recommended picks named first, per `skills/hitl/SKILL.md`'s
   shared quality gates) -- this is the "pick-several confirmation" shape
   `skills/brainstorm-mode/SKILL.md`'s HITL table already names for top
   candidates before DEVELOP; `/ultraideate` is simply where those candidates
   came from this time.
5. **Record the picks** so the measure below has data. After a shelled-out
   `rank`, use the `picks` subcommand with every id the operator picked:
   ```bash
   ~/.grip/venv/bin/python -m lib.ultraideate picks <run_id> <id> <id> --topic "<the same topic>" --k 5 --seal
   ```
   `run_id` is the `run_id` field of the rank command's JSON (or the "run id"
   line of its text output). Every `rank` run appends its shortlist to
   `logs/ultraideate-shortlists.jsonl` with that id and a digest of the topic,
   never the topic's words; the picks row carrying the same id closes it. A
   shortlist with no picks row after a day is a round the operator never came
   back to, and the always-on tick (`lib/grip_tick.py`) reports it by run id.
   `picks` copies the FULL ranked order from that shortlist row, so `report`
   computes the random baseline correctly; rebuilding it by hand left 3 of 23
   picks rows with a shorter order than their shortlist (2026-11-24). It
   writes nothing, and says why, for an unknown run, a different topic, an id
   the shortlist does not hold, or a run that already has a picks row. With
   `--seal` the recorded pick also gets a signed IDR leaf naming its run id
   (`seal_picks`), because the picks log is plain text that anyone can edit.
   In the same process as `combine()`, `record_picks(topic, ranked,
   picked_ids, k=5, run_id=run_id)` does the same job (k=5 matches the
   pre-registered hypothesis below). A run with fewer ideas than k is judged
   at its own length, where precision equals the random baseline.
6. **Continue into DEVELOP with the picked ideas only.** Everything not picked
   is dropped here, not silently carried forward.

### `--ideas-file`: skip DIVERGE

Already have a list of ideas (from a prior session, a different brainstorm, or
hand-written)? `rank "<topic>" --ideas-file ideas.json` ranks them directly --
a JSON array of strings, or `{"ideas": [{"text": ..., "proposed_by": [...]}]}`
for ideas with known provenance. DIVERGE never runs in this mode.

### `--seeds-file`: the operator's own ideas, scored with DIVERGE's

`rank "<topic>" --seeds-file seeds.json` takes the same file format, but
DIVERGE still runs. The seeds come first, as `seed-1`, `seed-2` and so on,
so they never share an id with a council idea (`idea-N`). A seed keeps its
`proposed_by`, and a council idea with the same normalised text is the same
idea: its speaker is credited on the seed, not listed twice. Jev scores every
idea together. The output shows the top `--top` ideas and then every seed
that ranked below them, marked `seed`, because the operator's own idea is
always shown, whatever its rank. The seeds file is read before DIVERGE runs,
so a broken file costs no council call. It cannot be combined with
`--ideas-file`, which skips DIVERGE.

### `--seal`: a signed record of the ranked set

`--seal` appends the full ranked set (ideas, scores, weights) as a signed IDR
leaf, through the same signer every other GRIP decision uses
(`lib.precog.idr.build_idr` + `append_idr` -- see `lib/prove_it_provenance.py`
and `lib/sovereign_council.py`, neither of which wrote its own signer either).
Run it under `~/.grip/venv/bin/python`, or the output says plainly that only a
demo (unsigned-in-the-cryptographic-sense) seal was possible -- it never
prints success for that case.

## Weights

`/hitl-decision-matrix` (`skills/hitl-decision-matrix/SKILL.md`) is where an
operator would lock a weight per criterion -- but as of this writing it
persists a locked decision as markdown prose (and, optionally, a
hypothesis-engine entry), not a per-criterion NUMBER a caller can read back
(`lib/hitl_decision_matrix.py:103`: `Decision.default: str`). So
`operator_weights()` reads a small JSON file
(`state/decision-weights/ultraideate.json`, `{"novelty": 2.0, ...}`) when one
exists, and otherwise weights every criterion equally and SAYS SO in the
output (`equal_weights_no_matrix_found` / `equal_weights_incomplete_matrix`) --
never silently assumes a matrix was consulted. Until that store exists for
real, every `/ultraideate` run reports equal weights; this is the documented
interim state until a numeric matrix exists, not a bug.

## The measure: precision@k against a random baseline

Pre-registered (see `learning/hypotheses.jsonl`): over the first 10 operator
picking sessions, Jev's top-5 precision against what the operator actually
picked beats the RANDOM baseline (picked_count / total_count for that
session) by at least 0.15, on average. `~/.grip/venv/bin/python -m
lib.ultraideate report` reads `logs/ultraideate-picks.jsonl` and prints both
numbers and their margin; empty log, it says so rather than dividing by zero.
This is the measure a later `/dfi-fib` tuning loop over the criteria wording
would use -- that loop itself is explicitly out of scope for this skill.

## Data limit

Internal and public text only: GRIP/HAL code, docs, logs, public legal
sources. Never personal matters, WhatsApp content, children's data, secrets or
client documents (the operator's own words, 2026-10-10). Every send -- to the
council and to Jev -- goes through `lib/decision_privacy.py::may_send` first;
refused text is never sent, and the feature degrades (fewer ideas, more
unscored criteria) rather than silently working around the refusal.

## Cross-references

- `skills/brainstorm-mode/SKILL.md` -- names this skill as the deep-workflow
  option alongside Broly CLUSTER.
- `skills/hitl/SKILL.md`, `skills/asking-users/SKILL.md` -- the `multiSelect`
  question shape used in step 4 -- with its sibling `skills/creating-wizards/SKILL.md`
  for a multi-step intake, if the topic itself needs clarifying first.
- `skills/hitl-decision-matrix/SKILL.md` -- where a real per-criterion weight
  store belongs, once it exists.
- `lib/prompt_routing_score.py`, `lib/prove_it_decide.py` -- the house pattern
  for a Jev reading this skill's CONVERGE follows (injectable `decide`, an
  uncertain band, never a safety gate). The routing shadow itself is off
  by default and requires `GRIP_PROMPT_ROUTING_SCORE=1`; see
  `docs/jev-shadows.md`.
- `lib/sovereign_council.py` -- DIVERGE's council, zero Anthropic seats by
  design.
- `docs/decide.md` (HAL) -- Jev's measured token ceilings, known weaknesses,
  and the data limit this skill restates.

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.3.0 | 2026-11-24 | `picks <run_id> <ids> --topic ... [--seal]` records a pick against the shortlist row's full ranked order, so a caller that shelled out to `rank` no longer rebuilds it by hand, and seals it on request; a run shorter than k is judged at its own length |
| 1.2.0 | 2026-11-24 | `rank --seeds-file`: the operator's seeds are scored with DIVERGE's ideas in one run and always shown; every JSON entry carries `id` and `seed`. Built for `/brainstorm`, after run b58e370627d1 needed a scratchpad script for the same job |
| 1.1.0 | 2026-10-28 | Every `rank` run records its shortlist in `logs/ultraideate-shortlists.jsonl` with a `run_id`, a digest of the topic and the ranked ids; the rank output carries the id, `record_picks` takes `run_id=`, and the always-on tick (`lib/grip_tick.py`) reports a shortlist with no picks row after a day (GRIP#8793, GRIP#8799) |
| 1.0.0 | 2026-10-12 | Initial skill -- Phase 5.5 of the Jev integration plan (`~/.grip/plans/snug-questing-quill.md`); the real live run found and fixed a token-estimate calibration bug and documented Jev's own answer jitter (`docs/measurements/2026-10-12-ultraideate-live-run.md`) |
