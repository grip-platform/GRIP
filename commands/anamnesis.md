# /anamnesis - the most-forgotten memory entry

Plato's anamnesis (ἀνάμνησις) - the soul recalling what it once knew.

Every existing GRIP retrieval tool surfaces what's HOT - recent commits,
active hypotheses, fresh insights, KONO similarity. This is the dual.
It surfaces what's been QUIET for too long, ranked by days since the
linked memory file was last touched.

Use when you suspect GRIP is acting from a stale mental model. The entry
you'd never have queried by name is the one most likely to surprise you.

## Usage

```
/anamnesis                       # The single most-forgotten entry, full body
/anamnesis -n 3                  # Top 3 most-forgotten
/anamnesis --min-days 30         # Only entries quiet for >= 30 days
/anamnesis --category Process    # Restrict to one MEMORY.md section
/anamnesis -n 5 -b 5             # Top 5, 5 body lines each (compact)
```

## Direct CLI (equivalent)

```bash
~/.claude/bin/anamnesis
```

## When to use

- Before a sprint that touches an old subsystem - check for a forgotten
  feedback memory that might invalidate your plan.
- When a /save run feels light on lessons - the most-forgotten entry
  often surfaces a non-obvious connection to today's work.
- During boredom-pause moments when you'd otherwise reach for novelty -
  recollection is cheaper than invention.

## When NOT to use

- During time-critical execution (the output is contemplative, not
  actionable).
- Right after a memory-pruning pass (everything is freshly touched).

## Falsification

The tool is wrong if the most-forgotten entry is consistently noise that
should be deleted rather than recalled. At that point the right move is
pruning the MEMORY.md index, not running anamnesis. Track via: ratio of
"actually useful" anamnesis surfaces over time. If most surfaces are
prune-candidates rather than recall-candidates, the index has rotted.
