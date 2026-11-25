---
name: linkedin-post-craft
description: Transform a plain LinkedIn draft into a structured, distinctive, Unicode-formatted post. Reason + review first (audience, intent, anti-fluff), lift messy paragraphs into A/B/C structure, then apply the 7-element Unicode toolkit that LinkedIn renders as bold/italic in plain text. Use when the operator invokes /linkedin-post, asks to "format/polish/structure a LinkedIn post", or hands a raw draft to make it land. Cross-refs authentic-writing so the anti-fluff gate fires by construction.
status: Active
effort: medium
version: 1.0.0
created: 2026-05-24
visibility: secret
triggers:
  - /linkedin-post
  - "LinkedIn post"
  - "format this post"
  - "polish my LinkedIn draft"
  - "make this post land"
---

# LinkedIn Post Craft

Turn a plain LinkedIn draft into a structured, distinctive post. LinkedIn
renders no markdown, but it DOES render Unicode Mathematical-Alphanumeric glyphs
as bold/italic inside plain text — so the styling survives copy-paste with no
images and no external tool.

The mechanical floor lives in `lib/linkedin_post_craft.py` (pure, dependency-
free, Goodhart-test-anchored). This SKILL is the reasoning layer the model
applies on top. The floor never relaxes: it converts glyphs and reports
anti-fluff violations; it never invents marketing language and never rewrites a
draft to hide a violation.

## Two layers (mechanical floor + AI craft)

- **L1 — mechanical (the module).** `bolden` / `italicise` / `frame` / `step`
  remap ASCII to the exact Math Sans-Serif codepoints; `antifluff_violations`
  reports banned marketing tokens. Deterministic, total, idempotent on
  already-styled input. This is the half that cannot drift.
- **L2 — AI craft (this prose).** Reason + review before styling, lift messy
  paragraphs into A/B/C structure, detect a subtle feature teaser, preserve
  intentional easter eggs (e.g. a `V>>` signature), and run the anti-fluff gate.
  L2 is judgement; it sits ON the floor, never under it.

## The 7-element Unicode toolkit

LinkedIn shows each of these as styled/structural text in a plain-text post.
The underlying characters stay copy-pastable.

| # | Element | Glyph | Use | Codepoint(s) | Module |
|---|---------|-------|-----|--------------|--------|
| 1 | Math Sans Bold | 𝗕𝗼𝗹𝗱 | emphasis (replaces `**bold**`) | U+1D5D4–U+1D607 (digits U+1D7EC–U+1D7F5) | `bolden(text)` |
| 2 | Math Sans Italic | 𝘐𝘵𝘢𝘭𝘪𝘤 | meta-asides ("quick shill", "watch this") | U+1D608–U+1D63B (no digit glyphs) | `italicise(text)` |
| 3 | Corner brackets | ⌜ A ⌟ | A/B/C structural framing | U+231C / U+231F | `frame(label)` |
| 4 | Heavy horizontal | ━━━ | section breathing room | U+2501 | `GLYPHS["rule"]` |
| 5 | Tracked-step bullet | ▸ | numbered/tracked step bullets | U+25B8 | `step(text)` |
| 6 | Step arrow | → | step transition (`build ─→ ship`) | U+2192 | `GLYPHS["arrow"]` |
| 7 | Middle-dot · / diamond ◆ | `A.com · B.com` / `◆` | URL handle joins · terminal marker | U+00B7 / U+25C6 | `GLYPHS["middot"]`, `GLYPHS["diamond"]` |

**Italic has no digits.** The Unicode Math Sans Italic block defines no digit
glyphs, so `italicise("v2")` keeps the `2` as ASCII rather than emitting a
missing-glyph box. The module enforces this; do not hand-craft italic digits.

## Reason + review protocol (run BEFORE styling)

1. **Audience + intent.** Who reads this, and what is the single action/feeling
   you want? One post = one point. If the draft has three points, the structure
   step will split them, not the styling.
2. **Anti-fluff gate (by construction).** Run `antifluff_violations(draft)`. Any
   hit is a blocker — rewrite the root sentence in plain, concrete, number-led
   language (per `authentic-writing`). Never style over fluff; styled fluff is
   still fluff. This gate fires whether or not the operator opted in.
3. **A/B/C structural lift.** Pull the messy paragraph into 2–4 framed sections
   (`⌜ A ⌟ … ⌜ B ⌟ …`) or tracked steps (`▸ … ─→ …`). Structure is the value;
   glyphs are the finish.
4. **Subtle teaser detection.** If the draft hints at a coming feature, name it
   without committing to a date or a promise ("watch this" in italic, not "we
   will ship X on Friday").
5. **Easter-egg preservation.** Keep intentional signatures/markers (`V>>`,
   `◆`) exactly where the author put them — do not "clean" them away.
6. **Style last.** Apply the toolkit. Bold for the 1–3 load-bearing phrases (not
   every line — over-bolding kills emphasis), italic for meta-asides, structural
   glyphs for sections. Then re-read once as a human would.

## Accessibility escape hatch (issue #3176 falsifier (c))

Unicode Math glyphs are read inconsistently by some screen readers (a bold "A"
may be announced as "mathematical bold capital A" or skipped). If the post is
accessibility-sensitive, or the operator asks, emit a **plain-ASCII variant**
alongside the styled one (same structure, `**bold**`-free, sections kept as
short lines). The structure survives; the glyphs are optional finish, never
load-bearing meaning.

## Worked examples

### A — plain → structured (thanks-to-a-contact, 2026-05-24)

Plain in:

> Big thanks to Jane for the legal-AI conversations this week. We're building
> something around provable AI decisions and it's coming together.

Crafted out (concept):

```
𝗕𝗶𝗴 𝘁𝗵𝗮𝗻𝗸𝘀, Jane.

⌜ this week ⌟
▸ legal-AI conversations that actually moved the design
▸ provable AI decisions — every call signed, tamper-evident

𝘞𝘢𝘵𝘤𝘩 𝘵𝘩𝘪𝘴. ◆

V>>
```

One point (gratitude → what it unlocked), framed, one teaser in italic, the
`V>>` easter egg kept, zero marketing adjectives.

### B — satire post (#1952): bold as the punchline

Satire lands when the emphasis is surgical. One bold phrase per beat, the rest
plain — the contrast IS the joke. Over-styling a satire post flattens it.

### C — 6-slide carousel (#1968): structure carries, glyphs finish

Each slide is one `⌜ N ⌟` framed claim + one tracked step (`▸ … ─→ …`). The
carousel's value is the A→B→C progression; the glyphs make each slide breathe.
Run the anti-fluff gate on every slide's caption, not just the first.

## Cross-reference: authentic-writing (hard, not opt-in)

This skill's step 2 calls the deterministic anti-fluff floor
(`antifluff_violations`), which mirrors the banned tables in the
`authentic-writing` skill. The gate therefore fires **by construction** on every
craft pass — the operator does not have to remember to invoke `authentic-writing`
first. A styled post that still contains banned marketing language is a failed
craft pass, not a finished one.

## Falsification (issue #3176, Rule R0)

This codification is wrong if any of: (a) posts produced via the skill do not
measurably outperform plain-text equivalents over ≥5 paired observations;
(b) the skill is invoked 0 times in 30 days post-merge (latent dead inventory —
auto-archived by `meta-unused-skills-blocker`); (c) the Unicode glyphs trip
LinkedIn accessibility readers in a way the operator deems unacceptable (the
plain-ASCII escape hatch above is the mitigation — if it is insufficient, the
glyph layer is dropped and only structure remains); (d) the Chrome-plugin council
returns KILL with reasoning that also kills the skill case. Track via the
acceptance criteria in #3176.

## Out of scope for this skill

- Posting to LinkedIn (manual — the operator publishes; this skill only crafts).
- The Chrome-plugin productisation (a separate `/broly` council + repo decision).

## Related

`/grip-post` (`skills/grip-post/SKILL.md`) is the voice, sourcing and delivery layer
over this toolkit: it drafts under the no-shilling and sourcing rules, gates the text
through the grip-post CLI's `check` and `ground-check`, renders one PDF or image, and
writes everything to an outbound folder without posting. Use this skill for the shape
of a post; use `/grip-post` when the post has to be publishable as-is.
