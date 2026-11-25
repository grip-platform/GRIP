---
name: encapsulate
description: Say what a passage means in one or two plain sentences, for the person who has to act on it. What happens, and what it leads to. Keeps the words they must type or look for, drops the rest.
argument-hint: "the passage to encapsulate; nothing means the last recommended next step"
---

# /encapsulate

Give the meaning of a passage in one or two plain sentences: what happens, and
what it leads to, for the person who has to act on it.

This is the `act` preset of `/clarify`. The rules shared by every preset in
`skills/clarify/SKILL.md` apply too, including its 80% ASD-STE100 register.

The passage is `$ARGUMENTS`. With no argument, encapsulate the most recent
`RECOMMENDED NEXT STEP` in this conversation, or the last block of instructions
if there is none.

## How to write it

- Two sentences is the cap, not the target. One is better when it carries the
  meaning.
- Plain words (`/plain-language` register): a reader who has never seen the
  system follows it in one pass. No jargon left standing, no acronym without
  its meaning.
- Say what the reader does and what it leads to, not how the system does it.
  "Make a new token for the laurie@ account and store it under the name
  `laurie`" is the meaning; the three commands are the mechanics.
- Keep the words the reader must type or look for, exactly as written
  (`grip-account mint`, `laurie-old`, a file name). Drop everything else that
  is only there for the record: digests, pull-request numbers, exit codes,
  unless the passage is about them.
- Keep "who does what". If the passage hands work between two parties, the
  sentence says which part is theirs and which is yours.
- Saying what a command or token in the passage does is the meaning, and is
  allowed: `/login` is signing in, `--as laurie` is "under the name `laurie`".
  Adding a step, a condition, a warning or a promise the passage does not carry
  is not, however true it is of the system. The encapsulation says less than
  the original, never more. (The first draft of the example below promised
  "without ever showing it", which is true of the tool and absent from the
  passage; the council's review of GRIP#7177 caught it.)

## Where it stops

- A safety warning, an irreversible action, or a step that must be done in an
  exact order is not compressed below the point of ambiguity. The Auto-Clarity
  Override applies: keep the full sentence, and say that is why it is longer.
- A passage with two independent meanings gets two sentences, one each, never
  one sentence that blurs them.

## How you know it went wrong

Two readers settle it, and neither needs to know the system. One has both the
passage and the encapsulation: every step, condition, warning or promise they
find in the encapsulation must be in the passage too, or it added something.
The other has only the encapsulation: they must be able to do the step from
it, or it dropped something they needed. An encapsulation that passes both is
right; one that fails either is rewritten, never defended.

## How it differs from its neighbours

| Command | Gives |
|---|---|
| `/tldr` | the one load-bearing fact, in at most 25 words, evidence tokens kept |
| `/eli5` | the idea rewritten for a bright five-year-old, with analogies |
| `/plain` | the same text with its jargon translated, term by term |
| `/encapsulate` | the meaning of a passage for the person who has to act on it: one or two complete sentences, what and what for, with the words they need intact |

## Example

Passage:

> RECOMMENDED NEXT STEP (yours) -- in your own terminal: `grip-account login
> laurie-main`, `/login` as development@codetonight.co.za, `/exit`, then
> `grip-account mint laurie-main --as laurie`; the report will say the old
> token is kept as `laurie-old`, and I take it from there with
> `grip-claude-fleet converge --apply`.

Encapsulated:

> Sign in once as development@codetonight.co.za where `grip-account login
> laurie-main` takes you, leave, and run `grip-account mint laurie-main --as
> laurie`: it makes a new token for that account and stores it under the name
> `laurie`, and its report says the old one is kept as `laurie-old`. What
> follows is mine to do.

Every clause there is either a word of the passage or what one of its commands
does. "Never shown", "year-long" and "the other machines" are all true of the
tool and were in the first draft; none is in the passage, so none is here.
