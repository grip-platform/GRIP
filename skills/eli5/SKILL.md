---
name: eli5
description: "Rewrite any document, explanation, or article for a bright 5-year-old — short sentences, concrete analogies, no jargon, no acronyms, but same respect for the reader. Use when the operator asks for an \"ELI5 version\", \"Explain Like I'm Five\", \"child-friendly version\", or \"make this understandable for a non-technical audience\". Sibling skill to `plain-language`, but goes further: plain-language strips jargon, `/eli5` reconstructs the whole story at the comprehension level."
---

# /eli5 — Explain Like I'm Five

This is the `novice` preset of `/clarify` (`skills/clarify/SKILL.md`). Its shared rules apply here too: nothing added, words to type unchanged, 80% ASD-STE100, and one structural analogy at most.

One-command translation of any text into language a bright child can follow, without patronising them or losing the point. The sibling of `/plain-language` — where that one swaps jargon for plain English, `/eli5` goes further: it rebuilds the story with analogies, shorter sentences, and concrete objects.

## Usage

```bash
/eli5 <text>                     # rewrite text at ELI5 level
/eli5 --from path/to/file.md     # rewrite a whole file
/eli5 --pdf <text>               # rewrite + emit styled PDF
/eli5 --paired <text>            # produce both technical + ELI5 side-by-side
```

## The five rules

Every `/eli5` rewrite follows the same five rules. They are the whole skill.

1. **No jargon, no acronyms, no brand names without a gloss.**
   - Replace proper-noun products with what they ARE. &ldquo;MCP&rdquo; → &ldquo;a way for helper robots to talk to each other.&rdquo;
   - If a brand is load-bearing (e.g. Claude, Anthropic), keep it but add a human-readable gloss the first time.
   - Drop all version numbers, SKU codes, SHAs, issue numbers, percentages with >2 sig-figs. Round aggressively.

2. **Sentences of 15 words or fewer, mostly.**
   - Full stops over commas. Split compound sentences.
   - One idea per sentence. If you can't say it simply, you don't understand it yet.

3. **Concrete analogies over abstractions.**
   - "Provider-agnostic" → "works with any helper, not just one family"
   - "Hypothesis engine" → "he has to guess what will happen first, then check if he was right"
   - "Self-improving" → "he looks at his own work to see what he got wrong, then fixes it"
   - Prefer metaphors from everyday life: toys, teams, games, cooking, playgrounds, families.

4. **Respect the reader.**
   - A five-year-old is bright. They can handle big ideas if the words are small.
   - NEVER say &ldquo;it's too complicated for you.&rdquo; Always find the honest analogy.
   - NEVER cut the heart of the story. If gratitude is the point, keep the gratitude.

5. **Preserve the shape, not the surface.**
   - A 1500-word essay becomes a 500-word retelling that has the same structure: setup → history → state of things → future → thanks.
   - Keep the pull-quotes, but rewrite their words.
   - Section headings become story chapters.

## Worked examples

| Before (jargon) | After (ELI5) |
|---|---|
| "HAL is provider-agnostic by construction." | "HAL works with any helper robot, not just one family." |
| "Hypothesis H091 registered with a falsification deadline of 2026-05-17." | "HAL wrote down what he thought would happen, and picked a day to check if he was right." |
| "The council returned a RETHINK verdict on the design claim." | "HAL's team said 'wait, let's think about this more first.'" |
| "Opus 4.7's 1M context window enables long-session memory." | "The newest version of Claude can remember a lot at once, so sessions don't forget things." |
| "Prompt-cache-aware CCH subscription reuse saves ~\$0.011/turn." | "HAL can remember what he said last time, so he doesn't have to pay to explain it again." |

## The sibling relationship

| Aspect | `/plain-language` | `/eli5` |
|--------|-------------------|---------|
| What it changes | Swaps jargon for plain English; structure stays | Rebuilds the whole story with analogies |
| Target reader | Busy non-specialist adult | Bright 5-year-old (or an adult outside the field) |
| Sentence length | Unchanged | 15 words or fewer, mostly |
| Proper nouns | Kept | Kept but glossed first time |
| Document length | Similar to source | Roughly one-third of source |
| Tone | Neutral professional | Warm, concrete, story-shaped |

Use `/plain-language` when the reader is smart but not in your field.
Use `/eli5` when the reader is *anyone at all* — including children, clients from another industry, or your own future self reading at 2am.

## Execution

When `/eli5 <text>` is invoked, Claude executes in the current turn:

1. **Scan the input** for every jargon term, acronym, proper noun, and long sentence. Mark them.
2. **Produce a glossary** (internal, not shown) mapping each marked item to its ELI5 equivalent via the five rules above.
3. **Rewrite paragraph by paragraph**, applying the glossary, shortening sentences, and replacing abstractions with analogies.
4. **Preserve structure** — section headings, pull-quotes, lists all survive, just with rewritten words.
5. **Emit the result** as text. If `--pdf` was passed, delegate to `/generate-pdf` with the rewritten content and the same brand config as the original document.

## What It Does NOT Do

| Not done by `/eli5` | Handled by |
|---------------------|-----------|
| Remove factual claims | Never — keep every claim, just rephrase it |
| Hide or soften numbers that matter | Keep them, but round and explain (&ldquo;196 PRs&rdquo; → &ldquo;196 little pieces&rdquo;) |
| Invent new facts or analogies that change the meaning | Never. Analogies must be structurally faithful to the original idea |
| Replace structured data (tables, code) with prose | Leave tables as tables; prose beside them may be simplified |
| Decide whether to ship an ELI5 version at all | Operator's call — this skill only runs on request |

## Safety invariants

1. **Faithful by construction.** Every analogy must preserve the original claim's truth conditions. &ldquo;HAL is faster than Claude&rdquo; cannot become &ldquo;HAL is the best robot&rdquo; — those aren't the same claim.
2. **No condescension.** Never refer to the reader as "a kid" or "a beginner" in the output. Write TO a bright reader, not ABOUT them.
3. **Preserve gratitude, credit, and tone.** If the original thanks someone, the ELI5 also thanks them. If the original is respectful of a predecessor, the ELI5 is too.
4. **Attribution stays.** Named people, companies, and products keep their names (with glosses). Don't anonymise.

## When to use

- A technical essay needs a marketing-friendly companion.
- A business stakeholder asks "can you explain this to me like I'm not technical?"
- An onboarding document needs to land with both engineers AND support staff.
- Any pair of documents you want to ship together — one for each audience.

## Falsification

This skill is wrong if any of the following hold after three months:

- ELI5 rewrites are judged by readers as "talking down" more than "meeting them where they are" (survey).
- The analogies in ELI5 outputs drift from the source claims so that fact-checkers flag ≥1 in 20 as misleading.
- Operators report they prefer `/plain-language` in >80% of cases — meaning `/eli5` is redundant, not distinct.

If falsified, merge `/eli5` back into `/plain-language` as a `--level=5` flag rather than keep it as a separate skill.

## Origin

Codified after the 2026-04-18 HAL Independence Article session. V&gt;&gt; asked for a technical essay; then asked for an ELI5 version; the pair revealed that the two renditions use different rule systems and deserve different skill entry points. `/plain-language` already existed for jargon stripping; `/eli5` covers the deeper reconstruction.
