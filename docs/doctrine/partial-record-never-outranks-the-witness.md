# A Partial Record Never Outranks the Witness: annex

The narrative behind `rules/partial-record-never-outranks-the-witness.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-09-01. The operator, reviewing his own message archive through GRIP, described
how a conversation had gone. The assistant replied that his account *"doesn't survive the
record. You did speak, at 20:30."*

The assistant had no record of that call. Calls do not appear in that message mirror at all —
not their occurrence, not their duration, not a word of their content. It had the text before
the call and the text after it, and it used that gap to contradict the operator about a
conversation he had personally been in. His answer: *"you don't know what was said in voice
calls."*

He was right, and the failure is not a missing feature. It is a reasoning error the missing
feature made possible.

## The measurement that makes this concrete

Taken on a real message mirror, 2026-09-01, across 18,040 records:

| Fact | Figure |
|---|---|
| Records that are **not text** | **4,682 — 26%** (3,194 image, 821 video, 376 voice, 291 document) |
| Call records of any kind | **zero** |
| Voice-note records carrying content | zero; each carries a stub such as `[voice note 21s]` |
| Message edits reflected | none — the mirror holds the **pre-edit** text |
| Deleted messages retained | yes, in full |

So the mirror is asymmetric in a way nothing documents: it **keeps what was withdrawn** and
**misses what was revised**, and it is wholly blind to a quarter of the traffic and to every
call.
