---
name: context-forensics
description: Explain where a session's context window actually went — decompose usage into ambient preload, rich media, tool-output bulk, harness injections, loop turns, and the accumulated-dialogue residual; deliver a one-paragraph composition-vs-leak verdict with every number verbatim. Use when the operator asks "where did my tokens go", "why is context at N%", questions a surprising context jump, or invokes /context-forensics.
status: Active
effort: low
version: 1.0.1
created: 2026-07-17
user_invocable: true
command: context-forensics
triggers:
  - /context-forensics
  - where did my tokens go
  - why is context at
  - explain token usage
  - context usage breakdown
  - is this a context leak
integrates:
  - context-gate
  - ultraclear
  - efficient
  - llm-usage
---

# Context Forensics

One question, answered as an audit rather than a vibe: **"Where did the window go — and is that composition or a leak?"**

Context percentage alone is noise. The useful answer names the components, prices
them, and renders a verdict. This skill encodes the output shape that earned the
operator's "super accurate and pleasant to read".

## Output contract (the part that makes it pleasant)

One short paragraph, in this exact order:

1. **The number in absolute terms** — "~41% of a 1M window" beats "41%". Map it
   against the operative thresholds so the reader knows how much runway remains
   (GRIP defaults: 70% = 700K, 80% = 800K on a 1M window; scale proportionally
   on a 200K tier).
2. **The verdict up front** — "plausible composition, not a leak" or "leak: X".
   Never make the reader assemble the verdict from the parts.
3. **The itemised why** — each big-ticket item named with its approximate cost
   and the one-clause reason it was worth paying (or wasn't).

Numbers stay verbatim — never rounded away (`rules/canonical-output-style.md`,
lossless clause). No table unless asked; the paragraph IS the deliverable.

### Example (the shape to imitate)

> That's ~41% of this session's 1M window (thresholds 70% = 700K, 80% = 800K —
> comfortable). It's plausible composition, not a leak: the ambient preload
> alone is heavy (the rules stack is ~73k by its own documentation, plus
> CLAUDE.md, the HOLISTIC kernel, memory and MCP schemas — likely 150k+ before
> turn one), then a 5-page PDF read with rendered page images (expensive), a
> 63.6KB probe output (mostly base64 false-positive noise from one over-broad
> case-insensitive grep — the refined query was the fix), the ~100-item
> task-list injection repeating per turn, and a verification loop adding
> turns. The remainder is accumulated dialogue — the ordinary turn history
> every long session carries (bucket 6). The named spikes plus that residual
> compose the 41%; the spikes alone do not sum to it, and the paragraph must
> say so rather than imply they do. (Illustration, not a ledger: per-item
> numbers in a real audit come from the same-turn probe, which is the only
> authoritative source — never from this example.)

## The six buckets (every window decomposes into these)

| # | Bucket | What lands here | Typical GRIP scale |
|---|--------|-----------------|--------------------|
| 1 | Ambient preload | rules stack (~73k per `rules/dispatch-economics.md`), the CLAUDE.md set, HOLISTIC kernel + warm-start, MEMORY.md, MCP tool schemas, skills/agents catalogues | ~150k+ before turn one |
| 2 | Rich media | PDFs read with rendered page images, screenshots, pasted images | page images dominate — a 5-page PDF costs far more than its text |
| 3 | Tool-output bulk | oversized probe outputs; broad case-insensitive greps false-positiving on base64/transcript noise; whole-file reads where offsets sufficed | a refined query fixes the *future* cost; the spent tokens stay in the window |
| 4 | Harness injections | task-list injections (re-sent per reminder), system reminders, hook contexts, file-change notices | a ~100-item task list is a recurring per-turn tax |
| 5 | Loop turns | verification/correction cycles (e.g. a detector-hook loop) — each cycle re-adds full turns | see `feedback_confab_flag_response_loop_external_only.md` |
| 6 | Accumulated dialogue | the ordinary conversation itself — prompts, replies, routine tool results that are individually small | the explicit residual after buckets 1–5; grows roughly linearly with turn count |

Buckets 1–5 rarely sum to the whole window — bucket 6 is the explicit
residual, never silently omitted. A residual far larger than what the
session's turn count plausibly explains is itself a leak signature: it
forces either a bucket redraw or a hunt for the unnamed consumer.

## Protocol

1. **Establish window + thresholds.** Model and window from session config;
   prefer `/context` (Claude Code ≥ 2.1.74, `rules/efficiency-rules.md` Rule 8)
   for the tool-by-tool mechanical breakdown.
2. **Attribute with evidence, never memory — the probe is a HARD
   precondition.** Run the probe in the SAME turn as the paragraph:
   `/context` first; otherwise transcript JSONL turn sizes (`jq` over the
   session file) and tool-result file sizes on disk. If neither probe ran
   this turn, say so and stop — never attribute from the bucket model
   alone; an attribution without a live source is itself a confabulation
   (`rules/prove-it-claims-canonical.md`).
3. **Judge composition vs leak.** Leak signatures: re-reads of unchanged files
   (Rule 2 cache violation), unbounded monitor/poll output, duplicated
   catalogue loads, subagent preload at the ~208K spawn ceiling, whole-file
   reads where `offset/limit` sufficed. Composition: every big item maps to a
   deliverable the operator recognises.
4. **Prescribe per bucket.** 1 → `/ultraclear` (not bare `/clear` — it drops
   the HOT set); 2 → `pages` param on PDFs, text-first ingestion; 3 → specific
   tokens over broad `-i` fishing, `lib/fold_compress` for >300-line inputs,
   RTK-routed Bash; 4 → prune stale tasks; 5 → answer detector hooks with
   external world-state only, never fresh self-report.
5. **Close with the contract paragraph.** Nothing else unless asked.

## Falsification

Wrong if, measured over the next 10 audits: (a) any bucket's attributed share
differs from `/context`'s mechanical per-tool breakdown by >30% relative, in
more than 3 of the 10 — buckets mis-drawn, redraw them (the comparison input
is the same-turn probe Protocol step 2 mandates, so the measurement is free);
or (b) the operator sends a same-session follow-up indicating the paragraph
failed to answer ("but where did it actually go") in ≥2 of the 10 — the
contract failed, restructure it (counted from session transcripts; a
follow-up requesting a DEEPER decomposition the protocol already allows —
"break down bucket 6" — is protocol-compliant curiosity and counts as a
pass, not a failure); or (c) producing the audit
costs more context than it explains (keep it to one paragraph plus the
optional table).

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.1 | 2026-07-17 | AGORA DEFER follow-up (merged PR #4051, dispatch run 29544511301): same-turn probe made a hard precondition (closes the confabulated-attribution gap); bucket 6 "accumulated dialogue" residual added and the example labelled illustration-not-ledger (spikes + residual compose the total; the probe is the only authoritative source); falsification clauses operationalised (10-audit window + measurement procedure, with a carve-out for protocol-compliant decomposition requests) |
| 1.0.0 | 2026-07-17 | Initial creation — crystallised from an operator-praised in-session context-usage explanation (V>> 2026-07-17: "super accurate and pleasant to read — change it into a skill") |
