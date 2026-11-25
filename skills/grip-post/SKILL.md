---
name: grip-post
description: Writes and gates a public post in GRIP's voice - plain language, no hype or shilling, every world-event claim sourced with a URL and the date it was fetched, opinion framed as the operator's own, a provenance block, copy-paste-ready plain text, one PDF or image per post, leak sweep and pixel check - by running the grip-post CLI's check, ground-check and Unicode transforms over the draft. Use when drafting a LinkedIn post, a public update, or a post series for the operator to publish by hand. Never posts anywhere.
status: Active
effort: medium
version: 1.0.0
created: 2026-09-08
user_invocable: true
command: grip-post
triggers:
  - /grip-post
  - "draft a LinkedIn post"
  - "post series"
  - "gate this post"
integrates:
  - authentic-writing
  - linkedin-post-craft
  - professional-pdf
  - redact
see_also:
  - social-banner
  - send-to-phone
---

# /grip-post - a public post in GRIP's voice, gated, rendered, never sent

**Plain English**: you have something to say in public and you want it to survive
contact with a sceptical reader. `/grip-post` turns a draft into plain text the operator
can paste into LinkedIn by hand. Every sentence about the world points at a source and
the date it was read. Every opinion is the operator's, said in the first person. The
draft is refused, not quietly rewritten, if it contains a marketing phrase. One PDF or
image is rendered per post, swept for anything that should never appear in public, and
looked at as pixels before it is called done. The result lands in an outbound folder with
a provenance block. It does not post. That last step is a human's.

This skill is a **wrapper**. The formatter and the anti-fluff gate already exist as the
`grip-post` CLI in the `CodeTonight-SA/grip-post` repository (a Chrome side panel, a
Safari app and a terminal front-end over one pure core). `linkedin-post-craft` already
covers structure and Unicode styling; `authentic-writing` already holds the banned
vocabulary. What was missing was the voice, the sourcing discipline, the delivery and
the refusal to post - so that is all this skill adds. Rebuilding the formatter here would
be a third implementation to drift.

## What it wraps

The CLI: `npx tsx bin/grip-post.ts <verb> [text | --file <path>]` inside the grip-post
checkout, with these verbs:

| Verb | What it does | Verdict shape |
|------|--------------|---------------|
| `check` | anti-fluff gate: banned phrases, em-dash and emoji density | `Verdict: CLEAN`, `WARN` or `DENY` |
| `ground-check` | regex floor for unsourced claims ("studies show", bare percentages) | `Grounding verdict: ... REVIEW` when something needs a source |
| `strip-tells` | removes AI tells and shows what went | transformed text |
| `bold` `italic` `brackets` `hr` `arrow` `handles` `diamond` | Unicode transforms that survive a LinkedIn paste | transformed text |

The CLI exits 0 on every verdict, so the skill reaches it through
`skills/grip-post/scripts/grip-post-gate`, which does three things:

1. Resolves the checkout without a literal home path (`rules/path-portability.md`):
   `$GRIP_POST_REPO`, then `~/CodeTonight/grip-post`, then the newest
   `~/.codex/plugins/cache/grip-post/grip-post/*/`. `grip-post-gate --where` prints the
   rung that won.
2. Runs the verb and echoes the CLI's output unchanged.
3. Turns the verdict into an exit code: `0` clean, `1` refused (`DENY` or `REVIEW`),
   `2` usage, `3` no checkout or no `npx`. A workflow gates on the code, never on the
   last line of text.

```bash
skills/grip-post/scripts/grip-post-gate --where
skills/grip-post/scripts/grip-post-gate check --file draft.txt
skills/grip-post/scripts/grip-post-gate ground-check --file draft.txt
skills/grip-post/scripts/grip-post-gate bold "one phrase to emphasise"
```

Math-bold Unicode is not read aloud by screen readers. Use the transforms on a phrase,
never on a paragraph, and prefer none at all when the sentence carries itself.

## Voice rules

These are the standing rules the draft is written under. They are named here so the
skill can be checked against them; the authority stays in the files.

- **No shilling, including of our own things.** Memory `feedback_no_shilling_general`
  (binding): no pitching, hyping, advocating or manufactured enthusiasm for any product,
  and GRASP, GRIP and HAL are explicitly included. A post may end on the proof-layer
  point - that a claim about a machine should come with a record a stranger can check -
  but it states the point and stops. No call to action, no "we built", no superlatives.
- **No fluff vocabulary.** `skills/authentic-writing/SKILL.md` carries the banned words
  and phrases; the CLI's `check` enforces the overlap mechanically and refuses with
  `DENY`. The refusal is the feature. The operator edits the draft; the skill never
  rewrites a banned phrase away in silence.
- **Opinion is the operator's, in the first person.** "I think", "my reading is",
  "what I would want to see". Never "experts agree", never the passive voice hiding who
  holds the view.
- **Nothing invented about a real person.** A named person appears only in words that
  were actually published, quoted from and linked to the place they were published, with
  the date. Where the operator's own published argument is the source, it is quoted as
  his argument, not stated as a fact about the world. No paraphrase that sharpens what
  was said.
- **Plain language first, concise second** (`rules/canonical-output-style.md`): ordinary
  words, full sentences, no telegram fragments. A first-time reader follows it in one
  pass.
- **Provenance is named** (`rules/provenance-name-the-authors.md`): the human author and
  the models that materially contributed, pinned by id where re-running matters. On a
  public post the human author is named by the operator's signature, "V>>" (the
  operator's decision of 2026-10-12), never by the full name in that rule's example.
- **Public surfaces carry no internal data** (`rules/visual-artifact-leak-prevention.md`):
  no home paths, hostnames, IPs, ports, keychain names, emails, phone numbers, client
  names or credential shapes - in the text or in the rendered pixels.

## Sourcing rule

Every claim about the world - who announced what, what a benchmark scored, what an
index did - carries the source URL and the date that source was read, in the post
itself, not in a footnote the reader cannot see. Three grades, and the wording follows
the grade:

| Grade | What was done | How it is written |
|-------|---------------|-------------------|
| primary, fetched | the publisher's own page or feed was read that session and the words quoted | stated plainly, quoted where it matters, URL and date given |
| reported | a major outlet's page was read; the underlying primary was not | "The Guardian reported on 5 September that ..." - the outlet is named as the source |
| headline only or unfetched | a headline, an index entry, a search summary | not used as a fact; at most "there is coverage of ..." with the URL, or left out |

`ground-check` must come back without a `REVIEW` before the draft moves on. A finding it
raises is either sourced or softened to a first-person view; it is never deleted to make
the gate pass. A number without a source is an opinion wearing a suit - either find the
source or say "my sense is".

Claims that are not settled are written as not settled. "Reported" is not "true";
"announced" is not "demonstrated"; "scored 99.9 percent under one harness" is not
"scored 99.9 percent".

## Workflow

Validate, fix, repeat, in this order; each step is a checkbox, and a red step sends the
draft back to the operator rather than forward to the next step.

1. **Collect the facts first.** Fetch every source the post will lean on and record URL,
   publisher, date published and date fetched. Mark each fact primary, reported or
   headline-only. Nothing goes in the draft that is not on this list.
2. **Draft** in plain text, first person for opinion, sources inline. For structure use
   `linkedin-post-craft` (its A/B/C shape), never its hype.
3. **Gate**: `grip-post-gate check --file post.txt` must exit `0`. On `DENY` name the
   phrase to the operator and stop. On `WARN` show the density number and trim.
4. **Ground**: `grip-post-gate ground-check --file post.txt` must exit `0`. Source or
   soften every finding.
5. **Transforms on selections only**, if at all: `bold` on a phrase, `hr` between
   sections. No transformed paragraphs.
6. **Provenance block** at the end of the post: the operator's signature, V>>, then "With
   assistance of" and the model ids that contributed.
7. **Render** one PDF and one PNG per post with `professional-pdf` (the neutral default
   variant unless the operator chose otherwise), the provenance block included.
8. **Sweep and look**: `bin/render-leak-sweep <html-and-post-dir>` must exit `0`;
   `bin/pdf-audit post.pdf` must exit `0`; then open the page images and read them as
   pixels - the audit renders them, it does not judge them.
9. **Deliver**: write `post-N.txt`, `post-N.pdf`, `post-N.png` and an `index.html` to
   `~/.grip/data/outbound/<campaign>/`, then `bin/grip-backup` each file.
10. **Stop.** Report the folder. Post nothing.

```bash
G=skills/grip-post/scripts/grip-post-gate
$G check --file post-1.txt && $G ground-check --file post-1.txt || exit 1
weasyprint post-1.html post-1.pdf
bin/render-leak-sweep "$OUT" && bin/pdf-audit "$OUT/post-1.pdf"
bin/grip-backup "$OUT/post-1.txt" "$OUT/post-1.pdf" "$OUT/post-1.png" --label linkedin-week
```

## Output contract

- **Plain text, copy-paste-ready.** The post is shown in a fenced code block so the
  operator can select it, copy it and paste it with no edit (memory
  `feedback-messages-must-be-copy-paste-ready`; never a markdown blockquote - the
  terminal's gutter character copies with the text). No markdown inside the post:
  LinkedIn renders none. Paragraph breaks are blank lines. Links are bare URLs.
- **Length**: LinkedIn truncates after roughly the first two hundred characters, so the
  first line carries the point; the whole post stays under three thousand characters.
- **Sources inline**, date and URL, in the sentence that uses them.
- **Provenance block** last, in this shape:

```text
Written by V>>.
With assistance of: <model id>, <model id>.
Sources fetched <date>; every link above was read that day.
```

- **Files per post**: `post-N.txt` (the exact text), `post-N.pdf` (A4, neutral variant,
  provenance on the page), `post-N.png` (the first page at 150 dpi, for the image slot),
  and one `index.html` per campaign listing every post with its verdicts.
- **Verdicts recorded**: the `check` and `ground-check` outputs for each post are kept
  beside it (in the index), so a later reader can see the gate actually ran.

## Example

A short worked run, from draft to artefacts. Dates are those of the run.

Draft handed in:

```text
We are thrilled to announce that GRASP seamlessly solves AI provenance. Studies show 90%
of AI claims are unverified.
```

Gate:

```text
$ grip-post-gate check --file draft.txt
Verdict: DENY
Banned phrases (2):
  - "thrilled to announce" at offset 7
  - "seamlessly" at offset 44
(exit 1)
```

The skill stops here and names the two phrases. It also flags, before the operator asks,
that the second sentence will fail `ground-check` ("studies show" with no source) and
that the first sentence is a pitch for our own tool, which `feedback_no_shilling_general`
forbids regardless of wording. The operator rewrites:

```text
OpenAI announced GPT-6 Astra on 3 September 2026 and called it its most capable model
(https://openai.com/index/gpt-6-astra, read 8 September). ARC Prize's own evaluation the
same day put it at 62.7 percent on ARC-AGI-3 with the standard harness and 99.9 percent
with a provider adapter (https://arcprize.org/blog/astra). Two numbers, one model.

My reading: the word "AGI" is doing work the evidence is not. What I would want next to
any capability claim is the harness, the cost and a record a stranger can re-run.

Written by V>>.
With assistance of: claude-fable-5-1.
Sources fetched 8 September 2026.
```

Gate again: `check` exits 0 (`Verdict: CLEAN`), `ground-check` exits 0. Render:
`post-1.html` from the neutral variant's template, `weasyprint` to `post-1.pdf`,
`pdftoppm -png -r 150 -f 1 -l 1` to `post-1.png`. `bin/render-leak-sweep` on the folder
exits 0; `bin/pdf-audit post-1.pdf` exits 0 and prints the page images, which are then
opened and read. Files are written to `~/.grip/data/outbound/<campaign>/` and backed up
with `bin/grip-backup`. The terminal report ends with the folder path and the sentence
"Nothing was posted."

## Falsification

This skill is wrong, and should be narrowed or retired, if over roughly the next twenty
drafts:

- the gate never returns `DENY` or `REVIEW` once - then the operator's drafts do not
  need a gate, and the skill is ceremony (measure: refusals per twenty drafts);
- a post reaches a public surface carrying a claim about the world with no source, or a
  claim about a real person that was not quoted from a publication - then the sourcing
  rule is not binding in practice and needs a mechanical check rather than a checklist
  (measure: unsourced claims found after the fact, target zero);
- the operator materially edits every output before posting it - then the voice rules
  are wrong about his voice and should be re-derived from what he actually posts
  (measure: operator edit rate);
- the leak sweep and the pixel check never once catch anything the text review missed
  across the campaign renders - then the render step is redundant for text-only posts
  and can drop to source-sweep only.

Never track the number of posts produced as success; a folder of unposted drafts nobody
wanted is the failure, not the metric.

## HALT classes

- **Never posts anywhere.** Not LinkedIn, X, Medium, Slack, Discord, WhatsApp or email.
  No browser automation against a publishing form. Nothing leaves the outbound folder
  except by the operator's own hand.
- **Never invents a quotation or a fact about a real person**, and never paraphrases a
  published argument into a stronger one.
- **Never rewrites a `DENY` away.** The gate refuses; the human edits.
- **Never renders a public artefact without the sweep and the pixel look.**
- **Never spends money** on a paid publishing or image service to satisfy a step.

## Relationship

Composes, and does not restate: `linkedin-post-craft` (structure and the Unicode
toolkit), `authentic-writing` (the banned vocabulary), `professional-pdf` (the render and
its mandatory audit), `redact` (when a draft touches client or personal context - run it
before any text reaches a cloud model), `social-banner` (a cover image when the post
needs one), `send-to-phone` (to hand the operator the folder path, never the post to a
public channel). Governed by `rules/canonical-output-style.md`,
`rules/provenance-name-the-authors.md`, `rules/visual-artifact-leak-prevention.md`,
`rules/prove-it-claims-canonical.md` (a "cannot verify" must name the scopes searched)
and the binding memories `feedback_no_shilling_general` and
`feedback-messages-must-be-copy-paste-ready`. The upstream CLI and its own skill live in
`CodeTonight-SA/grip-post`; when that CLI starts returning exit codes, the wrapper's
verdict parsing becomes a passthrough and `tests/test_grip_post_skill.py` says so.
