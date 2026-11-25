---
name: forget
description: The inverse of /remember — free a memory record's slot in the loaded MEMORY.md index on explicit operator intent, with a reason, keeping the record on disk and reporting what changed from a measurement (bin/grip-memory-lint corpus_stats before and after). Use when the operator says "/forget", "release this memory", "free the index slot", "stop loading X every session", or when the index is over its load limit and a named record should give up its slot. Only --release ships today; --retire and --purge are deferred to a second PR. Thin orchestrator over lib/forget.py.
---

# /forget — free an index slot, keep the record

> Rafiki: *"The past can hurt. But the way I see it, you can either run from it,
> or learn from it."*

`/remember` writes a lesson into the tier that is re-loaded every session.
`/forget --release` is its inverse for the memory tier: it takes a record **out
of the loaded index** on explicit operator intent, with a reason, and leaves the
record itself untouched on disk.

## Why this verb exists (measured, not assumed)

The index `MEMORY.md` is capped when it is loaded, so its slots are rival.
Measured 2026-09-07 (`plans/add-c6-10-to-the-woolly-token.md`): 470 memory
records on disk, 118 pointed to from the loaded index, 352 (75%) reachable by
grep only, and the index 17% over its load limit. Eviction today is by
*position in a truncated file*, and
`memory/reference_memory_index_eviction_has_no_valid_key.md` warns that no valid
"oldest" key exists. `/forget` supplies the key that record says is missing:
**explicit operator intent, one slug at a time, with a reason**.

## What ships now: `--release <slug>`

| Step | What happens |
|------|--------------|
| refuse first | a **pinned** record (the `GRIP-PINNED` block — `rules/binding-memory-records.md`), a slug with **no pointer**, a slug with **no record** on disk, an empty reason: each refuses before any file is touched |
| measure | `bin/grip-memory-lint`'s `corpus_stats()` — pointer lines and orphans — **before** |
| release | remove every `MEMORY.md` pointer line whose target is `<slug>.md` (atomic write) |
| manifest | append one JSON line to `<memory-dir>/.forgotten`: slug, reason, timestamp, the exact removed line(s) — enough to reverse by hand |
| measure again | the same stats **after**; the report is the delta, never a self-declared success. A release that did not shrink the pointer set by exactly one raises |

The report says one of two things, from the orphan delta: *the record is now
reachable by grep only*, or *still reachable through an inbound wikilink*.

## Procedure (thin — reimplement nothing)

1. Confirm the slug is a memory record (`<memory-dir>/<slug>.md` exists) and
   that the operator means the **memory tier**. A rule in `rules/` or a
   `CLAUDE.md` pointer is not a `--release`; say so.
2. Run the backend from the GRIP checkout:

   ```bash
   python3 lib/forget.py --release <slug> --reason "<why, one line>"
   # optional: --memory-dir <dir>   --root <grip-checkout>
   ```

3. Read the measured line back to the operator verbatim (`pointer_lines a -> b;
   orphans c -> d`). If it says "reachable by grep only", say that plainly.
4. `/forget` writes only the gitignored memory tier, like `/remember memory:` —
   no commit is needed. The `.forgotten` manifest lives beside `MEMORY.md`.

## Deferred, and why (not declared, so they cannot be mis-invoked)

- **`--retire <rule>`** — demotion down the knowledge-maturation functor
  (`rules/x.md` → `memory/x.md` with the falsifier that fired). Every
  falsification clause in the rule corpus resolves to *narrow / relax / retune*,
  none to *delete*, and `rules/knowledge-maturation-functor.md` has no inverse
  arrow. Adding one is a doctrine change and must be ratified exogenously
  (`rules/exogenous-anchor-law.md`) before any code acts on it.
- **`--purge <pattern>`** — genuine erasure for data that should never have been
  recorded, on `lib/wa_opt_out.py`'s shape (scope-limited, raising before any
  I/O, one tombstone with the identifier hashed). The chain half already
  exists: `context-supersede` (`MEMORY_CONTEXT.md`, `lib/context_chain.py`)
  masks a wrong delta forward without rewriting history.

Per `rules/declared-input-must-reach-the-actor.md` the parser does not accept
either flag today; `tests/test_forget.py` pins that. Measured 2026-09-08 against
`lib/forget.py` on `origin/main`, the refusal has two shapes and **only one of
them names the flag**:

```console
$ python3 lib/forget.py --retire cc-enforcement --reason "falsifier fired"
usage: forget.py [-h] --release SLUG --reason REASON [--memory-dir MEMORY_DIR]
                 [--root ROOT]
forget.py: error: the following arguments are required: --release
$ echo $?
2
```

That is the shape an operator actually hits, and it is misleading: argparse
reports the **missing `--release`** and exits before it ever looks at
`--retire`, so the message never mentions the flag that was typed. Supply the
required arguments and the real rejection surfaces —
`forget.py: error: unrecognized arguments: --retire cc-enforcement`. Both exit
`2` and touch nothing: argparse fails inside `parse_args`, so `main()` never
reaches `release()` and no index line, manifest entry, or record is written.

The claim "not accepted by the parser" is therefore mechanically true, but read
the first message for what it is — a missing-argument error, not a statement
that `--retire` does not exist. Until a second PR declares the verb, the honest
answer to *"why did `--retire` not work?"* is the paragraph above, not the
usage line.

## Falsification

`/forget --release` is working iff the released record is absent from the
loaded index of the next session AND still present, byte-identical, on disk.
It is wrong if `corpus_stats` ever reports the pointer set unchanged after a
non-refused release (then the writer and the lint disagree about what a
pointer is — fix the shared regex, never the report). Concretely: give the
index a line naming `<slug>.md` in a form the writer's regex strips but the
lint never counted as a pointer, and `pointer_lines` reads the same number
before and after a release that did remove a line. That case cannot report
success — `lib/forget.py`'s post-condition requires `after == before - 1` and
raises with both numbers named — so the falsifier here is a raise, not a
silently wrong report. It is also wrong if a pinned record is
ever released (the refusal is the unforgettable class's only mechanical
protection while `binding: irreversible` remains decorative).

## Related

`lib/forget.py` (backend) · `commands/forget.md` (slash entry) ·
`skills/remember` + `lib/remember.py` (the verb this inverts; they share one
memory-dir resolver) · `bin/grip-memory-lint` (the measurement) ·
`lib/binding_memory.py` (the pinned block) ·
`rules/binding-memory-records.md` · `rules/verify-canonical-not-claimed.md`.
