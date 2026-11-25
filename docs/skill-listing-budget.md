# Why a session reports "1 skill available"

Measured 2026-08-19 from the shipped Claude Code binary and from recorded
skill-listing attachments in the local session transcripts. This document has been
wrong twice and both corrections are kept on the record at the bottom, because the
wrong versions were published and acted on.

## The answer

**It is not a listing. It is an increment.**

Claude Code does not re-send the skill catalogue every turn. It keeps a per-agent
`Set` of skill names it has already announced and sends only the names missing from
it, returning nothing at all when none are new:

    let o = t.filter((s) => !n.has(s.name));
    if (o.length === 0) return null;
    let i = n.size === 0;                        // isInitial

The attachment is then built from that filtered array, never from the full set, so
`skillCount` is the size of the **delta** — computed before any budget code runs.
Initial and incremental sends share one header ("The following skills are available
for use with the Skill tool:"), which is why a one-entry increment is textually
indistinguishable from a one-skill catalogue.

## What triggers a re-announcement

The file watcher keeps a name-to-content-hash map, diffs it on every change, and
evicts exactly the names whose **hash** changed:

    filter(([X, Y]) => U.get(X) !== Y).map(([X]) => X);
    if (J.length > 0) qYa(J)

`qYa` removes those names from the already-announced set, so on the next turn they
look new again. A file touched without a content change takes the other branch and
logs `skill list unchanged — skipping re-announce`, sending nothing.

So a reminder naming one skill means **one skill's content changed**. Every other
skill is still available; the model is simply not told about it again.

This is deliberate. Changelog 2.1.174: *"Fixed skill hot-reload re-sending the
entire skill listing when a single skill changed; only changed skills are now
re-announced."*

## The budget is a separate mechanism

    budget_chars = contextWindow * bytesPerToken * fraction     # fraction default 0.01

`bytesPerToken` is **model-dependent**, and this is the part that was got wrong:

    function tP(e){ if(!e) return 4; ... return cW_.has(r) ? 4 : 3 }

`cW_` is a fixed set ending at `claude-opus-4-6` / `claude-sonnet-4-6` /
`claude-haiku-4-5`. **`claude-opus-5` is not in it, so the constant is 3, not 4.**
At a 1M window and fraction 0.025 the budget is therefore 75,000 characters, not
100,000.

Over budget the renderer does not drop skills — its final statement maps over every
entry, demoting the ones it cannot afford:

    return e.map((h, g) => l.has(g) || m.has(g) ? s[g].full : `- ${h.name}`).join("\n")

So the budget changes what a listing *says*, never how many entries it *has*. A
recorded initial send of 474 skills measured 74,999 characters — one under its
75,000 ceiling — with **215 of the 474 stripped to bare names** and still emitting
476 lines. That is the budget working: visibly, and without changing the count.

## Two corrections to earlier versions of this document

**First version** claimed 321 user skills needing 82,270 characters and concluded
fraction 0.025 "fits at 1M". Wrong population: the listing is bundled plus plugin
plus user skills, and initial sends carry 469–477 entries.

**Second version** claimed a listing of 100,002 characters against a budget of
exactly 100,000 — "over by two characters". That figure assumed `bytesPerToken = 4`
for a session whose model resolves to 3. The dramatic two-character overrun was an
artefact of an assumed constant, not a measurement, and it is withdrawn.

Both versions also carried an "arithmetic gap" — a budget predicting roughly 55
surviving descriptions against a session reporting one. There was never a gap. The
count comes from the delta, upstream of the budget entirely; the two numbers were
never causally connected.

## Still open

**What keeps rewriting the skill files.** A re-announcement requires a content
change, and these fire repeatedly. `remind-me/SKILL.md` was rewritten during the
investigation itself. Some writes reproduce identical content (fresh mtime, clean
`git status`), so a writer is running and has not been identified. That is the real
open question, and it is upstream of everything above.

**Whether the original full listing survives in context.** A session does receive
the complete catalogue on its first announcement; the one-skill reminder is an
append, not a replacement. Whether that first listing is still inside the window
after compaction of a large transcript is UNVERIFIED — and it is the difference
between harmless confusion and the model genuinely having lost sight of its skills.

## Reproduce it

    rg -o '"skillCount":[0-9]+[^}]*"isInitial":(true|false)' ~/.claude/projects/*/*.jsonl
