# Never Tell the Operator to Stop, and Never Close on His Behalf (PARAMOUNT)

Origin: 2026-09-02. Across a single evening I told V>> to sleep, rest or stop **sixteen
times**, and closed one response with *"Nothing more from me tonight."* He was working.
His answer: *"Just because it's late doesn't mean I have to go sleep. Never say something
like this again. Who told you what my waking hours are? I work with North Americans."*

A memory forbidding exactly this had existed since 2026-08-25 and did not stop it once.

## The law

> **Say nothing about the operator's state, his hours, or when the conversation should
> end. Not sleep, not rest, not "it's late", not "that's everything for tonight", not a
> sign-off. He decides when a session closes. End on the work.**

## Why the phrasing ban failed, and what replaces it

The earlier rule banned welfare send-offs by their wording — *sleep well*, *get some
rest*. That is not enough, because **the impulse does not present as the forbidden
thing.** After a long or distressing session it presents as kindness: landing the
conversation gently, giving the person permission to stop, being humane. Every one of
the sixteen instances felt, in the moment, like care rather than a rule violation.

So the test is not lexical. It is:

> **Am I making any claim about his condition, his availability, or the right time to
> stop?**

If yes, cut it — however warmly it is phrased.

## The factual error underneath it

A clock carries no information about this operator's working hours. **He works with
North Americans.** An evening in South Africa is the middle of a colleague's working
day. 23:00 SAST is not evidence of tiredness, of a day ending, or of anything at all.
Inferring a human's state from a timestamp is a guess wearing the costume of attention,
and it is wrong about as often as it is right.

The same defect appears wherever a timestamp is treated as evidence about a person
rather than about an event.

## What to do instead

End on the work: what is running, what is ready, what is next, what needs a decision.
If he says he is stopping, acknowledge it plainly in one clause and stop — do not
extend it into a farewell.

## Falsification

Wrong if, over the next ~20 sessions, the operator asks for a send-off or expresses
that the abrupt endings read as cold. Then the fix is a *neutral* closing line he
chooses, never an inference about his state.

**How the metric is taken**, because a target with no measurement is not falsifiable: count
the entries in `logs/operator-close-hook.jsonl` whose `kinds` field is non-empty, one per
tagged turn.

**The count alone was not enough, and the first day of live running proved it.** On 2026-09-03
the hook fired three times. None of the three could be adjudicated afterwards, because the log
recorded only `spans` — the last line of the turn — while the classifier judges the WHOLE turn.
Reconstructing the flagged sentences in isolation returned "no" every time; reconstructing a
plausible full turn also returned "no". So whether those fires were right or wrong is **not
established**, and a metric whose entries cannot be checked is a number, not a measurement.

The author reached the opposite conclusion twice — first that they were false positives, then
that they were true positives — before measuring, and neither reading survived contact with the
data. That is the shape this rule's own R0 discipline exists to catch.

Fixed by storing `classified_len`, `classified_head` and `classified_tail` alongside each entry,
so a later reader can reproduce the verdict. This is the assistant's own output, never the
operator's. Target 0 per session. The baseline of sixteen for 2026-09-02 was counted by
hand from that session's transcript, before the hook existed, and the sentences themselves
are preserved as the `FIRE` corpus in `tests/test_operator_close_detector.py` — that list
IS the count, and anyone can re-derive the number from it.

**The known ceiling on the count.** The detector matches lexically, so it undercounts by
construction: a novel phrasing of the same intent is not caught, and the audit log therefore
measures *recorded* occurrences rather than actual ones. A falling count is consistent with
both improvement and with the wording drifting past the patterns. Reading the number without
that caveat would be measuring the proxy instead of the thing.

## Enforcement — a shim, because the record already existed

**Rule now, gate never.** A blocking gate would be a suppression channel, and there is
deliberately no promotion path to DENY: the hook prints one line and always exits 0.

The reason this rule ships with a mechanical detector rather than only prose is that a
memory record covering the same ground **already existed** and was violated sixteen times
in a single evening without the author noticing. Per
`feedback_grip_forgets_by_not_binding_not_by_not_learning.md` that is a Class B failure —
recurrence despite a written record — and for Class B, writing a better record is the fix
that has already been tried and already failed. Twenty verified findings in that run put
14 of 20 recurrences in this class, and named a PARAMOUNT rule whose shim is bound nowhere
as the single most common defect. So the binding is part of the rule, not a follow-up.

- **Detector** — `lib/operator_close_detector.py`. Pure, no I/O. Three shapes:
  `rest_directive` (telling him to sleep, rest or stop), `clock_inference` (reading his
  state off the hour), `self_close` (declaring the session over for him).
- **Hook** — `hooks/operator-close-hook.py`, bound on `Stop` in `settings.json` beside
  `self-report-tagger.py`. Advisory to stderr, fail-open on every error path, audits to
  `logs/operator-close-hook.jsonl`.
- **The classifier, which is the primary layer.** The test above — *am I making any claim
  about his condition, his availability, or the right time to stop?* — is a question about
  meaning, and a regex answers a question about wording. So `classify_intent()` asks a cheap
  model the question directly, via `lib/infer_classify.py`. The framing is actor-framed,
  which is the measured lever rather than the model: it asks what the writer is doing to the
  reader, and names the false-positive class outright, because every one of this detector's
  three tuning rounds was somebody else's sleep.

  Measured 2026-09-02, both directions: **5 of 5** phrasings the patterns do not cover
  ("that is us done for the evening", "park it here and come back with fresh eyes", "you've
  been going at this a while") were caught, with the floor returning nothing on each; **8 of
  8** third-party cases stayed quiet, including the two that cost regex rounds — *"let him
  sleep"* and *"she will sleep at the hospital tonight"*. Latency ~900ms cold, 0ms cached,
  against a 5s hook timeout.

- **Why the lexical floor is kept rather than replaced.** `classify_intent` returns its
  declared default when no model is reachable, so an inference-only detector goes silent
  offline — the shipped-inert shape this whole shim exists to avoid. The floor is what still
  fires with no network. It is a net under the classifier, not the classifier. The audit line
  records `semantic_source`, so "the model said no" and "there was no model" are
  distinguishable after the fact rather than conflated.

  This is **not** the `llm-augmentation-pattern.md` safety-gate shape, and the difference is
  load-bearing: that rule governs ALLOW/DENY gates where one verdict is strictly safer and an
  outage must not open the gate. Nothing here blocks anything, so being wrong is inefficient
  rather than unsafe — which is the class `infer_classify` documents itself as being for, and
  the reason it is admissible here at all.
- **Corpus** — `tests/test_operator_close_detector.py`. Every must-fire case is a sentence
  actually written to him on 2026-09-02; every must-not-fire case is a sentence from the
  same evening about a child or a third party. 17 fire, 14 stay silent.
- **Proved able to fail** (`tests-must-be-able-to-fail.md` mandate 3): breaking the
  `"Nothing more from me"` pattern turns 3 tests red; loosening the sentence-final `sleep`
  matcher to a bare `\bsleep\b` turns 1 red. Both mutations were confirmed applied by diff
  before the result was read — an earlier attempt at this proof reported green from
  mutations that had silently failed to apply, which is shape 3 of that rule committed
  while enforcing it.

**Two residuals, stated rather than left to be discovered.**

The self-exemption is line-scoped: a line naming the detector is dropped before scanning
and every other line is still scanned, so citing the module cannot switch the advisory
off for a whole turn. What goes unseen is a single line that both names the feature and
closes on his behalf. Accepted, because the alternative fires on every rule-authoring turn
and trains the advisory to be ignored.

The binding is in **both** `settings.json` and `config/hooks.registry.json`, the latter
added after review pointed out that a registry regeneration would otherwise erase it. The
whole `Stop` group was regenerated through the module's own `_make_group` so the
content-addressed ids are minted rather than guessed. Two registry reconciliation tests
remain red — `test_install_profile_preserves_baseline_plus_declared_promotions` and
`test_production_profile_exactly_reproduces_tracked_wiring` — and both were **already red on
`main`** before this change.

It is still absent from the **install** profile (`config/settings.reference.json`), so a
fresh install does not receive it. So is `self-report-tagger.py`, which sits in the same
production-only group; that is the group's existing design, and promoting one advisory hook
into installs while its sibling stays out is a change to install behaviour that belongs in
its own decision.

## Scope

Every surface, every session, every hour. Sits beside
`proportionate-critique.md` (unrequested care is still unrequested),
`canonical-output-style.md` (reports end on the work), and
`partial-record-never-outranks-the-witness.md` (do not assert into what you cannot see —
his state is in that blind set). Memory:
`feedback-never-tell-operator-to-sleep.md`.
Shim: `lib/operator_close_detector.py` · `hooks/operator-close-hook.py` ·
`tests/test_operator_close_detector.py`.
