# Binding Memory Records — the pinned tier of the memory index

Origin: 2026-08-25, during a memory-substrate refactor. Rule-at-N=1, because
the failure it closes is silent by construction and its blast radius is a leak.

## The problem

A dispatched subagent inherits `MEMORY.md` and the safety-core rules, and
nothing else. Controlled probe: `HOLISTIC.md` never reaches delegated work, the
completeness ledger never reaches it, the extended rules never reach it. About
a hundred `MEMORY.md` pointer lines do. So for that actor `MEMORY.md` is not an
index of memory — it **is** memory, and whatever is not on it does not exist.

Eviction from that index has always been positional: `lines[:195]` in
`lib/unified_memory_brief.py`, a tail-drop. That makes **age** the predictor of
whether a record reaches a subagent. Measured on the live corpus: 10 of the 10
newest memory files had a live pointer, and 0 of the 100 oldest did. A cliff,
not a gradient.

Age is close to the worst available proxy for whether a record must travel. A
record written the day after an irreversible harm is, by construction, old by
the time the same harm is available again. Eight records in that class — every
one a standing instruction about a leak, a privacy boundary, or a harm to the
operator — had no pointer at all when this was measured. The actor that commits
a leak is a subagent running `git commit`, syncing a ledger, or sweeping a
mirror, and for that actor those eight instructions did not exist.

## The law

> **A memory record may declare itself BINDING. A binding record is
> partitioned out of the evictable tail rather than competing for position
> against the newest arrival. Declaring it requires naming a resolvable
> incident in which its absence caused harm that could not be undone, and the
> pinned set is capped, so the tier is rival by construction.**

## The field

Top-level frontmatter, on the record itself:

```yaml
binding: irreversible
binding_incident: feedback_public_text_ledger_leak_and_redaction_shield
```

**One value.** `irreversible` is the only accepted value; absent means not
pinned. There is deliberately **no middle rung**, and this is the load-bearing
design choice rather than an omission. A middle rung is what an author reaches
for when they cannot honestly claim the top one, and a tier everyone can reach
is a tier that protects nobody. The question a marker must answer is therefore
not *"is this important?"* — everything in the corpus is important, that is why
it was written down — but *"can I name the incident where an actor lacking this
line caused harm that could not be undone?"*

**The incident must resolve, and must name a SECOND artefact.**
`binding_incident` is checked against the same corpus resolver the memory lint
uses (`bin/grip-memory-lint`), so it must land on a real memory record, rule,
skill, or repo path. An incident nobody can open is a claim, not evidence.
Reusing that one resolver is deliberate: two readers with independent opinions
about whether a reference resolves turn one checked fact into two unchecked
ones.

Pointing at the record's own stem is refused, across the hyphen/underscore and
tier-prefix spellings the corpus uses interchangeably. Self-reference always
resolves, so permitting it would make the requirement vacuous — an author with
a preference rather than an incident could satisfy it by naming themselves. The
refusal forces every pin to point somewhere a reader can go and find the harm
accounted for, and it builds a citation graph across the corpus instead of
eight self-loops.

Be exact about what this does and does not prove. It is a **documentation**
gate: it compels the author to write down where the harm is recorded, and turns
an unresolvable pointer into a hard error. It is not proof that an incident
occurred — nothing mechanical can supply that. The scarcity that actually makes
the tier discriminate is the cap.

Because the check runs through `bin/grip-memory-lint`, that script must be
present on whichever tree is resolving. A missing resolver **refuses the pin**
rather than waving it through, so a partial deployment fails closed and loudly.

**A malformed declaration is an error, never a silent zero.** A wrong value, a
`binding:` nested under `metadata:`, a missing or unresolvable incident — each
raises rather than reading as "not pinned". A record that believes it travels
and does not is the exact failure this mechanism exists to remove, and it would
be indistinguishable from success.

**Auto-capture can never pin.** Any record carrying
`source: auto-captured from ★ Insight block` is refused outright, whatever its
frontmatter says. This is enforced at the reader, not only at the writer,
because the writer's safety currently rests on a whitespace collapse in a
different module (`insight_value_scorer._hook`) — a remote invariant that would
rot without ever announcing it. Binding is an operator judgement about
irreversible harm; no automatic pipeline is entitled to make it.

## The cap

The pinned block is capped at **1,200 characters** of entry lines. The two
marker lines are fixed overhead outside the cap, so editing a marker cannot
change how much an author may pin.

The cap is the anti-Goodhart defence, and it is not a budget. At roughly 12-14
one-line imperatives the set becomes rival: marking a thirteenth record does
not add protection, it forces an explicit trade against a named incumbent. So
`check_pinned_budget` raises a hard error **naming one incumbent that must give
way** — the smallest whose removal actually makes room, the least you can give
up — rather than warning with a number. A number invites widening the budget; a
name invites making the trade.

The number 1,200 is not a measurement of what fits. The harness load budget is
twenty times larger. It is deliberate scarcity, because an unbounded pinned tier
decays into a second copy of the index, at which point pinning means nothing
because everything is pinned.

## The block

Entries live between `<!-- GRIP-PINNED-START -->` and `<!-- GRIP-PINNED-END -->`
in `MEMORY.md`, one per line:

```
- <imperative> :: <record filename>
```

The imperative is written as a **directive, not a description**, and must be
actionable standing alone — for a subagent that line is the whole instruction,
because the record it names is not in that context and will not be opened. The
filename is the address a reader follows for the full account, not a substitute
for saying what to do.

`binding_memory.audit_block` reconciles the rendered block against the records
that declare a binding, in both directions. Without that reconciliation the
frontmatter field is decorative — a declared input reaching no actor, which
`rules/declared-input-must-reach-the-actor.md` forbids precisely because it
looks like it works.

**As shipped, it is decorative, and this paragraph described the failure before
causing it.** `audit_block` has zero production callers; it is reached only from
`tests/test_binding_memory.py`. So does `binding_of`, via `is_pinned` and
`pinned_records`, neither of which anything in production calls. Of this module,
only `partition_pinned` is imported by live code — once, at
`lib/unified_memory_brief.py:38`.

That means the eviction protection is real but keyed on the WRONG AXIS. What
survives a trim is whatever sits between the block delimiters. Declaring
`binding: irreversible` on a record grants it nothing, and a line placed in the
block without any declaration is protected exactly as well. The validation at
`binding_of` — rejecting a nested key, a non-value, a missing incident — runs
only when a test calls it.

The protection itself was verified: neutralising `partition_pinned` turns six
tests red, including the one asserting the block would have been dropped under
the positional policy. So the eight records genuinely survive. What was wrong
was this rule's account of WHY, which is the more dangerous half — a reader
trusting it would add `binding:` to a record and believe it protected.

Closing this means wiring `audit_block` into the write or hook path so drift
between declaration and block membership fails loudly. That is a change with
blast radius on every memory write, so it is named here rather than made
quietly. Until then: the block protects, the field does not, and anyone relying
on the field is relying on nothing.

Found by an independent verification agent, not by this rule's author, who had
mutation-tested his own work and concluded it was sound.

## Falsification

The metric is the **marking rate**: pinned records as a share of the corpus. At
the time of writing it is 8 of 419, **1.9%**.

- Expected steady state: **under 5%**.
- Above **10%**, the tier has stopped discriminating and the *test* is
  tightened — the incident requirement gets stricter, or the cap gets smaller.
  **The budget is never widened.** Widening it is the Goodhart move: it
  converts a scarce signal into a second index while every individual decision
  still looks locally reasonable.
- Wrong altogether if, over the next ~20 dispatched subagent tasks touching a
  pinned domain, no agent's behaviour differs from what it would have been
  without the block — then the imperatives are not actionable standing alone,
  and the fix is to rewrite them, not to pin more records.
- Also wrong if a leak or irreversible harm reaches production from a domain
  that **was** pinned — the imperative was present and insufficient, so the
  defect is in the wording, and pinning further records would be treating the
  symptom.

Track: marking rate; leaks reaching production from an unpinned domain that a
pin would have covered (target 0); pinned entries that no subagent decision
ever turns on. Never track the count of pinned records as a success measure —
that is the metric the cap exists to make un-gameable.

## Scope

Every project's `memory/MEMORY.md` and the records it indexes. Sits beside
`rules/verify-canonical-not-claimed.md` (a pointer is a claim about where a
record is, not evidence that it travels),
`rules/declared-input-must-reach-the-actor.md` (why the field must reach an
actor), `rules/tests-must-be-able-to-fail.md` (why a malformed declaration
raises instead of reading as zero), and
`rules/knowledge-maturation-functor.md` (rule-now because severe). Shim:
`lib/binding_memory.py` · `lib/unified_memory_brief.py::_trim_to_line_limit` ·
`tests/test_binding_memory.py`.
