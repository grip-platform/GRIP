# Prompt-audit sweep over GRIP's skills — 2026-08-11

## What this is (plain English)

Claude Code 2.1.221 added a tool that reads a prompt and tells you which parts of it were
written for an older model and are now doing harm. Instructions age: text added years ago to
make a weaker model plan properly, or to make it trigger at all, does not simply become
useless on a newer model — it can actively push the newer model into over-planning,
over-triggering, or rigid answers where judgement was wanted.

GRIP has 323 skills. Nobody had ever audited them for that kind of ageing, and until this
release there was no tool for it. This document is the first sweep.

**The headline is mostly good news.** GRIP is clean on nearly every ageing pattern the audit
looks for. There are no "think step by step" instructions, no scratchpad tags, no
numbered-step choreography, no "be thorough and do not be lazy" padding, and no references
to retired models. Those are five true zeros, and further down you can see the control test
that proves the scanner which found them was actually working.

**One real defect class turned up: skills that name a specific model version.** A skill
saying "Opus 4.7 plans each wave" was written when 4.7 was current. It is now two
generations behind, and in one case the stale version sits in the skill's `description`,
which is loaded into every single request. This pull request fixes two such skills as
worked examples and names the other two precisely so they can be fixed next.

**One thing is measured but deliberately not fixed**, because it is a judgement call for
V>> rather than a defect: 40 skill descriptions are long enough that together all
descriptions cost roughly 17,400 tokens on every request. Trimming them would save real
money and might make skills harder to find. That trade-off is set out at the end.

---

## The tool

```
Skill(claude-api, "prompt-audit")
```

Three things about it are worth knowing before you rely on it.

**It is a skill, not a command.** There is no `claude prompt-audit` binary. It is
LLM-driven: it reads files and reasons about them. Verified runnable on 2.1.227 — invoking
it loads the skill, and its authoritative pattern catalogue is the bundled
`shared/prompt-audit.md` (found under the installed bundled-skills directory for the
running version, at `claude-api/shared/prompt-audit.md`).

**It proposes a diff and never applies one.** Quoting its own prime directive: a finding you
cannot tie to a named pattern, with a reason grounded in the target model's documented
behaviour, is not a finding. And: "an audit that finds nothing should change nothing — a
clean surface is a valid outcome, and an empty diff beats a manufactured one."

**Its "What not to flag" keep-list binds as hard as its pattern tables.** This is the part
that matters most for GRIP, and it is why this sweep has an explicit non-findings section
below. Used carelessly, an audit like this deletes the very instructions that are holding a
system together.

---

## Coverage, stated honestly

| | |
|---|---|
| Ref audited | `origin/main` at `8dd1c9eb22e5c518a0c147a7622f9b710cd31bc2` |
| `SKILL.md` files, all | 401 |
| Live (excluding `_archived/`) | **323** — the audited corpus |
| Archived | 78 — excluded, see below |
| Read in full and reasoned over | 24 of 323 (7.4%) |
| Scanned mechanically for every pattern | 323 of 323 (100%) |

Two different activities, and the distinction matters. The pattern scans below cover the
whole corpus. Reading a file and forming a judgement about it covers 24. **A scan hit is a
candidate, not a finding** — every finding in this document was individually opened and
classified, which is why some scan hits appear under non-findings.

Archived skills are excluded on evidence, not assumption. Claude Code discovers skills with
a `skills/*/SKILL.md` glob, and archived skills sit one level deeper at
`skills/_archived/<name>/SKILL.md`. Tested directly: that glob returns 306 paths and **zero**
from `_archived`. GRIP's own code agrees — `lib/asset_counter.py` excludes `_archived` from
counting, `lib/grip_starter_kit.py` excludes it from packaged kits, and
`lib/focus_cards.py` defaults `include_archived=False`. Archived skills are dead weight, not
a live surface.

---

## Finding: skills that pin a model version

**The pattern.** A skill names a specific model generation in its prose. When the generation
moves on, the skill is quietly wrong, and nothing fails — so nobody notices. The audit's own
description of the mechanism is that nobody owns the removal, so prompts accumulate the union
of every generation's mitigations.

**Why it is not merely cosmetic.** GRIP's canonical `.claude/CLAUDE.md:18` on this same ref
states the routine default is **Opus 5 1M**. Skills naming Opus 4.7 therefore contradict the
canonical document *on the same commit* — this is an internal inconsistency, verifiable
without reference to what any particular session happens to be running.

Seven live skills name a model version. Four are genuine fossils. Two of the four are fixed
in this pull request; the other two are named here with exact coordinates.

### Fixed here

**`skills/context-extension/SKILL.md`** — ten lines pinned Opus 4.7, including line 2, the
frontmatter `description`. A description is loaded on every request, so this was the most
expensive instance in the corpus. Rewritten to be generation-agnostic ("current Opus defaults
to 1M") rather than deleted, because the skill's substance is still correct: its 700K/800K
thresholds are independently confirmed by `.claude/CLAUDE.md:18`, so the numbers stayed and
only the version attribution changed. Line 70 needed a different treatment — it *enumerated*
which generations default to 1M, a shape that goes stale by construction as generations are
added, so it became a statement about current generations instead of a list.

**`skills/rsi-aliases/SKILL.md:42`** — pinned "Opus 4.7 plans each wave; Sonnet 4.6
executes". Now "Opus plans each wave; Sonnet executes". Only the version numbers were
dropped; the role words stayed, which also keeps the line consistent with the unversioned
"Sonnet Loop B process" two lines below it.

**The general rule this suggests: name model *roles*, not model *versions*.** "Opus plans,
Sonnet executes" survives a generation change. "Opus 4.7 plans" does not.

### Reproducing the fix check

The probe below counts pinned versions in a file before and after. It is a **consistency
check, not a behavioural test** — it proves the pins are gone, not that any model behaves
differently.

```bash
# per file: pins on origin/main, then pins in the working tree
git show origin/main:<path> | rg -c 'Opus 4\.[0-9]|Sonnet 4\.[0-9]'
rg -c 'Opus 4\.[0-9]|Sonnet 4\.[0-9]' <path>
```

| File | Pins before | Pins after |
|---|---|---|
| `skills/context-extension/SKILL.md` | 12 | **0** |
| `skills/rsi-aliases/SKILL.md` | 2 | **0** |
| `skills/context-gate/SKILL.md` | 5 | 5 — untouched |
| `skills/ultrathink/SKILL.md` | 2 | 2 — untouched |

The last two rows are the point: the probe is not vacuously green. It still reports seven
pins in the files this pull request deliberately leaves alone, so a green result on the
first two rows means something.

### Not fixed — outside this pull request's file budget

Both verified against `origin/main`, ready to apply as-is.

**`skills/context-gate/SKILL.md`** — four lines, and the worst instance in the corpus because
it holds *two* generations at once:

| Line | Content |
|---|---|
| 148 | `Claude Opus 4.5 and Sonnet 4.5 have built-in context awareness. Monitor for:` |
| 292 | `For 1M context (Opus 4.7 default):` |
| 326 | `Opus 4.7 defaults to 1M context since April 2026. ACE extension is legacy.` |
| 332 | `| Opus 4.7 (default) | 1M | 700K | 800K |` |

Line 148 pins the 4.5 generation and lines 292–332 pin 4.7. That is the accumulation the
audit describes, visible inside a single file.

**`skills/ultrathink/SKILL.md:111-112`** — a model-selection table that directly contradicts
the canonical document:

| Source | Says |
|---|---|
| `skills/ultrathink/SKILL.md:111` | Surgery / VERIFY / hardest architecture → `Opus 4.8 at /effort xhigh` |
| `.claude/CLAUDE.md:18` | High-effort / surgery / VERIFY: **Opus 5 1M at `/effort xhigh`** |
| `skills/ultrathink/SKILL.md:112` | Frontier-generation onboarding opt-in → `Opus 4.8 or Fable 5` |
| `.claude/CLAUDE.md:18` | Frontier-generation onboarding opt-in: **Fable 5** |

`ultrathink` is described as always-active, so this is a live inconsistency rather than a
dormant one.

---

## Explicit non-findings — the keep-list working

These were scan hits that are **correct as written**. They are recorded because the risk with
an audit tool is not that it misses things; it is that someone runs it, sees the hits, and
"fixes" all of them.

**`skills/ultraprompt-picker/SKILL.md:119`** — "Authored with Claude Opus 4.8." This is a
provenance record, and `rules/provenance-name-the-authors.md` requires naming the specific
models that contributed. It is a true statement about the past. Genericising it would destroy
the very information the rule exists to preserve. **Never touch this class.**

**`skills/activate-grip-tui/SKILL.md:236,241,245,249`** — the onboarding model picker's option
list, including `"Opus 4.8" → "opus-4-8" (claude-opus-4-8[1m])`. These are **functional
identifiers**, passed to `lib/grip_model.py::set_model`, which validates against an allowlist.
Replacing them with role words would break the picker outright. Line 578 is a changelog
entry — also a historical record. (Whether that picker should additionally *offer* Opus 5 is
a real question, but it is a product question for onboarding, not a dated-prompting-pattern
finding, and it is out of scope here.)

**`skills/eli5/SKILL.md:55`** — `"Opus 4.7's 1M context window enables long-session memory."`
appears in a before-and-after table demonstrating how to translate jargon into plain English.
The version is *sample input*, not an instruction. Reported at low confidence and deliberately
left out of the diff, per the audit's instruction to flag low-confidence items in the report
rather than the diff. The one imperfection is that the "after" column reads "The newest
version of Claude", which is no longer true of 4.7 — a trivial wording matter, not a
prompting defect.

---

## Clean surface — five true zeros, with the control that proves it

Measured across all 323 live skills. The two controls exist because a scanner returning zero
is indistinguishable from a scanner that is broken.

| Pattern group | Probe | Files |
|---|---|---|
| 1b — API-superseded scaffolds | `think step by step` \| `take a deep breath` | **0** |
| 1b — API-superseded scaffolds | `<scratchpad>` \| `</scratchpad>` | **0** |
| 1b — retired model names | `claude-2` \| `claude-3` \| `claude-instant` | **0** |
| 1c — over-specification | `STEP [0-9]` choreography | **0** |
| 1c — generic-virtue padding | `do not be lazy` \| `do not stop early` \| `be accurate, thorough` | **0** |
| **CONTROL** | `MUST` \| `NEVER` \| `ALWAYS` | **236** |
| **CONTROL** | the word `skill` | **255** |

The controls fired on 236 and 255 files. The scanner works, so the five zeros are real
absences rather than a silent failure. This matters more than it looks: an earlier pass over
this same corpus produced a false zero purely from a bad flag (`rg -E` is `--encoding`, not
extended-regex), which is recorded in `rules/bash-safety.md`. A measurement without a control
is a claim, not evidence.

---

## For V>> to decide: the description token tax

Not a defect and not fixed here. A genuine trade-off with no technically correct answer.

Every skill's `description` is loaded on every request — that is how the model knows a skill
exists and when to reach for it. Measured across the 323 live skills:

| | |
|---|---|
| Skills carrying a description | 284 |
| Median length | 201 characters |
| Mean length | 245 characters |
| Longest | 822 characters |
| Over 400 characters | **40** |
| Over 600 characters | 8 |
| Total across all descriptions | 69,741 characters — roughly **17,400 tokens** |

The five longest: `skills/verdict/` (822), `skills/onboarding-interview/` (814),
`skills/ultralearn/` (763), `skills/finish/` (749), `skills/legal-provenance/` (683).

**Option A — trim the 40 long ones.** Rewrite each to name generalised categories of intent
instead of listing near-synonymous example queries. Recovers a meaningful share of 17,400
tokens on every request.

**Option B — keep them.** Accept the cost as the price of reliable routing across a
323-skill inventory where discovery is genuinely hard.

The audit refuses to settle this, and is right to. Its own notes say trigger text may
legitimately carry calibrated urgency because skills currently under-trigger, and that the
proper arbiter is a trigger evaluation — measuring whether a shorter description still gets
the skill invoked. **GRIP has no trigger eval.** So there is currently no way to tell whether
trimming would cost anything, in either direction. Building that eval is the honest
prerequisite for choosing A.

If B is chosen, the 17,400-token figure belongs somewhere visible — `rules/system-capabilities.md`
is the natural home — so the cost is revisited as the skill count grows rather than forgotten.

---

## Rejected: the migration guide's advice to delete verification scaffolding

The bundled `shared/model-migration.md`, in its "Migrating to Claude Opus 5" section, says
verbatim: **"Delete verification instructions from prompts and verification steps from the
harness"**, on the grounds that "removing them reduces over-verification with no capability
regression".

Applied literally to GRIP, that means deleting verify-canonical, R0, and `/prove-it`. **It
must not be applied.** The audit's own keep-list overrides it: prohibitions against current,
demonstrated failures stay, and the discriminator is whether the failure still reproduces on
the target model in this context.

It reproduces. Both of the following happened while producing *this* document:

- The `rg -E` flag error described above produced a confident zero from a broken scan — the
  exact failure `rules/bash-safety.md` was written to prevent.
- The investigation feeding this sweep reported 2 fossils; re-measuring found 4, plus 2
  legitimate keeps it had not classified, plus a token figure roughly 12% overstated because
  it counted 78 archived skills that are never loaded.

Those are current, demonstrated failures of exactly the kind verification scaffolding exists
to catch, observed on a current model. The guidance is sound advice for prompts written
defensively against older models; it is wrong about doctrines with live incident provenance.

---

## Limits of this document

Stated plainly so nothing here is read as stronger than it is.

**No before-and-after behavioural test was run on any fix.** The audit's own Step 7 says
removal is a hypothesis, not a conclusion, and asks for the instruction's purpose to be probed
before and after on a scratch copy. That was not done. What *is* proven is narrower and still
sufficient to act on: the pinned versions contradict `.claude/CLAUDE.md:18` on the same commit.
The claim "these edits improve behaviour" is **unproven**.

**Nothing prevents this fossil class from recurring.** The natural guard is a test asserting
that no live skill pins a superseded model version, and it does not exist. Writing one is
outside this pull request's declared file scope, so it is not included, but it is the
highest-value follow-up in this document — the absence of such a guard is precisely why two
generations of the same fossil were able to coexist unnoticed.

**Only 24 of 323 skills were read in full.** The pattern scans are complete; the judgement
is not. A dated instruction that matches none of the probes above would not have been found
outside those 24.

**Skill age could not be established.** `git log --follow --diff-filter=A` reports 2026-07
for essentially every skill in a repository whose first commit is 2026-01-01 — almost
certainly a mass-rename artefact. So "written for an older model" rests entirely on content
markers, never on dates.

**The 17,400-token figure assumes 4 characters per token.** It was not measured against the
real tokeniser. Treat it as an order of magnitude, not a measurement.

**Two of the audit's four pattern groups were not examined at all.** Group 3 (tool
descriptions) and Group 4 (request configuration, cache-hostile ordering, using an LLM to
execute a deterministic plan) are in the tool's scope but were out of this sweep's. GRIP's
tool descriptions and request-building code remain unaudited.

---

Primary Author: V
Provenance: Claude Opus 5 (1M context)
