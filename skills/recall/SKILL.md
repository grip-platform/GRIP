---
name: recall
description: The one recall command and GRIP's Rule 0 (Recall First). With no question it resumes the session from every memory subsystem; with a question it answers from local memory in layers, certain answers first, and says what it searched and what it did not. Use before any search, read or delegation, and when resuming work.
status: Active
effort: high
version: 1.0.0
invocable: true
invocable_name: recall
tokens: 900
triggers:
  - /recall
  - recall
  - retrieval
  - knowledge lookup
  - what do we know about
---

# /recall -- check what GRIP knows before searching (Rule 0, Recall First)

`/recall` replaces `/grip-first-thinking` (the retrieval doctrine) and `/recalling-state`
(the session resume). Both were removed on V>>'s ruling of 2026-10-30. It has two
faces:

```bash
/recall                                  # resume: every memory subsystem, as before
/recall "why did the judge move to Argon"   # ask: the layered cascade
/recall --not "a rendered AOD exists"    # absence: prove it, or say what was not searched
/recall --why "gemini-argon"             # follow the links from the matching records
/recall --deep "..."                     # allow the escalation layers
```

Behind them: `lib/grip_recall.py` for the resume, and `bin/grip-recall ask` for questions
(`--absence`, `--key`, `--why`, `--deep`, `--json`, `--timings`).

## The rule

Before searching, reading, browsing or delegating, check what GRIP already knows.
Escalate one layer at a time, and give the reason for each escalation. Delegating to an
Explore agent comes last, after every local layer has failed (88k tokens against about
200 for a local answer, `rules/efficiency-rules.md`).

## The layers

What is certain comes first, then cheaper layers before costlier ones. Steps S0 to L5
run locally with no model call. The target is under 50 ms warm. Measured on 2026-10-30
on the real corpus (1,614 documents) and the rebuilt moat (9,140 events), one process per
question: a repeated question took about 21 ms in-process and a new one 34-40 ms. The wall
time was 0.07-0.08 s, because Python's start-up is not counted in-process, so the target
that counts process start is not met yet (GRIP#8866).

| Step | What it checks | Certainty it can give |
|---|---|---|
| S0 | A snapshot of every store's version; a stale index is refreshed first | -- |
| memo | The same question on the same snapshot returns the same answer, byte for byte | as stored |
| L0 | V>>'s rulings: PARAMOUNT rules, pinned records, remembered laws (only live ones) | RULING |
| L1 | Signed records whose receipt re-checks (the recall moat store) | VERIFIED |
| L2 | Coverage of every store, and exact-key absence on a fresh catalogue | `key_absent` |
| L3 | BM25 over memory records, rules, docs, skills and HAL's absorbed sources | FOUND |
| L4 | Follow the links between records, by Personalized PageRank (`--why`, `--deep`) | FOUND |
| L5 | Merge by layer; list every superseded or conflicting record | -- |
| L6 | Escalations that `--deep` names under `scopes_not_searched` but does not run: vectors (`lib/grip_recall.py --instant`), transcripts (`rg`), a code walk (`fold_search`) | not sure locally |

Remote lookups -- GitHub, vendor documentation, the web, a delegated agent -- are not
part of `/recall`. They are separate tools, called explicitly, because they send the
question off the machine.

## Why keyword search comes before meaning search

The doctrine this replaces said semantic search was primary and keyword search a last
resort. The H866 head-to-head (GRIP#8513, 572 held-out questions, 2026-10-17) measured
the opposite: BM25 found the right record in the top 3 for 87.9% of questions, BM25
plus vectors for 89.0% (+1.0 point, p = 0.5), vectors alone about 80%, and the
embedding store as wired 50.0%. So BM25 is L3, and vectors stay an escalation until a
pre-registered test on low-overlap paraphrases shows a gain of 5 points or more.

## Reading an answer

- **Certainty:** RULING (V>> ruled it), VERIFIED (a signed record re-checked), FOUND (a
  search hit, `calibrated: false` until the calibration table exists), NOT_FOUND.
- **A ruling governs behaviour.** Where a ruling or record describes the world (which
  service, which flag, which path), fresher evidence on the main branch wins, and the
  answer lists the conflict.
- **Absence** has four statuses: `key_absent` (an exact key is missing from a complete,
  fresh catalogue), `no_evidence` (every searched store was fresh and found nothing),
  `not_searched_complete` (a store was stale, missing or skipped), and `sure_false`
  (a ruling says it does not exist). A timeout or a miss is never "does not exist".
- **Scopes:** every answer lists `scopes_searched` and `scopes_not_searched`. A negative
  claim names its scope (`rules/prove-it-claims-canonical.md`).
- **Privacy:** records marked `private: true` are never indexed, and the guardian and
  legal stores are outside the corpus.

## When this does not apply

1. Direct commands ("commit this", "edit line 42").
2. Pure creation ("write a script that does X").
3. Real-time external data (prices, weather, a live API).
4. An explicit request for an external search ("search the web for ...").
5. Conversation ("thanks").
6. Data already in the prompt.

## Honest limits

- L3 scores are not yet calibrated to a precision, so its answers say FOUND, never more.
- L4 stays out of the sure path until it beats BM25 alone on the hard test sets.
- L1 reports whether a record is genuine; whether it still binds the current main
  branch is not checked yet (`currently_binds: null`).
- The moat store is only as fresh as its last build; the answer's `coverage` shows its
  epoch.

## Enforcement

`hooks/grip-first-retrieval-hook.py` gates heavy delegation and
`hooks/grip-first-web-hook.py` gates web fetches, both through `lib/grip_first_common.py`.
`hooks/recall-preassert-gate.py` blocks a negative claim the moat contradicts, and
`hooks/recall-toolresult-gate.py` adds moat evidence after an empty search.

## Other harnesses

The rule holds in any harness: use its native memory and session search before costly
background work, and keep HAPPI's `cite.verify` provenance (`HAPPI.md`) when an answer
crosses harnesses.

## History

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-10-30 | One `/recall`: `/grip-first-thinking` (v2.1.0) and `/recalling-state` merged and removed. Planned with V>> through three HITL rounds, ultraideate run `546154aec246`, the Roundtable `5eff1b58` and the Council `5f4b8af1` (zero Anthropic seats, Gemini 4 Argon judging). Plan: `~/.grip/plans/fluffy-baking-map.md`. |
