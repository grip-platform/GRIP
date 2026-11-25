# /forget — free a memory record's index slot (the inverse of /remember)

Take a memory record **out of the loaded `MEMORY.md` index** on explicit
intent, with a reason. The record stays on disk, byte-identical; only its slot
is freed, and a `.forgotten` manifest beside the index keeps the exact removed
line so the release can be reversed by hand.

## Usage

```
/forget <slug> because <reason>
/forget --release <slug> --reason "<reason>"
```

The two forms are the same call. The first is what you type to GRIP: the skill
reads the slug and the words after `because` and passes them to the backend as
`--release` and `--reason`, so a reason is required either way. The second is
that backend invoked directly, and is the only form `lib/forget.py` parses.

## What it does

Invokes `skills/forget/SKILL.md`, whose deterministic backend `lib/forget.py`:

- refuses first — a pinned record (the `GRIP-PINNED` block), a slug with no
  pointer, a slug with no record on disk, or an empty reason — before touching
  any file;
- measures `bin/grip-memory-lint`'s `corpus_stats()`, removes every pointer
  line for `<slug>.md`, appends the manifest line, measures again;
- prints the delta (`pointer_lines a -> b; orphans c -> d`) and whether the
  record is now reachable by grep only or still reached by an inbound wikilink.

Only `--release` exists today. `--retire` (demote a rule down the
knowledge-maturation functor) and `--purge` (genuine erasure) are deferred to a
second PR and are **not** accepted by the parser — see the skill for why.

Do not offer either verb, and do not read the refusal aloud as written.
`--retire <rule> --reason "<why>"` exits `2` with `error: the following
arguments are required: --release` (drop `--reason` and it names both missing
flags) — argparse reports what is missing and never names `--retire` at all.
Say plainly that the verb does not exist yet; quoting that usage line invites
the operator to think they mistyped. Nothing is written on either path. The
exact messages and the reason for the two shapes are in
`skills/forget/SKILL.md`.

## Implementation

```bash
python3 lib/forget.py --release <slug> --reason "<one line>" \
  [--memory-dir <dir>] [--root <grip-checkout>]
```

See `skills/forget/SKILL.md` for the full procedure and the deferred verbs.
