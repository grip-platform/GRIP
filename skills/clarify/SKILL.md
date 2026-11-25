---
name: clarify
description: "Make a passage clear for the person who reads it or acts on it, and keep its receipts. One command for /tldr, /plain-language, /eli5 and /encapsulate, which are now its presets. The `essence` preset gives each decisive claim a status word, its best anchor and a check. The `simple` preset, also run as /simple, gives one plain line and then the idea for a bright beginner, with every fact kept. Writes in 80% ASD-STE100, checked by lib/ste_lint.py, and checks the output against the passage with lib/clarify_check.py."
user_invocable: true
command: clarify
triggers:
  - /clarify
  - /essence
  - clarify this
  - make this clear
---

# /clarify: clear, and with its receipts

## What it does

`/clarify` rewrites a passage for the person who must read it or act on it. It keeps the words that person must type, and it adds nothing that the passage does not say. With the `essence` preset, it also keeps the receipts. Each claim that changes the next action shows its status and the check that would prove it. The text follows 80% of ASD-STE100, and a program counts the parts that can be counted.

## Usage

```
/clarify [passage]            # the command picks the preset
/clarify line [passage]       # was /tldr
/clarify act [passage]        # was /encapsulate
/clarify plain [passage]      # was /plain-language
/clarify novice [passage]     # was /eli5
/clarify essence [passage]    # the meaning and its receipts (shortcut: /essence)
/clarify essence --diagram    # the same, as a Mermaid diagram
/clarify essence --html       # the same, as one web page
/clarify simple [passage]     # one plain line, then the idea, every fact kept (shortcut: /simple)
```

With no passage, clarify the last answer in this conversation. The old commands still work. Each one is a preset of this command, with its own length and audience.

## The presets

| Preset | Old command | Gives | Length |
|---|---|---|---|
| `line` | `/tldr` | the one fact that the reader needs | 25 words at most |
| `act` | `/encapsulate` | what the reader does, and what it leads to | two sentences at most |
| `plain` | `/plain-language` | the same text, with each insider term in plain words | as long as the passage |
| `novice` | `/eli5` | the idea for a bright beginner, with one structural analogy | short paragraphs |
| `essence` | (new) | the meaning, and the receipt for each claim that gates a decision | a short block, then a count line |
| `simple` | (new) | one plain line, then the idea for a bright beginner, with every fact kept | one line, then short paragraphs |

When no preset is named, choose one from the passage. A next step or a set of instructions gets `act`. A report with claims gets `essence`. An idea or a term gets `plain`. Say which preset you chose, in one line, at the end.

## The register: 80% ASD-STE100

Andrej Karpathy suggested this register on 2026-10-02, softened to "80% of the way to ASD-STE100" because the full specification is strict. Here, 80% means two halves.

The program enforces the half that it can count. Pass the draft to `lib/ste_lint.py` on standard input, as `commands/clarify.md` shows, or give it the path of a file. Fix every error before you show the draft:

- a descriptive sentence has 25 words at most, and an instruction 20.
- a paragraph has 6 sentences at most, and each list item counts as one paragraph.
- a word that the STE sheet does not approve is flagged with its approved word, for example `utilize` with "use" and `prior to` with "before".
- a prose paragraph of 40 words or more keeps at least 4 determiners in every 100 words, because telegram style is hard to read.

The writer follows the half that needs judgement. Use one word for one meaning, and the same word for the same thing every time. Use the active voice and the simple tenses in instructions. Put a WARNING or a CAUTION as the command first, and the risk after it. Use a vertical list for complex steps. To mention a word without using it, put it in backticks.

## The `essence` preset: the receipts

Give a receipt only to a claim that changes what the reader does next. Other claims keep their evidence in the full record. Each receipt has four parts:

1. A status word, one of four, each with one meaning. OBSERVED: seen in an artefact, such as command output, a file line or a page. INFERRED: it follows from observed facts by a step that you state. UNVERIFIED: said, but not checked. REFUTED: checked, and false.
2. The claim, in one sentence.
3. One anchor, the best available, in this order: a command with its output, a file and line, a URL, a seal.
4. A break line: "To break this, run X and look for Y."

An UNVERIFIED claim also carries the command that would check it. When you can run that command yourself, run it before you close and report what it showed. Only a check you cannot run becomes the RECOMMENDED NEXT STEP, owned by whoever can run it. A count line comes before the receipts: "N claims: M observed, K inferred, J unverified." An authors line names the people, then the model ids. A limits clause names what was not checked, in two sentences at most.

A seal proves integrity, never correctness. This command never seals anything. To seal a record and publish it, it calls `/witness`.

## The `simple` preset: the simplest faithful explanation

`/simple` runs this preset. It turns a passage or a concept into the simplest explanation that is still true. It has two parts:

1. One plain line, with the one fact that the reader needs, in 25 words at most, as in the `line` preset.
2. The explanation, for a bright beginner who reads it once. Use short paragraphs with one topic each. Define each term the first time that you use it.

Use an analogy only when its structure matches the idea. Each part of the analogy must map to one part of the idea, and each relation between the parts must hold in both. When no analogy fits, use none, because a wrong analogy teaches a wrong idea.

Simple never means wrong or vague. This preset keeps more than `novice` does:

- Keep each fact of the passage. Do not write a "Left out:" line, because this preset leaves nothing out.
- Keep each number, path, command, name, issue id and URL exactly as written. Explain a number, but do not round it. This replaces the advice in `skills/eli5/SKILL.md` to drop version numbers, SHAs and issue numbers, and to round.
- Keep each marked uncertainty, such as "may", "it is not established", UNVERIFIED and each status word. A deleted hedge makes a guess look like a fact.
- Put a WARNING or a CAUTION first and complete, before the plain line. The 25-word limit does not apply to it.

Before you show the result, run the two checks that `commands/clarify.md` shows: the linter, and the check against the passage. Add `--keep-all` to the second one. A token of the passage that the output drops is then an error, and a "Left out:" line does not excuse it. A concept with no passage has no text to check against. Then say only what you can support. Mark each claim that you did not check as UNVERIFIED.

Passage:

```text
The pipeline retries a failed upload 3 times, with a 30 s backoff, before it marks the job FAILED in `state/jobs.json`. It is not established whether a timeout also gets these retries.
```

Output:

```text
If an upload fails, the pipeline tries it 3 more times, 30 s apart, before it marks the job FAILED.

A pipeline is a series of steps that a program runs in order. When an upload in this pipeline fails, the pipeline tries the upload again, 3 times. Before each new try, it waits 30 s, and this wait is the backoff. After those 3 tries, the pipeline marks the job FAILED in `state/jobs.json`. It is not established whether a timeout also gets these retries.

It is like a call to a busy phone line. You wait, then you call again, and after a set number of calls you stop.
```

Each number, the path and the hedge stay as written. The analogy maps part to part. A call is an upload, and a busy line is a failure. The wait between calls is the backoff, and to stop is to mark the job FAILED.

## Formats

Text is the default. On Karpathy's ladder, the next steps are a diagram, a web page and a video:

- `--diagram` draws the same claims as a Mermaid diagram in a fenced block.
- `--html` builds one page with the Artifact tool, under that tool's own rules. Each claim is one row: status, claim, anchor and check.
- A video is a script and a storyboard only. Nothing in GRIP renders video, and no paid voice service is used.

Every format carries the same claims, status words and anchors. A format that drops a receipt is wrong, however good it looks.

## The invariants, and the check behind each

| Invariant | The check |
|---|---|
| Nothing added | Each number, command, path, id and name in the output is also in the passage. `lib/clarify_check.py` flags each number, code span, path, issue id and URL that the passage does not have. The two readers check names and that each sentence maps to a span of the passage. |
| Nothing dropped in silence | Each technical token of the passage is in the output, or in one line that starts "Left out:". `lib/clarify_check.py` warns about the rest. |
| Every fact kept (`simple`) | Each technical token of the passage is in the output, and nothing is left out. `lib/clarify_check.py --keep-all` makes each dropped token an error, and a "Left out:" line does not count. |
| Receipts kept | Each gating claim has a status word. `lib/clarify_check.py` checks that an OBSERVED anchor exists (the path is on disk, down to its line) and that an UNVERIFIED claim carries its command. With `--fetch-urls`, it also checks that each URL answers. |
| Words to type unchanged | Each code span, and each line of a fenced block, is copied byte for byte. `lib/clarify_check.py` flags any that the passage does not have. |
| Readable | `grip-py lib/ste_lint.py` reports 0 errors. |
| Safe | A WARNING or a CAUTION is never shortened, and the command comes before the risk. |

A program can check the structure. It cannot prove that a claim is true, and it cannot prove that every fact was found. It also cannot see a promise, a condition or a step that uses only ordinary words. Show a failed check. Do not hide it.

## How you know that it went wrong

Two readers settle it, and neither needs to know the system. The first reader has the passage and the output. Each step, condition, warning or promise in the output must also be in the passage. The second reader has only the output, and must be able to act on it. An output that fails either reader is rewritten, and never defended.

## What stays separate

`/caveman` stays separate. It drops facts and small words on purpose, so it would fail the invariants of this command every time. Use it only for working prose, and only when you ask for it.

`/witness` stays separate too. It seals records and publishes pages, and it must check the evidence without the help of the explanation that it checks. At its fullest level, `/clarify essence` calls it.

## Where it came from

The operator asked for one command on 2026-10-03, with provenance at its centre. Two models led the ideas: Astra (`gpt-6-astra`) and Fable (`claude-fable-5-1`). Claude Opus 5.5 (`claude-opus-5-5`) joined their results and ran the convergence. The operator chose the name, the presets and the register. The full record is in `PARAMOUNT_BACKUPS/2026-10-03/clarity-ideation-2026-10-03-dossier`.

## Falsification

Over the next 20 uses, compare this command with the old ones on the same kind of passage. It is worse if the operator acts correctly less often, or if its output fails the two-reader test more often. Then the presets go back to the old commands, which is cheap because the old names still work. The linter is wrong if it flags plain prose that reads well, or misses telegram text that the operator calls laborious. In that case, tune its numbers and keep the check.
